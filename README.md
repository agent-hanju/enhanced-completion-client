# completion_bridge

여러 LLM 벤더의 요청 API ·SSE 응답을 하나의 Python 모델로 연결하는 패키지

| 어댑터 | API |
|---|---|
| `chat_completions` | vLLM의 OpenAI compatible Chat Completions |
| `responses` | OpenAI Responses |
| `messages` | Anthropic Messages |
| `generate_content` | Gemini `streamGenerateContent` |

모든 응답은 `HubResponse`로 수렴하고 `HubMessage.of_response(response)`로 다음 요청 이력이 된다.
텍스트, 멀티모달 입력, 클라이언트 도구 호출·결과는 대상 벤더의 content 형태로 변환한다.

추론 서명과 서버 도구처럼 벤더에 종속된 항목은 원본 `native`/`raw`를 보존해 같은 벤더로만
재전송한다.

- `HubMessage`는 Anthropic Messages API의 Message 객체에 호환용 필드, 타입을 추가한 확장 형식이다.
- `build_request()`, `stream()`, `complete()`의 입력은 `(HubMessage | string) []`다. 문자열은 단일 텍스트 content만 있는 user `HubMessage`의 편의 입력이다.
- `stream()`은 변환이 끝난 `HubResponse` delta를 즉시 내보내며, 스트림 종료 후 `stream.result`에서 같은 delta들을 병합한 `HubResponse`를 얻는다.
- `complete()`는 스트림을 내부 소비하고 병합된 `HubResponse` 하나를 반환한다.
- `HubMessage.of_response(result)`로 HubResponse에서 HubMessage만 확인할 수 있다.

## Requirements

- Python 3.12 이상
- uv

## 설치

```bash
uv add completion_bridge
```

### 개발 환경 구성

```bash
uv sync --all-groups
uv run pytest
```

## 사용법

### 비동기 API

```python
from completion_bridge import Bridge, HubMessage
from completion_bridge.vendors import responses

async with Bridge(
    vendor=responses,
    base_url="https://api.openai.com",
    model="gpt-5.6-luna",
    api_key="...",
) as bridge:
    stream = bridge.stream(["서울을 한 단어로 설명해줘"])
    async for delta in stream:
        print(delta.text, end="")

    result = stream.result
    history = [
        "서울을 한 단어로 설명해줘",
        HubMessage.of_response(result),
        "영어로 바꿔줘",
    ]
    follow_up = await bridge.complete(history)
```

`complete()`는 내부적으로 스트림을 끝까지 소비해 병합된 `HubResponse`만 반환한다.
요청을 보낼 땐 `HubMessage`를 사용하며, `build_request()`는 네트워크 요청 없이 각 벤더사에 보내지는 변환 형태를 확인할 때 사용한다.

### 동기 API

```python
from completion_bridge import SyncBridge
from completion_bridge.vendors import chat_completions

with SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen3.8-27b",
) as bridge:
    result = bridge.complete(
        ["대한민국의 수도는?"],
        hyperparameters=Hyperparameters(max_completion_tokens=32, chat_template_kwargs={"enable_thinking":False})
    )
    print(result.text)
```

## 요청 옵션과 Hyperparameters

대화 이외의 생성 옵션은 `Hyperparameters` 객체에 둔다. 각 필드는 각 API와 사용 모델에 사용
가능한 필드들만 적용되며, 나머지 필드는 무시된다.

```python
from completion_bridge import (
    Hyperparameters,
    ResponseFormat,
    SyncBridge,
    ToolChoice,
)
from completion_bridge.vendors import chat_completions

defaults = Hyperparameters(
    max_completion_tokens=256,
    temperature=0.2,
    top_p=0.9,
    stop=["<END>"],
    tool_choice=ToolChoice(mode="auto"),
    response_format=ResponseFormat(
        type="json_schema",
        name="answer",
        json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    ),
    # 벤더별 고유 필드도 사용 가능
    top_k=40,                                     # vLLM Chat Completions에서만 사용
    include=["reasoning.encrypted_content"],      # Responses에서만 사용
    inference_geo="us",                          # Messages에서만 사용
    safety_settings=[{"category": "HARM_CATEGORY_HATE_SPEECH"}],  # Gemini만 사용
    chat_template_kwargs={"enable_thinking": False},  # vLLM 정식 필드. 최상위로 나간다
)

bridge = SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen-3.8-27b",
    hyperparameters=defaults,
)

# 호출별 값은 생성자에 등록했던 기본값에 오버라이드된다.
body = bridge.build_request(
    ["질문"],
    hyperparameters=Hyperparameters(max_completion_tokens=64),
)
```

필드 이름은 각 API의 wire 이름을 그대로 쓴다. 별도의 추상 대표 이름을 만들지 않는다. 알려진
필드가 대상 API에 없으면 그 요청에서 조용히 빠지고, 모델에 정의되지 않은 이름은
`extra="forbid"` 검증 오류가 되어 오타를 숨기지 않는다. 두 층위는 다르다.

**브리지는 모델을 보지 않는다.** 대상 API가 소유한 필드는 그대로 싣고, 그 값이 그 모델에서
유효한지는 판정하지 않는다. 예를 들어 Anthropic 최신 모델은 `temperature`를 받으면 400인데,
브리지는 막지 않고 벤더가 알려주게 둔다. 모델 능력 매트릭스는 모델이 나올 때마다 낡고, 코드에
넣으면 조용히 틀리기 때문이다. 어느 모델이 무엇을 거부하는지는
[모델별 요청 제약](docs/Support-Matrix.md#모델별-요청-제약)에 정리해 둔다.

### 필드 소유표

필드는 서로 독립이다. 한 필드를 여러 API가 소유할 수 있고, 대상이 소유하지 않은 필드는 그
요청에서 빠진다. **한 필드에 넣은 값이 다른 필드로 전이되지는 않는다.** 여러 벤더에 같은
설정을 보내려면 각 벤더의 필드를 각각 쓴다.

```python
# 같은 예산을 세 API에 보내려면 세 필드를 쓴다. 전이는 없다.
Hyperparameters(max_completion_tokens=256, max_tokens=256, max_output_tokens=256)
```

wire 이름이 같아도 값 계약이 다르면 벤더 접두어로 분리한다. OpenAI 계열 `service_tier`와
Anthropic의 `anthropic_service_tier`, Gemini의 `gemini_service_tier`는 서로 다른 필드다.
`metadata`와 `anthropic_metadata`도 그렇다.

`chat_completions`의 대상은 **vLLM**이다. OpenAI 호스티드 전용 기능
(`verbosity`, `web_search_options`, `service_tier`, `store`, `metadata`, `prediction`,
`prompt_cache_key` 등)은 vLLM `ChatCompletionRequest`에 없으므로 이 어댑터의 필드가 아니다.

| 필드 | vLLM Chat | Responses | Messages | Gemini |
|---|:---:|:---:|:---:|:---:|
| `temperature`, `top_p` | O | O | **모델 의존** | `generationConfig` |
| `top_k` | O | – | **모델 의존** | `generationConfig` |
| `seed` | O | – | – | `generationConfig` |
| `presence_penalty`, `frequency_penalty` | O | – | – | `generationConfig` |
| `max_completion_tokens` | O | – | – | – |
| `max_tokens` | – | – | O (필수) | – |
| `max_output_tokens` | – | O | – | `generationConfig` |
| `stop` | O | – | – | – |
| `stop_sequences` | – | – | O | `generationConfig` |
| `reasoning_effort` | O | – | – | – |
| `reasoning` | – | O | – | – |
| `output_config` | – | – | O | – |
| `thinking` | – | – | O | – |
| `betas` | – | – | O | – |
| `parallel_tool_calls` | O | O | – | – |
| `logprobs`, `logit_bias` | O | – | – | – |
| `top_logprobs` | O | O | – | – |
| `stream_options` | O | O | – | – |
| `background`, `conversation`, `previous_response_id`, `include`, `prompt`, `text`, `truncation` | – | O | – | – |
| `container`, `context_management`, `inference_geo`, `mcp_servers`, `cache_control` | – | – | O | – |
| `generation_config`, `tool_config`, `safety_settings`, `cached_content` | – | – | – | O |
| `metadata` | – | O | – | – |
| `anthropic_metadata` | – | – | O | – |
| `service_tier` | – | O | – | – |
| `anthropic_service_tier` | – | – | O | – |
| `gemini_service_tier` | – | – | – | O |
| `prompt_cache_key`, `prompt_cache_options`, `safety_identifier`, `store` | – | O | – | – |

**각 API에서 deprecated된 필드는 선언하지 않는다.** `user`(vLLM은 무시, Responses는
deprecated), Responses의 `prompt_cache_retention`, vLLM의 `max_tokens`가 그렇다. 정의되지
않은 이름은 검증 오류이므로 낡은 코드가 조용히 통과하지 않고 이주 지점을 알려준다.

Responses에는 top-level `logprobs`가 없다. `include`에 `message.output_text.logprobs`를 넣고
`top_logprobs`로 개수를 정한다.

#### Messages의 모델 의존 필드

Anthropic은 최신 모델에서 샘플링 파라미터를 **제거**했다. Opus 5, Sonnet 5, Opus 4.8/4.7,
Fable 5/5.1에 `temperature`/`top_p`/`top_k`를 보내면 **400**이다. Opus 4.6/Sonnet 4.6과 그 이전
모델(Haiku 4.5 등)에서만 받는다. 어댑터가 모델을 보고 생략해야 한다.

추론 깊이는 샘플링이 아니라 `output_config.effort`(`low`~`max`)와
`thinking={"type": "adaptive"}`로 조절한다. `thinking.budget_tokens`도 최신 모델에서 제거되어
400이다.

#### Messages의 beta 게이트 필드

아래는 필드만 넣는다고 동작하지 않는다. 대응하는 beta 헤더가 필요하므로
`MessagesAdapter(betas=[...])`와 짝지어야 한다.

| 필드 | 필요한 beta | 추가 조건 |
|---|---|---|
| `context_management` | `context-management-2025-06-27` | `edits`에 `clear_tool_uses_20250919` / `clear_thinking_20251015` |
| `context_management` (compaction) | `compact-2026-01-12` | `compact_20260112`. 응답의 compaction 블록을 이력에 그대로 되보내야 함 |
| `mcp_servers` | `mcp-client-2025-11-20` | **`tools`에 `{"type":"mcp_toolset","mcp_server_name":<같은 이름>}` 필수.** 단독으로 보내면 검증 오류 |
| `container` (Skills) | `code-execution-2025-08-25` | `code_execution_20260521` 도구와 함께 |
| `output_config.task_budget` | `task-budgets-2026-03-13` | `total` 최소 20,000 |

#### vLLM 고유 필드

vLLM `ChatCompletionRequest`가 OpenAI 표준에 더해 선언하는 필드다. 다른 세 API에는 대응
개념이 없어 그 요청에서 빠진다.

| 계열 | 필드 |
|---|---|
| 샘플링 | `min_p`, `repetition_penalty`, `length_penalty`, `use_beam_search`, `min_tokens`, `ignore_eos`, `stop_token_ids`, `include_stop_str_in_output`, `allowed_token_ids`, `bad_words`, `prompt_logprobs`, `skip_special_tokens`, `spaces_between_special_tokens`, `truncate_prompt_tokens`, `truncation_side` |
| 채팅 템플릿 | `chat_template`, `chat_template_kwargs`, `add_generation_prompt`, `continue_final_message`, `add_special_tokens`, `echo` |
| 멀티모달 처리 | `media_io_kwargs`, `mm_processor_kwargs` |
| 구조화 출력 | `structured_outputs` |
| RAG | `documents` |
| 운영 | `priority`, `request_id`, `return_tokens_as_token_ids` |

`chat_template_kwargs`는 **body 최상위 정식 필드**다. OpenAI SDK를 쓸 때 `extra_body`로 감싸는
것은 SDK가 미선언 필드를 통과시키는 방법일 뿐이고, 이 패키지는 wire body를 직접 만들므로 감쌀
이유가 없다. 그래서 `extra_body` 필드는 두지 않는다.

여러 후보를 반환하는 `n`/`candidateCount`는 `HubResponse`가 후보 하나만 표현하므로 필드로
두지 않는다.

### 예외: 네 wire 모양을 갖는 두 필드

`tool_choice`와 `response_format`만 타입 객체로 둔다. 입력은 두세 필드짜리 의도인데 wire
모양이 API마다 다르고, 셋은 이름까지 같아 접두어로 나누면 사용자가 벤더별 dict를 외워야 하기
때문이다. 이 둘은 하나만 쓰면 대상 API의 모양으로 투영된다.

| 입력 | Chat Completions | Responses | Messages | Gemini |
|---|---|---|---|---|
| `ToolChoice(mode="auto")` | `"auto"` | `"auto"` | `{"type":"auto"}` | `{"functionCallingConfig":{"mode":"AUTO"}}` |
| `ToolChoice(mode="required")` | `"required"` | `"required"` | `{"type":"any"}` | `mode: "ANY"` |
| `ToolChoice(mode="named", name="f")` | `{"type":"function","function":{"name":"f"}}` | `{"type":"function","name":"f"}` | `{"type":"tool","name":"f"}` | `mode:"ANY"` + `allowedFunctionNames:["f"]` |
| `ResponseFormat(type="text")` | `{"type":"text"}` | `text.format` | 생략 | 생략 |
| `ResponseFormat(type="json_object")` | `{"type":"json_object"}` | `text.format` | 생략 | 생략 |
| `ResponseFormat(type="json_schema", ...)` | `response_format.json_schema` (name·strict 포함) | `text.format` (name·strict 포함) | `output_config.format` (schema만) | `responseMimeType` + `responseJsonSchema` |

Messages와 Gemini에는 `text`/`json_object` 모드에 대응하는 개념이 없어 그 요청에서 빠진다.
`name`과 `strict`도 받는 API에만 실린다.

벤더 raw 필드를 직접 쓰면 그쪽이 이긴다. Responses에 `text={"format": {...}}`를 명시하면
`response_format` 투영 결과 위에 덮인다. 벤더 필드가 항상 우선이다.


## 도구 호출과 결과

허브에서는 `ToolUseBlock`과 `ToolResultBlock` 한 쌍을 사용한다.

```python
from completion_bridge import HubMessage, ToolResultBlock

first = await bridge.complete(messages, tools=tools)
assistant = HubMessage.of_response(first)

results = [
    ToolResultBlock(
        tool_use_id=call.id,
        name=call.name,
        content=run_tool(call.name, call.input),
    )
    for call in first.content
    if call.type == "tool_use"
]

second = await bridge.complete(
    [*messages, assistant, HubMessage(role="user", content=results)],
    tools=tools,
)
```

tool call/result 블록은 대상에 따라 다음처럼 내려간다.

| API | 호출 | 결과 |
|---|---|---|
| Chat Completions | assistant `tool_calls[]` | 별도 `role: tool` 메시지 |
| Anthropic | assistant `tool_use` | user `tool_result` 블록 |
| Responses | `function_call` Item | `function_call_output` Item |
| Gemini | model `functionCall` Part | user `functionResponse` Part |

벤더 API의 내장 도구(Web search, Web fetch, ... 등)는 실행될 경우 hitl loop를 타지 않고, 서버에서 실행하는 도구와는 다른 필드로 벤더 응답에 포함되어 전달된다.(주로 assistant content의 전용 블록이나 특수 필드를 통해 전달됨)

각 벤더에 상호 대응되는 벤더 내장 도구가 있다 하더라도, 서로의 형식으로 교차 변환할 경우 해당 벤더 API가 생성하지 않은 비신뢰적 내용을 대화 내용에 침투시킨 것으로 해석하여 보안 상 응답이 거절되는 경우가 많다.

따라서 벤더 API의 내장 도구의 경우 다른 벤더에 적용될 경우 현재 요청에는 제공되지 않는 가상의 서버 툴의 동작으로 변환해 제공하거나(벤더_tool_name 형태의 tool call과 user측 tool result), tool_result 형태로 정리할 수 없는 특수 툴 실행 결과의 경우 content 내에 직렬화하여 포함시킨다.

### 벤더 API 내장 도구 변환표

변환 결과는 세 가지 중 하나다.

| 결과 | 모양 | 조건 |
|---|---|---|
| **원본** | 벤더 native 블록을 그대로 재전송 | `source`가 대상과 같을 때. 항상 우선한다 |
| **가상 도구** | assistant `벤더_도구명` tool call + user tool result 쌍 | 원본에 호출과 결과가 **쌍으로** 남아 있을 때 |
| **직렬화** | content 안 XML-like 태그 | 쌍이 없을 때. 또는 결과가 대상의 tool result 모양에 담기지 않을 때 |

**실행 환경 의존성은 판정 기준이 아니다.** 가상 도구는 실행 제안이 아니라 이력 기록이다.
`벤더_도구명`은 그 요청의 `tools`에 없으므로 모델이 호출할 수 없고, 옮겨지는 것은 "그 도구가
이렇게 호출되어 이런 결과가 나왔다"는 사실뿐이다. bash 실행 기록이 옮겨간다고 그 서버의 shell이
따라가지 않는다. 좌표계나 working directory를 옮길 수 없다는 것은 그 도구를 **다시 호출 가능하게
만들 때**의 제약이고, 브리지는 그것을 하지 않는다.

도구 이름은 `anthropic_web_search`처럼 **벤더 브랜드 접두어**를 붙인다. 비이식 도구의
`ToolUseBlock.kind`가 이미 `anthropic_bash`, `anthropic_computer` 형태를 쓰고 있어 같은 규칙이다.
접두어 덕분에 대상 API의 실제 도구와 이름이 충돌하지 않는다.

행은 원본 벤더의 내장 도구, 열은 그 이력을 어느 API로 보낼 때의 결과다.

| 원본 도구 | 쌍 | → Chat Completions | → Responses | → Messages | → Gemini |
|---|:---:|---|---|---|---|
| **Anthropic** web search | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** web fetch | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** code execution | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** bash / text editor | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** computer / browser | O | 가상 + **결과 직렬화** | 가상 | **원본** | 가상 |
| **Anthropic** memory | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** tool search | O | 가상 | 가상 | **원본** | 가상 |
| **Anthropic** MCP | O | 가상 | 가상 | **원본** | 가상 |
| **Responses** web search | △ | 가상(호출만) | **원본** | 가상(호출만) | 가상(호출만) |
| **Responses** file search | O | 가상 | **원본** | 가상 | 가상 |
| **Responses** code interpreter | O | 가상 | **원본** | 가상 | 가상 |
| **Responses** image generation | O | 가상 + **결과 직렬화** | **원본** | 가상 | 가상 |
| **Responses** computer / shell / apply patch | O | 가상 + **결과 직렬화** | **원본** | 가상 | 가상 |
| **Responses** MCP call / list tools | O | 가상 | **원본** | 가상 | 가상 |
| **Responses** MCP approval | O | 가상 | **원본** | 가상 | 가상 |
| **Gemini** Google Search grounding | X | 직렬화 | 직렬화 | 직렬화 | **원본** |
| **Gemini** URL context | X | 직렬화 | 직렬화 | 직렬화 | **원본** |
| **Gemini** code execution | O | 가상 | 가상 | 가상 | **원본** |
| **Gemini** computer use | O | 가상 + **결과 직렬화** | 가상 | 가상 | **원본** |

**쌍 `X`인 것만 통째로 직렬화한다.** Gemini의 grounding과 url context는 call/result가 아니라
candidate에 붙는 응답 metadata다. 쌍으로 만들 호출 자체가 없으므로 근거 목록을 content에
직렬화한다.

**쌍 `△`.** Responses web search는 호출 Item은 있지만 검색 결과가 답변 텍스트의
`url_citation` annotation으로 흩어진다. 호출과 상태만 가상 도구가 되고, 근거는 인용 규칙을
따른다.

**"결과 직렬화"는 대상이 Chat Completions일 때만 나온다.** 나머지 셋은 tool result 안에 중첩
블록을 받지만, Chat Completions의 `role: tool` 메시지는 문자열 하나다. 스크린샷이나 생성
이미지처럼 텍스트로 접히지 않는 결과는 그 열에서만 태그로 직렬화된다.

| 대상 | tool result 내용 | 비텍스트 결과 |
|---|---|---|
| Chat Completions | `role: tool` 문자열 | **직렬화 필요** |
| Responses | `function_call_output` + 중첩 content part | 그대로 |
| Messages | `tool_result` + 중첩 block | 그대로 |
| Gemini | `functionResponse` 구조 JSON/parts | 그대로 |

**일반 function call은 이 표에 없다.** `ToolUseBlock.kind="function"`은 벤더 중립 실행 계약을
가지므로 네 API 사이에서 그대로 교차 변환된다. OpenAI custom tool은 Chat Completions와
Responses 사이에서만 이동한다.


## 멀티모달 입력

```python
from completion_bridge import AudioBlock, DocumentBlock, HubMessage, ImageBlock, TextBlock

message = HubMessage(
    role="user",
    content=[
        ImageBlock(url="https://example.com/chart.png", detail="low"),
        AudioBlock(data=audio_base64, format="wav"),
        DocumentBlock(
            id="report",
            title="보고서",
            data=pdf_base64,
            media_type="application/pdf",
        ),
        TextBlock(text="핵심만 설명해줘"),
    ],
)

wire = bridge.build_request([message])
```

공통 멀티모달 요청의 입력은 URL 또는 inline base64다. 대상 API가 그 유형의 네이티브 채널을
가지면 그 형태로 내려가고, 없으면 XML-like로 직렬화된다.

### 멀티모달 유형별 변환표

대상 API가 네이티브 채널을 가진 유형은 그 형태로 내려간다. 없으면 **직렬화**한다. 직렬화는
`<document>` 계열 XML-like 태그로 content의 적절한 위치에 넣는 것이고, 브리지에 그 유형의 텍스트
전처리기가 등록되어 있으면 추출한 내용을, 없으면 **전달할 수 없다는 사실을 명시한 태그**를 넣는다.
조용히 사라지는 경우는 없다.

| 유형 | 예시 media_type | Chat Completions | Responses | Messages | Gemini |
|---|---|---|---|---|---|
| 이미지 | `image/png`, `image/jpeg`, `image/webp`, `image/gif` | `image_url` (URL/data URL) | `input_image` (URL/data URL) | `image.source` (base64/URL) | `inlineData`/`fileData` |
| 오디오 | `audio/wav`, `audio/mpeg` | `input_audio` (**base64만**) | 직렬화 | 직렬화 | `inlineData`/`fileData` |
| 비디오 | `video/mp4` 등 | 직렬화 | 직렬화 | 직렬화 | `inlineData`/`fileData` |
| PDF | `application/pdf` | `file.file_data` (**base64만**) | `input_file` (base64/URL) | `document.source` (base64/URL) | `inlineData`/`fileData` |
| 평문 | `text/plain`, `text/markdown` | 직렬화 | 직렬화 | `document.source` (`type=text`) | `inlineData` (원문 텍스트로 처리) |
| 마크업 | `text/html`, `text/xml` | 직렬화 | 직렬화 | 직렬화 | `inlineData` (원문 텍스트로 처리) |
| 구조화 데이터 | `application/json`, `text/csv`, `text/tab-separated-values`, `application/yaml` | 직렬화 | 직렬화 | 직렬화 | 직렬화 |
| 소스 코드 | `text/x-python`, `text/javascript` 등 | 직렬화 | 직렬화 | 직렬화 | 직렬화 |
| Office 문서 | `.docx`, `.xlsx`, `.pptx` | 직렬화 | 직렬화 | 직렬화 | 직렬화 |
| 기타 바이너리 | 그 외 | 직렬화 | 직렬화 | 직렬화 | 직렬화 |

**"원문 텍스트로 처리"의 의미.** Gemini는 TXT/Markdown/HTML/XML을 받기는 하지만 렌더링 정보 없이
추출된 원문 텍스트로 다룬다. 차트·도표·서식은 어차피 사라진다. 그래서 이 두 행은 네이티브로
보내는 것과 직접 직렬화하는 것의 결과가 사실상 같고, 태그 구조를 통제할 수 있는 만큼
직렬화 쪽이 나을 수 있다. 아래 플래그로 고를 수 있게 두는 이유다.

**구조화 데이터·Office·소스 코드에 네이티브 칸이 없는 이유.** 네 API 모두 이들을 **inline
content로 받지 않는다.** Gemini의 File Search나 Responses의 file search처럼 벡터 스토어에
올려 쓰는 **서버 도구 경로**는 있지만, 그것은 원격 리소스 ID와 수명주기를 요구하므로 이 패키지의
공통 content 범위 밖이다. 호출자가 텍스트로 추출해 넘기거나, 직렬화에 맡긴다.

**`file_id`와 벤더 URI는 직렬화 대상이 아니라 오류다.** `file_id`, `container_id`, Gemini Files의
`gs://`/opaque URI는 브리지가 내용을 알지 못하므로 태그로 내릴 것 자체가 없다. 요청 생성 시
`MappingError`로 거부한다. 응답 원본에는 진단용으로 남지만, 호출자가 URL이나 inline base64로
물질화하기 전에는 다음 요청에 실리지 않는다. 내용을 **가진** 경우만 직렬화로 degrade하고,
내용을 **모르는** 경우는 실패시킨다.

### 직렬화 선택

네이티브 채널이 있어도 직렬화를 고를 수 있다. 블록 단위 플래그다.

```python
DocumentBlock(
    id="d1",
    title="분기 보고서",
    media_type="application/pdf",
    data=pdf_base64,
    serialize=True,   # 네이티브 file part 대신 텍스트로 내린다
)
```

전처리기는 브리지에 등록한다. 등록된 유형은 추출 텍스트가 태그 안에 들어가고, 등록되지 않은
유형은 전달 불가 표시만 남는다.

```python
bridge = SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen-3.8-27b",
    extractors={"application/pdf": my_pdf_to_text},
)
```

| 상황 | 직렬화 결과 |
|---|---|
| 전처리기 등록됨 | `<document id="d1" media-type="application/pdf"><content>추출 텍스트</content></document>` |
| 전처리기 없음 | `<document id="d1" media-type="application/pdf"><content unavailable="true">이 형식은 이 요청에 전달할 수 없다</content></document>` |
| 평문 문서 | `<document id="d1"><content>본문</content></document>` |

### 응답 멀티모달

대상 API가 이미지나 오디오를 생성하면 해당 블록으로 수집한다. 파일로 꺼낼 때는 블록의 저장
메서드를 쓴다.

```python
import base64

from completion_bridge import AudioBlock

audio = next(block for block in result.content if isinstance(block, AudioBlock))
audio_bytes = base64.b64decode(audio.data) if audio.data else b""
print(audio.transcript)
```

파일 확장자·codec은 요청에서 선택한 출력 audio format을 따른다.

수집한 응답 멀티모달을 다음 요청의 대화 이력에 실을 때도 위 변환표를 그대로 따른다. 대상이
그 유형의 입력 채널을 가지면 네이티브로, 없으면 직렬화된다. 예를 들어 Responses가 만든 출력
오디오를 Chat Completions 이력으로 보내면 `input_audio`로 내려가고, Messages로 보내면
직렬화된다.


## 인용과 annotation

네 API가 근거를 표현하는 방식이 세 갈래로 갈린다. 허브도 같은 세 갈래로 받는다. 하나로
평탄화하지 않는 이유는 표현력이 서로 다르기 때문이다 — 한 답변 구간이 여러 출처에 걸리는
관계는 단일 인용 목록으로 접으면 사라진다.

| 허브 | 의미 | 위치 표현 |
|---|---|---|
| `TextBlock.citations` (`Citation`) | 답변 구간 자체가 인용 단위 | 블록이 곧 구간. `source_start`/`source_end`는 **근거 원문** 좌표 |
| `AnnotationBlock` | 출력 text의 문자 범위를 나중에 가리킴 | `target_index` + `start_index`/`end_index` |
| `GroundingBlock` | 구간과 출처의 다대다 그래프 | `GroundingSupport`가 `source_indices`로 여러 출처를 참조 |

`Citation.cited_text`는 **근거 원문**이지 답변에 인용 표시가 붙은 문구가 아니다. 태그가 감싼
답변 문구는 부모 `TextBlock.text`에 있다. 둘을 섞지 않는다.

### 벤더별 원형

| API | 원형 | 허브 |
|---|---|---|
| Anthropic Messages | document block에 `citations: {enabled: true}`. 응답이 여러 `text` 블록으로 갈리고 인용된 블록에 `citations` 배열 | `TextBlock.citations`, `source="messages"` |
| OpenAI Responses | 출력 text의 `annotations` — `url_citation`, `file_citation`, `container_file_citation` | `AnnotationBlock` |
| vLLM Chat Completions | `annotations` 파서는 유지하지만 vLLM은 이 계열을 생성하지 않는다 | `AnnotationBlock` |
| Gemini | `citationMetadata.citationSources`(구간+URI)와 `groundingMetadata`(chunks·supports·검색 질의) | `AnnotationBlock`, `GroundingBlock` |
| 사용자 정의 XML | `<cite id="...">답변 구간</cite>` | `TextBlock.citations`, `source="cite"` |

Anthropic의 위치 종류는 `char_location`(평문), `page_location`(PDF, 1-indexed),
`content_block_location`(custom content) 셋이다. 공통 `source_start`/`source_end`로 정규화하고
원본은 `native`에 남긴다.

### 교차 변환

같은 벤더로 되보낼 때는 원본을 재전송한다. **다른 벤더로 갈 때는 네이티브 인용 채널이 없으므로
content에 직렬화한다.** 근거가 조용히 사라지는 것이 최악의 결과이기 때문이다.

| 허브 | 직렬화 형태 |
|---|---|
| `TextBlock.citations` | `<cite id="d1">답변 구간</cite>` — 태그가 답변 문구를 감싼다. `CiteVocabulary`의 문법을 그대로 쓴다 |
| `AnnotationBlock` | 구간을 감쌀 수 없으므로 본문 뒤 참고 목록으로 내린다 |
| `GroundingBlock` | 그래프라 평탄화하지 않는다. 출처 목록과 구간-출처 관계를 함께 내린다 |

`AnnotationBlock`을 인라인으로 감싸지 않는 이유는 도착 순서 때문이다. annotation은 대상 text
보다 늦게 올 수 있어 text block을 소급 분할하지 않고 독립 블록으로 둔다. 요청으로 되내릴 때도
같은 이유로 본문을 다시 자르지 않는다.

Gemini의 `groundingMetadata`와 `citationMetadata`는 candidate 전용 응답 metadata다. **같은
벤더로 되보낼 때도 요청 이력에서는 생략**되고, 다른 벤더로는 직렬화된다.

### 제약

- Anthropic에서 **document citations와 구조화 출력(`output_config.format`)은 상호 배타**다.
  함께 보내면 400이다.
- `citations: {enabled: true}`는 요청의 모든 document에 걸거나 하나도 걸지 않는다. 섞을 수 없다.

## 사용자 정의 content type

`CiteVocabulary`는 스트림에 걸쳐 잘린 `<cite id="...">...</cite>`를 파싱하고, 구조화된
`TextBlock.citations`를 다음 요청의 텍스트로 되돌린다. 이 어휘가 아래 첫 번째 방식의 내장
예시다.

```python
from completion_bridge import CiteVocabulary, Bridge
from completion_bridge.vendors import chat_completions

cite = CiteVocabulary()
bridge = Bridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen-3.8-27b",
    vocabularies=[cite],
)

prompt = cite.prompt_hint() + "

문서와 질문..."
result = await bridge.complete([prompt])
```

커스텀 필드는 두 수준으로 확장한다.

1. 모든 API의 text content에 XML-like 토큰으로 실으면 충분한 타입: `ContentBlock`을 상속해
   고정 `type: Literal[...]`을 선언하고, `Vocabulary.blocks`, `lower()`, 필요하면 상태를 갖는
   `lift_mapper()`를 구현한다. `ContentSchema`와 `TagParser`가 SSE chunk 사이에서 잘린 태그를
   처리한다. `Bridge(vocabularies=[...])`가 타입 등록, 요청 lowering, 응답 stream lifting을
   모두 연결한다.
2. 벤더 native 표현과 교차 호환 규칙이 필요한 타입: 공통 의미를 가진 Hub block을 먼저 정의한
   뒤 각 `VendorAdapter.build_body()`와 `to_hub()`에 그 API의 Item/Part/Block 변환을 구현한다.
   동일 벤더에서만 유효한 필드는 `source`와 `native`에 두고, 다른 벤더에는 공통 의미만 내리거나
   명시적으로 생략한다. native 채널이 없는 대상에도 보존해야 한다면 같은 Hub block용
   `Vocabulary`를 text fallback으로 함께 등록한다.

첫 번째 방식의 완전한 비인용 구현은 [custom vocabulary 실행 예제](examples/custom_vocabulary.py),
두 번째 방식의 내장 사례는 `DocumentBlock`, `TextBlock.citations`, `AnnotationBlock`,
`GroundingBlock`과 네 vendor adapter다. 실제 request/response JSON은
[변환 결과 문서](docs/Conversion-Examples.md)에 있다. `prompt_hint()`는 사용 지침을 제공할 뿐
Bridge가 시스템 프롬프트에 자동 삽입하지 않는다. 등록되지 않은 벤더 타입은
`VendorBlock`으로 떨어져 원본을 보존한다.

## 벤더별 특수 규약

- Anthropic은 `anthropic-version`을 자동으로 보낸다. MCP connector처럼 beta가 필요한 기능은 `MessagesAdapter(betas=["mcp-client-2025-11-20"])`로 명시한다.
- Responses의 reasoning은 표시 가능한 summary와 불투명한 `encrypted_content`를 구분한다. 같은 API로 이력을 되보낼 때 reasoning Item을 원형대로 재전송한다.
- 타 벤더 assistant 이력을 Responses로 보낼 때는 `id`/`status`가 필요한 output message를 위조하지 않고 OpenAI SDK의 `EasyInputMessageParam`(`role=assistant`, 문자열 content)을 사용한다. `phase=commentary|final_answer`가 있으면 함께 보존한다.
- Gemini의 `thoughtSignature`는 `functionCall`뿐 아니라 일반 Part에도 붙을 수 있다. 반환된 Part와 서명을 같은 Gemini 요청에서 그대로 재생한다.

## 예시

다양한 content·ReAct 흐름의 실제 4×4 pretty JSON은 [변환 규칙 및 실행 예시](docs/Conversion-Examples.md)에서 확인한다.

## 검증

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
uv build
```

라이브 테스트는 `.env.example`을 `.env`로 복사해 값을 넣은 뒤 명시적으로 실행한다.

```bash
uv run pytest -m "live and not slow" -s
```
