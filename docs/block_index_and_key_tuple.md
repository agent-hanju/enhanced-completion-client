# 블록 index와 seq 튜플

작성일 2026-09-30. 허브 content block의 식별 번호를 스트리밍용 `seq` 튜플과 최종 결과용
`index`로 나누는 변경의 설계 기록이다.

---

## 1. 문제

### 1.1 JavaScript 안전 정수 범위를 넘는 번호

`CiteVocabulary`의 `_CiteMapper`는 인용 때문에 나뉜 text 조각에 `-(1 << 60)`부터 1씩 줄인
번호를 매겼다. JavaScript는 JSON 숫자를 64비트 부동소수점으로 읽으므로 정확히 표현하는 정수가
±(2^53−1)까지다. 2^60 근처에서는 표현 가능한 값의 간격이 256이라 -2^60, -2^60-1, …이 모두 같은
숫자가 된다.

- 화면의 `Number.isSafeInteger` 검사에서 두 번째 조각부터 거절된다.
- 검사를 빼도 서로 다른 조각이 같은 번호가 되어 블록 하나로 합쳐진다.
- 저장된 메시지도 같은 번호를 들고 있어 불러오지 못한다.

Responses 어댑터도 같은 종류의 위험이 있었다. `output_index × 2^32 + content_index`로 블록
번호를, `target_index × 2^16 + n`으로 `annotation_index`를 만들었으므로 `output_index`가 32
이상이면 `annotation_index`가 2^53을 넘는다.

### 1.2 번호 체계가 어댑터마다 달랐다

| 어댑터 | 이전 hub index |
|---|---|
| Messages | 서버 `index` 그대로 |
| Chat Completions | 어댑터 카운터 0, 1, 2, … |
| GenerateContent | 어댑터 카운터 0, 1, 2, … |
| Responses | `output_index × 2^32 + content_index`, reasoning text는 `+ 2^31`, 좌표 없는 audio는 `-1` |
| `_CiteMapper` | 첫 조각은 원래 index, 이후 `-(1 << 60)`부터 감소 |

`AnnotationBlock.annotation_index`와 `GroundingBlock.candidate_index`는 `index`와 별개의 병합
키였고 일부 어댑터는 이 두 블록에 `index`를 주지 않았다.

### 1.3 벤더 index는 도착 순서를 보증하지 않는다

| 벤더 | 문서 | 공식 SDK의 가정 |
|---|---|---|
| Anthropic Messages | index는 최종 content 배열 위치. 순서 보증 문구는 없음 | `content_block_start`를 index 확인 없이 끝에 추가(`# TODO: check index`) |
| OpenAI Responses | 이벤트에 `output_index`, `content_index`, `sequence_number`가 있음. 순서 보증 문구는 없음 | item을 `output_index` 키 dict로 관리. "Stream indexes can have gaps" 주석 |
| Gemini GenerateContent | chunk 사이의 part index가 없음 | 해당 없음 |

스트리밍 중에는 "0부터 연속"과 "벤더 최종 배열 순서"를 함께 보장할 수 없다. 원본 0, 2가
도착한 시점에 2의 delta에 번호를 정해야 하는데, 1을 주면 뒤늦게 원본 1이 왔을 때 순서가 깨지고,
2를 주면 원본 1이 끝내 오지 않았을 때 연속이 깨진다. 이 시점에는 두 경우를 구분할 수 없다.

---

## 2. 결정

블록 식별 번호를 두 필드로 나눈다.

| 필드 | 위치 | 역할 |
|---|---|---|
| `seq: tuple[int, ...]` | delta | 같은 블록의 조각을 짝짓는 병합 키이자 블록 순서 키 |
| `index: int` | 최종 결과 | seq 순서로 늘어놓은 뒤 0부터 1씩 매긴 위치 |

delta에는 `index`가 없고 최종 결과에는 `seq`가 없다. 순서는 스트리밍 중에 정하지 않고 최종
결과를 만들 때 정한다. 이때는 모든 블록이 도착해 있으므로 빈 번호, 역순, 늦게 온 블록을 같은
규칙 하나로 처리한다.

### 2.1 seq의 모양

```
seq = (API 좌표 ..., after) + (vocabulary 성분 ...)
```

- API 좌표 개수는 API마다 고정이다. 한 스트림은 한 API에서만 오므로 한 응답 안의 seq는 길이가
  같다. 다른 API의 seq와 비교하는 일은 없다.
- `after`는 좌표가 있는 블록에서 0이다.
- 블록을 나누는 vocabulary는 끝에 자기 성분을 하나 추가하고 그 성분만 쓴다.
- 모든 성분은 정수다. 순서는 튜플 사전식 비교로 정한다.

### 2.2 API별 좌표

| API | seq (vocabulary 적용 전) | 좌표 |
|---|---|---|
| Messages | `(index, after)` | 서버 content block `index` |
| Responses | `(output_index, part_kind, part_index, after)` | `part_kind`는 한 reasoning item 안의 `summary` 배열(0)과 원문 추론 `content` 배열(1)을 구분하며, 다른 item의 part와 item 수준 블록은 0. `part_index`는 `content_index`, reasoning summary는 `summary_index`, item 수준 블록은 0. reasoning과 message는 서로 다른 item이므로 추론과 본문의 순서는 `output_index`로 정해진다 |
| Chat Completions | `(n, after)` | 어댑터가 블록 최초 등장 순서로 매기는 카운터 |
| GenerateContent | `(n, after)` | 어댑터의 part 카운터. 같은 종류의 연속 text는 같은 n |

### 2.3 좌표 없는 블록: after 규칙

좌표가 없거나 다른 블록에 딸린 블록은 기준 블록 바로 뒤에 둔다.

1. 기준 블록은 대상(`target`)이 있으면 대상 블록이고, 없으면 그 시점까지 발급한 seq 중 가장 큰
   블록이다. 사용자 화면으로는 맨 끝 블록이다.
2. 기준 블록의 seq에서 마지막 성분을 뺀 앞 성분을 그대로 쓰고, 그 앞 성분에서 발급한 after
   최댓값 + 1을 마지막 성분으로 쓴다.
3. 아직 발급한 seq가 하나도 없으면 앞 성분은 `(-1, 0, …)`이다. 모든 좌표 블록보다 앞에 온다.
4. after 값은 앞 성분별 카운터로만 발급한다. 벤더 번호를 after 값으로 직접 쓰지 않는다.
5. 여러 delta로 이어지는 블록은 식별 키를 두고 처음 나올 때 한 번만 발급한다.

규칙 4의 반례: text `(…, 0)` 뒤에 좌표 없는 audio가 와서 `(…, 1)`을 받은 뒤, 벤더
`annotation_index` 0을 after 1로 바꿔 쓰면 두 블록이 같은 seq가 되어 병합된다.

| API | after 블록 | 기준 블록 | 식별 키 |
|---|---|---|---|
| Responses | output text annotation, `file_path` | 대상 part | 없음. 중복 annotation은 기존처럼 제외 |
| Responses | 좌표 없는 audio / transcript | 맨 끝 | `("audio",)` |
| Responses | 응답 수준 `citations` | 맨 끝 | 없음 |
| Chat Completions | annotation | 대상 text, 정할 수 없으면 맨 끝 | 없음 |
| GenerateContent | `citationMetadata` annotation | chunk의 유일한 text, 없으면 맨 끝 | `("citation", 위치)` |
| GenerateContent | `groundingMetadata` | 맨 끝 | `("grounding",)` |
| GenerateContent | `urlContextMetadata`, `safetyRatings`, `promptFeedback` | 맨 끝 | 없음 |

식별 키는 이전 병합 동작을 옮긴 것이다. Gemini annotation은 이전에 `annotation_index`(배열 위치)
로, grounding은 항상 0인 `candidate_index`로 병합되었다.

### 2.4 vocabulary 규칙

`_CiteMapper`는 지나가는 모든 블록의 seq 끝에 성분 하나를 추가한다.

- text 조각: 원래 블록마다 0, 1, 2, …
- text가 아닌 블록: 0
- `AnnotationBlock.target_seq`: 0. 원래 text 블록의 첫 조각을 가리킨다.

vocabulary가 같은 마지막 칸을 나눠 쓰지 않는 이유: cite가 `(…,0)`, `(…,1)`, `(…,2)`로 나눈
뒤 다른 vocabulary가 `(…,1)`을 다시 나누면 같은 칸의 번호가 겹친다. 성분을 추가하면
`(…,1,0)`, `(…,1,1)`이 되어 겹치지 않는다.

### 2.5 최종 결과

`finalize()`가 병합된 응답에 한 번 적용된다. `SyncStream.result`, `AsyncStream.result`,
`partial`, `complete()`가 모두 이 결과를 돌려준다.

1. seq가 없는 블록이 있으면 `MappingError`를 낸다.
2. 블록을 seq 순서로 늘어놓고 `index`를 0부터 매긴다.
3. `AnnotationBlock.target_seq`를 해당 블록의 `index`로 바꿔 `target_index`에 넣는다. 대상 블록이
   없으면 `target_index`를 채우지 않는다(`None`).
4. `seq`와 `target_seq`는 `None`으로 비우고 `model_fields_set`에서도 제거한다. 병합 결과에서는
   delta에서 온 `seq`가 "값을 준 필드"이므로, 제거하지 않으면 `exclude_unset=True` 직렬화에
   `"seq": null`이 남는다.

최종 결과에서는 `content[i].index == i`가 성립한다.

### 2.6 삭제한 것

- `AnnotationBlock.annotation_index`, `GroundingBlock.candidate_index`. 요청 변환을 포함해 이
  값을 읽는 코드가 없고 병합 역할은 seq가 맡는다. 벤더 원문은 `native`에 남는다. 이전 JSON은
  `extra="allow"`로 계속 읽힌다.
- `_CiteMapper`의 `-(1 << 60)` 카운터와 첫 조각의 원래 index 재사용.
- Responses의 `_CONTENT_STRIDE`, `_REASONING_TEXT_OFFSET`, `_GLOBAL_AUDIO_INDEX`,
  `_ANNOTATION_STRIDE`, 응답 수준 citation의 `-(i+1)`.
- Chat Completions의 annotation 전용 카운터.

---

## 3. 소비자 계약

스트림을 직접 병합하는 소비자(백엔드를 거치는 프런트엔드 포함)는 다음을 따른다.

- delta의 블록은 `seq`가 같은 것끼리 병합한다. JSON에서는 배열이므로 원소를 차례로 비교한다.
- 화면 순서는 seq의 사전식 비교로 정한다. 새 seq가 오면 그 순서에 맞는 위치에 삽입한다.
- delta의 `index`는 쓰지 않는다. 값이 없다.
- 스트림이 끝나면 최종 결과로 교체한다. 저장은 최종 결과로 하며 최종 결과에는 `index`만 있다.

```js
function compareSeq(a, b) {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}
const sameSeq = (a, b) => compareSeq(a, b) === 0;
```

직렬화할 때 `exclude_unset=True`나 `exclude_none=True`를 쓰면 delta에는 `index` 키가, 최종
결과에는 `seq`와 `target_seq` 키가 남지 않는다. 대상을 찾지 못한 annotation에는
`target_index` 키도 남지 않는다. 두 옵션을 모두 쓰지 않으면 값이 `null`인 키로 남는다.

사용자 정의 `VendorAdapter`는 응답 content block마다 `seq`를 채워야 한다. 사용자 정의 vocabulary
매퍼가 블록을 나눈다면 seq 끝에 자기 성분을 추가해야 한다.

---

## 4. 남는 한계

- 인용 때문에 text가 여러 조각으로 나뉘어도 annotation의 `start_index`/`end_index`는 나뉘기 전
  블록 전체를 기준으로 한다. `target_index`는 첫 조각을 가리킨다. 이전과 같다.
- Gemini가 `citationMetadata`/`groundingMetadata`를 chunk마다 누적해서 보내는지 새 항목만
  보내는지 확인하지 못했다. 이전 병합 동작을 그대로 유지한다.
- 좌표 없는 블록은 도착 시점의 맨 끝 블록 뒤에 온다. 그 뒤에 좌표가 더 작은 블록이 도착하면
  그 블록이 앞에 온다.
- 이미 저장된 이전 메시지의 index 값은 이 변경으로 바뀌지 않는다.
