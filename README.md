# enhanced-completion-client

여러 LLM API의 요청·SSE 응답을 하나의 Python 모델로 연결하는 라이브러리다. 서버가 아니라
소비 애플리케이션 안에서 `Bridge` 또는 `SyncBridge` 인스턴스를 만들어 사용한다.

| 어댑터 | API |
|---|---|
| `chat_completions` | OpenAI 호환 Chat Completions, vLLM, DeepSeek 계열 |
| `responses` | OpenAI Responses |
| `messages` | Anthropic Messages |
| `generate_content` | Gemini `streamGenerateContent` |
| `single_agent`, `code_agent` | 사내 agent-studio SSE |

모든 응답은 `HubResponse`로 수렴하고 `HubMessage.of_response(response)`로 다음 요청 이력이 된다.
텍스트, 멀티모달 입력, 클라이언트 도구 호출·결과는 대상 벤더의 네이티브 wire 형태로 변환한다.
추론 서명과 서버 도구처럼 벤더에 종속된 항목은 원본 `native`/`raw`를 보존해 같은 벤더로만
재전송한다.

공개 입출력 계약은 다음과 같다.

- `build_request()`, `stream()`, `complete()`의 입력은 `Sequence[HubMessage]`다. 문자열과 mapping은
  각각 user `HubMessage`와 검증된 `HubMessage`로 바꾸는 편의 입력이다.
- `stream()`은 변환이 끝난 `HubResponse` delta를 즉시 내보내며, 스트림 종료 후
  `stream.result`에서 같은 delta들을 병합한 `HubResponse`를 얻는다.
- `complete()`는 스트림을 내부 소비하고 병합된 `HubResponse` 하나를 반환한다.
- 응답 이력은 `HubMessage.of_response(result)`로 만든다. 따라서 소비 애플리케이션은 어느
  벤더를 선택해도 Hub 타입만 다루고, wire JSON은 Bridge가 소유한다.

Augment/RAG 실행 기능은 포함하지 않는다. 문서와 인용은 입력·응답 블록으로만 다룬다.

## 설치

Python 3.12 이상과 `uv`를 사용한다.

```bash
uv add enhanced-completion-client
```

개발 환경은 다음과 같이 준비한다.

```bash
uv sync --all-groups
uv run pytest
```

## 비동기 API

```python
from enhanced_completion import Bridge, HubMessage
from enhanced_completion.vendors import responses

async with Bridge(
    vendor=responses,
    base_url="https://api.openai.com",
    model="gpt-5-mini",
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
`build_request()`는 네트워크 요청 없이 실제 wire body를 확인할 때 사용한다.

## 동기 API

```python
from enhanced_completion import SyncBridge
from enhanced_completion.vendors import chat_completions

with SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen3-8b",
) as bridge:
    result = bridge.complete(
        ["대한민국의 수도는?"],
        max_tokens=32,
        chat_template_kwargs={"enable_thinking": False},
    )
    print(result.text)
```

재시도와 SSE 재연결 정책은 내장하지 않는다. 필요하면 호출자가 주입하는 `httpx.Client` 또는
`httpx.AsyncClient`에 연결·프록시·TLS 정책을 설정한다.

## 요청 옵션과 Hyperparameters

대화 이외의 생성 옵션은 `Hyperparameters`에 둔다. 공통 필드는 의미가 같은 API에만 각 wire
이름과 중첩 구조로 투영하고, 나머지는 API별 섹션에 격리한다.

```python
from enhanced_completion import (
    ChatCompletionsParameters,
    GenerateContentParameters,
    Hyperparameters,
    MessagesParameters,
    OutputFormat,
    ResponsesParameters,
    SyncBridge,
    ToolChoice,
)
from enhanced_completion.vendors import chat_completions

defaults = Hyperparameters(
    max_output_tokens=256,
    temperature=0.2,
    top_p=0.9,
    stop_sequences=["<END>"],
    tool_choice=ToolChoice(mode="auto"),
    output_format=OutputFormat(
        type="json_schema",
        name="answer",
        json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    ),
    chat_completions=ChatCompletionsParameters(verbosity="low"),
    responses=ResponsesParameters(include=["reasoning.encrypted_content"]),
    messages=MessagesParameters(inference_geo="us"),
    generate_content=GenerateContentParameters(service_tier="PRIORITY"),
    vendor={"vllm": {"chat_template_kwargs": {"enable_thinking": False}}},
)

bridge = SyncBridge(
    vendor=chat_completions,
    base_url="http://127.0.0.1:8000",
    model="qwen3-8b",
    hyperparameters=defaults,
)

# 호출별 값은 생성자 기본값에 deep-merge된다.
body = bridge.build_request(
    ["질문"],
    hyperparameters=Hyperparameters(max_output_tokens=64),
)
```

공통 필드는 `max_output_tokens`, sampling, stop, penalty, reasoning effort, tool choice,
parallel-tool 정책, 출력 형식이다. 예를 들어 출력 예산은 Chat의
`max_completion_tokens`, Responses의 `max_output_tokens`, Messages의 `max_tokens`, Gemini의
`generationConfig.maxOutputTokens`가 된다. 지원하지 않는 필드는 그 요청에서 빠진다.

`chat_completions`/`responses`/`messages`/`generate_content` 섹션은 해당 API에서만 전송된다.
이 모델들은 선언된 주요 필드 외에도 `extra="allow"`이므로 새 API 필드를 해당 섹션 안에서 즉시
사용할 수 있다. 사용자 정의 어댑터는 `vendor={adapter.name: {...}}`로 확장한다. 기존
`complete(..., **params)` 호출도 유지하며, 이 값은 선택된 API 요청에만 마지막 덮어쓰기로
적용한다. 여러 후보를 반환하는 `n`/`candidateCount`는 Bridge가 후보 하나만 표현하므로 공통
필드로 만들지 않았다.

## 도구 호출과 결과

허브에서는 `ToolUseBlock`과 `ToolResultBlock` 한 쌍을 사용한다.

```python
from enhanced_completion import HubMessage, ToolResultBlock

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

동일한 허브 블록은 대상에 따라 다음처럼 내려간다.

| API | 호출 | 결과 |
|---|---|---|
| Chat Completions | assistant `tool_calls[]` | 별도 `role: tool` 메시지 |
| Anthropic | assistant `tool_use` | user `tool_result` 블록 |
| Responses | `function_call` Item | `function_call_output` Item |
| Gemini | model `functionCall` Part | user `functionResponse` Part |

Gemini의 `functionResponse.name`은 앞선 호출 ID로 함수명을 찾아 채운다. ID가 지원되는 API에서는
ID도 함께 보존한다. Anthropic과 Responses는 도구 결과 안의 이미지·파일 같은 중첩 블록도
지원되는 네이티브 content part로 변환한다.

벤더 내장 도구는 자동 등록하지 않는다. `tools`를 생략하면 wire에도 `tools` 필드가 없고,
web search 같은 내장 기능은 matching vendor의 `ToolDefinition.native(...)`를 명시한 요청에서만
활성화된다. computer/shell/code 계열 응답 block은 관찰을 위해 파싱하지만 실행 환경이나 세션을
Bridge가 관리하지 않는다.

## 멀티모달 입력

```python
from enhanced_completion import AudioBlock, DocumentBlock, HubMessage, ImageBlock, TextBlock

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

대상 API가 네이티브 입력 타입을 제공하면 이미지·음성·파일 Part로 보낸다. 지원하지 않는
조합은 임의의 잘못된 Part를 만들지 않는다. 평문 `DocumentBlock`은 네이티브 문서 채널이 없는
대상에서 XML-like 문서 텍스트로 내릴 수 있다. 현재 오디오 입력은 Chat Completions와 Gemini에
내리며, Responses의 output audio stream은 수집만 하고 요청에는 재생하지 않는다.

`input_audio`는 모델에 넣는 입력 음성이다. 음성 출력은 별도 응답 채널이며 Responses에서는
`AudioBlock.data`에 base64, `AudioBlock.transcript`에 전사문이 누적된다.

```python
import base64

from enhanced_completion import AudioBlock

audio = next(block for block in result.content if isinstance(block, AudioBlock))
audio_bytes = base64.b64decode(audio.data) if audio.data else b""
print(audio.transcript)
```

파일 확장자/codec은 요청에서 선택한 출력 audio format을 따른다. Responses 입력 content에는 현재
audio가 없으므로 수집한 `AudioBlock`을 다음 Responses 요청에 자동 재생하지 않는다.

공통 멀티모달 요청은 URL 또는 inline base64만 지원한다. `file_id`, `container_id`, Gemini Files의
`gs://`/opaque URI처럼 벤더 서버가 발급·관리하는 참조는 요청 생성 시 `MappingError`로 거부한다.
응답 원본에는 진단용으로 남지만, 호출자가 실제 파일을 URL이나 inline bytes로 물질화하기 전에는
다음 요청으로 재생하지 않는다.

## 인용과 사용자 정의 content type

`CiteVocabulary`는 스트림에 걸쳐 잘린 `<cite id="...">...</cite>`를 파싱하고, 구조화된
`TextBlock.citations`를 다음 요청의 텍스트로 되돌린다. 태그가 감싼 답변 구간은 부모
`TextBlock.text`, 인용 ID는 `Citation(source="cite")`에 저장한다. Anthropic native citation도
같은 필드에 저장하지만 `source="messages"`와 근거 원문 `cited_text`로 원형을 구분한다.

```python
from enhanced_completion import Bridge, CiteVocabulary

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

## 벤더별 특수 규약

- Anthropic은 `anthropic-version`을 자동으로 보낸다. Citations는 GA라 beta 헤더가 필요 없다.
  MCP connector처럼 beta가 필요한 기능은
  `MessagesAdapter(betas=["mcp-client-2025-11-20"])`로 명시한다.
- Responses의 reasoning은 표시 가능한 summary와 불투명한 `encrypted_content`를 구분한다.
  같은 API로 이력을 되보낼 때 reasoning Item을 원형대로 재전송한다.
- 타 벤더 assistant 이력을 Responses로 보낼 때는 `id`/`status`가 필요한 output message를
  위조하지 않고 OpenAI SDK의 `EasyInputMessageParam`(`role=assistant`, 문자열 content)을
  사용한다. `phase=commentary|final_answer`가 있으면 함께 보존한다.
- Gemini의 `thoughtSignature`는 `functionCall`뿐 아니라 일반 Part에도 붙을 수 있다. 반환된 Part와
  서명을 같은 Gemini 요청에서 그대로 재생한다.
- DeepSeek의 thinking/tool loop가 필요하면
  `ChatCompletionsAdapter(name="deepseek", reasoning_input_field="reasoning_content")`를 사용한다.

정확한 보존 범위, 최신 API 차이와 의도적 비지원은 [지원표](docs/Support-Matrix.md), 다양한
content·ReAct 흐름의 실제 4×4 pretty JSON은
[변환 규칙 및 실행 예시](docs/Conversion-Examples.md)에서 확인한다.

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
