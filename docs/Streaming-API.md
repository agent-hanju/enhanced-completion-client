# API 스트리밍 명세

이 문서는 현재 `completion_bridge` Python 구현의 공개 호출 방식과 요청·응답 모델,
스트리밍 delta 및 최종 결과 계약을 설명한다. 기준은 저장소의 0.2.0 코드다.
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
| `stream.partial` | 현재 병합 상태 `HubResponse` | 동일 |
| `stream.result` | 정상 순회 완료 후 최종 `HubResponse` | 동일 |
| `complete(messages, ...)` | `await`하여 최종 `HubResponse` 수신 | 최종 `HubResponse` 반환 |
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
| `extractors` | `None`. media type별 텍스트 추출 함수 mapping |
| `headers` | `None`. 기본 헤더와 벤더 헤더를 덮는 사용자 헤더 |
| `http_client` | `None`. 비동기는 `httpx.AsyncClient`, 동기는 `httpx.Client` |
| `timeout` | `120.0`. 내부 HTTP client 생성 시 전달 |

기본 헤더는 `Accept: text/event-stream`, `Content-Type: application/json`이다.
브리지가 생성한 HTTP client는 브리지가 닫으며, 주입한 client의 수명은 호출자가 관리한다.
주입 client에는 브리지의 `timeout`을 다시 적용하지 않는다. 스트림 재연결·재개·재전송은
브리지 자체에서 구현하지 않는다.

## 3. 요청 계약

> 메시지 단위 `synthetic` 필드의 외부 연동은
> [사용자 작성 assistant 메시지 연동 계약](Synthetic-Assistant-Messages.md)을 참고한다.

### 3.1 공개 메서드 입력

세 메서드 `build_request`, `stream`, `complete`는 다음 인자를 공유한다.

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
`HubResponse`의 delta·partial·최종 결과에는 이 필드가 없으며,
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
`HubRequest`, `HubMessage`, `HubResponse`, `Usage`는 선언 외 필드를 허용한다.

`response.text`는 `TextBlock.text`만 이어 붙인 편의 속성이다. 추론, 도구 인수,
오디오 transcript는 포함하지 않으며 일반 JSON 필드로 직렬화되지 않는다.
`response.blocks_of(ToolUseBlock)`처럼 특정 블록 타입을 조회할 수도 있다.

`stop_reason`은 생성 종료 사유이고 `block_reason`은 입력 거부로 생성하지 못한 사유다.
대표 종료 사유는 `end_turn`, `tool_use`, `max_tokens`, `content_filter`, `error`다.
타입은 닫힌 enum이 아니며 벤더 고유 문자열이 남을 수 있다. 모든 응답에 종료 사유나
사용량이 반드시 존재한다는 보장은 없다.

### 4.1 Content block

공통 필드는 `type: str`, `index: int | None = None`, `source: str | None = None`,
`native: dict = {}`이다. `source`는 원본 벤더, `native`는 원형 보존 정보다.

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
| `annotation` | `target_index`, `start_index`, `end_index`, `uri`, `title`: 본문 범위 주석 |
| `grounding` | `sources`, `supports`, `search_queries`: 근거 관계 |
| `citation` | 구 저장 데이터 호환용. 새 인용은 `text.citations` 또는 `annotation` 사용 |
| 기타 | 등록된 사용자 블록 또는 원형을 보존하는 `VendorBlock` |

블록별 전체 필드·타입은 [blocks.py](../src/completion_bridge/blocks.py)에 정의되어 있다.
벤더마다 지원 블록이 다르고 vocabulary를 등록하면 출력 블록과 분할 경계가 달라질 수 있다.

## 5. 스트리밍 출력 및 병합

### 5.1 청크 계약

하나의 수신 SSE 프레임은 0개 이상의 `HubResponse` delta가 된다. 하트비트는 생략하며,
mapper가 버퍼링한 내용은 종료 시 `flush()`에서 추가 delta로 나올 수 있다.
따라서 네트워크 청크, SSE 프레임, 공개 delta 사이에 일대일 대응이나 고정 개수는 없다.
메타데이터 또는 usage만 있는 delta의 `delta.text`는 빈 문자열일 수 있다.

내부 병합은 delta를 소비자에게 내보내기 전에 수행한다. 한 프레임에서 여러 delta가
생성되면 그 프레임의 delta들을 먼저 병합하므로 `partial`은 소비자가 방금 받은 delta보다
더 진행된 상태일 수 있다.

### 5.2 병합 규칙

| 대상 | 규칙 |
|---|---|
| `None` | 변경 없음. 기존 값을 지우지 않음 |
| 명시하지 않은 기본값 | 처음 상태를 채울 때 사용, 기존 값을 덮지 않음 |
| 일반 문자열 | 이어 붙이기: 본문, 추론, `input_json`, 음성 data/transcript 등 |
| overwrite 표시 / Literal / index 필드 | 최신 값으로 덮기 |
| 일반 숫자 | 합산. 단, usage처럼 overwrite 표시가 있으면 덮기 |
| 모델 | 필드별 재귀 병합 |
| 일반 dict | 키 단위 갱신. overwrite 표시 dict는 통째로 덮기 |
| 모델 리스트 | 명시된 index 메타 필드, 없으면 `index` 값으로 같은 슬롯 병합 |
| 키 없는 모델 / 원시값 리스트 | 도착 순서로 추가 |
| 선언 외 확장 필드 | 최신 값으로 교체 |

일반 content block은 같은 `index`를 가진 조각을 합치며 배열 위치나 `type`만으로
짝짓지 않는다. 일부 블록은 별도 index 메타 필드를 사용한다. 최종 블록 순서는 최초
등장 순서이고 숫자 index순 정렬이 아니다. `index=None` 블록은 독립 항목으로 추가한다.

#### Chat Completions의 블록 순서

Chat Completions 어댑터는 블록 최초 등장 순서대로 `index=0, 1, 2, …`를 부여한다.
타입별 음수나 `tool index + 1` 규칙은 사용하지 않는다. vocabulary 적용 전 어댑터의
최종 결과는 `content[i].index == i`이며, delta의 index는 delta 배열 위치가 아니라
누적 결과의 블록 위치다. 다른 벤더 및 vocabulary의 기존 식별 규칙은 변경하지 않는다.

- 같은 채널의 연속 조각은 같은 블록에 누적한다. role/usage/빈 delta는 구간을 끊지 않는다.
- content/reasoning/refusal/audio 채널이 바뀌거나 도구 호출이 끼면 다음 채널 조각은 새 블록이다.
- 도구 호출은 원본 `tool_calls[].index`를 내부 매핑해 최초 허브 번호를 유지한다.
  원본 index가 없는 완성 응답은 tool_calls 배열 위치를 사용한다.
- 한 이벤트에 여러 필드가 있으면 reasoning → content → refusal → audio → annotations →
  tool_calls 순서로 처리한다. 이는 어댑터 처리 순서이며 실제 생성 선후를 뜻하지 않는다.
- annotation도 새 순번을 받지만 텍스트 채널을 끊지는 않는다. 전체 content 문자열의 인용
  범위가 이미 수신한 한 텍스트 블록 안에 있으면 target_index와 블록 상대 오프셋으로 변환한다.
  여러 블록에 걸치거나 아직 대상을 결정할 수 없으면 target_index는 None이고 원본 범위를
  유지한다. 원본 annotation은 native에도 보존한다.

예: `content A → reasoning B → content C`는 `text #0(A), thinking #1(B), text #2(C)`다.
채널 전환 없는 같은 필드 내부의 원래 경계는 복원하지 않는다.

요청 변환에서는 0부터 연속한 순서 index가 모두 있는 HubMessage를 index순으로 순회한다.
미지정 index나 다른 벤더/vocabulary의 비연속 식별자가 있으면 기존 배열 순서를 유지한다.
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
  {"content": [{"type": "text", "index": 0, "text": "안녕"}]},
  {"content": [{"type": "text", "index": 0, "text": "하세요"}]},
  {"stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 2}}
]
```

병합 결과(`model_dump(exclude_none=True)`)는 다음과 같다.

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
  {"content": [{"type": "tool_use", "index": 1, "id": "call-1", "name": "get_weather", "input_json": "{\"city\":"}]},
  {"content": [{"type": "tool_use", "index": 1, "input_json": "\"서울\"}"}]},
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
`exclude_unset=True`는 명시되지 않은 `type` 판별자까지 생략할 수 있으므로 이 옵션만으로
독립적인 블록 JSON 계약을 만들지 않는다. 이 라이브러리는 외부 delta 재전송용 envelope,
완료 알림, 재개 ID를 제공하지 않으며 필요한 경우 소비 앱에서 별도로 정의해야 한다.

## 6. 종료·부분 결과·취소·오류

벤더 어댑터의 종료 판정 또는 SSE 입력의 정상 소진 뒤 mapper를 flush하고,
그 delta까지 모두 순회한 다음 최종 결과가 열리며 전송 리소스를 해제한다.
종료 프레임에 담긴 usage나 종료 사유도 먼저 처리한다.

| 상황 | `result` / `partial` |
|---|---|
| 순회 전 또는 순회 중 | `result`는 `StreamNotFinished`; `partial` 조회 가능 |
| 종료 프레임 후 끝까지 순회 | `result` 조회 가능 |
| 종료 프레임 없이 정상 EOF | 현재 구현은 완료로 처리, `result` 조회 가능 |
| 조기 중단·오류·취소로 끝까지 순회하지 않음 | `result` 대신 `partial` 사용 |

정상 EOF만으로 모델의 정상 생성까지 확인되지는 않는다. 최종 `stop_reason`,
`block_reason`, content를 별도로 확인한다. `stop_reason` delta를 받았더라도 루프를
즉시 중단하면 뒤의 usage나 flush 결과를 놓치고 `result`가 열리지 않을 수 있다.
최종 응답을 별도의 마지막 delta로 한 번 더 내보내지는 않는다.

`aclose()` / `close()`는 아직 닫히지 않았다면 mapper를 flush해 부분 결과에 반영하고
전송을 닫는다. 이 flush 결과는 소비자에게 yield하지 않는다. 이미 닫힌 스트림은 다시
flush하지 않는다. 예외 경로의 `finally`는 리소스를 해제하지만 정상 flush를 보장하지
않으므로 오류 후 `partial`은 이미 병합된 내용까지만 보존한다고 보아야 한다.
닫기 자체가 중단된 스트림을 정상 완료 상태로 바꾸지는 않는다.

| 오류 | 현재 동작 |
|---|---|
| HTTP 비성공 상태 | `TransportError`, `status_code`와 응답 본문 앞 최대 500자의 `detail` |
| 연결·읽기 timeout 등 | 전송 계층 예외가 그대로 전파될 수 있음 |
| 응답 디코딩·매핑 실패 | 어댑터의 `MappingError` 등 전파 |
| 입력 모델 검증 실패 | 모델 검증 예외 등 전파 |
| 등록된 extractor 실패 | 원래 예외를 원인으로 가진 `ExtractionError` |
| 완료 전 `result` 조회 | `StreamNotFinished` |

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
        if len(stream.partial.text) >= 100:
            break
finally:
    try:
        await stream.aclose()
    finally:
        await iterator.aclose()

partial = stream.partial
# 조기 중단했으므로 stream.result를 최종 응답으로 조회하지 않는다.
```

## 8. 구현 및 검증 근거

- [bridge.py](../src/completion_bridge/bridge.py): 공개 메서드, pipeline, 스트림 수명
- [hub.py](../src/completion_bridge/hub.py): 요청·응답 모델
- [blocks.py](../src/completion_bridge/blocks.py): 블록 필드 및 도구 인수 파싱
- [merge.py](../src/completion_bridge/merge.py): delta 병합
- [transport/http.py](../src/completion_bridge/transport/http.py): HTTP 전송 및 오류
- [test_bridge.py](../tests/test_bridge.py): EOF 완료, 부분 결과, usage, HTTP 오류
- [test_merge.py](../tests/test_merge.py): 병합 규칙
- [변환 예제](Conversion-Examples.md): 벤더별 요청·raw event·HubResponse JSON
