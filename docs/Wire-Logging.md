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

- `logs/bridge-calls.jsonl`: 실제 전송 요청과 완료된 HubResponse, 실패·취소 기록.
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
| `completion_bridge.wire.calls` | `response` | 최종 `HubResponse.model_dump(mode="json")` |
| `completion_bridge.wire.calls` | `error` | 예외 클래스 이름 `error_type` |
| `completion_bridge.wire.calls` | `cancelled` | 예외 클래스 이름 또는 빈 객체 |

모든 이벤트는 DEBUG다. Python LogRecord의 `wire` 필드에 `event`, `request_id`,
`vendor`, `model`, `sequence`, `elapsed_ms`, `payload`가 들어간다.
제공한 `JsonFormatter`는 여기에 UTC `timestamp`, `level`, `logger`를 추가해 JSONL로 출력한다.
SSE 순번은 호출마다 1부터 증가한다. 다른 이벤트의 sequence는 마지막 수신 SSE 순번이다.
`elapsed_ms`는 스트림 소비 시작 이후 경과 시간이며 소비자의 처리 시간도 포함한다.

SSE의 `data`는 문자열 그대로 기록한다. 이것은 파싱된 SSE 이벤트이며 HTTP 바이트 덤프나
벤더 응답을 재구성한 객체가 아니다. 완료 응답은 허브 형식이다.
각 JSONL 행의 request_id로 두 파일을 연결한다. 호출 기록 파일 안에서도 요청과 응답은
각각 별도 행이며, 동시에 진행하는 호출 때문에 서로 인접하지 않을 수 있다.

## 기록 시점과 범위

스트림을 실제 소비할 때 request를 기록한다. stream()만 만들고 소비하지 않으면 기록하지 않는다.
정상적으로 소비를 마치면 response를 한 번 기록한다. 실패는 error, 명시적 close/aclose 또는
이터레이터 종료·태스크 취소는 cancelled로 기록하며 완료 응답으로 취급하지 않는다.
단순 break 뒤에도 스트림을 보관한다면 즉시 close/aclose해야 종료 기록과 자원 해제가 보장된다.
요청 조립 단계의 오류, 프로세스 강제 종료는 종료 로그를 보장하지 않는다.

인증 헤더는 기록하지 않는다. URL과 요청·응답 본문은 기록하므로 URL에 인증정보를 넣지 않는다.
본문 로그에는 대화와 도구 데이터가 포함된다. 이 예제는 로그 수집을 명시적으로 켜는 설정이다.
로그가 꺼져 있으면 SSE/완료 응답의 로그용 직렬화를 수행하지 않는다.
로그 핸들러·직렬화 오류는 생성 요청으로 전파하지 않는다. 따라서 로그 저장 실패를 API 성공과
동일시할 수 없으며, 이 기능은 내구성을 보장하는 감사 저장소가 아니다.
