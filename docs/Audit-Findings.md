# 감사 결과와 코드 수정 목록

리팩터링 설계(README 표)를 확정하는 과정에서 **공식 문서와 대조해 드러난 코드 문제**와, 새 설계가
요구하는 **미구현 항목**을 기록한다. 설계 논의 자체는 README에 있고, 이 문서는 "그래서 코드를 뭘
고쳐야 하는가"만 다룬다.

상태: `미해결` / `진행중` / `해결`

## 검증 진행 상황

| 대상 | 상태 | 근거 |
|---|---|---|
| vLLM Chat Completions | 완료 | vLLM `ChatCompletionRequest` 문서 |
| Anthropic Messages | 완료 | `claude-api` 스킬 레퍼런스 |
| Gemini GenerateContent | 완료 | `GenerationConfig` 공식 스키마. `FinishReason` enum 전체 목록은 미확보 |
| OpenAI Responses | 완료 | `openai-python`의 `ResponseCreateParams` 타입 전체 대조 |

---

## A. 공식 문서 대조로 드러난 버그

실제 요청이 실패하거나 잘못된 값을 보내는 건이다. 오프라인 테스트가 respx mock이라 잡히지
않는다.

### A-1. Messages 샘플링 파라미터 — 사용자 책임으로 재분류

`해결(설계 결정)` · 코드 수정 없음

Anthropic 최신 모델(Fable 5/5.1, Opus 5, Opus 4.8/4.7, Sonnet 5)은 `temperature`/`top_p`/`top_k`를
받으면 400이다. 그러나 이것은 버그가 아니라 **투명 전달의 정상 동작**으로 결론냈다.

- `_put()`이 `None`이 아닌 값만 싣는다. `temperature`가 나가는 것은 사용자가 명시적으로 썼기
  때문이지 패키지가 넣는 것이 아니다
- 모델도 사용자가 골랐다. 두 값을 같이 쓴 것은 사용자다
- 400은 **시끄러운 실패**다. `TransportError.detail`에 벤더 오류 본문이 실려 원인이 보인다

전파 없이 API가 지원하는 것을 그대로 전달하는 전략을 택한 이상, 모델 층만 패키지가 떠안을
이유가 없다. 모델 능력 매트릭스는 모델이 나올 때마다 낡고, 코드에 넣으면 조용히 틀린다.
모델별 제약 목록은 코드에도 문서에도 두지 않는다. 문서에 둬도 모델이 나올 때마다 낡고,
낡은 채로 남으면 조용히 틀린 정보가 된다. 각 벤더의 사용 전략은 해당 API 문서를 따르게 한다.

**단, 이 논리는 시끄러운 실패에만 적용된다.** A-5(Gemini `promptFeedback`)처럼 조용히 성공한
척하는 실패는 위임할 수 없다.

### A-2. `mcp_servers`를 단독으로 보낸다 — 검증 오류

`해결` · `messages.py` `_reject_invalid_combinations()`

`mcp_servers=[{type:"url", url, name}]`만 보내면 거부된다. `tools`에
`{"type":"mcp_toolset","mcp_server_name": <같은 이름>}`이 함께 있어야 하고 beta
`mcp-client-2025-11-20`이 필요하다. 지금은 그대로 통과시킨다.

**수정 방향:** 짝이 없으면 요청 생성 시점에 실패시킨다. 조용히 400을 받는 것보다 낫다.

### A-3. citations와 `output_config.format`을 동시에 보낼 수 있다 — 400

`해결` · `messages.py` `_reject_invalid_combinations()`

Anthropic에서 document citations와 구조화 출력은 **상호 배타**다. 함께 보내면 400인데 지금은
막지 않는다. `citations_enabled`가 계산되는 자리에서 검사할 수 있다.

### A-4. `image_url.detail` — 현행 유지

`해결(설계 결정)` · 코드 수정 없음

vLLM 문서: "the `image_url.detail` parameter is not supported". 400인지 조용히 무시인지는
문서에 없다. 문구를 비교하면 `user`는 "This parameter is **ignored** by vLLM"이라고 명시하는데
`detail`은 "not supported"까지만 말한다.

vLLM이 OpenAI 타입을 미러링하는 호환 레이어라는 점과, 그냥 보내는 쪽의 위험이 유계라는 점
(400이면 오류에 바로 보이고, 무시면 무해)을 보아 **현행대로 보낸다.** `ImageBlock.detail`도
그대로 둔다.

미확인으로 남은 것: `low`로 토큰을 아끼려던 사용자가 조용히 무시당하면 알 방법이 없다. 문서에만
명시한다.

부수 발견 — vLLM의 `image_url`은 패키지가 모르는 두 가지를 더 받는다.
- `uuid` (선택) — 멀티모달 캐싱용
- `"image_url": "https://..."` 평문 문자열 형태 (객체가 아닌)

### A-5. 프롬프트가 차단되면 조용히 빈 응답을 만든다

`해결` · `HubResponse.block_reason` 신설. `promptFeedback`을 읽고 `safetyRatings`는 `VendorBlock`으로 보존

```python
candidates = chunk.get("candidates") or []
head = candidates[0] if candidates else {}
```

Gemini는 프롬프트가 안전 필터에 걸리면 `candidates`를 보내지 않고 `promptFeedback.blockReason`에
이유를 담는다. 지금은 `head = {}`로 넘어가 **블록도 `stop_reason`도 없는 빈 `HubResponse`**가
나온다. 호출자는 왜 비었는지 알 수 없다. `promptFeedback`은 코드 어디에서도 읽지 않는다.

400과 달리 **조용히 성공한 것처럼 보이는** 실패라 추적이 어렵다.

`BlockReason`은 `finishReason`과 다른 enum이다(safety, prohibited content, image safety,
blocklist 용어). 허브에 담을 자리를 정해야 한다 — `stop_reason`에 합칠지 별도 필드로 둘지.

### A-6. `logprobs` 이름 충돌을 분리하지 않았다

`해결` · `gemini_logprobs`(int)와 `logprobs`(bool)로 분리

| API | bool 스위치 | 개수 |
|---|---|---|
| vLLM / Responses | `logprobs` (bool) | `top_logprobs` (int) |
| Gemini | `responseLogprobs` (bool) | **`logprobs` (int)** |

`logprobs`가 OpenAI 계열에서는 bool, Gemini에서는 int다. "이름 같고 값 계약이 다르면 벤더
접두어로 분리" 규칙의 사례인데 지금 모델에는 Gemini 쪽이 아예 없다.


### A-7. Responses의 `previous_response_id`와 `conversation`이 상호 배타다

`해결` · `Hyperparameters._reject_exclusive_pairs()` validator

공식 문서: `previous_response_id` — "Cannot be used in conjunction with `conversation`". 지금은
둘 다 `_responses()`의 통과 목록에 있고 함께 보내도 막지 않는다. A-2와 같은 부류다.

### A-8. `prompt_cache_retention`이 deprecated다

`해결` · `prompt_cache_options` 추가, `prompt_cache_retention`에 deprecated 명시

Responses에서 `prompt_cache_retention`은 deprecated이고 `prompt_cache_options.ttl`로 대체됐다.
`prompt_cache_options`는 모델에 없다.

### A-11. Responses에 없는 `logprobs`를 소유 필드로 뒀다

`해결` · `parameters.py`

`ResponseCreateParams`에 top-level `logprobs`가 **없다**. Responses는 `include`에
`message.output_text.logprobs`를 넣고 `top_logprobs`로 개수를 정한다. `top_logprobs`는 있다.

같이 확인한 것: `stop`, `presence_penalty`, `frequency_penalty`, `seed`, `logit_bias`, `top_k`도
Responses에 없다. `truncation`은 `auto`/`disabled`, `service_tier`는
`auto`/`default`/`flex`/`fast`/`priority`/`ultrafast`로 Chat·Anthropic과 값 집합이 다르다
(접두어 분리가 옳았음을 확인).

### A-10. 필드 순서가 실행마다 달라진다 — prompt cache 무효화

`해결` · `parameters.py`

재작성 중 발견. 선택 목록을 `frozenset`으로 두면 반복 순서가 실행마다 달라져 wire body의 키
순서가 흔들린다. 프롬프트 캐싱은 **바이트 prefix 일치**라 키 순서가 바뀌면 캐시가 통째로
빗나간다. `tuple`로 바꿨다. 스냅샷 테스트가 이걸 잡아냈다.

### A-9. Responses `reasoning` 모델 게이트 — B-1으로 소멸

`해결(설계 결정)` · 별도 수정 없음

`reasoning`은 gpt-5·o-series 전용인데 `_responses()`가 `reasoning_effort`를
`reasoning={"effort": ...}`로 **변환**해서 무조건 만든다. B-1(완전 독립)에서 그 변환 자체가
사라지므로 함께 없어진다. Responses 사용자는 `reasoning={"effort":"high"}`를 직접 쓴다.

A-1과 같은 이유로, 남는 모델 게이트는 사용자 책임이다.

---

### A-12. Gemini 도구 스키마를 `parameters`에 실었다 — 400

`해결` · `vendors/generate_content.py`

라이브 호출에서 드러났다. `INVALID_ARGUMENT: Unknown name "additionalProperties" at
'tools[0].function_declarations[1].parameters'`.

`FunctionDeclaration`에는 파라미터 필드가 둘이고 서로 배타적이다. `parameters`는 OpenAPI 3.0
부분집합인 `Schema` 객체만 받아 `additionalProperties`, `$ref`, `oneOf` 같은 일반 JSON Schema
키워드를 거부한다. `parametersJsonSchema`는 일반 JSON Schema를 그대로 받는다.

허브의 `ToolDefinition.input_schema`는 Anthropic·Responses·Chat과 공유하는 값이라 일반 JSON
Schema일 수밖에 없다. 그것을 변형 없이 받는 쪽은 `parametersJsonSchema` 하나다. `parameters`를
쓰려면 키를 걸러내는 변환기가 필요한데, 벤더 방언 지식을 코드에 들이는 일이고 표현력이 조용히
깎인다. 필드를 바꾸는 쪽을 택했다.

`parametersJsonSchema`가 모든 모델과 API 버전에서 GA인지는 공식 문서에서 확인하지 못했다.

### A-13. 유효한 JSON 조각이 `ToolUseBlock.input`을 덮는다 — 400

`해결` · `blocks.py`

라이브 호출에서 드러났다. `messages.1.content.0.tool_use.input: Input should be an object`와
Gemini `contents[1].parts[0].function_call.args ... ":"`.

`parse_complete_input`이 `model_validator(mode="after")`라 델타 블록마다 실행된다. 스트리밍
조각은 임의 지점에서 끊기므로 그 자체로 유효한 JSON인 조각이 나온다. `'":"'`는 JSON 문자열
`:`로 파싱된다. 검증자가 그 결과를 `input`에 대입하면 Pydantic이 그 이름을
`__pydantic_fields_set__`에 추가하고, `StreamMerger`는 그 표시로 "델타가 실은 필드"를 판단한다.
`input`은 마지막 값이 이기는 필드라 조각의 추측이 누적 상태를 덮는다. `build()`의 재파싱은
`if self.input is not None` 조기 반환에 막힌다. 결과적으로 `input_json`은 온전한데 `input`만
어긋난다.

`input`을 읽는 Anthropic(`parts.py:97`)과 Gemini(`parts.py:225`)에서만 터진다. Chat과 Responses는
`input_json`을 읽어 드러나지 않는다. 도구를 쓴 대화를 되보내는 두 번째 호출에서 실패한다.

세 규칙을 함께 넣었다. 파싱되면 기존 `input`을 덮어 원문을 진실로 삼고, 결과가 객체일 때만
싣고(스칼라는 완성된 도구 인수일 수 없다), 대입한 이름을 `model_fields_set`에서 뺀다. 이미
저장된 대화도 다시 읽는 시점에 교정된다.

기존 회귀 테스트가 `input_json`만 단언하고 `input`을 보지 않아 통과했다. 조각 경계를 유효 JSON
지점에서 끊는 테스트를 `test_merge.py`에 넣었다.

### A-14. 함수 도구와 내장 도구를 함께 보내면 Gemini가 거절한다 — 400

`해결` · `vendors/generate_content.py`

`INVALID_ARGUMENT: Please enable tool_config.include_server_side_tool_invocations to use
Built-in tools with Function calling`. 두 종류 중 하나만 있으면 정상이므로, 내장 도구를 카탈로그에
등록해 둔 배포에서는 Gemini 모델의 모든 요청이 실패했다. 특정 프롬프트나 대화 상태와 무관하다.

`_tools()`는 native wire와 `functionDeclarations`를 한 배열로 만들어 `tools`에 실었지만 본문에
`toolConfig`를 만들지 않았다. Gemini는 둘을 섞을 때만 이 플래그를 요구해서, 한 종류만 쓰던
동안에는 드러나지 않았다.

`build_body`가 두 종류가 모두 있을 때 `toolConfig.includeServerSideToolInvocations`의 기본값을
채운다. `_tools()`가 함수 선언을 0번에 두므로 길이 2 이상이면 두 종류가 모두 있다는 뜻이고,
따로 표시를 만들지 않는다. `tool_choice`가 만든 `functionCallingConfig` 등 다른 키는 유지한다.

호출자가 `Hyperparameters.tool_config`나 raw `toolConfig`로 이 키를 명시하면 도구 구성과 무관하게
그 값을 그대로 보낸다. 조합이 거절될 것이 예상되어도 요청 전에 막지 않는다. 한 키의 값을 정하는
주체를 하나로 두는 편이 우선순위 규칙을 두는 것보다 단순하고, 거절 여부의 판단은 서버에 있다.

`Hyperparameters`가 `extra="forbid"`라 이 플래그를 넣을 방법이 없다고 볼 수 있으나, `tool_config`
필드가 이미 있어 수정 전에도 호출자가 직접 넣는 우회는 가능했다. 그 경로가 이제 명시값 경로다.

응답 쪽은 바꾸지 않았다. 이 플래그가 켜져 돌아오는 `toolCall`/`toolResponse` part는 이미
`ServerToolBlock`으로 보존하고 원형으로 재전송한다(`test_current_api_contracts.py`).

wire 수준 재현과 오류 문구는 외부 보고에 근거한다. 라이브 호출로 직접 확인하지 못했다.

### A-15. 인용 조각의 block index가 JavaScript 안전 정수 범위를 넘는다

`해결` · `seq.py`, `blocks.py`, `vocabularies/cite.py`, 어댑터 4곳

`_CiteMapper`가 인용 때문에 나뉜 text 조각에 `-(1 << 60)`부터 1씩 줄인 index를 매겼다. JSON을
JavaScript로 읽으면 2^60 근처의 정수는 256 간격으로만 표현되므로, 화면의 `Number.isSafeInteger`
검사에서 두 번째 조각부터 거절되었다. 검사를 빼도 서로 다른 조각이 같은 숫자가 되어 블록 하나로
병합되었다. Responses 어댑터의 곱셈 번호도 `output_index`가 32 이상이면 `annotation_index`가 2^53을
넘었다.

블록 식별을 delta의 `seq` 정수 튜플과 최종 결과의 `index`로 나눴다. 어댑터는 벤더 좌표로 seq를
만들고, 최종 결과는 seq 순서로 블록을 늘어놓아 `index`를 0부터 매긴다. 규칙과 결정 근거는
[블록 index와 seq 튜플](block_index_and_key_tuple.md)에 있다. delta에 `index`가 없어지고
`AnnotationBlock.annotation_index`, `GroundingBlock.candidate_index`를 삭제했으므로 스트림을 직접
병합하는 소비자와 사용자 정의 어댑터·vocabulary는 seq 계약을 따라야 한다.

화면 쪽 재현은 외부 보고에 근거한다. 라이브 호출로 직접 확인하지 못했다.

### A-16. 저장용 직렬화에 `"seq": null`이 남는다

`해결` · `seq.py` `finalize()`

A-15 수정 직후 외부 보고로 드러났다. 소비 앱은 최종 결과를 `exclude_unset=True`로 저장한다.
병합 결과 블록은 delta에서 온 `seq`가 이미 "값을 준 필드"이고 `model_copy(update=...)`도 갱신한
필드를 그렇게 표시하므로, `finalize()`가 값을 `None`으로 비워도 저장 JSON에 `"seq": null`과
`"target_seq": null`이 남았다. 대상이 없는 annotation에는 `"target_index": null`도 남았다.

`finalize()`가 두 필드를 `model_fields_set`에서 제거하고, `target_index`는 대상을 찾았을 때만
채운다. `exclude_unset=True` 직렬화에 두 키가 없는지 `test_seq.py`가 확인한다.

## B. 새 설계가 요구하는 미구현 항목

README 표가 기술하지만 코드에 아직 없는 것이다.

### B-1. `parameters.py` 전면 재작성

`해결`

- 전파 규칙 전부 삭제. 대상이 소유한 필드만 선택한다
- Chat Completions 필드를 vLLM 기준으로 교체
  - **제거:** `verbosity`, `web_search_options`, `service_tier`, `store`, `metadata`,
    `prediction`, `audio`, `modalities`, `prompt_cache_key`, `prompt_cache_retention`,
    `safety_identifier`
  - **추가:** `top_k`, `min_p`, `repetition_penalty`, `length_penalty`, `use_beam_search`,
    `min_tokens`, `ignore_eos`, `stop_token_ids`, `include_stop_str_in_output`,
    `allowed_token_ids`, `bad_words`, `prompt_logprobs`, `skip_special_tokens`,
    `spaces_between_special_tokens`, `truncate_prompt_tokens`, `truncation_side`,
    `chat_template`, `chat_template_kwargs`, `add_generation_prompt`,
    `continue_final_message`, `add_special_tokens`, `echo`, `media_io_kwargs`,
    `mm_processor_kwargs`, `structured_outputs`, `documents`, `priority`, `request_id`,
    `return_tokens_as_token_ids`
- `extra_body` 필드 삭제. `chat_template_kwargs`는 vLLM 정식 필드이므로 body 최상위로 나간다
- `OutputFormat` → `ResponseFormat` 이름 변경
- `max_output_tokens`/`stop_sequences` 같은 추상 대표 이름을 벤더 wire 이름으로 교체

### B-2. 벤더 내장 도구의 가상 도구 변환

`해결` · `vendors/tool_policy.py` `to_portable()`, `bridge.py`

어댑터 4곳을 고치지 않고 **요청 조립 직전 단일 전처리**로 넣었다. `HubRequest`를 만드는 곳이
한 군데뿐이라 거기서 `to_portable(content, target)`을 한 번 돌린다.

통하는 이유는 변환 결과가 `kind="function"`이기 때문이다. 어댑터의 기존
`can_replay_client_tool` 검사를 그대로 통과하므로 어댑터 코드는 손대지 않았다.
`source == target`이면 전처리가 건드리지 않아 native 재생 경로도 유지된다.

| 입력 | 결과 |
|---|---|
| `ServerToolBlock`(타 벤더) | assistant `ToolUseBlock` + user `ToolResultBlock` 쌍 |
| 비이식 `ToolUseBlock`/`ToolResultBlock`(`kind=anthropic_bash` 등) | `kind="function"` + 브랜드 접두어 이름 |
| `input_json`도 `output`도 없는 `ServerToolBlock` | 쌍을 만들지 않는다. 원격 참조에 갇혀 옮길 내용이 없다 |

`ServerToolBlock`이 호출과 결과를 한 블록에 갖고 있어 쌍으로 펼 수 있다. id는 원본을 쓰거나
없으면 만들므로 **참조 무결성을 브리지가 통제한다**.

브랜드 접두어는 `_BRAND`로 매핑한다(`messages`→`anthropic`, `responses`/`chat_completions`→
`openai`, `generate_content`→`gemini`). `ToolUseBlock.kind`가 이미 `anthropic_bash` 형태를
쓰고 있어 같은 규칙이다.

### B-3. 멀티모달 직렬화

`해결` · `blocks.py`, `vendors/parts.py`, `bridge.py`

가장 중요한 건 **`AudioBlock`이 messages/responses에서 조용히 사라지던 것**이었다. 네이티브
오디오 입력 채널이 없어 `as_*_part`가 `None`을 반환하고, `_lower_documents()`가 `DocumentBlock`만
보므로 어디에도 남지 않았다.

- `ImageBlock`/`AudioBlock`/`DocumentBlock`에 `serialize` 플래그 추가. 참이면 네이티브 채널이
  있어도 텍스트로 내린다. 네 `as_*_part` 맨 앞의 가드가 `None`을 반환해 기존 `plain` 경로를 탄다
- `Bridge(extractors={media_type: callable})` 전처리기 레지스트리 신설. `Vocabulary`는 내림/올림
  대칭이 규약이라 별개로 뒀다
- `UNAVAILABLE` 표시 — 전처리기가 없으면 내용 대신 전달 불가 사실을 남긴다
- image/audio는 `<attachments>`, 문서는 기존 `<documents>`를 유지한다. 라이브 테스트까지 후자를
  계약으로 걸고 있고, 의미도 다르다. 문서는 모델이 읽을 근거이고 전달 못 한 오디오는 누락 통지다

### B-5. Gemini `generationConfig`의 미지원 필드

`해결` · 12개 필드 추가. Gemini 어댑터의 키 이동 코드도 제거

공식 `GenerationConfig` 스키마에 있으나 모델이 모르는 필드다. 다수가 멀티모달 출력 계열이라
B-3의 응답 멀티모달 설계와 맞물린다.

`responseModalities`, `speechConfig`, `imageConfig`, `mediaResolution`,
`audioTranscriptionConfig`, `translationConfig`, `enableAffectiveDialog`,
`enableEnhancedCivicAnswers`, `responseLogprobs`, `logprobs`, `responseSchema`, `responseFormat`

`responseFormat`(ResponseFormatConfig)은 확인이 더 필요하다. README의 `ResponseFormat` 표는
Gemini 칸을 `responseMimeType` + `responseJsonSchema`로 적었는데, `generationConfig.responseFormat`이
별도 구조화 출력 경로로 새로 생긴 것으로 보인다. 대체인지 병존인지 미확인.

### B-6. Responses의 미지원 필드

`해결` · `max_tool_calls`, `moderation`, `prompt_cache_options`, `instructions` 추가

공식 파라미터 목록에 있으나 모델이 모르는 필드다.

`max_tool_calls`(내장 도구 총 호출 상한), `moderation`(입출력 모더레이션 설정),
`prompt_cache_options`(gpt-5.6+)

참고: OpenAI 계열에서 `user`는 deprecated이고 `safety_identifier`와 `prompt_cache_key`가
대체한다. vLLM은 `user`를 받되 무시하므로 세 API의 사정이 모두 다르다.

### B-4. 인용·annotation 교차 변환

`해결` · `bridge.py` `_Lowerer._lower_citations()` / `_lower_references()`

어떤 어휘도 가져가지 않은 블록을 처리하는 허브 수준 기본 동작에 넣었다. `_lower_documents()`와
같은 자리다.

| 허브 | 직렬화 | 배치 |
|---|---|---|
| `TextBlock.citations` | `<cite id="...">답변 구간</cite>` | 제자리 |
| `AnnotationBlock` | `<references><reference kind uri title/></references>` | 본문 뒤 |
| `GroundingBlock` | `<source/>` + `<support>` + `<query>` | 본문 뒤 |

`CiteVocabulary.lower`가 `source in (None, "cite")`인 인용만 처리하고 `citations`를 비우므로,
`_Lowerer`에 남는 것은 정확히 **다른 벤더의 native 인용**이다. 두 층이 겹치지 않는다.

문서는 본문 앞, 참고 목록은 본문 뒤다. 문서는 모델이 근거를 먼저 읽어야 하고, 참고 목록은
본문을 가리키므로 본문이 먼저 있어야 한다.

---

## C. 이름 변경

`해결`

README가 기술하는 공개 이름과 코드가 다르다.

| README | 코드 |
|---|---|
| ~~`StreamBridge`~~ | `Bridge` — **현행 유지로 결정** |
| `ResponseFormat` | ~~`OutputFormat`~~ — **해결** |
| `max_completion_tokens`, `stop`, `response_format` | ~~`max_output_tokens`, `stop_sequences`, `output_format`~~ — **해결** |

`StreamBridge`는 `SyncBridge`와 축이 맞지 않아(하나는 stream, 다른 하나는 sync) 채택하지
않았다. `Bridge`/`SyncBridge`를 유지한다. README를 코드에 맞췄다.

---

## D. 해결됨

| 항목 | 처리 |
|---|---|
| 패키지 리네이밍이 절반만 됨 — 빌드 실패 | `pyproject.toml`의 `name`/`module-name`, docstring 상호참조 갱신 |
| `EnhancedCompletionError` | `CompletionBridgeError`로 변경 |
| `Lowerer.lower_text` 주석 처리로 mypy 6건 | 프로토콜 복원. part 목록을 못 받는 자리의 전용 경계라 유지 |
| `to_hub()`를 iterator로 바꿀지 | 변경 없음. 사용자 표면은 이미 iterator이고 `flush` 순서 제어가 필요 |
| `TransportError.detail`의 500자 절단 유실 | `_detail()`로 복원 |
| `normalize_role`의 죽은 `system`/`tool`/`developer` 항목 | 삭제. 응답 방향 전용으로 축소 |
| `stop_reason_from_gemini`의 `.lower()` | 삭제. 표에 없으면 원문 유지 |
| `developer` role 처리 | API가 대화 목록 안에서 표현 가능하면 인라인, 아니면 hoist |
| Support-Matrix와 README의 내장 도구 정책 모순 | "실행 재개"와 "이력 이동" 2열로 분리 |
| `service_tier` 임시 제외 | 파라미터에 포함 |

---

## E. 미결 결정

| 항목 | 선택지 |
|---|---|
| 대화 중간 지시문의 hoist | 현행(위치 무시, 맨 앞 병합) 확정 여부 — 회귀 테스트는 이미 고정 |
| Gemini `blockReason`을 담을 자리 | `stop_reason`에 합침 / 별도 필드 |

## F. 확인하지 못한 것

`해결` — 둘 다 확인 완료.

| 항목 | 결과 |
|---|---|
| Gemini `FinishReason` enum 전체 목록 | `google-genai` SDK 생성 타입에서 **18개 전값** 확보. `_GEMINI_STOP`을 1:1 대응 셋(`STOP`/`MAX_TOKENS`/`SAFETY`)만 남기고 정리했다. 케이스만 바꾸던 `RECITATION`/`OTHER` 항목은 제거 |
| Gemini `generationConfig.responseFormat` | `ResponseFormatConfig`는 `TextResponseFormat`/`AudioResponseFormat`/`ImageResponseFormat`을 담는 **멀티모달 출력 설정**이다. `responseMimeType`/`responseJsonSchema`의 대체가 아니라 **공존**한다. Chat Completions의 `response_format`과 이름만 같아 `gemini_response_format`으로 분리 |
