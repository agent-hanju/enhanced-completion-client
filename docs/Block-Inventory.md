# 벤더 content block 목록

`streambind-base`(`~/workspace/streambind-base`, `dev.hanju.streambind.base`)의 타입 정의와
각 벤더 문서를 대조해 만든 목록이다. 허브 블록을 정하는 근거이며, 새 벤더를 붙일 때 먼저 읽는다.

**content block은 응답 전용이 아니다.** 요청에도 넣는 종류가 따로 있고, 두 방향이 겹치는 벤더와
갈리는 벤더가 있다.

| 벤더 | 요청·응답 유니온 |
|---|---|
| Anthropic Messages | **공용.** `Message.content`가 응답과 같은 `ContentBlock` 리스트 |
| OpenAI Responses | **갈림.** `Item`에 입력 항목과 출력 항목이 섞여 있고 `ContentPart`도 `input_*`/`output_*`로 갈린다 |
| Gemini | **공용.** `Part` 하나를 양쪽에 쓴다 |
| OpenAI Chat Completions | **갈림.** 요청은 `RequestContentPart`, 응답은 `Message` |

---

## 1. Anthropic Messages

`ContentBlock`이 `permits`하는 열 종류. 요청과 응답이 같은 유니온을 쓴다.

| 타입 | 방향 | 주요 필드 | 허브 대응 |
|---|---|---|---|
| `text` | 양방향 | `text` | `TextBlock` |
| `image` | 양방향 | `source{type, media_type, data}`, `cache_control` | `ImageBlock` |
| `tool_use` | 양방향 | `id`, `name`, `input` | `ToolUseBlock` |
| `tool_result` | 양방향 | `tool_use_id`, **`content: List<ContentBlock>`**, `is_error` | `ToolResultBlock` |
| `thinking` | 양방향 | `thinking`, `signature` | `ThinkingBlock` |
| `redacted_thinking` | 양방향 | `data` (암호화) | `VendorBlock` |
| `web_search_tool_result` | 응답 | `content: List<WebSearchResult>` | `ServerToolBlock` |
| `web_fetch_tool_result` | 응답 | `tool_use_id`, `content{url, content}` | `ServerToolBlock` |
| `bash_code_execution_tool_result` | 응답 | `tool_use_id`, `content{stdout, stderr, return_code}` | `ServerToolBlock` |
| `text_editor_code_execution_tool_result` | 응답 | `tool_use_id`, `content{file_type, content}` | `ServerToolBlock` |

상속으로 표현된 하위 종류가 셋 더 있다. 별개 개념이 아니라는 뜻이다.

- `ServerToolUseBlock extends ToolUseBlock` — 서버가 실행하는 도구 호출
- `McpToolUseBlock extends ToolUseBlock` + `serverName`
- `McpToolResultBlock extends ToolResultBlock`

**`streambind-base`에 없는 것.** `document` content block. `anthropic/` 아래에서 "document"는
`Citation`의 인용 필드와 `TextEditorTool`에만 나온다. 실제 API에는 있고 `citations: {enabled}`로
네이티브 인용을 켠다. 이 저장소가 새로 넣었다.

**`tool_result.content`가 블록 리스트다.** 이미지를 돌려주는 도구가 이 경로를 쓴다. 문자열
하나로는 표현할 수 없다.

---

## 2. OpenAI Responses

### Item (열한 종류)

| 타입 | 방향 | 주요 필드 | 허브 대응 |
|---|---|---|---|
| `message` | 양방향 | `role`, `content: List<ContentPart>` | 메시지 자체 |
| `function_call` | 응답 | `call_id`, `name`, `arguments` | `ToolUseBlock` |
| `function_call_output` | **요청** | `call_id`, `output` | `ToolResultBlock` |
| `reasoning` | 응답 | `encrypted_content`, `summary` | `ThinkingBlock` |
| `web_search_call` | 응답 | `status`, `action`, `results` | `ServerToolBlock` |
| `code_interpreter_call` | 응답 | `container_id`, `status`, `code` | `ServerToolBlock` |
| `image_generation_call` | 응답 | `status`, `prompt`, `size` | `ServerToolBlock` |
| `mcp_call` | 응답 | `server_label`, `name`, `arguments` | `ServerToolBlock` |
| `mcp_list_tools` | 응답 | `server_label`, `status`, `tools` | `ServerToolBlock` |
| `mcp_approval_request` | 응답 | `server_label`, `name`, `arguments` | `ServerToolBlock` |
| `mcp_approval_response` | **요청** | `approval_request_id`, `approve` | `ServerToolBlock` |

`function_call_output`과 `mcp_approval_response`가 **클라이언트가 되보내는 항목**이다.
후자는 HITL 승인 그 자체다. `common-hitl-chat`이 애플리케이션 계층에서 하는 일이 이 API에는
필드로 있다.

### ContentPart (여섯 종류)

| 타입 | 방향 | 주요 필드 | 허브 대응 |
|---|---|---|---|
| `input_text` | 요청 | `text` | `TextBlock` |
| `input_image` | 요청 | `image_url` | `ImageBlock` |
| `input_file` | 요청 | `file_id`, `filename`, `file_data` | `DocumentBlock` |
| `input_audio` | 요청 | `data`, `format` | `AudioBlock` |
| `output_text` | 응답 | `text`, `annotations` | `TextBlock` + `CitationBlock` |
| `refusal` | 응답 | `refusal` | `TextBlock` |

`output_text.annotations`가 인용이다. `url_citation`, `file_citation`, `file_path` 셋.

---

## 3. Gemini GenerateContent

`Part` 하나를 양쪽에 쓴다. 판별자가 없고 채워진 필드로 종류를 안다.

| 필드 | 방향 | 주요 내용 | 허브 대응 |
|---|---|---|---|
| `text` | 양방향 | 본문. `thought: true`면 추론 | `TextBlock` / `ThinkingBlock` |
| `inlineData` | 양방향 | `mimeType`, `data` (base64) | `ImageBlock` / `AudioBlock` / `DocumentBlock` |
| `fileData` | 양방향 | `mimeType`, `fileUri` | `DocumentBlock` |
| `functionCall` | 응답 | `name`, `args` | `ToolUseBlock` |
| `functionResponse` | **요청** | `name`, `response` | `ToolResultBlock` |
| `executableCode` | 응답 | `language`, `code` | `ServerToolBlock` |
| `codeExecutionResult` | 응답 | `outcome`, `output` | `ServerToolBlock` |
| `videoMetadata` | 요청 | 구간 지정 | `VendorBlock` |
| `thoughtSignature` | 양방향 | 추론 서명 | `ThinkingBlock.signature` |

candidate 수준 메타데이터도 블록으로 올린다.

| 필드 | 내용 | 허브 대응 |
|---|---|---|
| `citationMetadata.citationSources` | `startIndex`/`endIndex`가 **답변 좌표**, `uri`, `license` | `CitationBlock` |
| `groundingMetadata` | `groundingChunks`, `groundingSupports`, `searchEntryPoint` | `ServerToolBlock` |
| `urlContextMetadata` | `urlMetadata[]{retrievedUrl, urlRetrievalStatus}` | `ServerToolBlock` |
| `safetyRatings` | 등급 | `VendorBlock` |

---

## 4. OpenAI Chat Completions

`streambind-base`의 `RequestContentPart`는 `text`와 `image_url` 둘만 permit한다. 실제 API는
그보다 넓다.

| 타입 | 방향 | 주요 필드 | 허브 대응 | streambind-base |
|---|---|---|---|---|
| `text` | 요청 | `text` | `TextBlock` | 있음 |
| `image_url` | 요청 | `image_url{url, detail}` | `ImageBlock` | 있음 |
| `input_audio` | 요청 | `input_audio{data, format}` | `AudioBlock` | 없음 |
| `file` | 요청 | `file{file_id, filename, file_data}` | `DocumentBlock` | 없음 |

응답 쪽은 `content`, `reasoning`/`reasoning_content`, `tool_calls`뿐이다. 서버 도구 개념이 없다.

DeepSeek이 이 규약을 쓰고 `reasoning_content`로 추론을 준다. xAI Grok은 이 규약과 Responses
규약을 모두 제공하며 응답에 `citations`와 `server_side_tool_usage`를 싣는다.

---

## 5. 허브 블록 결론

| 허브 블록 | 근거 |
|---|---|
| `TextBlock` | 다섯 벤더 공통 |
| `ThinkingBlock` | 넷. 이름은 `thinking`/`reasoning`/`reasoning_content`/`thought` |
| `ToolUseBlock` | 다섯 |
| `ToolResultBlock` | 다섯. `content`가 블록 리스트여야 한다 |
| `ImageBlock` | 넷. base64 / url / file_id 세 출처 |
| `AudioBlock` | 둘 (Responses, Chat Completions). Gemini는 `inlineData` |
| `DocumentBlock` | 셋. Chat Completions는 `file` part, 그 밖엔 본문 태그 |
| `CitationBlock` | 셋. 답변 좌표와 원문 좌표가 다른 축 |
| `ServerToolBlock` | 넷. Chat Completions만 없다 |
| `VendorBlock` | 나머지 전부. 등록되지 않은 타입의 폴백 |
