# completion-bridge 0.4.3

`0.4.2` 대비 로그 상관 방식을 OpenTelemetry trace·span으로 바꾼다. 로그용 공개 API 일부를 제거했으므로
아래 적용 사항을 확인한다.

## 추가

- 런타임 의존성 `opentelemetry-api`. bridge는 API만 쓰고 `TracerProvider`와 exporter는 소비 앱이
  설정한다.
- 생성 호출마다 CLIENT span `chat {model}`, 토큰 수 조회마다 `count_tokens {model}`, 반복 실행마다
  `runtime`, 도구 콜백마다 `execute_tool {name}` span을 연다. 부모는 작업을 만든 시점의 context다.
  속성은 OpenTelemetry GenAI 의미 규약 이름을 쓴다. [로그와 span](Wire-Logging.md)을 참고한다.

## 변경

- 원본 통신 기록은 `completion_bridge.wire` 로거 하나에 낸다. 레코드 본문은 `msg`(JSON 객체), 속성은
  `extra`, 이벤트 이름은 `event_name` key다. 요청 레코드의 본문은 요청 JSON 객체이며 주소와 메서드는
  `url.full`, `http.request.method` 속성으로 옮겼다.
- `completion_bridge.operations`는 경고·실패 처리 결과만 낸다. 시작·종료와 소요 시간은 span이 갖는다.
- 레코드는 그 작업의 span을 현재 span으로 둔 채 만든다.

## 제거

- `JsonFormatter`. 레코드 형식은 소비 앱이 정한다. `examples/logging.common-hitl-chat.json`도 제거했다.
- SSE 이벤트 기록과 `completion_bridge.wire.sse`, `completion_bridge.wire.calls` 로거 이름.
- `stream()`·`complete()`의 `request_id` 인자, `Run.request_id`, `current_operation_id`, 레코드의
  `request_id`·`parent_request_id`·`sequence`. 호출 상관은 trace·span ID로 한다.
- `RuntimeExecution`의 `params` 예약 이름에서 `request_id`. vLLM 요청 파라미터 `request_id`
  (`Hyperparameters`)는 그대로 벤더에 보낸다.

## 적용

- logging 설정에서 `completion_bridge.logging.JsonFormatter`와 `completion_bridge.wire.calls`,
  `completion_bridge.wire.sse`를 참조하는 항목을 바꾼다. 원본 통신 기록은 `completion_bridge.wire`를
  DEBUG로 켜고, 소비 앱의 formatter가 `msg`와 `extra`를 쓰게 한다.
- `request_id`를 넘기던 호출은 그 인자를 지운다. 호출 상관이 필요하면 `TracerProvider`를 설정한다.
