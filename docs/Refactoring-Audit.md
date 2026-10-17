# 프로젝트 지침 준수 점검

기준: 사용자 전역 `AGENTS.md`, rules의 context7·explicit-iteration·general-interaction,
AGENTS에서 참조한 전체 guides. 프로젝트 리팩토링과 콜백 기반 반복 실행 API 변경을 반영했다.
사용자가 단계별 추정·재승인 없이 계속 진행하도록 지시했으므로 별도 설계 승인 대기는 적용하지 않았다.

## 기준별 적용과 근거

| 기준 | 적용 결과와 확인 대상 |
|---|---|
| application-design | Bridge는 요청·변환·run 진입점, RuntimeExecution은 모델 반복 호출, 콜백은 도구 실행과 수명, 어댑터는 벤더 변환, OperationLog는 운영 기록을 담당한다. 소스 AST 검사에서 100줄 초과 메서드와 공개 메서드 10개 초과 클래스는 없다. |
| structural-validation | 승인 입력·재개·등록 API를 추가하지 않았다. 승인 필요 시 HitlDraft를 반환하는 기존 구조를 유지한다. 모델 호출 중복 소비와 런타임 동시 소비는 명시적으로 거부한다. |
| simple-programming-models / explicit-iteration | src의 컴프리헨션·변환용 제너레이터 표현식·튜플 분해 반복문을 명시적 반복으로 변경했다. 기존 델타 전달·응답 병합 경로는 유지했다. |
| private-helpers | 미사용 `_model_of`, `_list_element`, `_parse_args`, `_document_blocks` 등을 제거했다. 한 번 호출하는 단순 전달 `_item_added`와 `_register`는 직접 처리한다. 선택한 변환 단계는 독립 검증 가치가 있어 유지한다. |
| comments-and-documentation | 소스의 비자명 클래스·메서드에 한국어 docstring을 작성하고 설명 주석을 한국어로 변경했다. 도구 지시문·타입 이름·API 식별자는 유지했다. 코드 주석의 과거 이식 배경을 제거했다. |
| logging | OperationLog는 시작·종료·수준·실행 ID·부모 ID·밀리초를 기록한다. 실패는 담당 계층에서 기록하며 본문·인자·예외 메시지는 제외한다. 별도 요청된 원본 wire DEBUG 로그는 명시적으로 활성화하는 API로 유지한다. |
| errors-and-validation | ExecutionStateError와 VendorStreamError를 추가했다. 네트워크 오류는 원인을 유지한 TransportError다. 태그·속성·좌표·호출 상한·중복 호출 ID·콜백 반환 타입 등을 명시적으로 검증한다. |
| parameters-and-immutability | 선택적 값은 `None` 타입을 명시한다. HubRequest 전체와 콜백 인자·반환 기록을 복사한다. 외부 Task 핸들은 복사하지 않는다. 누적 응답·Run은 실행 상태를 갱신하는 객체이므로 전체를 frozen으로 만들지 않았다. |
| algorithms | seq 사전식 순서와 기준별 증가 카운터의 불변식·복잡도를 문서화했다. 새로운 수학 알고리즘은 도입하지 않았다. 순서·인용 대상·빈 상태·중복·잘못된 입력은 seq 테스트로 확인한다. |
| data-structures | 좌표 정규화·StreamMerger·ContentSchema·파서의 순차 호출 조건과 공간·시간 특성을 문서화했다. 스키마 등록과 도구 배치 등록은 검증 후 적용한다. StreamMerger.apply는 트랜잭션을 제공하지 않음을 명시했다. |
| rest-resource-workflows | REST 서버·DB·트랜잭션은 이 패키지에 없다. 소비 앱에 대한 REST 리소스·DB 변경을 추가하지 않았다. |
| context7 | 일반 리팩토링·코드 검토는 해당 규칙의 제외 대상이다. 외부 API 계약을 추정으로 변경하지 않았다. |
| general-interaction | 설명과 새 문서는 한국어로 작성하고 실제 실행 결과와 제한을 구분한다. |

## 유지한 private 메서드의 책임

벤더별 `_ToHub`는 2개 공개 작업(map·flush)과 9~11개 내부 작업을 가진다.
내부 작업은 텍스트·추론·함수 호출·인용·미디어 등 서로 다른 이벤트의 입력 필드와 상태를
처리한다. 이벤트 변환은 한 스트림의 좌표·블록 상태를 공유하므로 클래스 분리는
상태 전달을 늘린다. 단순 호출 횟수만으로 모두 인라인 처리하지 않았다.
`_gemini_media_block`, `_assistant_input_message`, `_native_text_part`는 각각 미디어 분류,
외부 assistant 입력 변환, 완료 text/refusal 복원을 독립적으로 설명하고 검증할 수 있다.
파서의 지역 flush 함수는 실제 여러 출력 지점에서 호출되는 상태 처리다.

## 검증 범위

- Ruff lint·format, Mypy strict(src), Pyright(src·tests·examples)를 각각 실행한다.
- 오프라인 테스트는 벤더별 요청 변환·실제 규격 SSE·허브/원본 Response 병합·인용·런타임·취소·백그라운드 작업을 포함한다.
- 추가 테스트는 등록 실패 원자성, 잘못된 좌표와 이름, 중복·동시 소비, 취소 중 종료 기록,
  운영 로그의 민감값 제외, 핸들러 실패 격리, 네트워크 예외 원인 보존, 잘못된 도구 배치,
  로그용 누적기 생성 실패, 정리 실패 시 원래 예외 보존을 검증한다.
- Python 3.14.6/Pydantic 2.13.5와 최소 지원 Python 3.12.13/Pydantic 2.9.0에서 별도로 실행한다.
- `uv build`로 sdist·wheel을 생성하고 wheel에서 별도 import·호출을 확인한다.

2026-10-16 외부 테스트(`live and not slow`)는 9개 통과, 14개 실패다.
로컬 Chat Completions·tokenize는 HTTP 404, Anthropic 호출은 HTTP 401을 반환했다.
변경 전 HEAD 소스를 임시 디렉터리에서 import한 상태에서도 같은 14개 테스트가 실패했다.
대표 요청을 재확인해 변경 전의 404·401을 확인했다. 이 서비스의 실제 성공 동작은 현재
환경에서 확인하지 못했으며, 외부 테스트 전체가 통과했다고 보고하지 않는다.

## IDE와 로그 설정

[개발 환경](Development.md)은 가상환경 선택, 독립 타입 검사 명령과 추적 가능한 VS Code
설정 예제를 제공한다. [로그 문서](Wire-Logging.md)는 원본 로그와 운영 로그의 차이,
환경별 수준·출력·회전·보관·디스크 제한·권한·다중 프로세스 조건을 설명한다.

## 최종 확인 결과

2026-10-16 작업 트리 기준:

| 검사 | 결과 |
|---|---|
| Python 3.14.6 / Pydantic 2.13.5 기본 오프라인 테스트 | 625 passed, 27 deselected |
| Python 3.12.13 / Pydantic 2.9.0 기본 오프라인 테스트 | 625 passed, 27 deselected |
| 전체 소스 줄 커버리지 | 93.55% (설정 기준 70% 충족) |
| Ruff check / format check | 통과 |
| Mypy strict | 소스 31개 파일 통과 |
| Pyright | src·tests·examples 오류·경고 0 |
| 소스 AST | 31개 파일, 100줄 초과 메서드·컴프리헨션·튜플 분해 반복문 0, 비자명 선언 docstring 누락 0 |
| sdist·wheel 빌드 | 통과 |
| wheel 독립 import·모의 HTTP 호출·seq 발급 | 통과 |
| 외부 서비스 | 9 passed / 14 failed, 변경 전에도 같은 실패 확인 |

빌드 도구 uv 0.12.21은 프로젝트의 uv_build 범위(0.11.32 이상, 0.12 미만)에 대한
버전 차이 경고를 출력하지만 격리된 빌드는 성공한다. 이번 리팩토링에서 릴리스 버전이나
빌드 의존성 범위는 변경하지 않았다. 커밋·푸시는 수행하지 않았다.

패키지 정리 후 도메인 모델은 models, 실행은 runtime, 스트림 처리는 streaming으로 이동했다.
mapper·vocabulary와 운영·wire 로그 모듈을 각각 통합했다. 최상위 Python 파일은 17개에서
4개로 줄었다. 새 구조와 하위 모듈 import 이전은 [패키지 구성](Package-Structure.md)을 따른다.


반복 실행 API는 `Bridge.run(HubRequest, execute_tool=callback)`으로 변경했다.
콜백이 도구 실행과 정책 처리를 담당하며 모델용 결과가 없으면 `hitl_required`로 종료한다.
`Run.tasks`는 Task 핸들을 선택적으로 포함하는 HitlDraft 목록이다. 외부 작업은 자동 취소하지
않고 명시적 Run.aclose()로 정리한다. 자세한 계약은 [반복 실행 문서](Completion-Runtime.md)를 따른다.
외부 서비스 테스트 결과는 앞선 점검의 기록이며 이번 콜백 변경에서 재실행하지 않았다.
