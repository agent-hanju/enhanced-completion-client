# 사용자 작성 assistant 메시지: 외부 연동 계약

현재 구현된 메시지 단위 인터페이스와 외부 연동 계약이다.
`HubMessage.synthetic`의 기본값은 `False`이며 기존 호출은 필드를 생략할 수 있다.
기본 직렬화에는 `"synthetic": false`가 포함된다. 가상 메시지 기능을 사용하는 프로젝트만
작성 여부를 명시하고 저장·복원하면 된다.

## 1. 외부 애플리케이션이 전달할 정보

assistant 메시지의 선언 필드는 `synthetic: bool = False`다. 블록별 플래그는 없다.

| 값 | 의미 |
|---|---|
| `True` | 애플리케이션 또는 사용자가 작성·편집한 assistant 이력이라는 명시적 선언 |
| `False` 또는 생략 | 기존 모델 응답 재생 경로 사용. 원본 무결성을 검증했다는 의미는 아님 |

`synthetic=True`는 `role="assistant"`에서만 허용하며, 다른 역할에서는 입력 검증 오류로
처리한다. `user`, `system`, 도구 결과 메시지에는 표시하지 않는다.

모델 응답에 호출 블록 하나만 추가했어도 메시지 전체를 `synthetic=True`로 표시한다.
브리지는 작성·편집 여부를 자동 감지하지 않는다. `source=None`, 서명 누락, 도구 이름으로
synthetic 여부를 추측하지 않는다. 단순 저장·복원이나 `HubMessage.of_response(response)`로
원본 응답을 이력에 옮기는 것은 사용자 편집으로 취급하지 않는다.

## 2. 메시지 생성 인터페이스

### 직접 작성한 함수 호출과 결과

```python
import json

from completion_bridge import HubMessage, ToolResultBlock, ToolUseBlock

call_id = "app-call-001"
arguments = {"path": "/data/skills/translation"}

assistant_message = HubMessage.assistant(
    synthetic=True,
    blocks=[
        ToolUseBlock(
            id=call_id,
            name="activate_skill",
            input_json=json.dumps(arguments, ensure_ascii=False),
        ),
    ],
)
result_message = HubMessage(
    role="user",
    content=[
        ToolResultBlock(
            tool_use_id=call_id,
            name="activate_skill",
            content="번역 스킬을 활성화했습니다.",
        ),
    ],
)

history.extend([assistant_message, result_message])
response = await bridge.complete(history)
```

`input_json`은 기존 도구 인수 계약을 사용한다. 현재 Chat Completions 요청 변환까지 포함한
공통 사용 예에서는 JSON 문자열을 전달한다. 이 기능은 도구 인수 모델 변경을 포함하지 않는다.
호출의 `id`와 결과의 `tool_use_id`는 일치해야 한다.

도구 결과의 Hub 역할은 위 예처럼 `user`로 구성할 수 있다. 브리지가 대상에 따라 Chat
Completions의 `tool`, Gemini의 functionResponse 등 기존 요청 구조로 변환한다.
병렬 호출은 assistant 메시지에 함께 넣고, 대응 결과들도 다음 결과 메시지에 함께 넣는다.

이 예는 이미 애플리케이션이 수행한 작업을 이력에 기록한다. 플래그가 도구 실행이나 결과
생성을 수행하지는 않는다. 도구를 모델이 앞으로 호출할 수 있게 할지는 기존 `tools` 인자로
별도 결정한다.

### 일반 텍스트 및 편집된 응답

```python
# 직접 작성한 assistant 텍스트
message = HubMessage.assistant("이 작업은 애플리케이션에서 완료했습니다.", synthetic=True)

# 모델 응답을 그대로 보존
original = HubMessage.of_response(response)  # synthetic=False

# 모델 응답을 편집한 새 이력
edited = HubMessage(
    role="assistant",
    synthetic=True,
    content=edited_blocks,
)
```

`edited_blocks`는 호출자가 구성한 최종 블록 목록이다. 이 플래그는 서명된 블록 내용을
수정한 뒤 서명을 복구해 주는 기능이 아니다. 보존하는 실제 서명과 대응 원본 블록은 기존
벤더 계약에 맞게 유지해야 한다.

## 3. 저장·조회·요청 조립

외부 프로젝트는 메시지 DTO 또는 저장 표현에 `synthetic`을 메시지 수준 필드로 포함한다.
블록 payload만 저장해서는 이 선언을 보존할 수 없다. 구체적인 DB 컬럼/JSON 배치는 해당
프로젝트가 결정하되, 이력 조회 후 브리지에 전달할 때 같은 값을 복원해야 한다.

```json
{
  "role": "assistant",
  "synthetic": true,
  "content": [
    {
      "type": "tool_use",
      "id": "app-call-001",
      "name": "activate_skill",
      "input_json": "{\"path\": \"/data/skills/translation\"}"
    }
  ]
}
```

위 JSON의 `input_json`은 문자열이다. 저장·복원은 메시지 전체를 대상으로 수행한다.

```python
stored = assistant_message.model_dump(mode="json")
restored = HubMessage.model_validate(stored)

# 기존 호출 형태 그대로 사용한다.
body = bridge.build_request([*history, restored])
# await bridge.complete(messages), bridge.stream(messages), SyncBridge도 같은 계약이다.
```

이 예의 `history`는 restored를 추가하기 전 이력이다. 동일 메시지를 중복 추가하지 않는다.
`HubMessage` 객체 대신 mapping을 전달할 때도 동일하게 `synthetic` 키를 포함한다.
`HubResponse`의 스트리밍 delta·중간 결과·최종 결과에는 `synthetic` 필드가 없다.
스트리밍 응답에서 False를 강제로 넣는 것이 아니라, `HubMessage.of_response(response)`로
요청 이력에 옮길 때 기본값 False를 사용한다. 그 뒤 애플리케이션이 편집했다면 True를 명시한다.
작성 여부 선언은 요청 이력의 `HubMessage`에만 속한다.

## 4. 브리지의 처리 계약

| 대상 | synthetic assistant 처리 |
|---|---|
| Gemini GenerateContent | 메시지 선언을 변환 컨텍스트로 전달하고, 서명이 없는 함수 호출 Part에 필요한 특수 처리를 적용 |
| Chat Completions | 기존 assistant 필드 및 tool_calls 변환 유지 |
| Anthropic Messages | 기존 content block 변환 유지 |
| OpenAI Responses | 기존 input item 변환 유지 |

모든 대상에서 `synthetic` 자체는 벤더 요청에 넣지 않는다. 외부 프로젝트는 Gemini 전용
특수 문자열을 알거나 `ToolUseBlock.source="generate_content"`로 출처를 위장할 필요가 없다.
일반 text-only synthetic 메시지도 같은 공개 인터페이스를 사용하며 함수 호출 특수 처리는
적용되지 않는다.

Gemini 변환 규칙은 다음과 같다.

1. 명시적으로 synthetic인 assistant 메시지의 미서명 함수 호출만 특수 처리한다.
2. 같은 메시지에 실제 Gemini 서명이 있으면 그대로 보존한다. native에 보존된 서명도 확인한다.
3. 특수 값은 출력 Part에만 적용한다. 입력 메시지·블록의 source/signature/native를 변경하지 않는다.
4. 일반 모델 응답의 서명 누락을 자동으로 보완하지 않는다.
5. text·thinking 블록에 특수 서명을 생성하지 않는다.
6. 기존 벤더 간 이력 변환은 메시지를 자동으로 synthetic으로 바꾸지 않는다.

Google 문서의 수동 구성 함수 호출용 처리는 Gemini 어댑터가 담당한다. 실제 서명의 원문
보존 규칙과 직접 구성한 함수 호출의 예외는
[공식 thought signatures 문서](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures#faqs)를
기준으로 한다. 이 선언은 임의의 추론 블록이나 벤더 전용 도구를 유효하게 만드는 기능은 아니다.

## 5. 프로젝트별 책임과 연동 확인

| 담당 | 책임 |
|---|---|
| 외부 애플리케이션 | 어떤 assistant 이력을 작성·편집할지 결정, synthetic 선언·저장·복원, 호출 ID와 결과 연결 |
| completion_bridge 공통 모델 | 메시지 단위 필드 및 역할 검증, 요청 처리 중 선언 보존 |
| 벤더 어댑터 | 선언을 대상 API 표현으로 변환, 실제 서명 보존, 메타데이터의 요청 유출 방지 |

외부 프로젝트의 연동 완료 기준:

- 직접 작성하거나 편집한 assistant 메시지가 `synthetic=True`로 브리지에 도착한다.
- 저장 후 조회한 메시지도 해당 값을 유지한다.
- 정상 모델 응답을 그대로 재사용할 때는 기존 동작과 서명이 유지된다.
- 애플리케이션이 vendor source나 특수 서명 문자열을 직접 지정하지 않는다.
- 함수 호출과 결과의 ID·이름·순서를 유지한다.
- `build_request()` 결과에는 `synthetic` 키가 없고, Gemini의 가상 함수 호출에만 필요한 처리가 있다.

개발 단계의 신규 인터페이스 계약이므로 데이터 마이그레이션이나 이전 버전 호환 절차는
이 문서의 범위에 포함하지 않는다.
