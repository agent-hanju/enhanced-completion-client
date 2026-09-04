# PLAN — enhanced-completion-client Python 전환

갱신일: 2026-09-05

## 목표와 경계

이 프로젝트를 서버가 아닌 Python 3.12+ 라이브러리로 제공한다. 소비 프로젝트가 설정과 벤더
어댑터, 사용자 정의 Vocabulary를 등록해 `Bridge`/`SyncBridge` 인스턴스를 만들고 일반 LLM
API와 도메인 객체 사이의 변환기로 사용한다.

- 패키지 이름: `enhanced-completion-client`
- Python 모듈: `enhanced_completion`
- 지원 API: Chat Completions, OpenAI Responses, Anthropic Messages, Gemini GenerateContent,
  사내 single/coding agent SSE
- Augment/RAG 실행 계층: 제거. 문서와 인용 content block만 유지
- 재시도·SSE 재연결: 내장하지 않음. 주입한 httpx 클라이언트와 소비 애플리케이션의 정책
- 보존 정책: 공통 의미는 교차 벤더 변환, 벤더 고유 정보는 같은 벤더 재전송

레거시 구현 조사는 [Python-Port-Scope.md](docs/Python-Port-Scope.md), Java 변환 기준선은
[Support-Matrix.md](docs/Support-Matrix.md)의 레거시 대조와 최신 공식 API 감사를 따른다.

## 1. 요구 기능과 공개 API

### 1.1 Bridge 생성

```python
bridge = Bridge(
    vendor=responses,
    base_url="https://api.openai.com",
    model="gpt-5-mini",
    api_key="...",
    hyperparameters=Hyperparameters(max_output_tokens=512),
    vocabularies=[CiteVocabulary()],
    headers={},
    http_client=shared_async_client,
    timeout=120.0,
)
```

동기 사용처는 같은 생성자와 메서드 모양의 `SyncBridge`를 쓴다.

| 공개 메서드 | 계약 |
|---|---|
| `build_request(messages, tools=(), model=None, hyperparameters=None, **params)` | Hub 입력을 대상 API wire body로 변환. 전송 없음 |
| `stream(...)` | 델타 `HubResponse` iterator와 최종 `result` 제공 |
| `complete(...)` | 스트림을 끝까지 병합한 `HubResponse` 반환 |
| `close()` / `aclose()` | 브리지가 직접 만든 httpx 클라이언트만 닫음 |

### 1.2 허브 모델

`HubMessage.content`와 `HubResponse.content`는 같은 개방형 content block 리스트다.

| 블록 | 공통 의미 |
|---|---|
| `TextBlock` | 사용자에게 보이는 본문 |
| `ThinkingBlock` | 추론 요약/본문과 동일 벤더 재생용 signature·암호문 |
| `ToolUseBlock` | 클라이언트가 실행하거나 승인해야 하는 호출 |
| `ToolResultBlock` | 호출 ID와 짝을 이루는 클라이언트 결과 |
| `ImageBlock`, `AudioBlock`, `DocumentBlock` | 멀티모달 입력과 파일 참조 |
| `TextBlock.citations` | XML/Anthropic형 인용. 답변 구간은 부모 text, 근거 원문은 citation에 저장 |
| `AnnotationBlock` | OpenAI와 Gemini의 출력 text 범위 annotation |
| `GroundingBlock` | Gemini의 답변 구간과 복수 근거 source 관계 graph |
| `ServerToolBlock` | 벤더 서버가 이미 실행한 도구의 진행/결과 |
| `VendorBlock` | 알려지지 않은 타입의 원본 보존 폴백 |

모든 블록은 `source`와 `native`를 가질 수 있다. `source`가 대상 어댑터와 같을 때만 네이티브
원본을 재사용한다. 다른 벤더에는 공통 필드만 변환한다.

### 1.3 요청 Hyperparameters

`Hyperparameters`는 네 API에 공통인 생성 의도를 표현하고, 각 어댑터는 자신이 실제 지원하는
필드명과 중첩 구조만 wire body에 만든다. API별 옵션은
`ChatCompletionsParameters`/`ResponsesParameters`/`MessagesParameters`/
`GenerateContentParameters`에 격리하고, 사용자 정의 호환 서버는 `vendor[adapter.name]`을 쓴다.
생성자 기본값과 호출별 값은 deep-merge하며 레거시 `**params`가 마지막으로 덮는다.

### 1.4 도구 호출/결과 변환

`ToolUseBlock`과 `ToolResultBlock`은 다른 벤더에서도 생략하지 않는 공통 코어다.

| 대상 | `ToolUseBlock` | `ToolResultBlock` |
|---|---|---|
| Chat Completions | assistant `tool_calls` | 별도 `role: tool`, `tool_call_id` |
| Anthropic | assistant `tool_use` block | user `tool_result` block |
| Responses | `function_call` Item | `function_call_output` Item |
| Gemini | model `functionCall` Part | user `functionResponse` Part |

결과를 만들 때 가능하면 `ToolResultBlock.name`도 채운다. Gemini처럼 결과에 함수명이 필수인
대상은 이전 호출의 ID→이름을 이력에서 복원한다.

### 1.5 사용자 정의 content type

1. `ContentBlock`을 상속하고 고정 `type: Literal[...]`을 선언한다.
2. `Vocabulary.blocks`에 등록한다.
3. 요청 방향 `lower(blocks)`를 구현한다.
4. 응답 XML-like 스트림을 올려야 하면 상태를 가진 `lift_mapper()`를 구현한다.
5. `Bridge(vocabularies=[...])`에 객체를 등록한다.

내림과 올림은 같은 Vocabulary가 소유한다. 태그명, 속성, escape 규칙이 서로 어긋나지 않게 하기
위해서다. `CiteVocabulary`가 기본 구현 예시다.

### 1.6 VendorAdapter 확장

새 API는 다음 네 메서드만 구현한다.

- `build_body(HubRequest, Lowerer) -> dict`
- `decode(SseFrame) -> chunk | None`
- `is_terminal(SseFrame) -> bool`
- `to_hub() -> StreamMapper[chunk, HubResponse]`

SSE의 `event`, `data`, `id`, comment를 모두 봐야 하므로 SDK가 감춘 token iterator가 아니라
`SseFrame`을 경계로 둔다. 이것이 사내 agent SSE와 표준 SSE를 같은 전송 계층에서 처리하는
조건이다.

## 2. 사용할 레거시 자산

### 2.1 그대로 이식한 핵심

- `streambind`의 append 기본/overwrite 예외 델타 병합
- `model_fields_set`에 해당하는 “실제로 전송된 필드만 병합” 규칙
- content-stream tag parser의 chunk 경계 버퍼링과 flush
- mapper `map`/`flush` 합성과 flush 순서
- request message가 wire message 여러 개로 펼쳐지는 규칙
- citation의 답변 좌표와 문서 원문 좌표 분리
- OpenAI-compatible/Gemini/Responses → Anthropic형 role·stop reason 어휘 정규화

### 2.2 참고하되 교정한 부분

- Responses Item/ContentPart와 Gemini Content/Part의 두 계층을 유지
- `tool_result`를 각 API의 네이티브 tool message/result로 변환
- Responses encrypted reasoning을 표시 텍스트로 오해하지 않음
- Gemini `inlineData`를 MIME으로 분류하고 `fileData`까지 처리
- Gemini `functionResponse`의 name/id/structured response/parts 보존
- Chat Completions의 image/audio/file 멀티모달 입력 추가
- 초기 Java union에 없던 document, citation, server tool, 최신 tool Item/Part를 원본 보존

### 2.3 사용하지 않는 부분

- Spring WebFlux, Reactor, Jackson 다형 등록, Lombok
- Augmenter, vector/keyword/composite 검색 구현
- Java generic type-variable 해석기
- 서버 애플리케이션과 설정 자동 구성
- 라이브러리 자체 재시도와 reconnect 정책

## 3. 스택

| 항목 | 선택 |
|---|---|
| 런타임 | Python `>=3.12` |
| 빌드 | `uv`, `uv_build`, `src/` layout |
| HTTP/SSE | `httpx` + 자체 incremental SSE parser |
| 모델 | Pydantic 2, 런타임 개방형 block registry |
| 테스트 | pytest, pytest-asyncio, respx, pytest-cov |
| 품질 | ruff, mypy strict |
| 선택 개발 의존 | python-dotenv (`.env` 라이브 테스트만) |

공식 벤더 SDK는 런타임 의존으로 넣지 않는다. SDK마다 스트림 event 추상화와 content type이 달라
사내 SSE 및 사용자 정의 mapper를 한 경계로 합치기 어렵기 때문이다. 대신 공식 SDK의 생성 타입과
API 문서를 호환성 감사 자료로 사용하고 wire JSON/SSE는 이 패키지가 직접 처리한다.

## 4. 구현 단계와 상태

| 단계 | 상태 | 완료 조건 |
|---|---|---|
| Python 패키지/Bridge/SyncBridge | 완료 | 인스턴스 생성, build/stream/complete, context manager |
| 공통/벤더별 Hyperparameters | 완료 | 지원 필드 투영, API별 격리, 호출별 override |
| SSE parser와 merger | 완료 | split frame/tag/tool args, terminal payload, flush 회귀 테스트 |
| custom Vocabulary/Citations | 완료 | XML stream lift/lower, nested text citation, 요청 재전송 |
| Chat Completions/vLLM | 완료 | reasoning, tools, image/audio/file, refusal/audio/annotations |
| Anthropic Messages | 완료 | 문서 citations, signed thinking, tool/server blocks, beta header 설정 |
| OpenAI Responses | 완료 | Item/Part, reasoning replay, tools/results, server tools, terminal usage |
| Gemini GenerateContent | 완료 | Part MIME 분류, function response, signatures, server/new parts |
| agent-studio SSE | 완료(오프라인 fixture) | single/coding event 변형, terminal/error/source/tool 처리 |
| 최신 API 감사 | 완료 | 공식 문서와 레거시 규칙 차이 기록 |
| 오프라인 품질 게이트 | 완료 | pytest, ruff, mypy |
| 라이브 게이트 | 환경 의존 | `.env`가 채워진 endpoint만 명시 실행 |
| 패키지 빌드 | 완료 | `uv build` 성공, wheel/sdist에 cache·env·tests 미포함 확인 |

## 5. 테스트 매트릭스

| 축 | 필수 검증 |
|---|---|
| 방향 | request serialization, response SSE mapping, response→message→request |
| 실행 | async와 sync |
| 도구 | call/result ID·이름 연결, nested result blocks, server tool raw replay |
| 멀티모달 | base64, HTTP URL, file ID 명시적 거부, MIME별 Image/Audio/Document |
| 요청 옵션 | 공통 필드별 wire 투영, API별 격리, 사용자 정의 벤더 override |
| 추론 | Chat aliases, Anthropic signature, Responses encrypted item, Gemini signature |
| 인용 | XML-like stream split, native citation, answer/source coordinates |
| 견고성 | unknown block/item/part, extra fields, error/terminal/heartbeat |
| 실제 서버 | 짧은 일반 응답, tool call, dense history, cross-vendor history |

라이브 시험은 기본 pytest에서 제외한다. 429/503은 코드 결함과 구분해 skip 처리하고, 400/401과
파싱·계약 오류는 실패로 둔다.

## 6. 남은 운영 결정

- 사내 패키지 인덱스 또는 git 의존 중 배포 경로
- agent-studio 실제 서버를 띄운 후 프론트 번들에서 추출한 fixture와 최종 대조
- Gemini Interactions API가 실제 사용처가 되면 별도 어댑터 추가
- 변환 중 생략된 벤더 고유 블록을 호출자에게 보고하는 선택적 diagnostics/strict 정책

이 결정들은 현재 bridge 변환 계약을 막지 않는다.
