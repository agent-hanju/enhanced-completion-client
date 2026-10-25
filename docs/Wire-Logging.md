# completion-bridge 로그와 span

라이브러리는 `opentelemetry-api`로 작업 span을 만들고 표준 `logging`으로 레코드를 낸다.
`TracerProvider`, 로그 핸들러, 파일·보관 정책, 레코드 형식은 소비 앱이 정한다. 라이브러리는 파일을
만들거나 루트 로거·provider를 설정하지 않는다. provider를 설정하지 않은 앱에서는 span이 기록되지
않고 레코드에 trace·span ID가 없다.

common-hitl-chat은 `hitl_chat.tracing`의 formatter·filter와 `config/logging.yml`로 이 레코드를
OpenTelemetry Logs Data Model 필드의 JSON 한 줄로 쓴다.

## 레코드 규칙

레코드는 OpenTelemetry `LoggingHandler`와 같은 변환 규칙을 따른다.

- `msg`가 본문(body)이다. 원본 통신 레코드의 본문은 JSON 객체 그대로이며 문자열로 직렬화하지 않는다.
- `extra`의 key가 속성(attributes)이다. key는 의미 규약 이름(`gen_ai.*`, `url.full`,
  `http.request.method`, `error.type`)이나 `completion_bridge.` 접두 이름이다.
- `event_name` key는 이벤트 이름이다(`completion_bridge.request` 등).
- 레코드는 그 작업의 span을 현재 span으로 둔 채 만든다. 소비 앱의 filter·formatter는
  `opentelemetry.trace.get_current_span()`으로 trace·span ID를 레코드에 연결할 수 있다.

## span

| 작업 | span 이름 | 종류 | 주요 속성 |
|---|---|---|---|
| 생성 호출(`stream()`/`complete()`) | `chat {model}` | CLIENT | `gen_ai.operation.name`(`chat`), `gen_ai.request.model`, `completion_bridge.api`, `url.full`, 종료 시 `gen_ai.response.id`·`gen_ai.response.model`·`gen_ai.response.finish_reasons`·`gen_ai.usage.input_tokens`·`gen_ai.usage.output_tokens`, `completion_bridge.outcome` |
| 토큰 수 조회(`count_tokens()`) | `count_tokens {model}` | CLIENT | `gen_ai.request.model`, `completion_bridge.api`, `completion_bridge.outcome` |
| 반복 실행(`run()`) | `runtime` | INTERNAL | `completion_bridge.outcome`(실행 상태), `completion_bridge.model_calls` |
| 도구 콜백 | `execute_tool {name}` | INTERNAL | `gen_ai.operation.name`(`execute_tool`), `gen_ai.tool.name`, `gen_ai.tool.call.id`, `completion_bridge.outcome` |

- 부모는 작업 객체를 만든 시점의 context다. 생성 호출은 `stream()`을 부른 시점, 반복 실행은 `run()`을
  부른 시점이다. 반복 실행 안의 생성 호출과 도구 콜백은 `runtime` span의 하위 span이다. 도구 콜백은
  도구 span을 현재 span으로 두고 부르므로 콜백 안의 기록과 span은 그 아래에 들어간다.
- `completion_bridge.outcome`은 `response`, `merge_error`, `error`, `cancelled`(생성 호출),
  `completed`, `error`, `cancelled`(토큰 수 조회), 실행 상태(반복 실행), `completed`,
  `hitl_required`, `error`, `cancelled`(도구 콜백) 중 하나다.
- `completion_bridge.outcome`이 `error`인 작업은 span 상태가 ERROR다. 예외 클래스 이름은 `error.type`
  속성이나 이벤트에 쓴다. 상태 설명, 예외 이벤트, 예외 메시지·traceback은 기록하지 않는다.
- span 속성에는 본문·도구 인자·결과를 넣지 않는다. span 속성은 기본형과 그 배열만 받으므로 JSON
  본문을 넣으면 문자열로 한 번 더 직렬화된다. 본문은 아래 원본 통신 레코드의 body에 있다.
- 스트림을 만들기만 하고 소비하지 않으면 span을 시작하지 않는다.

## 원본 통신 기록

`completion_bridge.wire` 로거에 DEBUG로 낸다. 호출마다 요청 레코드 하나와 종료 레코드 하나다.

| `event_name` | body | 추가 속성 |
|---|---|---|
| `completion_bridge.request` | 변환 후 요청 JSON 객체 | `http.request.method`, `url.full` |
| `completion_bridge.response` | 벤더 API의 비스트리밍 응답 형식으로 병합한 객체 | `gen_ai.response.*`, `gen_ai.usage.*` |
| `completion_bridge.merge_error` | 병합 실패 예외의 `error_type`, `message` | `gen_ai.response.*`, `gen_ai.usage.*` |
| `completion_bridge.error` | 예외 클래스 이름 `error_type` | |
| `completion_bridge.cancelled` | 예외 클래스 이름 또는 빈 객체 | |

모든 레코드에 `completion_bridge.api`, `gen_ai.request.model`(있을 때), `elapsed_ms`가 있다.
`elapsed_ms`는 스트림 소비 시작 이후 경과 시간이며 소비자의 처리 시간도 포함한다.
SSE 이벤트는 기록하지 않는다. 스트리밍 호출의 응답 레코드는 조립한 최종 응답이다.
허브 형식인 `HubResponse`는 기록하지 않는다. 동시에 진행하는 호출의 요청·응답 레코드는 서로 인접하지
않을 수 있으며, 같은 호출의 두 레코드는 span ID가 같다.
벤더 형식이 문자열로 정한 필드(chat_completions의 `tool_calls[].function.arguments` 등)는 원문
그대로 문자열로 남는다.

## 원본 응답 병합

`response` 레코드의 body는 각 어댑터의 `to_native()` 누적기가 SSE chunk를 받아 만든 Response 객체를
`model_dump(mode="json", by_alias=True, exclude_unset=True)`로 직렬화한 JSON 객체다.
허브 변환과 별개로 같은 chunk의 복사본을 누적하므로 `HubResponse` 결과에 영향을 주지 않는다.
타입 객체의 계약과 로그 없이 사용하는 예제는 [스트리밍 API](Streaming-API.md#원본-response-객체)를
참고한다. 기본값을 가진 미수신 필드는 출력하지 않고, 명시적인 null과 확장 필드는 보존한다.

| 어댑터 | 형식 | 병합 규칙 |
|---|---|---|
| `responses` | `Response` | `response.completed`·`incomplete`·`failed` 이벤트의 `response` 객체를 그대로 쓴다 |
| `messages` | `Message` | `message_start`의 `message`에 블록을 `index` 순서로 추가하고 delta를 반영한다. `partial_json`은 연결 후 JSON으로 파싱해 `input`에 둔다. `message_delta`의 `delta`와 `usage`는 key 단위로 반영하며 `usage`의 null은 기존 값을 지우지 않는다 |
| `chat_completions` | `ChatCompletion` | 최상위 필드는 null이 아닌 나중 값을 쓰고 `object`는 `chat.completion`이다. choice별 `delta`를 `message`로 모은다. 문자열은 연결, 숫자는 합산, 객체는 재귀 반영, `index`가 있는 객체 리스트는 첫 chunk부터 index별 반영, 그 밖의 리스트는 끝에 추가한다. `index`·`type`·`role`·`finish_reason`·`stop_reason`은 나중 값을 쓴다. 도구 호출의 `id`·`function.name`·`function.arguments`는 조각을 연결하며 `logprobs` 항목은 개별 토큰으로 추가한다 |
| `generate_content` | `GenerateContentResponse` | candidate를 `index`별로 모은다. Google Gen AI SDK의 채팅 이력처럼 part는 text 조각도 합치지 않고 받은 순서대로 추가한다. 그 밖의 필드는 null이 아닌 나중 값을 쓰고 객체는 최상위 key 단위로 합친다 |

Google Gen AI Python SDK의 `generate_content_stream()`은 chunk를 반환하며, 채팅 기능도 각
chunk의 content를 순서대로 보관한다. 이 라이브러리는 로그에 응답 객체 하나를 기록하기 위해
candidate별로 part 목록과 메타데이터를 모은다. 같은 요청의 비스트리밍 응답과 part 구성이 같다고
보장하지 않는다. part를 합치지 않으므로 `thoughtSignature`와 해당 part의 대응을 유지한다.
`logprobsResult`처럼 parts 외 필드가 chunk마다 일부씩 오면 마지막 chunk의 값만 남는다.

### 공식 SDK 참조와 검증

HTTP 전송과 허브 변환에는 기존 구현을 사용하며, 공식 SDK를 런타임 의존성에 추가하지 않는다.
원본 응답 누적은 다음 공식 구현을 참고한다.

- [OpenAI Chat Completions 누적기](https://github.com/openai/openai-python/blob/main/src/openai/lib/streaming/chat/_completions.py)와
  [delta 병합](https://github.com/openai/openai-python/blob/main/src/openai/lib/streaming/_deltas.py):
  문자열 조각 연결, 첫 목록을 포함한 tool index별 병합, logprobs 토큰 추가.
- [OpenAI Responses 누적기](https://github.com/openai/openai-python/blob/main/src/openai/lib/streaming/responses/_responses.py):
  정상 종료 이벤트의 최종 Response 사용. 이 라이브러리는 incomplete·failed의 Response도 기록한다.
- [Anthropic Messages 누적기](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/streaming/_messages.py):
  message_start로 시작, 본문·thinking 연결, 서명 교체, 도구 입력 JSON 누적, 누적 usage 교체.
- [Google Gen AI 채팅 이력](https://github.com/googleapis/python-genai/blob/main/google/genai/chats.py):
  각 chunk의 content와 part 순서 보존.

2026-10-15에 별도 임시 환경의 `openai==3.31.0`, `anthropic==1.14.0`,
`google-genai==2.31.0`으로 동일한 테스트 입력을 처리해 비교했다. OpenAI의 choice·usage 및 최종
Response, Anthropic의 본문·thinking·서명·인용·도구 입력·usage, Google 채팅 이력의 part 목록이
일치했다. SDK가 추가하는 기본 null 필드와 파싱 보조 필드는 wire 필드 비교에서 제외했다.
이 검증은 실제 모델 호출을 포함하지 않는다.

누적 규칙이 없는 이벤트·delta 종류, index 순서 위반, 파싱할 수 없는 도구 입력, 종료 응답 객체가
없는 Responses 스트림은 `MappingError`가 된다. 이 오류는 생성 요청으로 전파하지 않고
`response` 대신 `merge_error`로 기록한다. 이후 chunk는 누적하지 않는다.
응답 모델에 선언된 필드의 타입이 잘못되면 `pydantic.ValidationError`가 발생하며 동일하게
`merge_error`로 기록한다.

## 기록 시점과 범위

스트림을 실제 소비할 때 span을 시작하고 request를 기록한다. stream()만 만들고 소비하지 않으면 기록하지
않는다. 정상적으로 소비를 마치면 response 또는 merge_error를 한 번 기록한다. 실패는 error, 명시적
close/aclose 또는 이터레이터 종료·태스크 취소는 cancelled로 기록하며 완료 응답으로 취급하지 않는다.
단순 break 뒤에도 스트림을 보관한다면 즉시 close/aclose해야 종료 기록, span 종료, 자원 해제가 보장된다.
요청 조립 단계의 오류, 프로세스 강제 종료는 종료 기록을 보장하지 않는다.

인증 헤더는 기록하지 않는다. URL과 요청·응답 본문은 기록하므로 URL에 인증정보를 넣지 않는다.
본문 기록에는 대화와 도구 데이터가 포함된다. 로그가 꺼져 있으면 완료 응답의 로그용 직렬화를 하지 않는다.
원본 응답 누적 여부는 스트림 소비를 시작할 때 `completion_bridge.wire`의 DEBUG 활성 여부로
정한다. 그때 꺼져 있었다가 종료 전에 켜지면 그 호출의 response 본문은 null이다.
로그 핸들러·직렬화·provider 오류는 생성 요청으로 전파하지 않는다. 따라서 로그 저장 실패를 API 성공과
동일시할 수 없으며, 이 기능은 내구성을 보장하는 감사 저장소가 아니다.

## 운영 기록

작업의 시작·종료와 소요 시간은 span이 갖는다. `completion_bridge.operations` 로거에는 주의가 필요한
처리 결과만 낸다. 요청·응답 본문, 도구 인자·결과, 예외 메시지·traceback은 넣지 않는다.

| `event_name` | 수준 | 속성 |
|---|---|---|
| `completion_bridge.native_merge_failed` | WARNING | `error.type`, `completion_bridge.recovery` |
| `completion_bridge.tool_limit_reached` | WARNING | `completion_bridge.limit`, `completion_bridge.recovery` |
| `completion_bridge.cleanup_failed` | 원래 오류가 있으면 WARNING, 없으면 ERROR | `error.type`, `completion_bridge.recovery` |
| `completion_bridge.finished` | 생성 호출·토큰 수 조회가 실패로 끝날 때 ERROR | `completion_bridge.outcome`, `error.type` |
| `completion_bridge.failed` | 반복 실행이 처리 중 예외로 끝날 때 ERROR. 이미 기록된 모델 실패는 제외 | `error.type` |

모든 운영 레코드에 `completion_bridge.operation`과 `elapsed_ms`가 있다. 열린 span이 있으면 같은
이름의 span 이벤트도 더한다. 상위 런타임은 이미 기록된 모델 실패를 다시 ERROR로 기록하지 않는다.
콜백 예외는 런타임에서 기록한다. 도구 승인·검토 정책은 콜백의 책임이며 라이브러리가 판정 기록을
만들지 않는다.

## 환경별 출력과 보관

라이브러리는 핸들러, 파일 회전, provider를 자동 설정하지 않는다. 소비 앱이 환경별로 정한다.

| 환경 | 운영 기록 | 원본 통신 기록 | span |
|---|---|---|---|
| 개발 | INFO 이상 | 원본 확인이 필요할 때 DEBUG | 앱의 provider와 exporter |
| 시험 | 시험이 캡처하는 수준 | 해당 시험에서만 DEBUG | 시험용 메모리 exporter |
| 운영 | WARNING 이상 | 요구사항에 따라 DEBUG, 아니면 끔 | 앱의 provider와 exporter |

원본 통신 기록에는 사용자가 요청한 원래 본문·인자·결과가 포함되므로 일반 운영 로그처럼 취급하면
안 된다. 앱 실행 계정과 승인된 운영 계정만 로그 디렉터리를 읽을 수 있도록 파일 권한을 설정한다.
여러 프로세스가 같은 회전 파일을 공유하지 않도록 프로세스별 경로나 중앙 수집기를 사용한다.
단일 레코드가 파일 크기 제한보다 클 수 있으므로 원본 기록을 켜는 앱은 디스크 사용량을 감시한다.
