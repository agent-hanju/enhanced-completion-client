# API 스트리밍 명세

이 문서는 현재 `completion_bridge` Python 구현의 공개 호출 방식과 요청·응답 모델,
스트리밍 delta 및 최종 결과 계약을 설명한다. 기준은 저장소의 0.3.1 코드다.
벤더 API의 전체 스키마와 지원 범위는 [지원표](Support-Matrix.md), 실제 변환 JSON은
[변환 예제](Conversion-Examples.md)를 함께 참고한다.

## 1. 처리 흐름과 반환값

```text
Bridge / SyncBridge 생성
  → 메시지 목록 + 호출 옵션
  → 내부 HubRequest 구성
  → 벤더 요청 body 변환
  → HTTP POST / SSE 수신
  → 벤더 mapper → 등록된 vocabulary mapper
  → HubResponse delta 순회 + 내부 병합
  → 최종 HubResponse
```

여기서 API는 Python 라이브러리 API다. 서버 측 SSE 응답을 받아 Python 객체로 제공하며,
브라우저에 전송할 별도 SSE endpoint나 `event: delta` / `event: done` 규약은 정의하지 않는다.

| 호출 | 비동기 `Bridge` | 동기 `SyncBridge` |
|---|---|---|
| `build_request(messages, ...)` | 벤더 요청 body `dict` | 동일 |
| `stream(messages, ...)` | `AsyncStream` 반환, `await` 없이 호출 | `SyncStream` 반환 |
| 스트림 순회 | `async for delta in stream` | `for delta in stream` |
| 순회 값 | `HubResponse` delta | 동일 |
| `stream.result` | 현재 `HubResponse` 또는 초기 `None` | 동일 |
| `complete(messages, ...)` | `await`하여 `HubResponse | None` 수신 | `HubResponse | None` 반환 |
| `count_tokens(messages, ...)` | `await`하여 `TokenCount` 수신 | `TokenCount` 반환 |
| 스트림 닫기 | `await stream.aclose()` | `stream.close()` |
| 브리지 닫기 | `await bridge.aclose()` | `bridge.close()` |

`build_request()`는 네트워크를 호출하지 않는다. `stream()`은 요청을 조립하고 스트림을
반환하며 실제 HTTP 전송은 순회를 시작할 때 발생한다. `complete()`도 내부적으로 동일한
스트리밍 경로를 끝까지 소비한다. 스트림은 한 번만, 하나의 소비자가 순회하는 용도로 사용한다.

## 2. 객체 생성

```python
from completion_bridge import Bridge, Hyperparameters
from completion_bridge.vendors import chat_completions

bridge = Bridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="your-model",
    hyperparameters=Hyperparameters(temperature=0.2),
    timeout=120.0,
)
```

| 생성자 인자 | 기본값 / 의미 |
|---|---|
| `vendor` | 필수. `VendorAdapter` |
| `base_url` | 필수. 뒤의 `/`를 제거한 뒤 어댑터 경로를 붙인다 |
| `model` | `None`. 요청별 기본 모델 |
| `api_key` | `None`. 인증에 사용할 키 |
| `hyperparameters` | `None`. `Hyperparameters` 또는 mapping |
| `vocabularies` | `()`. 생성 시 등록하며 요청 lowering과 응답 lifting에 사용 |
| `headers` | `None`. 기본 헤더와 벤더 헤더를 덮는 사용자 헤더 |
| `http_client` | `None`. 비동기는 `httpx.AsyncClient`, 동기는 `httpx.Client` |
| `timeout` | `120.0`. 내부 HTTP client 생성 시 전달 |

기본 헤더는 `Accept: text/event-stream`, `Content-Type: application/json`이다.
`api_key`는 Chat Completions와 Responses에서는 `Authorization: Bearer ...`, Messages에서는
`x-api-key`, Gemini에서는 `x-goog-api-key`로 전달한다. 인증 우선순위는 사용자 `headers`,
Bridge의 비어 있지 않은 `api_key`, 기존 어댑터의 키 순이며 헤더 이름의 대소문자를 구분하지 않는다.

Gemini도 `vendor=generate_content, model=..., api_key=...`로 생성한다. `for_model()`을
직접 호출하거나 빈 키를 Bridge에 전달할 필요가 없다. Bridge는 모델별 경로를 사용하는
어댑터의 선택적 `for_model(model) -> VendorAdapter` 계약으로 새 어댑터를 얻는다.
이 메서드는 원본을 변경하지 않아야 한다. 기존 `for_model()` 호출 방식도 사용할 수 있다.
`VendorAdapter.path`는 읽기 계약이므로 일반 속성과 읽기 전용 property 모두 허용한다.
요청별 `model=`은 생성 경로와 토큰 수 측정 경로에 적용되며 기본 모델을 변경하지 않는다.

브리지가 생성한 HTTP client는 브리지가 닫으며, 주입한 client의 수명은 호출자가 관리한다.
주입 client에는 브리지의 `timeout`을 다시 적용하지 않는다. 스트림 재연결·재개·재전송은
브리지 자체에서 구현하지 않는다.

## 3. 요청 계약

> 메시지 단위 `synthetic` 필드의 외부 연동은
> [사용자 작성 assistant 메시지 연동 계약](Synthetic-Assistant-Messages.md)을 참고한다.

### 3.1 공개 메서드 입력

네 메서드 `build_request`, `stream`, `complete`, `count_tokens`는 다음 인자를 공유한다.

```python
bridge.stream(
    messages,                  # Sequence[HubMessage | str | Mapping[str, Any]]
    tools=(),                  # Sequence[ToolDefinition]
    model=None,                # str | None
    hyperparameters=None,      # Hyperparameters | Mapping[str, Any] | None
    **params,                  # 호출별 벤더 확장 옵션
)
```

문자열은 `HubMessage.user(text)`로 바뀐다. mapping은 `HubMessage`로 검증한다.
`HubRequest` 객체 자체를 `stream()`의 첫 인자로 전달하는 인터페이스는 없다.

옵션 적용 순서는 생성자의 `hyperparameters` → 호출별 `hyperparameters` → `params`다.
호출별 hyperparameter의 명시적 `None`은 해당 기본값을 해제한다. `params`는 벤더 투영 후
덮어쓰지만, 어댑터가 보호하는 예약 필드는 그대로 적용되지 않을 수 있다.
모델은 `model or 생성자_model`로 선택한다. 세부 옵션은 [README](../README.md#요청-옵션과-hyperparameters)에 정리되어 있다.

### 3.2 HubRequest

브리지가 내부에서 구성하여 벤더 어댑터로 넘기는 중립 요청이다.

| 필드 | 타입 | 기본값 |
|---|---|---|
| `model` | `str \| None` | `None` |
| `messages` | `list[HubMessage]` | `[]` |
| `tools` | `list[ToolDefinition]` | `[]` |
| `hyperparameters` | `Hyperparameters` | 빈 옵션 객체 |
| `params` | `dict[str, Any]` | `{}` |

개념상 요청 JSON은 다음과 같다. 이 JSON을 그대로 벤더 서버에 보내는 것은 아니다.

```json
{
  "model": "your-model",
  "messages": [
    {"role": "user", "content": [{"type": "text", "text": "안녕하세요"}]}
  ],
  "tools": [],
  "hyperparameters": {"temperature": 0.2},
  "params": {}
}
```

### 3.3 HubMessage / ToolDefinition

`HubMessage.synthetic: bool = False`는 애플리케이션이 작성·편집한 assistant 이력의 선언이다.
`HubMessage.assistant(..., synthetic=True)`와 mapping 입력 모두 지원한다.
True는 assistant 역할에서만 허용하며 다른 역할에서는 입력 검증 오류가 발생한다.
기본 직렬화에 포함되지만 벤더 요청 필드로 전달하지 않는다.
Gemini는 명시적인 synthetic 메시지의 미서명 함수 호출에만 특수 처리를 적용한다.
`HubResponse`의 delta·result에는 이 필드가 없으며,
`HubMessage.of_response(response)`로 이력에 옮기면 기본값 False가 된다.

`HubMessage`는 필수 `role: str`과 기본값이 `[]`인 `content: list[Block]`을 갖는다.
`HubMessage.user()`, `.system()`, `.assistant()`로 만들 수 있다.
`HubMessage.of_response(result)`는 응답 role(없으면 `assistant`)과 content를 복사한다.
응답 id, model, usage, stop_reason은 대화 메시지로 옮기지 않는다.

`ToolDefinition`의 필수 필드는 `name: str`이다. 나머지는 `description=""`,
`input_schema={}`, `vendor=None`, `wire={}`이다. 벤더 내장 도구는
`ToolDefinition.native(vendor, wire)`로 명시한다.

요청 첨부의 원격 `file_id` / `container_id` 참조는 공통 요청 경로에서 거부될 수 있다.
URL 또는 inline data를 사용한다. 벤더별 변환·생략 정책은 지원표를 따른다.

### 3.4 토큰 수 측정

`count_tokens()`는 동일한 `build_request()` 경로로 생성 body를 만든 뒤 어댑터의
`token_count_calls()`를 실행한다. SSE나 생성 응답을 만들지 않고 JSON 응답을 읽는다.
비동기는 `await bridge.count_tokens(...)`, 동기는 `bridge.count_tokens(...)`로 호출한다.

결과는 `TokenCount(input_tokens: int, tokenized: TokenizedPrompt | None)`이다.
vLLM Chat Completions는 `/tokenize` → `/detokenize`를 호출하고 `tokenized.text`와
`tokenized.token_ids`를 채운다. 다른 어댑터의 `tokenized`는 `None`이다.
Messages는 `/v1/messages/count_tokens`, Responses는 `/v1/responses/input_tokens`,
Gemini는 모델의 `:countTokens` 경로를 사용한다. 엔드포인트별 생성 전용 필드 제외 및
Gemini 요청 wrapping은 어댑터가 처리한다.

HTTP 비성공 응답은 `TransportError`, 예상 JSON 객체·필드가 없는 응답은 `MappingError`다.
실행 예제와 엔드포인트 표는 [README](../README.md#토큰-수-측정)에 있다.

## 4. HubResponse

delta, 부분 결과, 최종 결과는 모두 같은 타입이다. delta는 이번에 추가되거나 갱신된
필드만 의미가 있으며, 매번 전체 응답을 담는 snapshot이 아니다.

| 필드 | 타입 | 기본값 | 병합 |
|---|---|---|---|
| `id` | `str \| None` | `None` | 새 값으로 덮기 |
| `model` | `str \| None` | `None` | 새 값으로 덮기 |
| `role` | `str \| None` | `None` | 새 값으로 덮기 |
| `content` | `list[Block]` | `[]` | 블록 키에 따라 병합 또는 추가 |
| `stop_reason` | `str \| None` | `None` | 새 값으로 덮기 |
| `block_reason` | `str \| None` | `None` | 새 값으로 덮기 |
| `usage` | `Usage \| None` | `None` | 하위 필드 병합 |

`Usage.input_tokens`, `Usage.output_tokens`는 각각 `int | None`이고 기본값은 `None`이다.
이 두 필드는 합산하지 않고 최신 값으로 덮는다. 벤더 확장 필드도 보존할 수 있다.
`Usage.native`(`dict[str, Any] | None`)는 벤더가 보낸 usage 객체 원본이다. 캐시·추론 토큰 같은
세부 항목도 그대로 두며, 스트림 조각마다 온 원본을 최상위 key 단위로 합친다. 같은 key는 나중
조각의 값이 대신한다.
`HubRequest`, `HubMessage`, `HubResponse`, `Usage`는 선언 외 필드를 허용한다.

`response.text`는 `TextBlock.text`만 이어 붙인 편의 속성이다. 추론, 도구 인수,
오디오 transcript는 포함하지 않으며 일반 JSON 필드로 직렬화되지 않는다.
`response.blocks_of(ToolUseBlock)`처럼 특정 블록 타입을 조회할 수도 있다.

`stop_reason`은 생성 종료 사유이고 `block_reason`은 입력 거부로 생성하지 못한 사유다.
대표 종료 사유는 `end_turn`, `tool_use`, `max_tokens`, `content_filter`, `error`다.
타입은 닫힌 enum이 아니며 벤더 고유 문자열이 남을 수 있다. 모든 응답에 종료 사유나
사용량이 반드시 존재한다는 보장은 없다.

### 4.1 Content block

공통 필드는 `type: str`, `seq: tuple[int, ...] | None = None`, `index: int | None = None`,
`vendor: str | None = None`, `native: dict = {}`이다. `vendor`는 원본 벤더, `native`는 원형
보존 정보다. `seq`는 delta에만 있는 병합·순서 키이고 `index`는 최종 결과에만 있는 위치다.
규칙은 [블록 index와 seq 튜플](block_index_and_key_tuple.md)에 있다.

0.3.1부터 블록의 벤더 식별 필드는 `source` 대신 `vendor`다. 이전 키의 자동 alias는 없다.
`Citation.source`와 `SearchResultBlock.source`는 별개의 의미이므로 바꾸지 않는다.
저장 데이터·호출 코드 적용 사항은 [0.3.1 변경 보고](Release-0.3.1-common-mcp-server.md)에 있다.

| `type` | 주요 필드 / 의미 |
|---|---|
| `text` | `text`, `citations`, `signature`: 본문과 인용 |
| `thinking` | `thinking`, `signature`, `encrypted_content`: 추론 |
| `tool_use` | `id`, `name`, `kind`, `input_json`, `input`, `signature`: 클라이언트 도구 호출 |
| `tool_result` | `tool_use_id`, `content`, `structured_content`, `blocks`, `is_error`: 도구 결과, 주로 요청 방향 |
| `server_tool` | `id`, `name`, `status`, `input_json`, `output`, `is_error`, `raw`: 서버 실행 도구 |
| `image` | `media_type`, `data`, `url`, `file_id`: 이미지 |
| `audio` | `data`, `uri`, `format`, `media_type`, `transcript`: 음성 |
| `document` | `id`, `title`, `text`, `data`, `uri`, `media_type`: 문서, 주로 요청 방향 |
| `search_result` | `source`, `title`, `content`(청크 문자열 목록), `citations_enabled`: 검색 결과, 요청 방향 |
| `annotation` | `target_seq`(delta) / `target_index`(최종 결과), `start_index`, `end_index`, `uri`, `title`: 본문 범위 주석 |
| `grounding` | `sources`, `supports`, `search_queries`: 근거 관계 |
| `citation` | 구 저장 데이터 호환용. 새 인용은 `text.citations` 또는 `annotation` 사용 |
| 기타 | 등록된 사용자 블록 또는 원형을 보존하는 `VendorBlock` |

블록별 전체 필드·타입은 [blocks.py](../src/completion_bridge/models/blocks.py)에 정의되어 있다.

`tool_result.blocks`는 `list[Block]`이다. JSON에서 읽을 때 `type`으로 등록된 블록 클래스로
복원되므로 저장할 때 중첩 블록의 `type`이 남아 있어야 한다.

검색 결과처럼 전달 가능한 블록이 있으면 그 본문을 사용하고 `structured_content`를 중복해서
보내지 않는다. 다만 모든 어댑터에서 단순히 `blocks`의 유무만으로 결정되는 것은 아니다.

| 어댑터 | 도구 결과 모델 입력 선택 |
|---|---|
| Chat Completions | 중첩 블록의 렌더링 문자열이 있으면 `content` 뒤에 추가. 없으면 `structured_content` JSON, 그마저 없으면 `content` |
| Messages | 블록이 없으면 비어 있지 않은 `content` 우선, 그다음 `structured_content` JSON. 블록이 있으면 지원되는 part와 `content` 사용 |
| Responses | 렌더링 part가 있고 중첩 블록도 있으면 part 목록. 그렇지 않으면 `structured_content` JSON을 우선하고 본문 또는 fallback 사용 |
| Generate Content | 동일 벤더의 `native`가 있으면 원형 재생. 그 외에는 블록이 있을 때 본문·검색 텍스트와 지원 part, 없으면 `structured_content` 또는 본문 |

따라서 `structured_content`를 항상 모델에 숨겨지는 저장 전용 필드로 취급하면 안 된다.

`search_result`는 Anthropic에 네이티브 `search_result`로 보낸다. 이 API는 search result가 있는 `tool_result`의 모든 블록이 search result이기를 요구하므로, `content`나 다른 블록이 함께 있으면 `MappingError`다. 한 요청의 search result는 모두 같은 인용 설정이어야 하므로 하나라도 `citations_enabled`가 참이면 전부 켠다. 인용을 켠 document나 search result와 `output_config.format`을 함께 보내면 `MappingError`다. 다른 벤더에는 `SearchResultBlock.to_prompt()`의 `<document id="출처">` 텍스트로 보내며, 청크마다 `<content>`를 하나씩 쓴다.
벤더마다 지원 블록이 다르고 vocabulary를 등록하면 출력 블록과 분할 경계가 달라질 수 있다.

## 5. 스트리밍 출력 및 병합

### 5.1 청크 계약

하나의 수신 SSE 프레임은 0개 이상의 `HubResponse` delta가 된다. 하트비트는 생략하며,
mapper가 버퍼링한 내용은 종료 시 `flush()`에서 추가 delta로 나올 수 있다.
따라서 네트워크 청크, SSE 프레임, 공개 delta 사이에 일대일 대응이나 고정 개수는 없다.
메타데이터 또는 usage만 있는 delta의 `delta.text`는 빈 문자열일 수 있다.

내부 병합은 각 delta를 소비자에게 내보내기 직전에 수행한다. 한 프레임에서 여러 delta가
생성돼도 하나를 반영하면 즉시 전달하고 다음 delta를 처리한다. 병합 실패 시 남은 delta는
처리하지 않으며, 실패한 delta와 부분 반영 결과는 예외에 포함한다.

### 5.2 병합 규칙

| 대상 | 규칙 |
|---|---|
| `None` | 변경 없음. 기존 값을 지우지 않음 |
| 명시하지 않은 기본값 | 처음 상태를 채울 때 사용, 기존 값을 덮지 않음 |
| 일반 문자열 | 이어 붙이기: 본문, 추론, `input_json`, 음성 data/transcript 등 |
| overwrite 표시 / Literal / 병합 키 필드 | 최신 값으로 교체 |
| 일반 숫자 | 합산. 단, usage처럼 overwrite 표시가 있으면 덮기 |
| 모델 | 필드별 재귀 병합 |
| 일반 dict | 키 단위 갱신. overwrite 표시 dict는 통째로 덮기 |
| 모델 리스트 | 병합 키 표시 필드(content block은 `seq`), 없으면 `index` 값으로 같은 슬롯 병합 |
| 키 없는 모델 / 원시값 리스트 | 도착 순서로 추가 |
| 선언 외 확장 필드 | 최신 값으로 교체 |

content block은 같은 `seq`를 가진 조각을 합치며 배열 위치나 `type`만으로 짝짓지 않는다.
모든 벤더 어댑터는 응답의 모든 content block에 `seq`를 채운다. 병합이 끝난 최종 결과
(`result`, `complete()`)는 블록을 `seq` 사전식 순서로 늘어놓고 `index`를 0부터
1씩 매긴 뒤 `seq`를 비운다. 따라서 최종 결과에서는 `content[i].index == i`이고, 도착 순서가
아니라 벤더 좌표 순서를 따른다. Bridge는 입력 delta에서 `seq`가 없는 블록을 발견하면
`MappingError`를 원인으로 한 `StreamMergeError`를 전달한다. 오류 상태에 좌표가 없는
블록이 남았을 때의 `result`는 재정렬하지 않은 best effort 모델이다.

`seq`는 변환기별 최대 길이로 -1 정규화한 논리 좌표 뒤에 vocabulary 성분이 이어지는 정수
튜플이다. annotation은 대상 블록의 하위 좌표를, 응답 수준 메타데이터는 변환기가 정한
고정 그룹 좌표를 사용한다. API별 좌표와 정규화 규칙은
[블록 index와 seq 튜플](block_index_and_key_tuple.md)에 있다.

#### delta를 직접 병합하는 소비자

- delta 블록은 `seq`가 같은 것끼리 병합한다. JSON에서는 정수 배열이므로 원소를 차례로 비교한다.
- 화면 순서는 `seq`의 사전식 비교로 정한다. 새 `seq`가 오면 그 순서에 맞는 위치에 삽입한다.
- delta에는 `index`가 없다. `index`는 최종 결과에만 있다.
- 스트림이 끝나면 최종 결과로 교체하고, 저장도 최종 결과로 한다.

#### Chat Completions의 블록 순서

Chat Completions 어댑터는 블록 최초 등장 순서대로 번호 `n=0, 1, 2, …`를 부여하고
일반 채널은 `seq=(n, -1)`, 도구는 같은 그룹에서 `seq=(n, tool_index)`를 쓴다.

- 같은 채널의 연속 조각은 같은 블록에 누적한다. role/usage/빈 delta는 구간을 끊지 않는다.
- content/reasoning/refusal/audio 채널이 바뀌거나 도구 호출이 끼면 다음 채널 조각은 새 블록이다.
- 도구 호출은 원본 `tool_calls[].index`를 내부 매핑해 최초 허브 번호를 유지한다.
  원본 index가 없는 완성 응답은 tool_calls 배열 위치를 사용한다.
- 한 이벤트에 여러 필드가 있으면 reasoning → content → refusal → audio → annotations →
  tool_calls 순서로 처리한다. 이는 어댑터 처리 순서이며 실제 생성 선후를 뜻하지 않는다.
- annotation은 텍스트 채널을 끊지 않는다. 전체 content 문자열의 인용 범위가 이미 수신한 한
  텍스트 블록 안에 있으면 그 블록을 `target_seq`로 두고 범위를 블록 상대 오프셋으로 변환하며,
  annotation 자체는 그 블록 바로 뒤의 seq를 받는다. 여러 블록에 걸치거나 아직 대상을 결정할 수
  없으면 `target_seq`는 None이고 원본 범위를 유지하며, 새 출현 그룹을 배정한다.
  최종 결과에서 `target_seq`는 대상 블록의 `target_index`로 바뀐다. 원본 annotation은 native에도
  보존한다.

예: `content A → reasoning B → content C`는 `text #0(A), thinking #1(B), text #2(C)`다.
채널 전환 없는 같은 필드 내부의 원래 경계는 복원하지 않는다.

요청 변환에서는 0부터 연속한 순서 index가 모두 있는 HubMessage를 index순으로 순회한다.
미지정 index가 있거나 index가 연속하지 않으면 기존 배열 순서를 유지한다.
assistant의 text-only content는 순서대로 concat하고, thinking은 기존 동일 벤더 및
reasoning_input_field 설정에 따라 해당 필드로 concat한다. 도구 호출은 각각 tool_calls
항목으로 변환하며 허브 index를 요청에 보내지 않는다. 위 예는 content="AC", 설정된
reasoning 필드="B"로 내려간다. 멀티모달 content 배열의 기존 변환은 유지한다.

일반적인 dict 병합이나 `delta.text` 누적만으로 전체 응답을 복원할 수 없다.

### 5.3 텍스트 delta → 최종 결과 예시

다음은 병합 동작을 설명하는 최소 예시다. 벤더별 실제 청크 순서와 생략 필드는 달라질 수 있다.

```json
[
  {"id": "resp-1", "model": "your-model", "role": "assistant"},
  {"content": [{"type": "text", "seq": [0, 0], "text": "안녕"}]},
  {"content": [{"type": "text", "seq": [0, 0], "text": "하세요"}]},
  {"stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 2}}
]
```

최종 결과(`stream.result.model_dump(exclude_none=True)`)는 다음과 같다.

```json
{
  "id": "resp-1",
  "model": "your-model",
  "role": "assistant",
  "content": [{"type": "text", "index": 0, "native": {}, "text": "안녕하세요", "citations": []}],
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 5, "output_tokens": 2}
}
```

### 5.4 도구 인수 분할 예시

```json
[
  {"content": [{"type": "tool_use", "seq": [1, 0], "id": "call-1", "name": "get_weather", "input_json": "{\"city\":"}]},
  {"content": [{"type": "tool_use", "seq": [1, 0], "input_json": "\"서울\"}"}]},
  {"stop_reason": "tool_use"}
]
```

최종 블록의 `input_json`은 `{"city":"서울"}`, `input`은 `{"city": "서울"}`이 된다.
`input_json`이 유효한 JSON 객체일 때 모델 검증 과정에서 `input`을 채운다. 부분 JSON,
잘못된 JSON, 객체가 아닌 JSON은 이 자동 파싱으로 `input`을 채우지 않는다.
최종 결과라도 잘못된 인수를 별도 오류로 강제 변환하지 않으므로 호출자가 검증해야 한다.
브리지는 클라이언트 도구를 실행하지 않는다. 완성된 도구 호출을 검증·실행한 뒤 결과를
`ToolResultBlock`으로 후속 메시지에 넣는 것은 호출자의 책임이다.

### 5.5 JSON으로 전달할 때

객체를 외부로 전송하려면 `delta.model_dump(mode="json", exclude_none=True)` 또는
`delta.model_dump_json(exclude_none=True)`로 직렬화할 수 있다. 기본값도 출력될 수 있다.
`exclude_unset=True`나 `exclude_none=True`를 쓰면 delta에는 `index` 키가, 최종 결과에는
`seq`와 `target_seq` 키가 남지 않는다.
`exclude_unset=True`는 명시되지 않은 `type` 판별자까지 생략할 수 있으므로 이 옵션만으로
독립적인 블록 JSON 계약을 만들지 않는다. 이 라이브러리는 외부 delta 재전송용 envelope,
완료 알림, 재개 ID를 제공하지 않으며 필요한 경우 소비 앱에서 별도로 정의해야 한다.

## 6. 종료·부분 결과·취소·오류

벤더 어댑터의 종료 판정 또는 SSE 입력의 정상 소진 뒤 mapper를 flush하고,
전송 리소스를 해제한다. 종료 프레임에 담긴 usage나 종료 사유도 먼저 처리한다.
각 delta는 모델에 직접 병합하고 성공한 직후 소비자에게 전달한다.
같은 원본 이벤트에서 나온 delta들도 개별적으로 반영·전달한다. 실패 시 중단하고
실패 delta와 그 시점의 결과를 `StreamMergeError`로 전달한다.

| 상황 | `result` |
|---|---|
| delta 처리 전 | `None` |
| 순회 중 | 현재 병합 모델 |
| 정상 종료 | flush 출력까지 반영한 모델 또는 `None` |
| 병합 오류 | 실패 delta를 중단 지점까지 반영한 모델 또는 `None` |
| 전송·매핑 오류, 취소 | 그때까지 반영한 모델 또는 `None` |

`result`는 별도 검증 없이 현재 모델의 독립된 복사본을 반환한다. 오류 후 결과에는
실패 delta의 일부가 포함될 수 있고 스키마 유효성을 보장하지 않는다. 정상적인 결과는
seq를 index로 변환하며, 실패로 seq가 없는 블록이 남으면 원래 좌표 형태 그대로 반환한다.
정상 완료·오류·취소 상태는 데이터와 별개다. 정상 EOF만으로 모델의 정상 생성까지
확인되지는 않으므로 `stop_reason`, `block_reason`, content를 별도로 확인한다.
`stop_reason` delta를 받자마자 중단하면 뒤의 usage나 flush 결과를 놓칠 수 있다.
최종 응답을 별도의 마지막 delta로 한 번 더 내보내지는 않는다.

`aclose()` / `close()`는 아직 닫히지 않았다면 mapper를 flush해 부분 결과에 반영하고
전송을 닫는다. 이 flush 결과는 소비자에게 yield하지 않는다. 이미 닫힌 스트림은 다시
flush하지 않는다. 예외 경로의 `finally`는 리소스를 해제하지만 정상 flush를 보장하지
않으므로 오류 후 `result`은 이미 병합된 내용까지만 보존한다고 보아야 한다.
닫기 자체가 중단된 스트림을 정상 완료 상태로 바꾸지는 않는다.

| 오류 | 현재 동작 |
|---|---|
| HTTP 비성공 상태 | `TransportError`, `status_code`와 응답 본문 앞 최대 500자의 `detail` |
| 연결·읽기 timeout 등 | 전송 계층 예외가 그대로 전파될 수 있음 |
| 응답 디코딩·매핑 실패 | 어댑터의 `MappingError` 등 전파 |
| 입력 모델 검증 실패 | 모델 검증 예외 등 전파 |
| 모델 병합 실패 | `StreamMergeError`, `delta`·`result`와 `__cause__` 제공 |

벤더 오류가 응답의 `stop_reason="error"` 등으로 표현되는 경우도 있으므로 모든 실패가
예외로만 전달되는 것은 아니다. 오류를 공통 SSE error 이벤트로 바꾸거나 자동 재시도하지 않는다.

## 7. 사용 예제

### 7.1 비동기 스트림

```python
import asyncio
from completion_bridge import Bridge, HubMessage
from completion_bridge.vendors import chat_completions

async def main():
    async with Bridge(
        vendor=chat_completions,
        base_url="http://127.0.0.1:8000",
        model="your-model",
    ) as bridge:
        history = [HubMessage.user("안녕하세요")]
        stream = bridge.stream(history)
        try:
            async for delta in stream:
                print(delta.text, end="", flush=True)
            result = stream.result
            print(result.model_dump_json(exclude_none=True))
            history.append(HubMessage.of_response(result))
        finally:
            await stream.aclose()

asyncio.run(main())
```

### 7.2 동기 스트림

```python
from completion_bridge import SyncBridge
from completion_bridge.vendors import chat_completions

with SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="your-model",
) as bridge:
    stream = bridge.stream(["안녕하세요"])
    try:
        for delta in stream:
            print(delta.text, end="", flush=True)
        result = stream.result
    finally:
        stream.close()
```

### 7.3 조기 중단 후 부분 결과

아래 코드는 이미 생성한 비동기 `bridge`를 사용한다. 반복 iterator를 보관해 종료 순서를
명확히 하고, 스트림을 먼저 닫아 아직 닫히지 않은 mapper의 flush 기회를 확보한다.

```python
stream = bridge.stream(["긴 설명을 작성해줘"])
iterator = stream.__aiter__()
try:
    async for delta in iterator:
        print(delta.text, end="")
        if stream.result is not None and len(stream.result.text) >= 100:
            break
finally:
    try:
        await stream.aclose()
    finally:
        await iterator.aclose()

result = stream.result
# 종료 상태와 무관하게 현재 반영 결과를 조회한다. 응답이 없으면 None이다.
```

## 8. 구현 및 검증 근거

- [bridge.py](../src/completion_bridge/bridge.py): 공개 메서드, pipeline, 스트림 수명
- [hub.py](../src/completion_bridge/models/hub.py): 요청·응답 모델
- [blocks.py](../src/completion_bridge/models/blocks.py): 블록 필드 및 도구 인수 파싱
- [merge.py](../src/completion_bridge/streaming/merge.py): delta 병합
- [http/client.py](../src/completion_bridge/http/client.py): HTTP 전송 및 오류
- [test_bridge.py](../tests/test_bridge.py): EOF 완료, 부분 결과, usage, HTTP 오류
- [test_merge.py](../tests/test_merge.py): 병합 규칙
- [test_token_count.py](../tests/test_token_count.py): 토큰 수 요청·응답 및 오류
- [test_search_results.py](../tests/test_search_results.py): 검색 결과 변환·인용·중첩 JSON 복원
- [변환 예제](Conversion-Examples.md): 벤더별 요청·raw event·HubResponse JSON

## 원본 Response 객체

각 어댑터의 `to_native()`는 `NativeAccumulator[응답 타입]`을 반환한다. 누적기는 로깅이나
HTTP 호출 없이도 사용할 수 있으며, `apply()`로 디코딩된 chunk를 반영하고 정상 종료 후
`build()`로 API별 Pydantic 응답 객체를 얻는다.

| 어댑터 | `build()` 반환 타입 | 중첩 속성 예시 |
|---|---|---|
| `chat_completions` | `ChatCompletionsResponse` | `response.choices[0].message.content` |
| `responses` | `ResponsesResponse` | `response.output[0].content` |
| `messages` | `MessagesResponse` | `response.content[0].text` |
| `generate_content` | `GenerateContentResponse` | `response.candidates[0].content.parts` |

네 응답 타입은 `completion_bridge`와 `completion_bridge.vendors`에서 import할 수 있다.
하위 모델은 `completion_bridge.models.native`에서 제공한다. 공식 SDK 타입이 아닌 이 라이브러리의
응답 타입이며, SDK 의존성을 추가하지 않는다. Google 응답 속성은 `usageMetadata`,
`thoughtSignature`처럼 REST JSON 필드명을 사용한다.

```python
from collections.abc import Iterable

from completion_bridge import MessagesResponse, SseEvent
from completion_bridge.vendors import messages


def merge_messages(frames: Iterable[SseEvent]) -> MessagesResponse:
    """정상적으로 수신한 Messages SSE 이벤트를 원본 응답 객체로 누적한다."""
    native = messages.to_native()
    for frame in frames:
        if frame.is_comment_only:
            continue
        chunk = messages.decode(frame)
        if chunk is not None:
            native.apply(chunk)
        if messages.is_terminal(frame):
            break
    return native.build()
```

`frames`는 이미 SSE 파싱을 마친 이벤트 목록이나 이터레이터다. 호출자는 정상 종료 여부를
확인한 뒤 `build()`를 한 번 호출한다. 같은 chunk를 다른 처리에도 사용한다면 `apply()`에는
깊은 복사본을 전달한다. 누적기에 전달한 chunk는 누적기가 소유한다.

모델의 주요 응답 구조·도구 호출·사용량은 타입으로 선언하며, 미디어나 도구별 고유 payload는
JSON 객체로 유지한다. 모든 계층에서 추가 필드를 허용한다. 누적 중 아직 받지 않은 필드를
표현하기 위해 메타데이터 필드는 기본값을 가지므로, 공식 API의 모든 필수 필드가 존재하는지를
검사하는 완전한 스키마 검증기는 아니다. 선언된 필드의 타입은 생성·속성 대입 시 검증하며,
잘못된 타입은 `pydantic.ValidationError`, 누적 순서·지원 규칙 위반은 `MappingError`가 된다.

JSON이 필요하면 `response.model_dump(mode="json", by_alias=True, exclude_unset=True)`를
사용한다. 미수신 필드의 기본값은 출력하지 않고, 명시적인 null과 확장 필드는 보존한다.
벤더별 병합 규칙은 [원본 응답 병합](Wire-Logging.md#원본-응답-병합)에 설명한다.

`Bridge`는 호출 로그가 활성화됐을 때 이 누적기를 사용한다. `stream.result`와 `complete()`의
반환 타입은 기존과 같이 `HubResponse`다.

## 요청 추적 로그

`Bridge`와 `SyncBridge`의 `stream()` / `complete()`는 선택적 키워드 인자
`request_id: str | None = None`을 받는다. 생략하면 UUID4를 생성하며 벤더 요청에는 전달하지 않는다.
요청 JSON, SSE, 같은 벤더 API 형식으로 병합한 완료 응답과 실패·취소를 전용 DEBUG 로거로 기록한다.
[로그 계약과 파일 저장 설정](Wire-Logging.md)을 참고한다.


## 델타 누적

`StreamMerger(Model)`은 모델 필드의 병합 선언에 따라 델타를 누적한다. Bridge는
`StreamMerger(HubResponse)`를 사용하며, 입력 델타를 변경하지 않고 소비자에게 전달한다.
벤더 원본 응답은 벤더별 `NativeAccumulator`가 별도로 처리한다.

### 스키마와 오류 처리 주의사항

델타와 결과는 같은 모델 타입을 사용한다. 모델은 부분 데이터를 표현할 수 있어야 하며,
생략될 수 있는 필드는 기본값 등으로 생략을 허용하는 편이 좋다. 스키마 제약을 검사하거나
`model_validate()`를 추가 호출하지 않는다. `validate_assignment=True`도 추가하지 않는다.
일반 `getattr`·`setattr`을 사용하므로 모델이 원래 정의한 속성 대입 동작은 따른다.
기존 값에 대한 None 입력은 무시한다. 처음 받은 필수 nullable 필드의 명시적 None은 보존한다.

`apply()`는 하나의 모델을 직접 변경한다. 새 모델의 일반 값은 초기화 시 복사하고 하위
모델·목록을 DFS로 처리한다. 누적 모델 전체의 후보 복사·확정·롤백·재검증은 없다.
실패하면 남은 작업을 중단하고 `StreamMergeError`를 발생시킨다.

- `error.delta`: 실패한 입력 delta 원본. 정상 delta로 전달하지 않는다.
- `error.result`: 중단 지점까지 반영한 모델의 독립된 스냅샷 또는 None.
- `error.__cause__`: 실제 실패 원인.

오류 결과는 진단·보존용 best effort 데이터이며 검증된 정상 응답이라는 보증은 없다.
오류 전 성공 delta는 이미 소비자에게 전달됐고, 실패 delta는 예외를 통해 별도로 제공된다.
정상 종료 여부·오류·취소는 결과 데이터와 별개다. `build()`와 스트림의 `partial`은 없다.

### 병합 규칙과 실행 순서

문자열 연결·숫자 합산·사전의 얕은 갱신·식별 목록 병합 규칙을 적용한다.
overwrite·index·Literal 필드는 교체하고 extra 필드는 새 값으로 교체한다.
같은 목록의 식별 필드는 고정하며 다른 필드로 변경하면 병합 오류가 발생한다.
일반 모델과 목록을 직접 사용하고, 전용 누적 컨테이너와 실행 클래스는 사용하지 않는다.
이름 있는 내부 메서드와 인수를 작업 스택에 등록하며, 자식 작업은 다음 형제보다 먼저
완료한다. 일반 값만 있는 목록은 원소별 실행 객체를 만들지 않고 추가한다.

식별 목록의 검색 사전은 delta 사이에도 유지한다. 검색은 평균 O(1)이며 처음 검색 사전을
구성할 때만 기존 원소를 순회한다. 목록·모델 교체 시 버리는 하위 검색 정보는 제거한다.
`result` 조회와 오류 스냅샷 복사는 데이터 크기에 비례한다. 문자열 연결 및 도구 인수 JSON
파싱, 소비자가 매 delta마다 전체 결과를 조회하는 비용은 별도로 누적될 수 있다.
런타임의 `active_response`는 조회 시에만 스냅샷을 만들고, 종료 시 한 번 보관한다.
[성능 측정](StreamMerger-Benchmark.md), [관련 코드 검토](StreamMerger-Best-Effort-Review.md)를 참고한다.

### 식별 목록 단순화 기록 (2026-10-17)

한때 같은 Indexed List에서 식별 필드가 다른 경우를 상정한 디자인의 Indexed List를
만들었지만, 해당하는 케이스가 없어 구현을 단순화하였다. 후에 필요한 케이스가 생긴다면
원복한다. 식별 필드 고정 정책은 유지한다. 이후 모델 인스턴스에 직접 병합하도록 변경하면서
Indexed List 전용 클래스는 제거했으며, 검색 사전은 목록이 유지되는 동안 재사용한다.


`Vocabulary`는 추상 기반 클래스다. 하위 클래스는 `blocks()`를 구현하여 등록할
블록 타입의 고정 튜플을 반환한다. 클래스 속성이나 프로퍼티 대신 일반 메서드를 사용한다.
`Vocabulary()`와 blocks를 구현하지 않은 하위 클래스의 생성은 TypeError로 거부된다.
`lower()`와 `lift_mapper()`는 선택 사항이며 기본 구현은 None을 반환한다.


HTTP 요청은 `completion_bridge.http.client.HttpClient`와 `AsyncHttpClient`가 담당한다.
각 객체가 httpx 클라이언트와 소유권을 관리하고 `post_json()`·`stream_sse()`를 제공한다.
비동기 객체의 두 메서드는 각각 await·async for로 사용한다. 자체 연결 풀은 최초 요청에
생성하며 close()/aclose()에서 종료한다. 외부에서 주입한 httpx 클라이언트는 종료하지 않는다.
기존 transport 패키지와 클라이언트를 인자로 받던 전역 요청 함수는 제공하지 않는다.
SSE 이벤트와 파서는 `completion_bridge.http.sse`에 있다.
