# 레거시 변환 규칙 기준선

`streambind-base` `origin/develop`의 세 매퍼를 읽어 정리했다. 이 파일은 **기존 동작을 빠뜨리지
않기 위한 마이그레이션 기준선**이지 현재 벤더 API의 정답 목록이 아니다. 최신 공식 규약과
대조해 수정한 내용은 [Current-API-Audit.md](Current-API-Audit.md), 블록 목록은
[Block-Inventory.md](Block-Inventory.md)에 있다.

- `compatible/ChatCompletionsToMessagesMapper.java`
- `gemini/GeminiToMessagesMapper.java`
- `responses/ResponsesToMessagesMapper.java`

**세 매퍼 모두 Anthropic Messages를 허브 정규형으로 삼는다.** 타입 이름만이 아니라 **값의
어휘까지** 그쪽으로 모은다. 이것이 이 저장소가 놓쳤던 핵심이다. 타입만 맞추고 값을 벤더별로
흘려보내면 소비 앱이 벤더를 알아야 `stop_reason`을 읽는다. 그러면 허브가 아니다.

아래 표의 “버린다”, encrypted content를 thinking 본문으로 쓰는 규칙, 모든 Gemini inline data를
이미지로 보는 규칙 등은 원 구현의 기록으로만 남긴다. Python 구현은 감사 문서에 적은 교정 규칙을
따른다.

---

## 1. role 정규화

| 원본 | 허브 | 근거 |
|---|---|---|
| chat `user` | `user` | |
| chat `assistant` | `assistant` | |
| chat `system` | `user` | Anthropic messages에 system role이 없다 |
| chat `tool` | `user` | Anthropic에서 도구 결과는 user 메시지다 |
| gemini `model` | `assistant` | |
| gemini `user` | `user` | |
| responses (전부) | `assistant` | 응답이므로 고정 |
| 알 수 없음 | `assistant` | |

이 저장소는 요청 방향에서 `system`을 Anthropic 최상위 `system` 필드로 올린다. 매퍼의
`system → user`보다 정확하므로 그 차이는 유지한다. 다만 **도구 결과는 반드시 `user` 턴**이어야
한다.

## 2. stop_reason 정규화

허브 어휘는 Anthropic이다.

| chat completions | Gemini | Responses status | 허브 |
|---|---|---|---|
| `stop` | `STOP` | `completed` | `end_turn` |
| `length` | `MAX_TOKENS` | `incomplete` | `max_tokens` |
| `tool_calls`, `function_call` | | | `tool_use` |
| `content_filter` | `SAFETY` | | `content_filter` |
| | `RECITATION` | | `recitation` |
| | `OTHER` | | `other` |
| | | `failed` | `error` |
| | | `cancelled` | `cancelled` |
| 그 밖 | 그 밖(소문자화) | 그 밖 | 원본 유지 |

## 3. content 변환

### chat completions 응답 메시지

순서가 규칙이다. `reasoning` → `content` → `refusal` → `tool_calls`.

| 필드 | 허브 블록 |
|---|---|
| `reasoning` / `reasoning_content` | `ThinkingBlock` |
| `content` | `TextBlock` |
| `refusal` | `TextBlock`, 본문 앞에 **`[Refused] `** 접두 |
| `tool_calls[]` (`type == "function"`) | `ToolUseBlock` |

`ToolUseBlock`은 **파싱된 객체와 원문 문자열을 모두** 든다. `input`(Map)과 `inputStr`이다.
소비 앱이 다시 파싱하지 않게 한다.

### Gemini Part

판정 순서가 규칙이다. `thought` → `text` → `functionCall` → `functionResponse` → `inlineData`.

| Part 필드 | 허브 블록 |
|---|---|
| `thought == true` | `ThinkingBlock(thinking=text, signature=thoughtSignature)` |
| `text != null` | `TextBlock`. **빈 문자열도 블록을 만든다** |
| `functionCall` | `ToolUseBlock(id, name, input=args)` |
| `functionResponse` | `ToolResultBlock(id, response["result"])` |
| `inlineData` | `ImageBlock.base64(mimeType, data)` |
| 그 밖 | 없음 (null) |

### Responses Item

| Item | 허브 블록 |
|---|---|
| `message` | `content`의 `ContentPart`를 각각 변환 |
| `function_call` | `ToolUseBlock(id=callId, name, input+inputStr)` |
| `function_call_output` | `ToolResultBlock(callId, output)` |
| `reasoning` | `ThinkingBlock`. `summary[].text`를 개행으로 이어붙이고, 없으면 `encryptedContent` |
| 그 밖(`web_search_call` 등) | **버린다** |

| ContentPart | 허브 블록 |
|---|---|
| `output_text` | `TextBlock(text)` |
| `refusal` | `TextBlock("[Refused] " + refusal)` |
| 그 밖 | 없음 |

## 4. index 부여

세 매퍼 모두 **도착 순서로 0부터 채운다**. `block.setIndex(blocks.size())`다.

이 저장소는 다르다. 스트리밍 누적 때문에 고정 인덱스를 쓴다. 본문 `0`, 추론 `-1`, 도구
`1+`이다. 조각이 여러 프레임에 걸쳐 오므로 같은 블록에 모으는 키가 필요하고, 도착 순서로는
프레임마다 같은 인덱스가 다른 블록을 가리킨다.

매퍼는 비스트리밍 변환이라 순서 부여가 맞고, 이쪽은 스트리밍이라 고정 키가 맞다. 의도적
차이이며 유지한다.

## 5. 이 저장소가 규칙을 따르지 않던 지점

| 항목 | 규칙 | 고치기 전 상태 |
|---|---|---|
| `stop_reason` 어휘 | Anthropic으로 정규화 | 벤더 원본을 그대로 흘려보냄 |
| Gemini `SAFETY`/`RECITATION`/`OTHER` | 각각 매핑 | 소문자화만 |
| Gemini `functionResponse` | `ToolResultBlock` | `VendorBlock` |
| Gemini `inlineData` | `ImageBlock` | `VendorBlock` |
| Responses `refusal` | `[Refused] ` 접두 | 접두 없음 |
| chat completions `refusal` | `TextBlock` + 접두 | 아예 안 봄 |
| Responses `reasoning` summary | 개행으로 이어붙임 | 델타만 봄 |
| `ToolUseBlock` 파싱 결과 | `input` + `inputStr` 둘 다 | `input_json`만 |
| Anthropic 도구 결과 role | `user` 고정 | 원 메시지 role 사용 |
