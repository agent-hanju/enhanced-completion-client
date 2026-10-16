# 패키지 구성

`completion_bridge`의 공개 진입점은 최상위 `__init__.py`다.
일반 소비자는 `from completion_bridge import Bridge, HubRequest, ToolOutput`을 사용한다.
구현 파일은 데이터·스트림 처리·실행·벤더·전송 책임에 따라 배치한다.

```text
completion_bridge/
  __init__.py          공개 API 재노출
  bridge.py            단일 요청·스트림과 비동기 run 진입점
  errors.py            공통 예외 계층
  logging.py           운영 추적·원본 통신 로그·JSON 포맷터
  models/
    blocks.py          콘텐츠·인용·미디어·도구 블록과 타입 등록
    hub.py             메시지·요청·응답·사용량·토큰 수·도구 정의
    parameters.py      요청 옵션과 검증
    native.py          벤더별 원본 Response 타입
    tools.py           도구 호출·HITL 기록과 콜백 반환 계약
  streaming/
    mapping.py         스트림 매퍼·합성·사용자 어휘 변환 계약
    merge.py           타입 기반 델타 병합
    sequence.py        블록 순서 발급과 최종 index 확정
    content.py         태그 스키마와 점진적 태그 해석
  runtime/
    execution.py       Run·RuntimeExecution·모델 반복 호출
  vendors/             벤더별 요청·델타·원본 응답 변환
  vocabularies/        구체적인 어휘 구현
  transport/           HTTP·SSE 전송과 SSE 파서
```

`models`에는 상태 표현과 검증·직렬화에 필요한 타입을 모은다.
`models.tools`는 HITL 데이터와 ToolOutput·ToolCallback 계약을 제공한다.
`Run`과 `RuntimeExecution`은 모델 반복 호출과 연결 정리를 담당한다.
정책·실제 도구 실행·백그라운드 작업 생성은 소비 앱의 콜백이 담당한다.

작았던 mapper와 vocabulary는 델타 변환 및 어휘 확장 계약이라는 공통 용도로
`streaming.mapping`에 합쳤다. 운영 로그와 원본 통신 로그는 `logging`에서 관리하며
기존의 서로 다른 로거 이름과 활성화 조건은 유지한다.
이미 기능이 큰 모델 파일들은 파일 수를 줄이기 위해 하나의 거대 모델 파일로 합치지 않았다.

## import 변경

패키지 이동은 최상위 공개 이름을 유지한다. 다만 반복 실행 API 재설계에 따른
타입 제거와 호출 변경은 [반복 실행 문서](Completion-Runtime.md)를 따른다. 이전 루트 하위 모듈을 직접 참조한 코드는 다음과 같이 변경한다.
이전 경로용 빈 전달 파일은 만들지 않는다.

| 이전 하위 모듈 | 새 하위 모듈 |
|---|---|
| blocks / hub / parameters / native | models의 같은 이름 |
| runtime_models | models.tools |
| runtime의 실행 구현 | runtime.execution (runtime 공개 import도 지원) |
| mapper / vocabulary | streaming.mapping |
| merge | streaming.merge |
| seq | streaming.sequence |
| contentstream | streaming.content |
| wire_logging / operation_logging | logging |

예를 들어 `completion_bridge.seq.SeqAllocator`는
`completion_bridge.streaming.sequence.SeqAllocator`로 변경한다.
logging 설정의 formatter 경로는 `completion_bridge.logging.JsonFormatter`를 사용한다.
이전 모듈 경로를 저장한 pickle 등은 자동 이전하지 않는다. 배포 시 직접 하위 모듈을
참조하는 소비 코드와 설정의 이전이 필요하다.
