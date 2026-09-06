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
from completion_bridge import StreamBridge, HubMessage
from completion_bridge.vendors import responses

async with StreamBridge(
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

대화 이외의 생성 옵션은 `Hyperparameters` 객체에 둔다. 각 필드는 각 API와 사용 모델에 사용 가능한 필드들만 적용되며, 나머지 필드는 무시된다.(단 service_tier 필드는 임시적으로 제외한다.)

```python
from completion_bridge import (
    Hyperparameters,
    OutputFormat,
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
    verbosity="low",                              # vLLM Chat Completions에서만 사용
    include=["reasoning.encrypted_content"],      # Responses에서만 사용
    inference_geo="us",                          # Messages에서만 사용
    safety_settings=[{"category": "HARM_CATEGORY_HATE_SPEECH"}],  # Gemini만 사용
    extra_body={"chat_template_kwargs":{"enable_thinking":False}}}, # vLLM Chat Completions에서만 확장 필드로 사용
)

### 대응표

// TODO: 지칭하는 이름이 다르더라도 기능과 값이 동일하여 하나의 필드만 적용하면 다른 벤더 API에도 전이되는 hyperpamameter들을 정리하여(확장 대표 parameter를 따로 만들지 말고 각 API에 제공되는 해당하는 필드들 중 하나에 세팅되면 다른 parameter에도 자동 세팅되는 형태로) 표로 작성할 것.(단, 역할이 같더라도 입력 형태가 다르다면 서로 분리하고, 호환되지 않도록 해야 한다. 호환 대상을 좁고 엄격하게 잡을 것)

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

// TODO: 현재 지원하는 벤더 API 내장 도구 목록에 대해, 어떤 tool call과 result 형태로 변환되는지, 벤더 API별로 작성할 것(row: 벤더 API 내장 도구, 지원 여부, column: chat_completions, responses, messages, generateContent 별 변환 결과)([지원표](docs/Support-Matrix.md)의 내용을 여기로 옮겨 비지원 이유까지 지원 여부에 작성해둘 것)

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

대상 API가 네이티브 멀티모달 입/출력을 지원한다면 이미지·음성·파일을 지원하는 형태로 보내고 받을 수 있다.
- 입력: 지원될 경우 해당하는 형식으로 변환되어 전달되고, 지원되지 않거나 content 직렬화 여부를 true로 선택한다면, bridge에 해당 유형에 대한 텍스트 전처리 과정이 등록되어있으면 XML-like 직렬화되어 content의 적절한 위치에 포함, 등록되어있지 않다면 직렬화 태그 내에 구체 내용 대신 전달될 수 없는 내용임을 명시하여 포함된다.
- 출력: 사용자에겐 멀티모달 파일을 다운로드할 수 있도록 메서드를 제공한다. 요청에 대화내역으로 포함될 경우 bridge에 해당 유형에 대한 텍스트 전처리 과정이 등록되어있으면 XML-like 직렬화되어 적절한 위치에 포함, 등록되어있지 않다면 직렬화 태그 내에 구체 내용 대신 전달될 수 없는 내용임을 명시하여 포함된다.


```python
import base64

from completion_bridge import AudioBlock

audio = next(block for block in result.content if isinstance(block, AudioBlock))
audio_bytes = base64.b64decode(audio.data) if audio.data else b""
print(audio.transcript)
```

파일 확장자/codec은 요청에서 선택한 출력 audio format을 따른다.

공통 멀티모달 요청은 URL 또는 inline base64만 지원한다. `file_id`, `container_id`, Gemini Files의 `gs://`/opaque URI처럼 벤더 서버가 발급·관리하는 참조는 요청 생성 시 `MappingError`로 거부한다.

### 변환표

// TODO: 멀티모달입력 유형에 대해(pdf, 구조화된 데이터 document, 각종 document 확장자들은 document로 묶지 말고 별개 취급하여) row는 멀티모달 유형, column은 4개 호환 API로의 변환 결과 및 직렬화 시 처리를 표로 작성한다.

## 인용과 사용자 정의 content type

`CiteVocabulary`는 스트림에 걸쳐 잘린 `<cite id="...">...</cite>`를 파싱하고, 구조화된
`TextBlock.citations`를 다음 요청의 텍스트로 되돌린다. 태그가 감싼 답변 구간은 부모
`TextBlock.text`, 인용 ID는 `Citation(source="cite")`에 저장한다. Anthropic native citation도
같은 필드에 저장하지만 `source="messages"`와 근거 원문 `cited_text`로 원형을 구분한다.

```python
from completion_bridge import Bridge, CiteVocabulary

cite = CiteVocabulary()
bridge = Bridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen3-8b",
    vocabularies=[cite],
)

prompt = cite.prompt_hint() + "\n\n문서와 질문..."
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

// TODO: 인용과 사용자 정의 conent type을 섹션을 구분해 벤더 별 citation, annotation에 대한 설명과 타 벤더에 제공될 때는 직렬화되어 content에 적절히 포함된다는 내용을 적는다. 사용자 정의 content type은 정의 방법을 설명하는 기존의 문단을 그대로 사용한다.

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
