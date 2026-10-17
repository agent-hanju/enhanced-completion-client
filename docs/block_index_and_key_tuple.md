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

### 2.1 발급 정책

현재 발급 규칙의 기준 문서는 [seq 좌표 발급 정책](Sequence-Policy.md)이다.

- 각 변환기가 논리 좌표와 최대 길이를 결정한다.
- 수신 index·분류값·출현 순번은 0부터 사용한다.
- 짧은 좌표는 오른쪽에 `-1`을 채워 최대 길이로 정규화한다.
- Messages는 길이 1, Chat Completions와 GenerateContent는 길이 2, Responses는 길이 4다.
- 커스텀 변환은 정규화된 좌표 뒤에 자신의 좌표를 추가한다. 확장 깊이는 1로 제한하지 않는다.
- 같은 블록은 전체 좌표를 재사용한다. 이벤트 중복 제거는 별도 책임이다.

### 2.2 표시 순서

Messages는 수신 content index, Chat은 채널 출현 그룹과 원본 tool index,
Responses는 정보 그룹·output·item별 하위 위치, Gemini는 정보 그룹과 part 출현 번호를 쓴다.
메타데이터 위치는 최초 수신 시점의 마지막 블록으로 추론하지 않는다.

### 2.3 반복 갱신

Responses 완료 스냅샷은 이미 스트리밍한 본문을 다시 누적하지 않는다. 같은 대상·index의
annotation은 중복 생성하지 않는다. Gemini candidate 인용은 원본 배열 위치로 식별하며,
grounding·URL 조회·안전 평가는 각각 고정 좌표를 갱신한다.

### 2.4 vocabulary 규칙

`_CiteMapper`는 지나가는 모든 블록의 seq 끝에 성분 하나를 추가한다.

- text 조각: 원래 블록마다 0, 1, 2, …
- text가 아닌 블록: 0
- `AnnotationBlock.target_seq`: 0. 원래 text 블록의 첫 조각을 가리킨다.

vocabulary가 같은 마지막 칸을 나눠 쓰지 않는 이유: cite가 `(…,0)`, `(…,1)`, `(…,2)`로 나눈
뒤 다른 vocabulary가 `(…,1)`을 다시 나누면 같은 칸의 번호가 겹친다. 성분을 추가하면
`(…,1,0)`, `(…,1,1)`이 되어 겹치지 않는다.

### 2.5 최종 결과

`finalize_response()`가 병합된 응답에 한 번 적용된다. `SyncStream.result`, `AsyncStream.result`,
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
- 위치 없는 정보의 그룹 순서는 변환기의 명시적 정책이다. 수신 시점으로 위치를 결정하지 않는다.
- 이미 저장된 이전 메시지의 index 값은 이 변경으로 바뀌지 않는다.
