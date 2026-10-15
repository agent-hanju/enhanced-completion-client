# common-hitl-chat에서 completion-bridge 로그 저장

라이브러리는 파일을 생성하거나 루트 로거를 설정하지 않는다. 애플리케이션이
[설정 예제](../examples/logging.common-hitl-chat.json)를 기존 logging 설정에 병합해서 사용한다.
이 문서는 설정 예제를 제공하며 common-hitl-chat 저장소 자체는 변경하지 않는다.

## 설정 적용

예제 JSON을 애플리케이션의 설정 디렉터리로 복사하고, 시작 시 한 번 적용한다.
`filename`은 현재 작업 디렉터리 기준이므로 운영 환경에서는 절대 경로로 지정하는 편이 좋다.
기존 logging 설정이 있다면 `formatters`, `handlers`, `loggers` 항목을 병합하고 한 번만 적용한다.

```python
import json
import logging.config
from pathlib import Path

Path("logs").mkdir(parents=True, exist_ok=True)
config = json.loads(Path("logging.common-hitl-chat.json").read_text(encoding="utf-8"))
logging.config.dictConfig(config)
```

- `logs/bridge-calls.jsonl`: 실제 전송 요청, 같은 API의 비스트리밍 응답 형식으로 병합한 완료 응답,
  실패·취소 기록.
- `logs/bridge-sse.jsonl`: 허브 변환 전 SSE 이벤트. 주석과 종료 이벤트도 포함한다.
- 두 파일은 각각 크기 기준으로 회전하며 백업 5개를 유지한다.
- 멀티프로세스 서버는 프로세스별 파일을 사용하거나 단일 로그 수집 프로세스로 전달한다.
  예제의 회전 파일 핸들러는 여러 프로세스가 같은 파일에 쓰는 용도가 아니다.

## 호출과 분류

```python
result = await bridge.complete(history, request_id="chat-run-123-call-1")
# request_id 생략 시 호출마다 UUID4 문자열 생성
```

`Bridge`와 `SyncBridge`의 `stream()` / `complete()`에서 지원한다.
ID는 벤더 요청 본문이나 헤더로 전송하지 않는다. 여러 API 호출에 같은 ID를 재사용하면
구분할 수 없으므로 호출마다 다른 값을 사용한다. 별도 ID 보관소는 없다.
`build_request()`와 `count_tokens()`는 이번 로깅 대상에 포함하지 않는다.

| 로거 | event | payload |
|---|---|---|
| `completion_bridge.wire.calls` | `request` | `method`, `url`, 변환 후 요청 `body` |
| `completion_bridge.wire.sse` | `sse` | `event`, `id`, `data`, `retry`, `comments` |
| `completion_bridge.wire.calls` | `response` | 벤더 API의 비스트리밍 응답 형식으로 병합한 객체 |
| `completion_bridge.wire.calls` | `merge_error` | 병합 실패 예외의 `error_type`, `message` |
| `completion_bridge.wire.calls` | `error` | 예외 클래스 이름 `error_type` |
| `completion_bridge.wire.calls` | `cancelled` | 예외 클래스 이름 또는 빈 객체 |

모든 이벤트는 DEBUG다. Python LogRecord의 `wire` 필드에 `event`, `request_id`,
`vendor`, `model`, `sequence`, `elapsed_ms`, `payload`가 들어간다.
제공한 `JsonFormatter`는 여기에 UTC `timestamp`, `level`, `logger`를 추가해 JSONL로 출력한다.
SSE 순번은 호출마다 1부터 증가한다. 다른 이벤트의 sequence는 마지막 수신 SSE 순번이다.
`elapsed_ms`는 스트림 소비 시작 이후 경과 시간이며 소비자의 처리 시간도 포함한다.

SSE의 `data`는 문자열 그대로 기록한다. 이것은 파싱된 SSE 이벤트이며 HTTP 바이트 덤프가 아니다.
완료 응답은 요청과 같은 벤더 API 형식이다. 허브 형식인 `HubResponse`는 기록하지 않는다.
각 JSONL 행의 request_id로 두 파일을 연결한다. 호출 기록 파일 안에서도 요청과 응답은
각각 별도 행이며, 동시에 진행하는 호출 때문에 서로 인접하지 않을 수 있다.

## 원본 응답 병합

`response` payload는 각 어댑터의 `to_native()` 누적기가 SSE chunk를 받아 만든 Response 객체를
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

스트림을 실제 소비할 때 request를 기록한다. stream()만 만들고 소비하지 않으면 기록하지 않는다.
정상적으로 소비를 마치면 response 또는 merge_error를 한 번 기록한다. 실패는 error, 명시적 close/aclose 또는
이터레이터 종료·태스크 취소는 cancelled로 기록하며 완료 응답으로 취급하지 않는다.
단순 break 뒤에도 스트림을 보관한다면 즉시 close/aclose해야 종료 기록과 자원 해제가 보장된다.
요청 조립 단계의 오류, 프로세스 강제 종료는 종료 로그를 보장하지 않는다.

인증 헤더는 기록하지 않는다. URL과 요청·응답 본문은 기록하므로 URL에 인증정보를 넣지 않는다.
본문 로그에는 대화와 도구 데이터가 포함된다. 이 예제는 로그 수집을 명시적으로 켜는 설정이다.
로그가 꺼져 있으면 SSE/완료 응답의 로그용 직렬화를 수행하지 않는다.
원본 응답 누적 여부는 스트림 소비를 시작할 때 `completion_bridge.wire.calls`의 DEBUG 활성 여부로
정한다. 그때 꺼져 있었다가 종료 전에 켜지면 그 호출의 response payload는 null이다.
로그 핸들러·직렬화 오류는 생성 요청으로 전파하지 않는다. 따라서 로그 저장 실패를 API 성공과
동일시할 수 없으며, 이 기능은 내구성을 보장하는 감사 저장소가 아니다.
