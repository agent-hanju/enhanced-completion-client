# 최신 API 감사와 변환 정책

확인일: 2026-09-05

이 문서는 `streambind-base`의 변환을 호환 기준선으로 삼되, 그 규칙을 현재 공식 API의 정답으로
간주하지 않고 다시 검증한 결과다. 레거시 규칙 자체는 [Conversion-Rules.md](Conversion-Rules.md),
타입별 목록은 [Block-Inventory.md](Block-Inventory.md)에 분리했다.

## 결론

가장 일관적인 방식은 모든 벤더를 억지로 하나의 최소 텍스트 모델로 줄이는 것이 아니다.

1. 텍스트·멀티모달 입력·클라이언트 도구 호출/결과는 허브의 공통 의미 모델로 변환한다.
2. 같은 벤더로 되보낼 때 필요한 서명, 암호문, Item/Part 원본은 각 블록의 `source`와
   `native`/`raw`에 함께 보존한다.
3. 다른 벤더로 옮길 수 없는 reasoning과 서버 실행 도구는 임의의 가짜 함수 호출로 바꾸지 않고
   생략한다.
4. 사용자 정의 content type만 명시적인 Vocabulary로 text 채널에 내리고 다시 올린다.

이 방식은 공통 분모와 벤더 고유 정보를 동시에 보존한다. “모든 것을 공통 JSON으로 정규화”하면
Responses reasoning의 암호문이나 Gemini thought signature를 잃고, “원본 JSON만 저장”하면
교차 벤더 변환이 불가능해진다.

## 레거시 규칙에서 수정한 오류

| 기존 규칙 또는 누락 | 문제 | 현재 처리 |
|---|---|---|
| `tool_result`를 일반 content처럼 취급 | 벤더별로 요구하는 role/Item 위치가 다름 | Chat `role:tool`, Anthropic user `tool_result`, Responses `function_call_output`, Gemini user `functionResponse` |
| Responses 요청 이력에서 `tool_use` 누락 | 호출 없이 결과만 전송되어 잘못된 대화가 됨 | `function_call`과 `function_call_output`을 순서대로 재생 |
| Gemini `functionResponse.name = call_id` | `name`은 함수명이고 ID와 의미가 다름 | 이전 `functionCall`의 ID→이름을 추적하고 `id`는 별도 보존 |
| Gemini `response.result`만 문자열화 | 구조화 결과와 멀티미디어 `parts` 손실 | 전체 `response`, 선택적 `parts`, 평문 편의값을 함께 보존 |
| Gemini의 모든 `inlineData`를 이미지로 변환 | PDF·오디오·영상이 이미지로 오분류됨 | MIME에 따라 Image/Audio/Document로 분기 |
| Gemini `fileData`, 일반 Part 서명 누락 | 파일 URI와 Gemini 3 도구 이력 검증 실패 | `fileData`, 모든 Part의 `thoughtSignature`, 원본 Part 재생 |
| Responses `encrypted_content`를 reasoning 텍스트로 표시 | 암호문은 사람이 읽는 추론이 아님 | `thinking=summary`, `encrypted_content`는 불투명한 별도 필드 |
| Responses의 알려지지 않은 Item을 버림 | 서버 도구와 새 Item 전체 손실 | 알려진 서버 도구는 `ServerToolBlock`, 나머지는 `VendorBlock` 원본 보존 |
| Responses 종료 프레임을 먼저 종료 처리 | `response.completed`의 status/usage 손실 | 종료 프레임도 decode한 뒤 종료 |
| Chat 요청 Part가 text/image뿐 | 최신 audio/file 입력 누락 | `input_audio`, `file`, data URL 이미지 지원 |
| Chat URL citation을 평면 필드로 읽음 | 현재 `url_citation` 중첩 구조를 놓침 | 중첩 객체를 정규화하고 원본 annotation 보존 |
| stop reason을 원문 그대로 노출 | 소비자가 벤더를 알아야 함 | Anthropic 어휘(`end_turn`, `max_tokens`, `tool_use`)로 정규화 |
| 허브 `content: list[ContentBlock]` 기본 직렬화 | Pydantic이 하위 블록 필드를 버리고 `source`도 제외 | `SerializeAsAny`와 직렬화되는 provenance로 JSON/DB 왕복 보장 |

## 현재 공식 API와 구현 범위

### OpenAI Responses

Responses는 단순 message 배열이 아니라 Item 목록이며, message Item 안에 다시 ContentPart가 있다.
수동으로 대화 이력을 관리할 때 reasoning Item도 후속 입력에 포함해야 하고, stateless/ZDR 환경에서
불투명 reasoning을 받으려면 `include=["reasoning.encrypted_content"]`가 필요하다. 이 라이브러리는
summary와 encrypted content를 분리해 저장하고 같은 Responses 요청에 Item으로 되보낸다.

현재 입력 Item 유니온과 스트림 이벤트 유니온은 초기 `streambind-base`보다 훨씬 넓다. 함수·custom
도구 외에도 computer/shell/apply-patch 계열 클라이언트 호출, web/file search, code interpreter,
image generation, MCP Item을 원본 보존 대상으로 둔다. 서버가 새 Item을 추가해도
`VendorBlock`으로 떨어진다.

근거: [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses),
[Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)

### OpenAI Chat Completions와 vLLM 호환

Chat 요청 content는 현재 text, `image_url`, `input_audio`, `file`을 지원한다. 응답 message에는
content 외에도 refusal, URL citation annotations, audio, function/custom tool calls가 올 수 있다.
생성 예산은 `max_completion_tokens`가 현재 이름이고 `max_tokens`는 최신 OpenAI 모델에서
deprecated다. 다만 vLLM과 기존 호환 서버 때문에 이 라이브러리는 파라미터를 강제로 개명하지 않고
호출자가 준 이름을 통과시킨다.

근거: [Chat Completions reference](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions)

### Anthropic Messages

현재 입력 `ContentBlockParam`과 출력 ContentBlock은 초기 Java sealed union보다 넓고 방향별 허용
타입도 완전히 같지는 않다. text/image/document, tool use/result, thinking/redacted thinking, 검색·웹
fetch·코드 실행·MCP 블록을 공통 블록 또는 원본 보존 블록으로 처리한다.

extended thinking과 redacted thinking은 반환된 블록과 signature를 수정하거나 순서를 바꾸지 않고
같은 Claude 요청에 되보내야 한다. Citations는 GA이므로 더 이상 citation beta 헤더가 필요 없지만,
한 요청의 문서들은 citation을 모두 켜거나 모두 꺼야 하며 structured output과 함께 쓸 수 없다.
MCP connector는 별도 beta 기능이므로 `anthropic-beta: mcp-client-2025-11-20`이 필요하다.

근거: [Messages API](https://platform.claude.com/docs/en/api/http/messages),
[citations](https://platform.claude.com/docs/en/build-with-claude/citations),
[MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector),
[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)

### Gemini GenerateContent

Gemini `Part`는 text, `inlineData`, `fileData`, `functionCall`, `functionResponse`, executable code,
code execution result뿐 아니라 현재 `toolCall`/`toolResponse`도 포함한다. `FunctionResponse.name`은
필수이고 `id`를 사용했다면 호출과 결과에서 일치해야 한다. 결과는 임의 JSON 객체이며 멀티미디어
`parts`도 포함할 수 있다.

Gemini 3의 thought signature는 function call에만 붙는다고 가정하면 안 된다. 일반 최종 Part에도
붙을 수 있고, function calling 이력에서는 서버가 반환한 Part와 필드를 원래 순서 그대로 재생해야
한다. 이 라이브러리는 Part 전체를 `native`에 보존하고 공통 필드만 갱신해 재전송한다.

`generateContent` 문서는 현재 Legacy 섹션에 있고 더 새로운 Interactions API가 존재한다. 하지만
이 패키지의 계약과 사내 사용처는 GenerateContent이므로 새 API는 별도 어댑터로 추가해야 하며
기존 어댑터의 이름만 바꾸지 않는다.

근거: [GenerateContent reference](https://ai.google.dev/api/generate-content),
[thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures),
[tool combinations](https://ai.google.dev/gemini-api/docs/generate-content/tool-combination)

## 공식 Python SDK와의 비교

| SDK | SDK가 더 잘하는 것 | 이 패키지가 직접 유지할 것 |
|---|---|---|
| `openai` | OpenAPI에서 생성된 최신 request/response/event 타입, Chat·Responses snapshot 누적, structured output parsing | OpenAI 호환 서버의 비표준 필드, 원본 SSE frame, Responses→타 벤더 변환 |
| `anthropic` | Messages typed blocks, sync/async stream helper, beta tool runner | 타 벤더 block 정규화, 사용자 정의 XML-like stream, agent SSE |
| `google-genai` | `Part`/`Content` 타입, automatic function calling, Live API helper | GenerateContent raw Part 보존, 타 벤더 이력 변환, 단일 허브 결과 |

OpenAI SDK 자체 accumulator는 `output_index`/`content_index`로 snapshot을 만들고 Chat helper는
content/refusal/tool arguments를 누적한다. 이 구현의 `StreamMerger`와 같은 문제를 공식 SDK도
해결하지만 결과 타입은 해당 벤더에 닫혀 있다. Anthropic tool runner와 Google automatic function
calling은 도구를 실제 실행하는 애플리케이션 기능이므로 이 패키지에 복제하지 않는다.

공식 SDK를 어댑터 내부에 넣지 않는 이유는 기능 부족이 아니라 경계 때문이다. SDK가 제공하는
typed events를 사용하면 표준 벤더 하나에는 편하지만, vLLM의 선언 밖 필드와 사내 `event:` 이름,
CSRF 헤더, `[DONE]` 변형을 같은 parser에 넣기 어렵다. 따라서 런타임 의존은 httpx/Pydantic으로
유지하고 공식 SDK의 생성 타입 파일을 주기적인 호환성 감사 기준으로 사용한다.

근거: [OpenAI Python streaming helpers](https://github.com/openai/openai-python/blob/main/helpers.md),
[OpenAI Responses event union](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_stream_event.py),
[Anthropic Python tool runner](https://github.com/anthropics/anthropic-sdk-python/blob/main/tools.md),
[Google Gen AI Python SDK](https://github.com/googleapis/python-genai)

## xAI와 DeepSeek 재평가

xAI는 더 이상 단순 Chat Completions 호환만으로 보는 것이 충분하지 않다. Responses API와 서버
도구를 지원하고 `web_search_call`, `x_search_call`, `code_interpreter_call`, `file_search_call`,
`mcp_call`, citations, server-side tool usage를 제공한다. 별도 xAI 전용 허브를 만들기보다
`ResponsesAdapter`의 원본 보존 경로를 재사용하는 것이 맞다.

근거: [xAI web search](https://docs.x.ai/developers/tools/web-search),
[tool usage details](https://docs.x.ai/developers/tools/tool-usage-details),
[citations](https://docs.x.ai/developers/tools/citations)

DeepSeek은 여전히 Chat Completions 계열이지만 thinking/tool loop에서는 이전 assistant 메시지의
`reasoning_content`를 되보내야 한다. 실험적 vision 입력과 strict tool mode도 별도 규약이 있다.
따라서 `ChatCompletionsAdapter(name="deepseek", reasoning_input_field="reasoning_content")`를
사용하고 strict mode의 beta base URL 선택은 호출 애플리케이션이 소유한다.

근거: [chat completion](https://api-docs.deepseek.com/api/create-chat-completion/),
[thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[tool calls](https://api-docs.deepseek.com/guides/tool_calls/)

## 보존 보증

| 변환 | 보증 |
|---|---|
| 응답 SSE → `HubResponse` | 알려진 공통 의미와 알 수 없는 원본을 함께 보존 |
| `HubResponse` → 같은 벤더 요청 | 해당 API가 입력 이력으로 허용하는 Item/Part와 서명은 원형 재생 |
| `HubResponse` → 다른 벤더 요청 | text/image/audio/document/tool call/result 중 대상이 지원하는 의미만 변환 |
| reasoning → 다른 벤더 | 변조하지 않고 생략 |
| 서버 실행 도구 → 다른 벤더 | 클라이언트 함수 호출로 위조하지 않고 생략 |
| custom block | 등록한 Vocabulary의 lowering/lifting 계약에 따름 |

“같은 벤더 무손실”은 서버가 반환한 모든 메타데이터를 다음 요청이 받아준다는 뜻이 아니다.
Gemini candidate safety/grounding metadata처럼 출력 전용인 필드는 허브에서 조회할 수 있도록
보존하지만 요청 Part로 넣지 않는다. 보증 대상은 해당 API가 대화 이력으로 재사용하도록 정한
블록·Item·Part다.

## 테스트 범위

- 모든 어댑터의 request serializer와 SSE mapper
- stream delta 병합과 종료 프레임
- 네 벤더의 tool call/result 쌍과 role/Item 위치
- Chat image/audio/file, Responses input image/audio/file, Anthropic image/document,
  Gemini inline/file data
- Responses encrypted reasoning 재생
- Anthropic/Gemini signature 보존
- Responses/Anthropic/Gemini 서버 도구 원본 보존
- 사용자 정의 citation 어휘의 스트림 경계 파싱과 왕복
- 동기·비동기 전송 API
- 키가 있을 때만 도는 실제 벤더 및 vLLM 라이브 테스트
