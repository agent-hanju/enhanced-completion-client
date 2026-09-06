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
대신 [Support-Matrix](Support-Matrix.md)에 레퍼런스로 둔다 — 문서는 낡아도 조용히 틀리지 않는다.

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
