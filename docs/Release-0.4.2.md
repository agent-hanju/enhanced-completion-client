# completion-bridge 0.4.2

`0.4.1` 대비 모델·도구 반복 실행 API를 추가하고 패키지 구성과 스트림 병합 방식을 변경한다.
일부 공개 API를 제거·변경했으므로 아래 적용 사항을 확인한다.

## 추가

- `Bridge.run()`이 `HubRequest`와 비동기 도구 콜백으로 모델 호출을 반복한다.
  관련 공개 이름은 `Execution`, `Run`, `RuntimeExecution`, `ToolPolicy`, `ToolCall`,
  `ToolCallback`, `ToolOutput`, `HitlDecision`, `HitlDraft`, `ExecutionStateError`,
  `RuntimeCancelled`, `RuntimeErrorWithPartial`이다. 사용법은 [반복 실행 문서](Completion-Runtime.md)를 따른다.
- 스트림 병합 실패는 `StreamMergeError`로 전달한다. 실패한 `delta`, 그 시점의 `result`,
  원래 예외(`__cause__`)를 제공한다.
- 벤더가 반환한 오류를 나타내는 `VendorStreamError`를 공개한다.

## 변경

- 각 delta를 모델에 직접 병합하고 성공한 직후 소비자에게 전달한다. `stream.result`의 반환 타입은
  `HubResponse | None`이며 delta 처리 전에는 `None`이다. 오류 후 결과는 스키마 유효성을 보장하지 않는다.
  상태별 결과는 [API 스트리밍 명세](Streaming-API.md)를 따른다.
- `StreamMapper`가 Protocol에서 추상 기반 클래스로 바뀌었다. 사용자 정의 mapper는 `StreamMapper`를
  상속해 구현한다.
- 하위 모듈 경로가 바뀌었다. 최상위 `completion_bridge`에서 import하는 이름은 유지한다.
  하위 모듈을 직접 참조한 코드와 logging formatter 경로는 [패키지 구성](Package-Structure.md)의
  import 변경 표를 따라 변경한다.

## 제거

- `stream.partial`. 진행 중 상태는 `stream.result`로 조회한다.
- `CiteVocabulary`, `cite_schema`, `ExtractionError`와 `vocabularies` 패키지.
  인용 Vocabulary 구현 견본은 `examples/cite_vocabulary.py`에 있다.
