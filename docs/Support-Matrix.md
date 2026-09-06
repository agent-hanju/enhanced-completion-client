# 지원 범위와 의도적 비지원

확인일: 2026-09-05

이 문서는 `enhanced-completion-client`가 **wire JSON/SSE 변환기로서 보장하는 범위**와 벤더
리소스·세션 수명주기처럼 의도적으로 소비 애플리케이션에 맡긴 범위를 구분한다. 다양한 content와
ReAct 흐름의 실제 pretty JSON은 [변환 규칙 및 실행 예시](Conversion-Examples.md) 한 문서에서
확인한다.

## 상태 정의

| 상태 | 의미 |
|---|---|
| 지원 | 공통 의미 변환과 회귀 테스트가 있다 |
| 원형 보존 | `source`와 `native`/`raw`로 수신 정보를 보존한다. 자동 기능 활성화를 뜻하지 않는다 |
| 명시적 native | 호출자가 대상 벤더의 wire 정의를 직접 전달한 요청에서만 활성화한다 |
| 부분 지원 | 정보 일부를 text fallback하거나 대상 API가 표현하지 못하는 부분을 생략한다 |
| 외부 책임 | 브리지 밖의 provider/consumer 애플리케이션이 소유한다 |
| 비지원 | 공개 API와 구현이 없다 |

## 한눈에 보는 범위

아래 숫자는 제품 품질 점수가 아니라, 명시적으로 정의한 계약 단위의 구현 현황이다.

| 계약 단위 | 현황 | 비고 |
|---|---:|---|
| 주요 요청 serializer | 4/4 | Chat Completions, Messages, Responses, GenerateContent |
| 주요 스트림 응답 mapper | 4/4 | 모두 `HubResponse`로 수렴 |
| sync/async API | 2/2 | `SyncBridge`, `Bridge` |
| 공통 Hub content | 10 blocks + nested citation + fallback | text, thinking, tool use/result, image, audio, document, annotation, grounding, server tool + vendor fallback |
| 공통 요청 옵션 투영 | 4/4 | `Hyperparameters`를 API별 wire 이름과 중첩으로 변환 |
| API 고유 요청 옵션 | 지원 | 평평한 필드 중 활성 API가 지원하는 것만 선택, 나머지는 생략 |
| 사용자 정의 block/vocabulary 확장 | 지원 | 런타임 등록, stream lifting, request lowering 왕복 테스트 |
| generic function call/result 교차 변환 | 4/4 | 여러 ReAct 턴 포함 |
| stateless web search native 정의 | 3/3 | Messages, Responses, GenerateContent에 명시적으로 전달 |
| 환경 의존 client tool 교차 변환 | 4/4 | 실행 가능한 도구가 아니라 이력 기록으로 옮긴다. [변환표](../README.md#벤더-api-내장-도구-변환표) 참조 |
| image 입력 | 4/4 | inline base64 또는 URL; `file_id`는 거부 |
| document 입력 | 4/4 | native file/document 또는 평문 fallback |
| audio 입력 | 2/4 | Chat Completions/Gemini 지원; Anthropic/Responses 현재 입력 union에는 없음 |
| audio 응답 수집 | 2/4 | Chat assistant audio, Responses 전역 audio stream |
| citation/annotation 응답 수집 | 4/4 | 원형 종류를 분리하며 다음 요청 재생 가능 여부는 API별로 다름 |
| Gemini grounding graph | 지원 | source/support 관계와 검색 질의를 별도 block으로 보존 |
| server tool 응답 수집 | 3/3 | Chat Completions에는 해당 Item 계층이 없음 |
| 업로드·파일 수명주기 | 0/4 | 의도적 외부 책임 |
| 원격 실행 세션 수명주기 | 0/3 | 의도적 외부 책임 |
| 자동 tool 실행 loop | 0/4 | 의도적 비지원 |

## Content block 지원표

`입력`은 HubMessage를 해당 요청으로 내리는 기능, `응답`은 wire 응답을 HubResponse로 올리는
기능이다.

| Hub 의미 | Chat Completions | Anthropic Messages | OpenAI Responses | Gemini GenerateContent |
|---|---|---|---|---|
| `TextBlock` | 입력/응답 지원 | 입력/응답 지원 | `input_text`/`output_text` 지원 | text Part 지원 |
| `ThinkingBlock` | `reasoning(_content)` 확장 수집, 설정 시 동일 벤더 재생 | signature 포함 동일 벤더 재생 | summary와 encrypted content 분리, 동일 벤더 Item 재생 | thought와 `thoughtSignature` 동일 벤더 재생 |
| `ToolUseBlock` | assistant `tool_calls` | `tool_use` | function/custom/computer/shell 계열 Item | `functionCall` |
| `ToolResultBlock` | `role: tool` message | user `tool_result`, 중첩 block 지원 | 각 `_call_output` Item, 중첩 content part 지원 | user `functionResponse`, 구조 JSON/parts 지원 |
| `ImageBlock` | URL/data URL 입력 | base64/URL | `input_image` URL/data | `inlineData`/URL 기반 `fileData` |
| `AudioBlock` | inline `input_audio` | 비지원, 교차 변환 시 생략 | 전역 output audio stream 수집; 입력/이력 재생은 비지원 | audio MIME의 inline/URL data |
| `DocumentBlock` | `file` 입력, 평문은 XML-like text fallback | native `document`, citations 설정 | `input_file`, 평문 fallback | document MIME의 inline/file data, 평문 fallback |
| `TextBlock.citations` | `CiteVocabulary`의 XML-like cite 왕복 | native citation과 XML cite를 `source`로 구분, native는 동일 벤더 재생 | XML-like cite 왕복 | XML-like cite 왕복 |
| `AnnotationBlock` | URL citation annotation 수집 | 해당 없음 | output annotation 수집·동일 벤더 재생 | `citationMetadata` 범위 수집 |
| `GroundingBlock` | 해당 없음 | 해당 없음 | 해당 없음 | source/support graph, 검색 질의와 UI metadata 수집 |
| `ServerToolBlock` | 해당 계층 없음 | server/MCP/web/code block 수집 및 raw 보존 | web/file/code/image/MCP Item 수집 및 raw 보존 | executable code/result/tool Part 수집 및 raw 보존 |
| `VendorBlock` | raw content가 입력 허용 형태일 때 동일 벤더 재생 | 원격 ID 없는 raw block만 동일 벤더 재생 | Item/Part 계층을 구분해 동일 벤더 재생 | 원격 ID 없는 raw Part만 동일 벤더 재생 |

### 사용자 정의 XML-like content type 확장

구조는 완성되어 있으며 `Citation`이나 `CiteVocabulary`에 종속되지 않는다. 개발자는 다음
공개 확장점만 구현하면 된다.

1. `ContentBlock`을 상속하고 고유한 `type: Literal[...]` 판별자를 선언한다.
2. `ContentSchema.bind()`로 의미 경로, canonical tag, alias, 허용 attribute를 정의한다.
3. 상태를 갖는 `StreamMapper.map()/flush()`에서 `TagParser.feed()/flush()` 이벤트를 사용자 block
   delta로 변환한다.
4. `Vocabulary.blocks`, `lower()`, `lift_mapper()`를 구현한다. `lower()`는 사용자 block을 안전하게
   escape한 XML-like text로 되쓰고, `lift_mapper()`는 스트림마다 새 mapper를 반환한다.
5. `Bridge(..., vocabularies=[MyVocabulary()])`에 등록한다. Bridge가 block 레지스트리와
   `vendor → hub → vocabulary` stream pipeline, 요청 방향 lowering을 함께 구성한다.

`prompt_hint()`는 편의 관례일 뿐 Bridge가 시스템 프롬프트에 자동 삽입하지 않는다. 모델에게
태그 출력을 요구할지는 사용하는 애플리케이션이 결정한다. 한 wire text block을
`text → custom → text`처럼 여러 의미 block으로 분할할 때 파생 block에 원래 transport index를
그대로 복사하면 병합기가 같은 슬롯으로 판단한다. 이 경우 `index=None` 또는 충돌하지 않는
synthetic index를 사용해야 한다.

[실행 가능한 비인용 예제](../examples/custom_vocabulary.py)는 `BadgeBlock`을 등록하고,
`<badge>`가 세 SSE delta에 걸쳐 잘린 응답을 JSON block으로 올린 뒤 네 벤더의 text part로 다시
내린다. 결과 JSON은 [변환 규칙 및 실행 예시](Conversion-Examples.md)에 포함되고 snapshot
테스트가 생성기 출력과 완전 일치를 검사한다.

### 요청 Hyperparameters 지원표

`Hyperparameters`는 공통 필드와 API 고유 필드를 한 평평한 모델에 선언한다. 각 어댑터는 지원
목록만 선택하므로 알려진 미지원 필드는 조용히 빠진다. 반면 모델에 정의되지 않은 이름은
`extra="forbid"` 검증 오류가 되어 오타를 숨기지 않는다. 표준보다 먼저 추가된 호환 서버 필드는
명시적 escape hatch인 `extensions`로 현재 대상 요청에만 통과시킬 수 있다.

| 공통 의도 | Chat Completions | Anthropic Messages | OpenAI Responses | Gemini GenerateContent |
|---|---|---|---|---|
| 출력 예산 | `max_completion_tokens` | `max_tokens` | `max_output_tokens` | `generationConfig.maxOutputTokens` |
| temperature/top-p | 최상위 | 최상위, 모델별 제한은 서버가 검증 | 최상위 | `generationConfig.temperature/topP` |
| top-k | 생략 | 최상위, 모델별 제한은 서버가 검증 | 생략 | `generationConfig.topK` |
| seed | 최상위 | 해당 없음 | 공통 자동 투영 안 함 | `generationConfig.seed` |
| stop sequence | `stop` | `stop_sequences` | 해당 없음 | `generationConfig.stopSequences` |
| presence/frequency penalty | 최상위 | 해당 없음 | 해당 없음 | `generationConfig`의 camelCase 필드 |
| reasoning effort | `reasoning_effort` | `output_config.effort` | `reasoning.effort` | 비동등하므로 전용 `thinkingConfig` 사용 |
| named tool choice | function wrapper | `type=tool` | function item | `ANY + allowedFunctionNames` |
| parallel tool 제어 | `parallel_tool_calls` | `disable_parallel_tool_use` 반전 | `parallel_tool_calls` | 공통 자동 투영 안 함 |
| JSON schema 출력 | `response_format.json_schema` | `output_config.format` | `text.format` | `responseJsonSchema` |
| service tier | `service_tier` | `anthropic_service_tier` → `service_tier` | `service_tier` | `gemini_service_tier` → `serviceTier` |

평면 모델은 다음 API 고유 필드군도 선언한다.

- Chat Completions: `audio`, `modalities`, `logprobs`, `prediction`, `response_format`,
  `verbosity`, `web_search_options` 등
- Responses: `background`, `conversation`, `previous_response_id`, `include`, `prompt`,
  `reasoning`, `text`, `truncation` 등
- Messages: `container`, `context_management`, `inference_geo`, `mcp_servers`, `thinking`,
  `output_config` 등
- GenerateContent: `generation_config`, `tool_config`, `safety_settings`, `cached_content` 등
- 둘 이상이 공유: `prompt_cache_key`, `store`, `stream_options`, `top_logprobs`, `user` 등.
  실제 지원 대상만 선택한다.

wire 이름은 같지만 값 계약이 다른 필드는 섹션 대신 평평한 벤더 접두 필드로 구분한다. 예를
들어 OpenAI 계열 `service_tier`와 Anthropic의 `anthropic_service_tier`, Gemini의
`gemini_service_tier`는 서로 다른 필드다. `metadata`도 OpenAI 계열용이고 Anthropic 구조는
`anthropic_metadata`로 분리한다.

생성자 기본값과 호출별 `hyperparameters=`는 deep-merge된다. 레거시 `**params`는 활성 API
request에만 마지막 덮어쓰기로 적용된다. 후보 수(`n`, `candidateCount`)는 현재 Hub가 후보 하나만
표현하므로 선언하지 않는다. `extensions`로 wire에 넣을 수는 있지만 다중 후보 응답 처리는
별도로 구현해야 한다.

### 필드 유효성은 판정하지 않는다

**브리지는 모델을 보지 않는다.** 대상 API가 소유한 필드는 그대로 싣고, 그 값이 그 모델에서
유효한지는 판정하지 않는다. 전파 없이 투명하게 전달하는 전략의 귀결이다.

같은 API라도 모델에 따라 받는 필드와 값 범위가 다르다. 어떤 계열은 샘플링 파라미터를 거부하고,
어떤 값 범위는 특정 모델에서만 열리고, 어떤 필드는 beta 헤더를 함께 요구한다. 그 목록을 코드나
이 문서에 담지 않는다 — 모델이 나올 때마다 낡고, 낡은 채로 남으면 조용히 틀린 정보가 된다.

**각 벤더의 사용 전략은 해당 API 문서를 참고한다.** 어긋나면 벤더가 400으로 알려주고, 그
응답 본문은 `TransportError.detail`에 실린다.

#### Gemini `FinishReason`

허브 어휘에 1:1 대응물이 있는 셋만 옮기고 나머지 15개는 원문을 유지한다.

| Gemini | 허브 | Gemini | 허브 |
|---|---|---|---|
| `STOP` | `end_turn` | `MALFORMED_FUNCTION_CALL` | 원문 |
| `MAX_TOKENS` | `max_tokens` | `UNEXPECTED_TOOL_CALL` | 원문 |
| `SAFETY` | `content_filter` | `TOO_MANY_TOOL_CALLS` | 원문 |
| `RECITATION` | 원문 | `IMAGE_SAFETY` | 원문 |
| `LANGUAGE` | 원문 | `IMAGE_PROHIBITED_CONTENT` | 원문 |
| `OTHER` | 원문 | `IMAGE_RECITATION` | 원문 |
| `BLOCKLIST` | 원문 | `IMAGE_OTHER` | 원문 |
| `PROHIBITED_CONTENT` | 원문 | `NO_IMAGE` | 원문 |
| `SPII` | 원문 | `FINISH_REASON_UNSPECIFIED` | 원문 |

`BLOCKLIST`/`PROHIBITED_CONTENT`/`SPII` 계열을 `content_filter`로 접지 않는다. 모두 콘텐츠
정책 정지이지만 사유가 서로 다르고, 금칙어와 PII 검출은 소비 앱이 다르게 다뤄야 한다.

프롬프트 자체가 차단되면 `candidates`가 오지 않고 `promptFeedback.blockReason`에 사유가 실린다.
이것은 `finishReason`과 다른 축이라 `HubResponse.block_reason`으로 따로 받는다.

#### Gemini `responseFormat`

`ResponseFormat` 타입 객체는 구 필드로 투영한다(`responseMimeType` + `responseJsonSchema`).
문서가 구 필드를 deprecated로 표기하지 않았고 그쪽이 더 오래 지원되기 때문이다.

`generationConfig.responseFormat`은 출력 형식을 modality별로 모은 새 구조이고 구 필드와
공존한다. 그쪽을 쓰려면 `gemini_response_format`에 직접 넣는다. 그 필드를 설정하면 투영이
**생략된다** — 브리지가 만든 `responseMimeType`이 호출자가 쓴 값과 모순될 수 있기 때문이다.
`responseModalities`나 `imageConfig`처럼 호출자가 직접 쓴 필드끼리의 조합은 브리지가 개입하지
않는다.

### 최신 공식 타입과의 차이

2026-09-05에 최신 공식 Python SDK(`openai==3.8.0`, `anthropic==1.3.0`,
`google-genai==2.22.0`)를 프로젝트 의존성에 넣지 않은 임시 환경에서 열어 공개 타입 유니온을
대조했다.

- Responses user 입력 message의 content part는 현재 text/image/file만 허용한다. 독립
  `ResponseInputAudioParam` 타입이 SDK에 남아 있더라도 현재 입력 유니온에는 연결되지 않아
  audio를 요청으로 만들지 않는다.
- Responses assistant output message는 `id`/`status`와 `output_text`/`refusal` content를 요구한다.
  원 Responses 응답은 그 메타데이터를 보존하고, 타 벤더 assistant 이력은 문자열 content의
  Easy Input message로 변환한다. 실제 endpoint가 assistant의 `input_text` part를 거부하는 것도
  라이브 회귀 테스트로 확인했다.
- OpenAI Python SDK 3.8.0의 Easy Input/Output message에는 assistant `phase`가 있다. HubMessage에
  `phase="commentary"` 또는 `"final_answer"`가 있으면 Responses 이력에 보존한다.
- Responses의 built-in tool은 요청의 `tools`에 정의를 넣어서 활성화한다. 브리지는 이를 자동으로
  넣지 않으며, 다른 벤더에 묶인 native 정의를 빈 JSON-schema function으로 바꾸지도 않는다.
- Responses의 `tool_search_call`, `tool_search_output`, `program`, `program_output`,
  `additional_tools`, configuration-update/compaction 계열처럼 아직 공통 의미를 정하지 않은 최신
  Item은 `VendorBlock`으로 원형 보존한다.
- Anthropic의 `code_execution_tool_result`와 `tool_search_tool_result`는
  `ServerToolBlock`으로 정규화한다. `container_upload`의 원본 file ID는 응답 진단용으로
  보존하지만 요청으로 재생하지 않는다.
- Anthropic 입력 union에 추가된 `search_result`는 정확한 native wire가 필요하면
  `VendorBlock(source="messages")`로 보존하고, 교차 벤더의 텍스트 근거로는 `DocumentBlock`을
  사용한다. `container_upload`는 file ID 기반이라 normalized 요청에서 제외한다.
- Responses reasoning summary의 독립 경계는 `summary_index`로 식별하고, audio transcript의 현재
  이벤트명은 `response.audio.transcript.delta`다. 구형 호환 서버 표기도 함께 읽는다.
- Chat Completions 요청은 text/image뿐 아니라 inline `input_audio`와 inline `file`을 지원한다.
  공통 `max_output_tokens`는 최신 생성 예산 필드 `max_completion_tokens`로 투영한다. 특정 호환
  서버가 구형 이름만 받으면 adapter별 `vendor` 옵션 또는 레거시 `**params`로 덮을 수 있다.
- Anthropic citations는 GA라 beta header가 필요 없다. MCP connector처럼 실제 beta 기능만
  `MessagesAdapter(betas=[...])`로 활성화한다.
- Gemini `thoughtSignature`는 function call뿐 아니라 일반 Part에도 올 수 있어 Part 원본과 순서를
  보존한다. 더 새로운 Interactions API는 GenerateContent와 다른 프로토콜이므로 현재 범위 밖이다.
- Gemini의 `audioTranscription` Part는 `AudioBlock.transcript`로 누적하고 원 Part로 되쓴다.
  `mediaProcessing`, `partMetadata`, `mediaResolution`, `videoMetadata`처럼 공통 의미가 없는 Part
  메타데이터는 known block의 `native` 또는 `VendorBlock`에 보존한다. 새 built-in tool 정의는
  `ToolDefinition.native()`로 버전 독립적으로 통과시킨다.
- xAI의 Responses/server-tool 확장은 `ResponsesAdapter` 원본 보존 경로를 재사용할 수 있다.
  DeepSeek thinking은 `ChatCompletionsAdapter`의 `reasoning_content` 설정으로 처리한다.

요청 옵션은 2026-09-05의 공식 create 계약을 다시 대조했다. 공통 이름이 같아 보여도 중첩과
허용 모델이 다른 필드는 평면 모델에 두되 각 어댑터의 지원 목록으로 필터링한다. 세부 중첩
설정은 `reasoning`, `text`, `output_config`, `generation_config`, `tool_config`가 공통 투영 결과를
deep-merge해 덮는다.

- [OpenAI Chat Completions create](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [OpenAI Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Anthropic Messages create](https://platform.claude.com/docs/en/api/http/messages/create)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)

### `input_audio`, 출력 음성, Easy Input Message

`input_audio`는 모델이 **입력으로 듣는 음성**이다. 모델이 음성 파일을 생성한다는 뜻이 아니다.
Chat Completions에서는 user content의 `input_audio`, Gemini에서는 audio MIME의
`inlineData`/`fileData`로 내려간다. 반대로 생성 음성은 응답 채널이다. Chat의 assistant audio와
Responses의 `response.audio.delta`를 `AudioBlock`으로 수집하며, `data`는 base64 음성 바이트이고
`transcript`는 별도 전사 텍스트다. Responses가 현재 이 값을 입력 message content로 받지 않으므로
같은 Responses 이력에도 음성 바이트를 재주입하지 않는다.

Easy Input Message는 이 프로젝트가 만든 명칭이 아니라 OpenAI Python SDK의 실제
`EasyInputMessageParam` 타입명이다. 별도 endpoint나 새로운 role이 아니라 Responses 요청에 넣는
간소화된 message Item이다. SDK 타입은 content에 문자열 또는 input-content 리스트를 허용하지만,
현재 실제 endpoint는 assistant role의 `input_text` part를 거부한다. 따라서 서버가 발급한
`id`/`status`가 없는 타 벤더 assistant 이력은 다음처럼 문자열로 보낸다.

```json
{
  "type": "message",
  "role": "assistant",
  "content": "이전 답변"
}
```

반면 실제 Responses 출력 message는 다음처럼 출력 전용 메타데이터와 `output_text`를 보존해 같은
Responses API에 재생한다.

```json
{
  "type": "message",
  "id": "msg_123",
  "role": "assistant",
  "status": "completed",
  "content": [{"type": "output_text", "text": "이전 답변", "annotations": []}]
}
```

대조 근거:
[OpenAI SDK EasyInputMessageParam](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/easy_input_message_param.py),
[Responses input content union](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_message_content_list_param.py),
[Responses input file fields](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_file_param.py),
[Responses output message](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_output_message_param.py),
[Responses create with explicit tools](https://developers.openai.com/api/reference/resources/responses/methods/create),
[Anthropic input block union](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/content_block_param.py)

### 교차 벤더 손실 규칙

- text와 generic JSON-schema function call/result는 공통 의미로 변환한다.
- computer, shell/Bash, apply-patch, browser처럼 실행 환경이나 세션에 묶인 client tool call/result는
  수신 이력으로 파싱하되 공통 기능으로 활성화하지 않고, 다른 벤더의 평범한 function call로
  바꾸지 않는다.
- image/audio/document는 대상이 지원하는 입력 Part로 변환한다. 평문 문서는 native 문서 채널이
  없으면 `<documents>` 텍스트로 내린다.
- thinking/reasoning은 서명·암호문 계약이 벤더마다 달라 다른 벤더로 옮기지 않는다.
- server tool block을 client function call로 위조하지 않는다. 다른 벤더에서는 생략한다.
- Anthropic citation과 XML cite는 `TextBlock.citations`를 공유하되 `Citation.source`로 구분한다.
  OpenAI/Gemini의 답변 범위 annotation은 `AnnotationBlock`, Gemini의 관계형 grounding은
  `GroundingBlock`이므로 서로 오변환하지 않는다.
- 응답 전용 annotation/grounding/safety metadata는 Hub에서 조회할 수 있지만, 원 API가 이력
  표현을 제공하는 native annotation 외에는 요청 Part에 넣지 않는다.

## 벤더 내장 도구와 서버 도구

모든 Bridge 호출의 기본값은 `tools=()`이고, 네 공식 어댑터는 비어 있으면 wire의 `tools` 필드도
만들지 않는다. 내장 도구는 matching vendor의 `ToolDefinition.native(vendor, wire)`를 명시한
요청에서만 활성화된다. 다른 벤더용 native 정의를 전달하면 그 대상에서는 생략한다.

도구별 call/result content block을 **파싱하는 것**은 도구를 **활성화하거나 실행하는 것**과
별개다. 파서는 실제 응답과 저장 이력을 관찰할 수 있게 남겨둔다.

**활성화와 이력은 별개다.** 아래는 도구를 **켜는** 쪽 규칙이다. 이미 실행된 도구의 기록을 다른
벤더로 **옮기는** 규칙은 [내장 도구 변환표](../README.md#벤더-api-내장-도구-변환표)에 있다.

| 기능 | 활성화 | 이 API에서의 응답 수집 |
|---|---|---|
| Anthropic web search/fetch | 명시적 native | `ServerToolBlock` |
| Responses web search | 명시적 native | `web_search_call`과 인용 |
| Gemini Google Search | 명시적 native | grounding metadata |
| code/computer/shell 계열 | 명시적 native | call/result 파싱. 실행 환경과 세션은 외부 책임 |
| file search/tool search/MCP | 명시적 native | 정의와 응답 wire 보존. ID·인증·연결 수명주기는 외부 책임 |
| Anthropic `container_upload` | 해당 없음 | raw와 `file_id`를 진단용 보존; 요청 재생은 명시적 오류 |
| Responses MCP approval | 명시적 native | approval request는 client `ToolUseBlock`, response는 `ToolResultBlock` |
| Gemini executable code/result, toolCall/toolResponse | 명시적 native | 원래 Part와 순서 보존 |
| Gemini grounding/url context metadata | 명시적 native | candidate 전용이라 같은 벤더 요청 이력에서도 생략 |

**어떤 경우에도 브리지가 도구를 자동 등록하지 않는다.** 다른 벤더에서 옮겨온 가상 도구
(`anthropic_web_search` 등)는 이력에만 존재하고 요청 `tools`에는 들어가지 않는다. 따라서 모델이
그것을 호출할 수 없고, 옮겨진 것은 실행 능력이 아니라 기록이다.

## 파일·세션·외부 리소스 수명주기

정규화된 요청은 실제 내용을 가진 inline base64 또는 URL만 받는다. 벤더 서버가 발급한
`file_id`/`container_id`는 발급 서버, endpoint, purpose, 권한, 처리 상태와 만료에 묶여 있으므로
공통 content로 취급하지 않는다. 응답 객체의 `native`/`raw`에는 진단 목적으로 남을 수 있지만,
그 블록을 다음 요청으로 재생하면 `MappingError`를 발생시킨다.

| 기능 | 상태 | 경계 |
|---|---|---|
| 요청에 base64 파일 직접 첨부 | 지원 | Image/Audio/Document block이 대상 native part로 변환됨 |
| 기존 URL/URI 첨부 | 지원/부분 | 대상 API가 해당 media URL/URI 방식을 받을 때 사용 |
| 기존 `file_id` 첨부 | 비지원 | 모든 Bridge request에서 명시적 오류; 조용히 생략하지 않음 |
| 파일 업로드 API 호출 | 비지원 | Files API별 endpoint, multipart, 목적값이 달라 별도 client 소관 |
| 업로드 완료 polling | 비지원 | 처리 상태와 준비 완료 판단은 소비 앱 소관 |
| file ID 소유권·만료 검증 | 비지원 | ID 기반 요청 자체를 받지 않음 |
| 파일 조회·삭제·갱신 | 비지원 | 리소스 관리 API는 패키지 범위 밖 |
| Responses `container_id` | 응답 보존만 | raw로 조회 가능하지만 요청 재생은 거부 |
| 원격 code container 생성·재접속 | 비지원 | 세션 핸들 생성과 수명주기는 소비 앱/벤더 SDK 소관 |
| Anthropic `container_upload.file_id` | 응답 보존만 | raw로 조회 가능하지만 요청 재생은 거부 |
| Anthropic code execution 결과 포함 정책 | 외부 책임 | native tool 정의와 `response_inclusion` 같은 요청값을 호출자가 구성 |
| Gemini code execution 세션 연속성 | 외부 책임 | executable code/result Part는 보존하지만 원격 상태는 관리하지 않음 |
| `previous_response_id`/conversation ID | 외부 책임 | 평면 `Hyperparameters`로 명시 통과 가능하나 자동 저장·주입하지 않음 |
| MCP 서버 인증 갱신·연결 상태 | 비지원 | connector 설정은 native tool로 전달보낼 수 있으나 인증 수명주기는 외부 책임 |

따라서 “raw block을 보존한다”는 것은 관찰·로그·별도 처리용 정보가 남는다는 뜻이지, 그 ID를
브리지가 다시 요청에 넣는다는 뜻이 아니다. 호출자가 원격 산출물을 직접 다운로드해 URL 또는
inline base64 `ImageBlock`/`DocumentBlock`으로 물질화한 뒤에만 일반 content 변환 경로를 탄다.

대표적인 외부 상태는 다음과 같다.

- Responses의 `conversation`은 입출력 Item을 서버 대화에 자동 추가하며,
  `previous_response_id`도 서버 저장 상태를 가리킨다. 브리지는 해당 요청 파라미터를 통과시키지만
  대화 ID를 선택하거나 저장하지 않는다.
- Anthropic Files API는 업로드 때 만료 시간을 설정할 수 있고, code execution이 만든
  `container_upload.file_id`도 원격 container에 귀속된다. 브리지는 응답 원본에만 ID를 보존한다.
- Gemini File API 파일은 처리 상태가 있고 표준 업로드 파일은 임시 보관된다. 업로드, ACTIVE
  polling, 만료 전 재업로드는 브리지 밖의 작업이다.
- 원격 세션에 붙는 managed agent/code sandbox는 파일 mount, 인증, 실행 환경, 재접속을 함께
  관리해야 하므로 wire content 변환과 합치지 않는다.

근거:
[Responses create와 conversation state](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
[Anthropic Files upload](https://platform.claude.com/docs/en/api/http/beta/files/upload),
[Gemini File API](https://ai.google.dev/api/files)

### 실행 환경·세션 의존 도구 분류

도구를 **다시 호출 가능하게 만드는 것**과 **호출 기록을 옮기는 것**은 다르다. 아래 "실행 재개"
열이 전자, "이력 이동"이 후자다.

| 계열 | 활성화 | 응답 표현 | 실행 재개 | 이력 이동 |
|---|---|---|---|---|
| 일반 function call/result | 명시적 portable tool 정의 | `ToolUseBlock`/`ToolResultBlock` | 지원 | 그대로 교차 변환 |
| web search/fetch | 명시적 native 정의 | `ServerToolBlock`/grounding metadata | 대상 벤더 native 정의 필요 | 가상 도구 |
| Responses computer/shell/apply patch | 명시적 native 정의 | 전용 call/output Item | 제외 | 가상 도구 |
| Anthropic Bash/computer/text editor/browser/memory | 명시적 tool 정의 | `tool_use`/`tool_result` 및 전용 결과 block | 제외 | 가상 도구 |
| Anthropic/Responses server code execution | 명시적 native 정의 | code/container Item·block | 제외 | 가상 도구 |
| Gemini computer use | 명시적 native 정의 | action `functionCall`과 screenshot 이력 | 제외 | 가상 도구 |
| Gemini built-in code execution | 명시적 native 정의 | `executableCode`/`codeExecutionResult` Part | 제외 | 가상 도구 |
| file search/tool search | 명시적 native 정의와 원격 리소스 | 전용 Item·block | 제외 | 가상 도구 |
| MCP | 명시적 native 정의와 인증 | MCP call/result/approval | 연결·인증 필요 | 가상 도구 |
| Gemini grounding/url context | 명시적 native 정의 | candidate metadata | 제외 | 직렬화 (쌍 없음) |

**"실행 재개 제외"의 근거는 이력 이동에 적용되지 않는다.** 좌표계, 현재 화면, working directory,
mount, 설치 패키지, 권한, 이전 명령의 side effect를 옮길 수 없다는 것은 그 도구를 대상 벤더에서
**다시 호출할 수 있게 만들 때**의 제약이다. 가상 도구는 요청 `tools`에 등록되지 않으므로 모델이
호출할 수 없고, 옮겨지는 것은 "이렇게 호출되어 이런 결과가 나왔다"는 사실뿐이다. 어떤 경로로도
요청 `tools`에 자동 추가되는 것은 없다.

## Content block의 반복과 식별

ReAct 여부와 무관하게 `text → image → text → text → tool → text`처럼 어떤 타입도 여러 번 나타날
수 있다는 전제로 Hub와 병합기를 설계했다. 병합 규칙은 타입이 아니라 `index`다. 같은 index의
delta는 한 블록에 누적하고, 다른 index는 타입이 같아도 독립 블록으로 유지한다. index가 없는
완성 블록도 각각 append한다.

다만 수신 wire가 독립 블록의 경계를 제공하지 않으면 브리지가 그 경계를 추측해서 복원할 수는
없다.

| 원 API | 같은 타입 반복의 보존 수준 |
|---|---|
| Anthropic Messages | `content_block.index`가 있어 text/thinking/tool/result를 포함한 독립 블록을 정확히 구분 |
| OpenAI Responses | `output_index` + `content_index`, reasoning의 `summary_index`로 독립 Item/Part를 정확히 구분 |
| Gemini GenerateContent | 한 chunk의 `parts[]` 위치는 정확히 구분; chunk 사이에는 part index가 없어 같은 위치·같은 종류의 연속 text를 하나의 streaming block으로 누적 |
| Chat Completions | content, reasoning, refusal, audio는 각각 단일 delta 채널이라 채널 안의 원래 block 경계를 복원할 수 없음; `tool_calls[].index`만 복수 호출을 구분 |

[변환 규칙 및 실행 예시](Conversion-Examples.md)는 같은 메시지 안의 독립 text block 두 개를 네
request 형식으로 내린 실제 JSON을 포함한다. [ReAct 순서 테스트](../tests/test_react_sequences.py)는
core Hub block 각 타입의 동일 타입 반복, Anthropic/Responses/Gemini의 반복 Part, Chat의 단일
채널 한계를 고정한다.

## ReAct와 반복 tool/thinking 순서

사용자의 기억이 맞다. client tool은 여러 HTTP 턴에 걸쳐 반복되고, 서버 내장 도구는 한 응답
안에서도 여러 호출/결과와 thinking을 교차시킬 수 있다. Anthropic의 extended thinking 계열은
반환된 thinking/redacted-thinking block을 수정하거나 재정렬하지 않고 같은 API에 되보내야 한다.

```text
assistant(thinking, tool_use A)
→ user(tool_result A)
→ assistant(thinking, tool_use B)
→ user(tool_result B)
→ assistant(text)
```

현재 보장 범위는 다음과 같다.

| 경우 | 결과 |
|---|---|
| 여러 HTTP 턴에 걸친 client tool loop | 네 serializer 모두 assistant/result 턴과 call ID 순서 보존 |
| 한 응답의 여러 tool call | Chat의 `tool_calls[].index`, 나머지 block/item/part 위치로 구분 |
| Chat의 반복 reasoning delta + 병렬 tool call | reasoning은 한 채널로 누적하고 각 tool index별 인수와 ID를 독립 병합 |
| Anthropic의 thinking → server tool/result → thinking → client tool | content block index와 도착 순서 보존, signature 포함 동일 벤더 재생 |
| Anthropic server loop 제한 도달 | `pause_turn`을 그대로 노출하므로 소비 앱이 반환 content로 다음 요청을 결정 가능 |
| Responses의 reasoning → server tool → reasoning → client tool | `output_index`와 `content_index`/`summary_index`를 합성한 key로 순서 보존 |
| Gemini의 thought → code/result → thought → functionCall | 여러 Part의 도착 순서와 `thoughtSignature` 보존 |
| Chat의 reasoning/text/tool 간 세밀한 interleave | 프로토콜이 별도 delta 필드로 제공하므로 reasoning 1개, text 1개 채널로 합쳐짐; 여러 tool call 순서는 보존 |
| 환경 의존 server tool block | 한 응답 안의 순서는 보존하지만 실행·후속 세션 연결은 Bridge 지원 범위가 아님 |

회귀 테스트는 [test_react_sequences.py](../tests/test_react_sequences.py)에 있다. 여기서 Chat의
반복 reasoning delta와 병렬 tool index, Anthropic/Responses/Gemini의 두 thinking 구간 및 여러
도구 블록, 네 serializer의 2회 client tool cycle을 고정한다. Anthropic 서버 도구 문서는 한
요청 안의 agentic loop와 제한 도달 시 `pause_turn`을 명시한다. Gemini code execution도 오류 시
코드를 다시 생성할 수 있어 executable-code/result 쌍이 반복될 수 있다.

- [Anthropic server tools and agentic loop](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools)
- [Gemini code execution](https://ai.google.dev/gemini-api/docs/code-execution)

## 의도적으로 패키지에 넣지 않은 기능

- 자동 tool 실행과 agent loop 제어
- 파일 업로드/삭제 및 업로드 처리 polling
- 원격 code interpreter/container 세션 관리
- `previous_response_id`, conversation, cache의 자동 상태 저장
- 스트림 재연결과 요청 재시도 정책
- 벤더 인증 토큰 갱신
- Responses Batch/Realtime, Gemini Live/Interactions 같은 별도 프로토콜
- Augment/RAG 검색 실행

이 기능들은 변환 객체보다 긴 수명과 부작용을 가지므로 provider/consumer 애플리케이션 또는 공식
SDK가 소유한다. 브리지는 요청 body 생성, SSE 해석, Hub block 보존과 변환에만 책임을 둔다.

## 검증 근거

2026-09-05 최종 실행 결과:

| 검증 | 결과 | 비고 |
|---|---:|---|
| `uv run pytest -q` | 352 passed, 23 deselected | 생성 문서 snapshot과 파라미터 투영 포함 |
| `uv run ruff check src tests examples` | 통과 | lint/import 순서 포함 |
| `uv run mypy src` | 통과 | strict 설정 |
| `uv build` | 통과 | sdist와 wheel 생성 |
| `pytest -m "live and not slow"` | 13 passed, 6 skipped | 세 외부 벤더 통과; 로컬 vLLM 설정 공란이라 6건 skip |
| 세 벤더 native web-search live | 2 passed, 1 skipped | Anthropic/Responses 통과, Gemini는 quota 429로 skip |

라이브 과정에서 Responses가 foreign assistant의 `input_text` part를 400으로 거부한 결함을 실제로
찾았고, 문자열 Easy Input content로 수정한 뒤 실패했던 dense content 이력을 다시 보내
`end_turn` 응답을 확인했다. 따라서 아래 pretty JSON과 지원표는 최초 가정이 아니라 수정 후
실행 결과를 기준으로 한다.

- [기본 변환 실행기](../examples/conversion_matrix.py): 일반 tool call/result의 4×4 변환
- [content/ReAct 실행기](../examples/rich_content_matrix.py): 멀티모달, 중첩 tool result, native
  citation/server tool, 반복 block, 명시적 web-search 활성화의 4×4 변환
- [custom vocabulary 실행기](../examples/custom_vocabulary.py): 비인용 block 등록과 분할 stream 왕복
- [문서 생성기](../examples/render_conversion_examples.py): 세 실행기의 현재 출력을 snapshot 문서로 갱신
- [현재 API 계약 테스트](../tests/test_current_api_contracts.py): native opt-in과 최신 필드
- [ReAct 순서 테스트](../tests/test_react_sequences.py): 반복 턴과 indexed block 순서
- [문서 snapshot 테스트](../tests/test_conversion_examples.py): 실행 결과와 pretty JSON 문서의 동기화

견본의 “전체”는 공개 core Hub block 계열과 변환 정책 계열 전체를 뜻한다. 버전이 붙은 모든 벤더
내장 tool 이름과 이벤트 문자열을 중복 나열한다는 뜻은 아니다. 같은 실행 주체·보존 정책별 대표
payload를 견본과 회귀 테스트에서 실행한다.
