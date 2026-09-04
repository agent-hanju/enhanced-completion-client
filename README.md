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
대상에서 XML-like 문서 텍스트로 내릴 수 있다.

## 인용과 사용자 정의 content type

`CiteVocabulary`는 스트림에 걸쳐 잘린 `<cite id="...">...</cite>`를 파싱하고, 구조화된
`CitationBlock`을 다음 요청의 텍스트로 되돌린다.

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

새 content type은 `ContentBlock`을 상속하고 `Vocabulary.lower()`와 필요 시
`Vocabulary.lift_mapper()`를 구현한다. `Bridge`에 Vocabulary를 등록하면 블록 타입도 런타임
레지스트리에 등록된다. 등록되지 않은 벤더 타입은 `VendorBlock`으로 떨어져 원본을 보존한다.

## 벤더별 특수 규약

- Anthropic은 `anthropic-version`을 자동으로 보낸다. Citations는 GA라 beta 헤더가 필요 없다.
  MCP connector처럼 beta가 필요한 기능은
  `MessagesAdapter(betas=["mcp-client-2025-11-20"])`로 명시한다.
- Responses의 reasoning은 표시 가능한 summary와 불투명한 `encrypted_content`를 구분한다.
  같은 API로 이력을 되보낼 때 reasoning Item을 원형대로 재전송한다.
- Gemini의 `thoughtSignature`는 `functionCall`뿐 아니라 일반 Part에도 붙을 수 있다. 반환된 Part와
  서명을 같은 Gemini 요청에서 그대로 재생한다.
- DeepSeek의 thinking/tool loop가 필요하면
  `ChatCompletionsAdapter(name="deepseek", reasoning_input_field="reasoning_content")`를 사용한다.

정확한 보존 범위와 레거시 규칙의 수정점은 [현재 API 감사](docs/Current-API-Audit.md), 전체 타입
목록은 [블록 인벤토리](docs/Block-Inventory.md), Java 기준선은
[변환 규칙](docs/Conversion-Rules.md)에 기록되어 있다.

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
