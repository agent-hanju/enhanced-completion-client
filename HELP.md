# Enhanced Completion Client - 개발자 가이드

## fluxhandle 0.3.0 API 참조

이 프로젝트는 `fluxhandle` 라이브러리를 사용하여 스트리밍 응답을 처리합니다.

### 핵심 개념

#### 1. DeltaMapper<T, R>

스트리밍 delta를 변환하는 **stateful** 함수형 인터페이스입니다.

```java
@FunctionalInterface
public interface DeltaMapper<T, R> {
    /**
     * 입력 delta를 0개 이상의 출력으로 변환합니다.
     * - 빈 리스트: 이 delta는 출력 없이 내부 상태만 업데이트
     * - 단일 요소: 일반적인 1:1 변환
     * - 여러 요소: 하나의 입력이 여러 출력으로 분할
     */
    List<R> map(T delta);
}
```

**사용 예시:**
```java
public class MyMapper implements DeltaMapper<Input, Output> {
    private StringBuilder accumulated = new StringBuilder();

    @Override
    public List<Output> map(Input delta) {
        if (delta.getContent() != null) {
            accumulated.append(delta.getContent());
            // 스트리밍 delta 반환
            return List.of(Output.builder()
                .content(delta.getContent())
                .build());
        }
        return List.of(); // 출력 없음
    }
}
```

#### 2. DeltaMerger<T>

`DeltaMapper`가 반환한 delta들을 자동으로 병합합니다.

**병합 규칙:**

| 타입 | 동작 | 예시 |
|------|------|------|
| `String` | concatenate | `"Hello" + " World"` → `"Hello World"` |
| `Integer`, `Long` | sum | `10 + 5` → `15` |
| `Object` (중첩) | recursive merge | 필드별 재귀 병합 |
| `List<Primitive>` | extend | `[1,2] + [3]` → `[1,2,3]` |
| `List<Object>` | index 기반 merge | 같은 index끼리 병합 |

**List<Object> 병합 상세:**
```java
// Delta 1: choices = [{ index: 0, content: "Hello" }]
// Delta 2: choices = [{ index: 0, content: " World" }]
// Delta 3: choices = [{ index: 1, content: "Bye" }]

// 결과: choices = [
//   { index: 0, content: "Hello World" },
//   { index: 1, content: "Bye" }
// ]
```

#### 3. @StreamIndex

기본적으로 `index` 필드를 사용하지만, 다른 이름의 필드를 사용하려면 어노테이션을 붙입니다.

```java
public class ToolCall {
    @StreamIndex
    private Integer idx;  // "index" 대신 "idx" 사용

    private String id;
    private ToolFunction function;
}
```

#### 4. FluxHandle<T, R>

스트리밍 소스를 래핑하고, 변환 및 병합을 수행합니다.

```java
// 생성
FluxHandle<Input, Output> handle = new FluxHandle<>(
    flux,              // Flux<Input> 소스
    mapper,            // DeltaMapper<Input, Output>
    Output.class,      // 결과 타입 (DeltaMerger용)
    listener           // FluxListener<Output> 콜백
);

// 결과 조회 (blocking)
Output result = handle.get();
Output result = handle.get(30, TimeUnit.SECONDS);

// 상태 확인
handle.isCancelled();
handle.isError();
handle.getError();

// 취소
handle.cancel();
```

**핵심 특징: 에러/취소 시에도 부분 결과 반환**

```java
// 스트림 중간에 에러 발생해도
handle.get();  // → 에러 전까지 누적된 부분 결과 반환
handle.isError();  // → true
handle.getError(); // → 발생한 예외
```

#### 5. SimpleFluxHandle<T>

입력과 출력 타입이 동일할 때 사용합니다. 내부적으로 `DeltaMerger`만 사용.

```java
SimpleFluxHandle<MyDto> handle = new SimpleFluxHandle<>(
    flux,           // Flux<MyDto>
    MyDto.class,    // 타입
    listener        // FluxListener<MyDto>
);
```

#### 6. FluxListener<R>

스트리밍 이벤트 콜백 인터페이스입니다.

```java
FluxListener<Output> listener = new FluxListener<>() {
    @Override
    public void onNext(Output delta) {
        // 각 delta 수신 시 호출
        System.out.print(delta.getContent());
    }

    @Override
    public void onComplete() {
        // 스트림 정상 완료
    }

    @Override
    public void onError(Throwable e) {
        // 에러 발생
    }

    @Override
    public void onCancel() {
        // 취소됨
    }
};
```

---

## 프로젝트 구조

```
me.hanju.enhancedcompletion/
├── EnhancedCompletionClient.java      # 메인 클라이언트
├── assembler/                         # 스트리밍 변환기
│   ├── EnhancedCompletionDeltaMapper  # ChatCompletion → Enhanced 변환
│   └── AugmentResultDeltaMapper       # RAG 결과 변환
├── augmenter/                         # RAG 구현체
│   ├── MockAugmenter                  # 테스트용
│   ├── KeywordMatchAugmenter          # 키워드 기반
│   ├── VectorDBAugmenter              # 벡터 DB (abstract)
│   └── CompositeAugmenter             # 조합
├── payload/
│   ├── augment/                       # RAG 관련 DTO
│   ├── completion/                    # OpenAI 호환 DTO
│   ├── document/                      # 문서 DTO
│   └── message/                       # 메시지 DTO
└── exception/
```

---

## FluxAssembler → DeltaMapper 마이그레이션

### Before (0.2.0)

```java
public class MyAssembler implements FluxAssembler<Input, Output> {
    private State state = new State();

    @Override
    public void applyDelta(Input delta) {
        // 상태 누적
        state.accumulate(delta);
    }

    @Override
    public Output build() {
        return new Output(state);
    }
}

// 사용
FluxHandle<Input, Output> handle = new FluxHandle<>(
    flux,
    assembler,
    listener  // FluxListener<Input> - 입력 타입!
);
```

### After (0.3.0)

```java
public class MyMapper implements DeltaMapper<Input, Output> {
    private State state = new State();

    @Override
    public List<Output> map(Input delta) {
        // 상태 누적
        state.accumulate(delta);

        // 스트리밍용 delta 반환 (DeltaMerger가 최종 병합)
        return List.of(Output.builder()
            .field(delta.getField())  // 이번 delta의 변경분만
            .build());
    }
}

// 사용
FluxHandle<Input, Output> handle = new FluxHandle<>(
    flux,
    mapper,
    Output.class,  // 추가됨: DeltaMerger용 타입
    listener       // FluxListener<Output> - 출력 타입!
);
```

### 주요 차이점

| 항목 | 0.2.0 | 0.3.0 |
|------|-------|-------|
| 인터페이스 | `FluxAssembler<T, R>` | `DeltaMapper<T, R>` |
| 메서드 | `applyDelta()` + `build()` | `map()` 단일 메서드 |
| 반환 | void + 최종 결과 | 매번 `List<R>` 반환 |
| Listener 타입 | `FluxListener<T>` (입력) | `FluxListener<R>` (출력) |
| 병합 | 수동 구현 | `DeltaMerger` 자동 처리 |

---

## Augmenter 인터페이스

```java
public interface Augmenter {
    String getName();
    Flux<AugmentResult> augment(AugmentRequest request);
}

public record AugmentRequest(
    List<Message> history,  // 대화 내역
    String query            // 검색 쿼리
) {}

public interface AugmentResult {
    List<? extends IDocument> getDocuments();
}
```

### 제공되는 구현체

#### MockAugmenter - 테스트용

```java
// 기본 사용
Augmenter mock = new MockAugmenter();

// 커스텀 설정
Augmenter mock = MockAugmenter.builder()
    .name("test-augmenter")
    .documents(List.of(myDoc1, myDoc2))
    .delay(Duration.ofMillis(100))      // 네트워크 지연 시뮬레이션
    .streamIndividually(true)            // 문서를 개별 스트리밍
    .build();
```

#### KeywordMatchAugmenter - 키워드 기반 검색

```java
KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);

// 문서 인덱싱 (수동 키워드)
augmenter.indexDocument(doc1, List.of("java", "spring"));
augmenter.indexDocument(doc2, List.of("kotlin", "coroutine"));

// 또는 자동 키워드 추출
augmenter.indexDocument(doc3);  // 제목에서 키워드 추출
```

#### VectorDBAugmenter - 벡터 DB 연동

```java
public class PineconeAugmenter extends VectorDBAugmenter {

    public PineconeAugmenter() {
        super("pinecone", 5, 0.7f);  // topK=5, threshold=0.7
    }

    @Override
    protected Mono<float[]> embedQuery(String query) {
        // OpenAI Embeddings API 호출
        return openAiClient.embeddings(query);
    }

    @Override
    protected Flux<IDocument> searchSimilar(float[] embedding, int topK, float threshold) {
        // Pinecone 검색 API 호출
        return pineconeClient.query(embedding, topK, threshold);
    }
}
```

#### CompositeAugmenter - 여러 Augmenter 조합

```java
Augmenter composite = CompositeAugmenter.builder()
    .name("multi-source")
    .addAugmenter(keywordAugmenter)
    .addAugmenter(vectorAugmenter)
    .maxTotalDocuments(10)  // 전체 최대 문서 수
    .build();
```

### 커스텀 구현 예시

```java
public class MyAugmenter implements Augmenter {

    @Override
    public String getName() {
        return "my-augmenter";
    }

    @Override
    public Flux<AugmentResult> augment(AugmentRequest request) {
        // 검색 수행
        List<IDocument> docs = searchDocuments(request.query());

        // 결과 반환 (단일 또는 스트리밍)
        return Flux.just(() -> docs);
    }
}
```

---

## Exception 계층

```
FluxHandleException (base)
├── FluxListenerException   # 콜백 실행 중 에러
├── MergeException          # 병합 중 에러
└── MetadataException       # 리플렉션 메타데이터 에러
```

---

## 요구사항

- Java 21+
- Project Reactor Core
- Spring WebFlux (optional, for WebClient)
