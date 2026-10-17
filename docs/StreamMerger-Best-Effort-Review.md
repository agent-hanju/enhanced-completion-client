# Best effort 전환에 따른 관련 코드 검토

복원 기준: 원자적 모델 구현 `c841584`. 이 문서는 실제 코드 경로를 기준으로 정리한다.

| 위치 | 발견한 전제·비용 | 조치 |
| --- | --- | --- |
| StreamMerger.apply | 전체 후보 복사 → 전체 검증 → 참조 교체 | 삭제. 기존 모델에 바로 반영하고 첫 오류에서 중단 |
| 새 모델 초기화 | 비어 있는 후보 생성 과정에서 기본값 팩터리·초기화 훅 재실행 | 새 입력의 일반 값만 소유권을 분리해 복사. 모델·목록은 DFS로 채우며 이미 반영한 누적 모델은 복사하지 않음 |
| 필드 대입 | 후보를 미완성 상태로 만들기 위해 내부 사전을 직접 수정 | getattr/setattr로 교체. 명시 입력 여부도 유지 |
| 결과·오류 스냅샷 | 외부에서 모델을 수정하거나 이후 delta가 적용돼도 기록이 바뀌지 않아야 함 | 조회·실패 때만 복사 유지. 원자성 목적의 복사가 아님 |
| `_transform(validate=...)` | 검증과 복사가 한 함수에 결합 | 삭제. 입력 분리·조회·오류 보존을 위한 복사만 유지 |
| 목록 검색 | 후보 객체가 바뀌어 delta마다 검색 사전 재생성 | 목록과 검색 사전을 유지. 교체한 하위 데이터의 캐시 해제 |
| 목록 원소 작업 | 일반 값도 원소마다 partial 생성 | 일반 값만 있는 목록은 바로 추가 |
| ToolUseBlock.input | 전체 재검증 시 validator가 조립한 JSON을 파싱 | 검증 호출 대신 model_post_init·input_json 속성 대입에서 파생값 계산 |
| Messages 서버 도구 인수 | 서버 도구 시작 뒤 ToolUseBlock 델타가 나와 서로 다른 모델의 기본값이 혼합됨 | 기억한 블록 종류에 맞춰 ServerToolBlock 델타 생성. 중복 kind/input extra가 사라지고 input_json 원문은 유지 |
| Bridge._emit | 한 이벤트의 모든 delta 병합 후 전달 | 이전 변경에서 제거. 이번에도 각 성공 delta 직후 전달 유지 |
| Bridge._accept | 출력 정렬에 필요한 seq 검사 | 유지. 원자성 장치가 아니라 출력 프로토콜의 필수 정보 검사 |
| Bridge.result | 정상·오류 결과 모두 seq 정렬을 가정 | 좌표가 미완성인 오류 상태는 원래 형식으로 반환해 조회 실패 방지 |
| Bridge 오류 | 실패 delta를 호출자가 받지 못함 | StreamMergeError.delta·result·__cause__ 제공 |
| RuntimeExecution | 매 delta마다 stream.result 전체 스냅샷 복사 | active_response 조회 시에만 복사. 종료 시 한 번 보관 |
| RuntimeExecution.partial | 도구 실행 전체의 Run 기록을 보존하는 API | 스트림 partial과 별개이므로 유지 |
| RuntimeExecution 오류 | 실행 오류로 감싸며 원인을 유지 | 유지. RuntimeErrorWithPartial.__cause__가 StreamMergeError를 보존 |
| common-hitl-chat._call | 오류 후에도 결과를 runtime에 보존하고 원래 오류 재전파 | 유지·실제 PostgreSQL 통합 시험. 실패 delta의 부분 결과도 보존 |
| common-hitl-chat.advance | CompletionBridgeError를 문자열 outcome.error로 변환 | 검토: 일반 UI 오류 처리 계약. 구조화된 병합 진단 정보는 이 단계에서 전파되지 않음 |
| common-hitl-chat SSE | 실패·완료 이벤트에 메시지 중심 오류 표현 | 검토: 실패 delta·결과를 프론트까지 전송하려면 별도 wire 계약 확장이 필요. 이번에 임의 확장하지 않음 |
| 벤더 NativeAccumulator | 원본 이벤트별 상태·검증·최종 응답 조립 | Hub delta 병합기와 별도 책임이므로 유지 |
| 도구 draft 등록·DB transaction | 별도의 업무 단위 원자성 | delta 병합 정책과 무관하므로 유지 |

## 실제 전달 범위

새 모델 초기화에서 복사한 일반 값도 현재 상태에 즉시 포함된다. 기존 모델은 해당 필드
작업 시점에 갱신한다. 라이브러리는 실패한 델타를 정상 yield하지 않고 StreamMergeError로 제공한다.
직접 호출자는 delta·부분 결과·원인을 모두 읽을 수 있다. common-hitl-chat의 `_call`도
원래 오류를 유지하지만 상위 advance는 오류 문자열로 바꾼다. 따라서 현재 브라우저는
보존된 응답과 오류 메시지를 받으며, 실패한 delta 원본까지 구조화된 형태로 받지는 않는다.
이 차이를 일반 델타 전송으로 덮지 않는다.

## 남아 있는 비용과 제약

검색 사전 재생성 때문에 생기던 목록 전체 재검색은 제거했다. result 스냅샷은 여전히
전체 데이터 복사이며, 호출자가 매 delta마다 직접 조회하면 누적 비용이 커질 수 있다.
일반 문자열을 매번 연결하거나 조립 중인 도구 인수 JSON을 파싱하는 비용도 남는다.
별도 모델 검증·대입 검증 설정·롤백은 추가하지 않았다. 모델이 자체 정의한 설정과
setter는 적용되며, 실패 후 모델의 스키마 유효성은 보증하지 않는다.
