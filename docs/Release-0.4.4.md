# completion-bridge 0.4.4

`0.4.3` 대비 `Hyperparameters.tool_choice`의 입력을 Anthropic Messages API의 `tool_choice` 객체 형식으로
바꾼다. 이전 `ToolChoice(mode=..., name=...)` 입력은 검증 오류이므로 아래 적용 사항을 확인한다.

## 변경

- `tool_choice`는 다음 네 객체 중 하나를 받는다. 생략하거나 `None`이면 이전처럼 보내지 않는다.

  | 입력 | Chat Completions | Responses | Messages | GenerateContent |
  |---|---|---|---|---|
  | `{"type": "auto"}` | `"auto"` | `"auto"` | 입력 그대로 | `mode: AUTO` |
  | `{"type": "none"}` | `"none"` | `"none"` | 입력 그대로 | `mode: NONE` |
  | `{"type": "any"}` | `"required"` | `"required"` | 입력 그대로 | `mode: ANY` |
  | `{"type": "tool", "name": N}` | `{"type": "function", "function": {"name": N}}` | `{"type": "function", "name": N}` | 입력 그대로 | `mode: ANY`, `allowedFunctionNames: [N]` |

- `type`은 필수다. `name`은 `tool`에서만 필수이며 빈 문자열일 수 없고, 다른 type에서는 거부한다.
  문자열 축약형(`"auto"`), 선언하지 않은 필드, 이전 `mode` 형식은 모두 검증 오류다.
- `auto`, `any`, `tool`은 선택적 `disable_parallel_tool_use`(bool)를 받고 `none`은 받지 않는다.
  - Messages: `tool_choice` 안에 그대로 보낸다.
  - Chat Completions·Responses: 반대 값을 최상위 `parallel_tool_calls`로 보낸다.
  - 최상위 `parallel_tool_calls`를 함께 지정하면 두 값이 반대여야 한다. 같은 값이면 충돌로 보고
    `Hyperparameters` 생성 시 검증 오류다. 생성자 기본값과 호출별 값을 합친 결과에도 같은 검사를 한다.
  - GenerateContent: `functionCallingConfig`에 병렬 호출 제어가 없어, 이 필드를 지정하면 요청 생성 시
    `ValueError`다. 최상위 `parallel_tool_calls`는 이전처럼 GenerateContent 요청에서 빠진다.
- 최상위 `parallel_tool_calls`를 Messages의 `tool_choice.disable_parallel_tool_use`로 반전해 넣는
  기존 동작은 유지한다. `none`에는 넣지 않는다.
- `Hyperparameters.merged()`는 `tool_choice`를 필드 단위로 합치지 않는다. 호출별 값이 있으면 객체 전체를
  바꾼다. 기본값이 `{"type": "tool", "name": "a"}`이고 호출별 값이 `{"type": "none"}`이면 결과는
  `{"type": "none"}`이다.
- 공개 JSON Schema는 `type`을 discriminator로 하는 `oneOf`이며, type별 필수·허용 필드와
  `additionalProperties: false`를 표현한다.

## 추가

- 공개 이름 `ToolChoiceAuto`, `ToolChoiceNone`, `ToolChoiceAny`, `ToolChoiceTool`.
  `ToolChoice`는 이 넷을 `type`으로 구분하는 타입 별칭이다. 클래스가 아니므로 `ToolChoice(...)` 호출이나
  `isinstance(value, ToolChoice)` 검사는 할 수 없다.

## 적용

| 이전 | 이후 |
|---|---|
| `ToolChoice(mode="auto")` | `ToolChoiceAuto(type="auto")` 또는 `{"type": "auto"}` |
| `ToolChoice(mode="none")` | `ToolChoiceNone(type="none")` 또는 `{"type": "none"}` |
| `ToolChoice(mode="required")` | `ToolChoiceAny(type="any")` 또는 `{"type": "any"}` |
| `ToolChoice(mode="named", name="f")` | `ToolChoiceTool(type="tool", name="f")` 또는 `{"type": "tool", "name": "f"}` |
| `choice.mode == "none"` | `choice.type == "none"` 또는 `isinstance(choice, ToolChoiceNone)` |

- 설정 파일·요청 DTO에 `mode`/`name` 형식을 정의한 호출 프로젝트는 의존성 버전만 올려서는 이전되지
  않는다. 그 형식을 위 객체 형식으로 바꾼다.
- `disable_parallel_tool_use`와 `parallel_tool_calls`를 같은 값으로 함께 지정한 설정은 이제 오류다.
  하나만 남기거나 반대 값으로 맞춘다.
- 이름 지정(`tool`)이나 강제 호출(`any`)을 지원하지 않는 모델이 있다. bridge는 이를 `auto`로 바꾸지 않고
  벤더 오류를 그대로 전달한다.

## 검증 범위

- 오프라인 테스트(`tests/test_parameters.py`): 입력 수용·거부, JSON Schema, 네 API 변환,
  병렬 옵션의 전송·충돌·GenerateContent 오류, `merged()`의 교체 동작.
- 실제 vLLM 호출(`tests/test_live.py::TestLiveToolChoice`). vLLM v0.29.0, Qwen3-8B AWQ,
  `--tool-call-parser hermes` 서버에서 확인했다.
  - `auto`는 수용되었고 모델이 도구를 호출했다. `any`는 도구가 필요 없는 질문에도 호출했고,
    `tool`은 다른 도구가 맞는 질문에도 지정한 도구만 호출했다.
  - `none`에서는 도구 호출 블록이 없었다. 다만 vLLM은 기본값으로 `tool_choice`와 관계없이 도구 정의를
    프롬프트에 포함하므로, 모델이 `<tool_call>` 문자열을 본문 텍스트로 출력했다. 프롬프트에서 도구 정의를
    빼려면 서버 옵션 `--exclude-tools-when-tool-choice-none`을 쓴다.
  - 요청에 없는 이름을 지정한 `tool`은 vLLM이 400으로 거부했고 bridge는 그 오류를 그대로 전달했다.
  - 세 도시의 날씨를 요청했을 때 `disable_parallel_tool_use=false`는 호출 3개, `true`는 호출 1개였다.
- Anthropic Messages, OpenAI Responses, Gemini GenerateContent의 실제 호출은 확인하지 않았다.
