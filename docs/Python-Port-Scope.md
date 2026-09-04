# Python 라이브러리 전환 범위 조사

작성일 2026-09-04. 대상 커밋 `92d5a2d`, 브랜치 `agent-hanju/scorpionfish`.

목표 세 가지를 전제로 현행 Java 구현을 해부하고, Python 라이브러리로 옮길 때 새로 써야 하는
범위를 정리한다.

1. 설정을 등록해 인스턴스를 만들어 쓰는 Python 라이브러리 형태로 전환
2. augment(RAG) 기능 제거
3. 사내 특이 SSE 동작 대응

---

## 1. 현행 구현의 핵심 요소

### 1.1 의존 구조

현행 라이브러리는 자기 코드보다 자작 의존 세 개가 더 크다. 실제 코드 줄 수(주석·공백 제외)는
다음과 같다.

| 구성 | 줄 수 | 역할 |
|---|---|---|
| `enhanced-completion-client` (src/main) | 1,034 | HTTP 호출, DTO, citation 파서 |
| `streambind` 0.1.0 | 1,362 | 리플렉션 기반 델타 병합 |
| `content-stream-adapter` 0.1.6 | 1,130 | 스트리밍 XML-like 태그 파서 |
| `fluxhandle` 0.4.2 | 393 | 스트림 핸들, 리스너, 블로킹 결과 |

추가로 Spring WebFlux(HTTP·SSE), Jackson(직렬화·다형성), Lombok(빌더)에 의존한다. Spring과
Jackson은 `compileOnly`/`implementation`이지만 실제로는 없으면 동작하지 않는다.

### 1.2 실행 경로

`EnhancedCompletionClient.stream()` 한 번의 호출은 다음 순서로 흐른다.

```text
WebClient POST {baseUrl}/v1/chat/completions  (Accept: text/event-stream)
  -> bodyToFlux(String)          SSE data 필드만 문자열로
  -> takeUntil("[DONE]") + filter
  -> ObjectMapper.readValue -> ChatCompletionResponse
  -> StreamHandle.subscribe(flux, EnhancedCompletionDeltaMapper)
       -> mapper.map(chunk)                1:N 델타 변환 (cite 파싱)
       -> StreamMerger.applyDelta(delta)   누적
       -> FluxListener.onNext(delta)       사용자 콜백
  -> onComplete: mapper.flush() -> 잔여 델타 -> merger.build()
handle.get()  최종 병합 결과 (블로킹)
```

핵심은 **하나의 스트림이 두 갈래로 소비된다**는 점이다. 사용자는 콜백으로 토큰 단위 델타를
받고, 동시에 `StreamMerger`가 같은 델타를 접어서 최종 객체를 만든다. `complete()`는 별도
비스트리밍 경로가 아니라 `stream()` 후 `get()`을 호출하는 래퍼일 뿐이다. 요청 본문의
`stream` 필드는 `toRequest(request, true)`에서 항상 참으로 고정된다.

### 1.3 데이터 모델

요청 계층은 제네릭 메시지 타입으로 갈라진다.

- `BaseCompletionRequest<T extends IMessage>` — OpenAI 표준 파라미터 전부
- `ChatCompletionRequest extends BaseCompletionRequest<Message>` — 실제 전송 페이로드
- `EnhancedCompletionRequest extends BaseCompletionRequest<IMessageable>` — 사용자 대면 타입

`IMessageable.toMessage()`가 두 계층을 잇는 seam이다. 사용자 정의 메시지 타입이 자기만의
필드를 갖되 전송 직전에 평범한 role·content 쌍으로 접히도록 한다. 기본 구현체는 다섯 개다.

| 타입 | 특징 |
|---|---|
| `BaseMessage` | role + content |
| `ToolMessage` | role을 tool로 고정, `tool_call_id` 필수 |
| `AttachedMessage` | documents를 문서 태그로 직렬화해 content 뒤에 붙임 |
| `ResponseMessage` | reasoning, tool_calls 포함 |
| `CitedMessage` | citations 포함, `toMessage()`에서 cite 태그를 복원 |

응답 계층은 `BaseCompletionResponse<T extends ResponseMessage>`와 중첩 `Choice<T>`,
`Usage`다. `Choice`가 message와 delta를 둘 다 갖고, 델타 매퍼는 delta를 먼저 보고 없으면
message를 읽어 스트리밍과 비스트리밍 응답을 한 코드로 처리한다.

### 1.4 델타 병합 규칙 (streambind)

`StreamMerger`는 대상 타입을 리플렉션으로 훑어 필드별 병합 전략을 정한다. 기본값이 이어붙이기라는
점이 중요하다.

| 필드 종류 | 기본 동작 |
|---|---|
| String | 이어붙이기 |
| Number | 더하기 |
| 객체 | 재귀 병합 |
| primitive List | 뒤에 추가 |
| 객체 List | 인덱스 필드로 짝지어 병합 |
| Map | 키별 병합 |

덮어쓰기가 필요한 필드에만 `@StreamOverwrite`를 붙인다. 현행 DTO에서 이 표시가 붙은 곳은
id, object, created, model, finish_reason, role, ToolCall.id, ToolCall.type, ToolFunction.name
아홉 군데다. 나머지 content, reasoning, ToolFunction.arguments는 표시가 없어 자동으로 이어붙는다.

객체 List의 짝짓기는 `@StreamIndex`가 붙은 필드, 또는 이름이 그냥 index인 정수 필드로 한다.
`ToolCall.index`와 `Choice.index`가 어노테이션 없이 이 규칙에 걸린다. 누적은 실제 객체가 아니라
문자열 키 맵에 하고, 인터페이스나 추상 타입 필드는 예약 키에 런타임 타입을 저장해뒀다가
`ObjectBuilder`가 복원한다.

### 1.5 citation 파이프라인

이 프로젝트에서 가장 이식 가치가 높은 부분이다. `EnhancedCompletionDeltaMapper`가
`ContentStreamAdapter` 위에 얹은 상태 기계다.

스키마는 하나다. cite 태그 안에 id 태그가 중첩되고, rag가 cite의 별칭이다. 어댑터는 청크 경계를
넘는 태그를 버퍼링해 경로가 붙은 토큰을 돌려준다.

매퍼는 네 개의 상태를 든다.

- `currentIndex` — 지금까지 방출한 본문 content의 문자 수
- `citeStartIndex` — cite 진입 시점의 `currentIndex`
- `citeIdBuilder` — id 경로의 텍스트 누적. 본문 인덱스에는 반영하지 않는다
- `citationIndex` — citation 일련번호

경로별 처리는 이렇다. 루트와 cite 경로의 텍스트는 content 델타로 방출하며 `currentIndex`를 늘린다.
id 경로의 텍스트는 방출하지 않고 식별자로만 모은다. cite 이탈 시점에 index, id, startIndex,
endIndex를 담은 `Citation`을 만들어 별도 델타로 방출한다. 스트림이 끝났는데 태그가 안 닫혔으면
`flush()`가 마지막 citation을 만들어낸다.

결과적으로 인용 구간은 content 문자열과 위치 정보로 분리된다.
`CitedMessage.getContentWithCitations()`가 이 과정을 역으로 돌려 원본 태그를 복원한다.

한 입력 청크가 여러 출력 델타가 될 수 있으므로 매퍼는 리스트를 반환한다. role, reasoning,
toolCalls는 그중 **첫 번째** 델타에만 합쳐 붙인다.

`src/test/java/me/hanju/enhancedcompletion/assembler/EnhancedCompletionDeltaMapperTest.java`가
1,027줄로 이 계약을 고정한다. 한 글자씩 입력, 태그가 청크 경계에서 쪼개지는 경우, 닫는 태그 끝과
여는 태그 시작이 한 청크에 들어오는 경우, 빈 인용, 미닫힘 태그까지 검증한다.
**이 테스트 목록이 곧 Python 이식의 인수 조건이다.**

### 1.5.1 사라진 확장점 (중요)

최초 커밋 `ed497ee`에는 상속 기반 확장점이 있었다. `EnhancedCompletionResponseAssembler`가
`processTaggedToken(TaggedToken)`을 protected로 열고, 파싱 상태 접근자 여덟 개
(`getContent`, `getCitations`, `getCurrentIndex`/`setCurrentIndex`, `getCiteIdBuilder`,
`getCiteStartIndex`/`setCiteStartIndex`, `getAndIncrementCitationIndex`)를 함께 노출했다.
`StreamingEnhancedAssembler`가 그것을 상속해 오버라이드하는 것이 원래 사용법이었다.

**다만 그때도 태그 집합 자체는 바꿀 수 없었다.** `CITE_SCHEMA`가 private static final이고
`adapter`도 private final이라, 하위 클래스가 바꿀 수 있는 것은 이미 인식된 세 경로
(`/`, `/cite`, `/cite/id`)를 **어떻게 처리할지**뿐이었다. `<thinking>` 같은 새 태그를 등록하려면
스키마를 갈아야 하는데 그 문이 없었다. 스키마에 없는 태그는 파서가 일반 텍스트로 흘려보내므로
오버라이드한 메서드가 호출조차 되지 않는다.

커밋 `bdfeee1`의 StreamMapper 아키텍처 마이그레이션에서 이 protected seam이 사라졌다. 현재
`EnhancedCompletionDeltaMapper`는 필드와 메서드가 전부 private이다.

**하지만 이것은 확장점의 후퇴가 아니라 확장 단위의 교체다.** 새 아키텍처의 확장점은 메서드
오버라이드가 아니라 타입 삼중쌍이다. 다음 절에서 다룬다.

### 1.5.1.1 진짜 확장 단위 — 타입 삼중쌍

응답 계층이 제네릭인 이유가 여기 있다.

```java
BaseCompletionResponse<T extends ResponseMessage>       // 골격
  ChatCompletionResponse   extends BaseCompletionResponse<ResponseMessage>  // wire
  EnhancedCompletionResponse extends BaseCompletionResponse<CitedMessage>   // cite 어휘용
```

`T`가 확장점이다. 새 어휘를 쓰려면 세 개를 함께 정의한다.

| 자리 | 기본 구현 | 사용자 정의 |
|---|---|---|
| 메시지 타입 | `CitedMessage extends ResponseMessage` | `MyMessage extends ResponseMessage` |
| 응답 타입 | `EnhancedCompletionResponse` | `MyResponse extends BaseCompletionResponse<MyMessage>` |
| 매퍼 | `EnhancedCompletionDeltaMapper` | `StreamMapper<ChatCompletionResponse, MyResponse>` |

**즉 `CitedMessage`와 `EnhancedCompletionResponse`는 `<cite>` 어휘를 위한 기본 구현 하나일 뿐이고,
확장 모델의 특권적 위치가 아니다.** `StreamHandle<R>`은 `Class<R>`을 받고 `StreamMerger<R>`도
제네릭이므로 하부 배관은 이미 전부 열려 있다.

`GenericTypeTest`가 이 설계의 흔적이다. `BaseCompletionResponse<T>`의 `choices` 필드에서 `T`가
TypeVariable로 남는 것을 조사하는데, streambind의 `TypeVariableResolver`가 그 문제를 풀려고
존재한다. 임의의 사용자 응답 타입을 병합하려면 반드시 필요한 부분이다.

**막혀 있는 것은 아키텍처가 아니라 클라이언트 API 하나다.** `EnhancedCompletionClient.stream()`이
결과 타입과 매퍼를 하드코딩한다.

```java
// 현재 — 고정
new StreamHandle<>(EnhancedCompletionResponse.class, listener);   // :63
new EnhancedCompletionDeltaMapper();                              // :71

// 필요한 것 — 오버로드 하나
<R> StreamHandle<R> stream(request, StreamMapper<ChatCompletionResponse, R> mapper,
                           Class<R> resultType, FluxListener<R> listener);
```

Python 포트는 이 오버로드를 처음부터 공개 API에 넣는다.

**Python 포트가 풀어야 할 진짜 문제가 여기다.** 원래 의도는 "인용 파싱은 기본 제공, 필요하면
content에 실리는 XML-like 구조를 바꾼다"인데, 구현이 그 절반만 열어뒀고 지금은 그마저 닫혀 있다.
포트는 이 요구를 처음부터 설계에 넣어야 한다. 열어야 할 것은 두 층이다.

| 층 | 무엇 | 구 구현 |
|---|---|---|
| 스키마 | 어떤 태그를 인식할지 | 닫혀 있음 (private static final) |
| 매핑 | 인식된 경로를 어떤 델타로 바꿀지 | `ed497ee`에서 열림, 현재 닫힘 |

`content-stream-adapter` 0.3.0의 경로·태그 분리가 정확히 첫 번째 층을 위한 구조다. 상속이 아니라
스키마 주입으로 푸는 편이 Python에 더 맞는다(2.2절).

### 1.5.2 요청 쪽은 이미 열려 있다 — 비대칭

응답 쪽과 달리 **요청 쪽 직렬화는 세 층 전부 열려 있다.**

| 층 | seam | 형태 |
|---|---|---|
| 메시지 전체 | `IMessageable.toMessage()` | 인터페이스 메서드. 구현이 곧 직렬화 |
| 문서 첨부 묶음 | `AttachedMessage.serializeDocuments()` | public, `@SuperBuilder`라 상속 가능 |
| 문서 한 건 | `IDocument.toSerializedPrompt()` | `default` 메서드. 타입별 오버라이드 |

`EnhancedCompletionRequest.toChatCompletionRequest()`가 전송 직전에 각 메시지의 `toMessage()`를
부른다(`EnhancedCompletionRequest.java:39`). 무엇을 어떤 문자열로 만들지는 전적으로 구현자
몫이다. README에도 커스텀 메시지 타입 정의와 `mapper.registerSubtypes()` 등록이 문서화돼 있다.

**두 쪽은 같은 어휘를 쓰는 한 쌍이고, 왕복이 실제로 일어난다.** `CitedMessage.toMessage()`가
`getContentWithCitations()`로 cite 태그를 복원해 다시 내보낸다
(`CitedMessage.java:70`). 대화를 이어가면 파싱해서 구조화한 것을 다음 요청에 태그로 되쓴다.

그래서 한쪽만 열면 반쪽이다. 인용 어휘를 `<doc ref="X">본문</doc>`으로 바꾸기로 하면 요청 쪽은
`toMessage()` 오버라이드로 즉시 되지만, 응답 쪽 파서는 그 태그를 모른다. 스키마에 없으니 일반
텍스트로 흘려보내고 인용이 추출되지 않는다. 왕복이 깨진다.

**Python 포트의 설계 원칙은 여기서 나온다. 요청 직렬화와 응답 파싱을 한 쌍으로 묶어 함께
교체하게 한다.** 어휘를 한 곳에 선언하고 양쪽이 그것을 참조하는 구조가 맞다.

```python
class CiteVocabulary:
    """요청 직렬화와 응답 파싱이 공유하는 어휘."""
    def schema(self) -> ContentSchema: ...      # 응답: 인식할 태그
    def render(self, citation) -> str: ...      # 요청: 태그로 되쓰기
    def on_enter/on_text/on_exit(...): ...      # 응답: 경로별 델타 매핑
```

Java는 이 둘이 서로 다른 패키지의 서로 다른 클래스에 흩어져 있어 짝이 어긋날 수 있었다.
`CitedMessage.getContentWithCitations()`가 쓰는 태그 문자열과 `EnhancedCompletionDeltaMapper`의
`CITE_SCHEMA`가 하드코딩으로만 일치한다. 포트에서는 이것을 한 객체로 묶는다.

### 1.6 제거 대상: augment

제거 범위는 다음과 같다.

| 대상 | 처리 |
|---|---|
| `spi/augment/Augmenter.java` | 삭제 |
| `spi/augment/AugmentResult.java` | 삭제 |
| `spi/augment/SimpleAugmentResult.java` | 삭제 |
| `assembler/AugmentResultDeltaMapper.java` | 삭제 |
| `EnhancedCompletionRequest.augmenter` | 필드 삭제 |
| `EnhancedCompletionResponse.augmentResult` | 필드 삭제 |
| 클라이언트 `stream()`의 RAG 분기 | 삭제 |
| 클라이언트 `applyAugmentResult()` | 삭제 |
| `src/test/.../augmenter/` 5개 파일 | 삭제 |
| README의 Augmenter 절 | 삭제 |

`stream()`에서 augmenter 분기가 사라지면 메서드가 여덟 줄로 줄고, RAG 완료를 기다리려고 별도
스케줄러에서 블로킹하던 구조도 함께 없어진다. 현행 구현의 가장 까다로운 부분이 여기라 제거 효과가
크다.

**남길 것**은 분명히 해둔다. `AttachedMessage`, `IDocument`, `SimpleDocument`는 augment SPI와
독립이다. 문서를 직접 들고 있는 호출자가 프롬프트에 붙이는 경로이므로 유지한다.

---

## 2. Python 이식 시 새로 써야 하는 것

Java 자산 중 Python에 대응물이 없는 것이 이식 비용의 대부분이다. 항목별로 정리한다.

### 2.1 스트림 병합 — streambind 대체 (가장 큼)

**필요한 이유.** OpenAI 스트리밍은 조각을 흘려보내고 최종 객체를 클라이언트가 접어야 한다.
공식 파이썬 SDK에도 누적기가 있지만 OpenAI 스키마 전용이라 `CitedMessage.citations`처럼 이쪽에서
추가한 필드를 모른다.

**범용 엔진은 필수다. 빼면 확장 모델이 무너진다.** 1.5.2절의 확장 단위가 (메시지 타입, 응답 타입,
매퍼) 삼중쌍이므로, `StreamMerger`가 병합해야 할 타입은 사용자가 정의한 임의의
`BaseCompletionResponse<T>` 하위 타입이다. 구체 타입 하나를 위한 누적기로는 이 모델을 지탱할 수
없다.

`GenericTypeTest`가 그 증거다. `EnhancedCompletionResponse.class.getGenericSuperclass()`가
`BaseCompletionResponse<CitedMessage>`로 나오는지, `choices` 필드의 `List<Choice<T>>`에서 `T`가
TypeVariable로 남는지를 조사한다. streambind의 `TypeVariableResolver`가 정확히 이 문제를 풀려고
존재한다. 즉 범용 리플렉션 엔진은 과설계가 아니라 확장 모델의 하중을 받는 부분이다.

**권장 방향.** 리플렉션을 흉내내지 말고 Pydantic v2 모델 위에 필드 메타데이터로 규칙을 선언한다.
Python은 제네릭 소거가 없고 `Model.model_fields`로 필드 타입을 그대로 읽을 수 있으므로
`TypeVariableResolver`에 해당하는 부분이 통째로 필요 없다. Java 1,362줄 중 상당량이 여기서 빠진다.

```python
class ToolCall(BaseModel):
    index: int
    id: str | None = Field(default=None, json_schema_extra={"stream": "overwrite"})
    type: str | None = Field(default=None, json_schema_extra={"stream": "overwrite"})
    function: ToolFunction | None = None
```

Java의 어노테이션 세 개가 그대로 대응된다. `@StreamOverwrite`는 overwrite 표시로,
`@StreamIndex`는 index 표시로, `@StreamList(index=...)`는 필드 레벨 설정으로 옮긴다. 누적은
딕셔너리에 하고 마지막에 `model_validate`로 세운다. Java가 예약 키에 런타임 타입을 심어두던 트릭은
Pydantic의 discriminated union으로 대체되므로 오히려 단순해진다.

**규모.** Java 1,362줄에서 Map, 배열, record, TypeVariable 처리를 덜어내면 Python 300~400줄
수준으로 본다. 이 항목은 착수 범위에서 뺄 수 없다.

### 2.2 태그 파서 — content-stream-adapter 대체

**필요한 이유.** 여는 태그가 청크 경계에서 쪼개져 도착한다. 정규식으로는 풀 수 없고, 부분 일치를
버퍼에 잡아두는 스트리밍 매처가 필요하다.

**요구 기능.**

- 부분 일치 버퍼링 — 태그일 수도 있는 접미사를 붙들고 다음 청크를 기다린다
- 별칭 — rag를 cite와 같은 경로로 본다
- 중첩 경로
- 속성 — 여는 태그의 속성 파싱
- flush — 미완성 버퍼를 텍스트로 되돌린다
- 스키마에 없는 태그는 일반 텍스트로 흘려보낸다

**주의점 하나.** 사용 중인 배포본 0.1.6은 패키지가 `me.hanju.adapter`이고 경로, 내용, 이벤트,
속성을 한 레코드에 담아 돌려준다. 그런데 `workspace/content-stream-adapter`의 로컬 소스는 이미
`dev.hanju.adapter`로 재설계돼 있고 API가 다르다. 신 API는 **의미 경로와 태그 이름을 분리**한다.

```java
ContentStreamAdapter.from(TransitionSchema.root().path("cite").toPaths())
    .bind("/cite").tag("cite").alias("rag").attr("id")
    .build();
```

출력도 레코드 하나가 아니라 Enter, Exit, Text 세 개의 sealed 타입이다. `common-hitl-chat`의
`CitationAwareLlmProvider`가 이미 신 API를 쓰고 있다. **Python 포트는 구 API가 아니라 이 신 API를
따라가는 것이 맞다.** 의미 경로와 표면 문법이 분리돼 있어 태그 형태가 달라져도 소비 코드가 안
바뀐다.

같은 이유로 인용 태그 문법이 두 갈래라는 점도 확인해야 한다. 이 저장소는 id를 중첩 태그로 받고,
`common-hitl-chat`은 여는 태그의 속성으로 받는다. 어느 쪽이 사내 모델의 실제 출력인지 확정한 뒤
스키마를 정한다.

**알고리즘 선택.** 원본은 Aho-Corasick을 쓴다. 실제 패턴 수가 태그당 두 개로 열 개를 넘지 않으므로
Python에서는 단순 트라이나 선형 스캔으로 충분하다. Aho-Corasick 자체를 옮길 필요는 없다.

**규모.** Python 250~350줄로 본다.

### 2.3 스트림 핸들 — fluxhandle 대체

**필요한 이유.** Java `StreamHandle`은 델타 콜백과 최종 결과를 동시에 제공한다. Python에는
Reactor가 없으므로 이디엄을 바꿔야 한다.

**권장 방향.** 리스너 인터페이스를 그대로 옮기지 말고 async iterator로 바꾼다.

```python
stream = client.stream(request)
async for delta in stream:
    print(delta.choices[0].delta.content, end="")
result = stream.result  # 병합된 최종 응답
```

`FluxListener`의 네 콜백은 각각 이렇게 대응된다. onNext는 이터레이션, onError는 예외 전파,
onComplete는 이터레이션 종료, onCancel은 `aclose()` 또는 태스크 취소다. `complete()`는 스트림을
끝까지 돌려 병합 결과를 돌려주는 코루틴이 된다.

동기 API를 함께 낼지는 결정이 필요하다. httpx가 동기와 비동기 양쪽을 지원하므로 파서와 병합기를
공유하고 전송 계층만 둘로 두면 된다.

**규모.** Python 150~200줄.

### 2.4 SSE 전송 계층

**현행의 한계.** `WebClient.bodyToFlux(String.class)`는 SSE 프레임에서 data 필드만 뽑아 문자열로
준다. 이벤트 이름, id, retry, 주석 라인은 전부 버려진다. 표준 OpenAI 스트림에는 data밖에 없으니
문제가 없지만, 이름 붙은 이벤트를 쓰는 서버에는 대응할 수 없다.

**권장 방향.** httpx에 프레임 전체를 보존하는 SSE 파서를 얹는다. `httpx-sse`가 event, data, id,
retry를 모두 노출하므로 후보로 적합하다. 다만 의존을 늘리지 않으려면 SSE 파싱 자체는 60줄 정도라
직접 쓰는 편도 가능하다.

**반드시 다뤄야 할 것.**

- data 여러 줄은 개행으로 이어붙인다
- `[DONE]`으로 종료하되, 이 표지가 안 오는 서버도 있다
- 주석 전용 라인(`:`로 시작)을 무시한다. 표준 SSE 규칙이다

재연결은 요구에 넣지 않는다. OpenAI 호환 chat completions 스트림에는 `id:` 필드가 없어 재개할 수
없다. `Last-Event-ID` replay는 `common-hitl-chat`이 브라우저에게 내보내는 Turn 이벤트 스트림의
성질이고 LLM 엔드포인트와 무관하다. 3절의 두 해석 중 첫 번째를 택할 때만 필요해진다.

### 2.5 직렬화와 다형성 — Jackson·Lombok 대체

| Java | Python |
|---|---|
| `@JsonProperty("top_p")` | `Field(alias="top_p")` + `populate_by_name` |
| `@JsonAlias({"reasoning", "reasoning_content"})` | `AliasChoices` |
| `@JsonInclude(NON_NULL)` | `model_dump(exclude_none=True)` |
| `@JsonTypeInfo(DEDUCTION)` | discriminated union 또는 명시 타입 필드 |
| `@SuperBuilder` / `@Builder` | 생성자 키워드 인자 |
| `mapper.registerSubtypes(...)` | 레지스트리에 서브클래스 등록 |

`@JsonTypeInfo(use = DEDUCTION)`은 필드 구성만 보고 구현체를 고르는 방식이라 Pydantic에 직접
대응물이 없다. `IMessageable`은 사용자가 직접 만들어 넣는 타입이므로 역직렬화가 실제로 필요한
경로인지 먼저 확인하고, 필요 없다면 다형 역직렬화를 아예 빼는 편이 낫다.

`ResponseMessage`의 reasoning 별칭은 유지해야 한다. vLLM 버전에 따라 필드 이름이 갈린다.

### 2.6 설정 객체

현행 `EnhancedCompletionProperties`는 baseUrl과 apiKey 둘뿐이고, 요청 경로는 클라이언트에
하드코딩돼 있다. 목표가 설정을 등록해 인스턴스를 만드는 구조이므로 여기서 넓혀야 한다.

```python
client = EnhancedCompletionClient(
    base_url="...",
    api_key=None,
    model="...",  # 기본 모델
    completions_path="/v1/chat/completions",
    timeout=120.0,
    headers={},  # 사내 게이트웨이용 추가 헤더
    citation_schema=CITE_SCHEMA,  # 태그 스키마 교체 가능
    sse=SseOptions(...),  # 사내 SSE 방언
)
```

E2E 테스트에 사내 주소와 모델명이 하드코딩돼 있다. Python 쪽에서는 환경변수로 빼고 저장소에
남기지 않는다.

---

## 3. 사내 특이 SSE 동작

`workspace/common-hitl-chat`을 세 브랜치 전부 뒤졌다. main과 cero는 같은 커밋이고,
`align-dev-hanju-tool-stream`만 세 커밋 앞서 있다. **SSE 계약을 문서로 적어둔 파일은 그 브랜치에만
있다.** `docs/API_SPECIFICATION.md`는 main에 존재하지 않고 커밋 `9addc07`에서 새로 추가됐다.
main이 아닌 다른 브랜치라는 기억과 일치한다.

거기 적힌 SSE는 **그 서버가 브라우저에게 내보내는** Turn 이벤트 스트림 계약이다. 표준 OpenAI
스트림과 다른 점이 다음과 같다.

- 이름 붙은 이벤트를 쓴다. turn 상태 4종, HITL 이벤트 8종, tool stream 6종, 스냅샷 1종
- 종료 표지가 없다. 종료는 terminal 이벤트 이름으로 판별한다
- 모든 이벤트가 같은 봉투를 쓴다. eventId, turnId, type과 종류별 필드, occurredAt
- eventId가 Turn 안에서 단조 증가하는 replay 커서다. `Last-Event-ID`로 재연결하면 그 이후를 다시
  보낸다
- 커서가 버퍼 1,024건 밖이거나 프로세스가 재시작됐으면 스냅샷 이벤트로 현재 상태를 한 번에 보낸 뒤
  실시간으로 잇는다
- 15초마다 주석 전용 하트비트를 보낸다
- 본문과 추론이 `contentPartType`으로 갈린다
- 클라이언트 연결 해제가 취소가 아니다. 취소는 별도 API로만 한다

같은 브랜치의 `docs/ARCHITECTURE.md`에는 `dev.hanju/tool-stream` v1 계약도 함께 적혀 있다. MCP
progress 알림을 실어나르는 규약으로, 알림 메타에 marker를 두고 answer 조각은 seq 순으로
이어붙이고 완성 객체는 kind별 목록에 넣는다.

**여기서 확인이 필요하다.** 위 내용은 사내 챗 서버가 *발신하는* SSE다. 반면 이 라이브러리는 LLM
엔드포인트의 SSE를 *수신하는* 쪽이다. 두 가지 해석이 가능하다.

1. Python 라이브러리가 사내 챗 서버의 Turn 이벤트 스트림도 소비하게 한다. 그렇다면 이름 붙은
   이벤트, eventId 재연결, 스냅샷, 하트비트를 파서가 다뤄야 한다
2. 사내 LLM 서빙 자체가 표준과 다르게 SSE를 내보내는 것을 말한다. 그렇다면 그 동작을 적은 문서를
   찾지 못했다. 실제 응답 샘플이 필요하다

어느 쪽인지에 따라 2.4절의 설계가 달라진다.

---

## 4. 권장 패키지 구조

```text
enhanced_completion/
  __init__.py            공개 API
  client.py              EnhancedCompletionClient
  config.py              설정 dataclass
  transport/
    sse.py               SSE 프레임 파서 (event/data/id/retry 보존)
    http.py              httpx 기반 전송, 동기와 비동기
  payload/
    completion.py        요청과 응답 모델
    message.py           IMessageable 계열
    document.py          IDocument, SimpleDocument
  streambind/            2.1 대체
    merger.py
    metadata.py
  contentstream/         2.2 대체
    adapter.py
    schema.py
  mapper/
    citation.py          EnhancedCompletionDeltaMapper 이식
  handle.py              2.3 대체
```

streambind와 contentstream은 이 라이브러리와 독립적으로 쓸 수 있는 물건이다. 지금은 안에 두되
패키지 경계를 지켜서 나중에 분리할 수 있게 둔다. Java 쪽이 세 개의 별도 저장소로 나뉜 이유가
그것이다.

---

## 5. 작업 순서

의존은 아래에서 위로 흐르지만, **착수 순서를 의존 순서와 같게 두지 않는다.** 태그 파서부터
만들면 가장 어렵고 가장 고립된 조각을 먼저 붙드는 셈이고, 한참 동안 돌아가는 물건이 없다. 게다가
제일 불확실한 것(3절, 사내 SSE)이 맨 뒤로 밀린다. 불확실한 것을 먼저 없애고, 얇게라도 끝에서
끝까지 통하는 경로를 먼저 세운다.

### 0단계 — 실제 응답 원문 확보

착수 전에 사내 엔드포인트에 스트리밍 요청을 한 번 보내고 SSE 원문을 그대로 덤프해 둔다.

```bash
curl -N -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"model":"...","messages":[{"role":"user","content":"안녕"}],"stream":true}' \
  "$BASE_URL/v1/chat/completions" | tee sse-dump.txt
```

이 덤프 하나가 3절의 두 해석 중 어느 쪽인지, 종료 표지가 오는지, 이름 붙은 이벤트를 쓰는지,
reasoning 필드 이름이 무엇인지, 인용 태그 문법이 중첩인지 속성인지를 한 번에 확정한다. 이후 모든
단계의 회귀 시험 fixture로도 쓴다. 비용이 가장 싸고 없애는 불확실성이 가장 크다.

### 1단계 — 얇은 수직 슬라이스

설정 객체, httpx 전송, SSE 파서, 요청·응답 Pydantic 모델을 최소한으로 붙여 원본 청크를 그대로
흘리는 것까지 만든다.

```python
client = EnhancedCompletionClient(base_url=..., model=...)
async for chunk in client.stream(request):
    print(chunk.choices[0].delta.content, end="")
```

여기까지가 목표 1번(설정 등록해 인스턴스를 만들어 쓰는 라이브러리)의 실체다. 목표 2번(augment
제거)은 이식 목록에서 처음부터 빼는 것으로 끝난다. 이 시점에 이미 사내 엔드포인트에 붙여
검증된다.

### 2단계 이후

| 순서 | 작업 | 검증 |
|---|---|---|
| 2 | 범용 병합 엔진 | 사용자 정의 응답 타입이 규칙대로 접히는지 |
| 3 | contentstream 태그 파서 | Java 테스트의 청크 분할 케이스 전부 이식 |
| 4 | cite 어휘 기본 구현 + 확장 API | `EnhancedCompletionDeltaMapperTest` 전 케이스 |
| 5 | 사내 SSE 방언 | 0단계 덤프 기준 |

**2단계에서 범용 엔진을 뺄 수 없다.** 1.5.1.1절의 확장 단위가 타입 삼중쌍이므로 임의의 사용자
응답 타입을 병합해야 한다. 구체 타입 전용 누적기로는 확장 모델이 성립하지 않는다.

3~4단계가 전체에서 가장 무겁다. 태그 파서는 없어도 1단계 클라이언트가 동작하므로 첫 착수 대상은
아니지만 **선택 기능이 아니라 코어다.** 4단계에서는 cite 어휘 구현을 옮기는 데 그치지 말고,
사용자가 자기 삼중쌍을 등록하는 공개 API를 함께 낸다. Java 클라이언트가 결과 타입과 매퍼를
하드코딩해 막아둔 자리가 여기다.

---

## 부록 A. 이식하면서 고칠 현행 문제

조사 중 발견한 것들이다. Python 쪽에서 반복하지 않는다.

- `complete()`가 항상 스트리밍으로 요청한다. 비스트리밍 요청을 못 보낸다
- 요청 경로가 하드코딩돼 있다
- E2E 테스트에 사내 주소와 모델명이 박혀 있고 비활성화 어노테이션이 주석 처리돼 있어 CI에서 돌면
  실패한다
- `EnhancedCompletionProperties`에 타임아웃 설정이 없다
- 스트림 취소가 핸들에만 있고 HTTP 연결 해제와 연결돼 있지 않다
