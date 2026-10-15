# completion-bridge 0.4.0

비교 기준은 `0.3.4`다. 원본 API 응답을 타입 객체로 누적하는 공개 계약을 추가하고,
호출 로그의 완료 응답을 허브 표현에서 해당 API의 원본 응답 형식으로 변경한다.

## 원본 Response와 누적기

- `ChatCompletionsResponse`, `ResponsesResponse`, `MessagesResponse`,
  `GenerateContentResponse`를 공개한다. 주요 본문·도구 호출·사용량도 하위 모델로 제공한다.
- `VendorAdapter.to_native()`는 스트림마다 새 `NativeAccumulator[응답 타입]`을 반환한다.
  `apply(chunk)`로 누적하고 정상 종료 후 `build()`로 Response 객체를 얻는다.
- 누적기는 로깅과 독립적으로 사용할 수 있다. SDK를 런타임 의존성에 추가하지 않는다.
- 추가 필드와 명시적인 null을 보존한다. 모델에 선언된 필드의 타입은 생성·속성 대입 시
  검증하며, 미디어나 도구별 고유 payload는 JSON 구조로 유지한다.

[타입·반환 계약과 사용 예제](Streaming-API.md#원본-response-객체)를 참고한다.

## 호출 로그 변경

- `completion_bridge.wire.calls`의 `response.payload`는 `HubResponse` 대신 원본 Response를
  `model_dump(mode="json", by_alias=True, exclude_unset=True)`로 직렬화한 객체다.
- 누적·타입 검증 실패는 `merge_error`로 기록하며 허브 스트림 소비로 전파하지 않는다.
- `Bridge`는 호출 로그를 켠 경우에만 원본 응답 누적기를 생성한다.
- `stream.result`와 `complete()`의 반환 타입은 기존처럼 `HubResponse`다.

[로그 계약과 설정](Wire-Logging.md)을 참고한다.

## 사용자 정의 어댑터 적용 사항

`VendorAdapter`에 `to_native()` 계약이 추가됐다. 직접 작성한 어댑터는 이 메서드를 구현하고,
`apply(chunk)`와 Pydantic 응답 객체를 반환하는 `build()`를 가진 누적기를 제공해야 한다.
기존 호출 로그에서 허브 응답 필드를 읽는 소비자는 원본 API 형식으로 처리 로직을 변경해야 한다.

## 검증 범위

- 오프라인 테스트 530개와 Ruff·mypy 검사 통과.
- Python 3.12·Pydantic 2.9.0에서도 전체 오프라인 테스트 통과.
- OpenAI·Anthropic·Google 공식 SDK와 비교한 응답 누적 사례 7개 일치.
- 호출 로그를 켠 전체 테스트에서 의도적인 오류 테스트 외의 병합 오류 없음.
- 라이브 API 테스트 27개는 제외했다.
