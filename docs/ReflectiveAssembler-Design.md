# ReflectiveAssembler 설계 문서

## 1. 개요

### 1.1 배경

OpenAI Chat Completion API의 스트리밍 응답은 delta 방식으로 전달됩니다. 각 chunk는 변경된 필드만 포함하며, 클라이언트는 이를 누적하여 최종 결과를 조립해야 합니다.

현재 이 패턴을 구현하려면 매 DTO마다 별도의 Assembler를 작성해야 하며, 이는 반복적이고 오류가 발생하기 쉽습니다.

### 1.2 목표

Reflection과 어노테이션을 활용하여 **임의의 DTO 클래스에 대해 자동으로 delta 조립을 수행**하는 범용 `FluxAssembler` 구현체를 제공합니다.

### 1.3 적용 대상

- OpenAI Chat Completion streaming response
- 유사한 delta 기반 스트리밍 API (Anthropic, Google AI 등)
- 커스텀 스트리밍 DTO

---

## 2. Delta 스트리밍 규칙

OpenAI 스트리밍 API의 delta 규칙:

| 규칙 | 설명 | 예시 |
|------|------|------|
| **모든 필드 nullable** | 변경 없는 필드는 null | `{"content": null, "role": null}` |
| **변경된 필드만 포함** | null이 아닌 필드 = append할 값 | `{"content": "Hello"}` |
| **List는 index 기반** | item 객체에 index 필드 포함, single item list로 전달 | `{"choices": [{"index": 0, "delta": {...}}]}` |

### 2.1 실제 스트리밍 예시

```json
// Chunk 1: 메타데이터 + role
{"id":"chatcmpl-123","choices":[{"index":0,"delta":{"role":"assistant"}}]}

// Chunk 2-N: content 조각들
{"id":"chatcmpl-123","choices":[{"index":0,"delta":{"content":"Hello"}}]}
{"id":"chatcmpl-123","choices":[{"index":0,"delta":{"content":" world"}}]}

// 최종 결과 (조립 후)
{"id":"chatcmpl-123","choices":[{"index":0,"message":{"role":"assistant","content":"Hello world"}}]}
```

### 2.2 Tool Call 스트리밍 예시

```json
// Tool call도 index 기반으로 누적
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","function":{"name":"get"}}]}}]}
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"_weather"}}]}}]}
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"loc"}}]}}]}
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ation\":"}}]}}]}
```

---

## 3. API 설계

### 3.1 어노테이션

```java
package me.hanju.fluxhandle.assembler;

/**
 * String 필드에 대해 append 동작을 지정합니다.
 * 이 어노테이션이 없는 String 필드는 replace 동작을 수행합니다.
 */
@Target(ElementType.FIELD)
@Retention(RetentionPolicy.RUNTIME)
public @interface StreamAppend {
}

/**
 * List 필드가 index 기반으로 병합되어야 함을 지정합니다.
 * List의 item 클래스에는 @StreamIndex가 붙은 필드가 있어야 합니다.
 */
@Target(ElementType.FIELD)
@Retention(RetentionPolicy.RUNTIME)
public @interface StreamIndexedList {
}

/**
 * List item의 index 식별자 필드를 지정합니다.
 * Integer 타입이어야 합니다.
 */
@Target(ElementType.FIELD)
@Retention(RetentionPolicy.RUNTIME)
public @interface StreamIndex {
}
```

### 3.2 ReflectiveAssembler 클래스

```java
package me.hanju.fluxhandle.assembler;

/**
 * Reflection 기반 범용 FluxAssembler 구현체.
 * 어노테이션을 기반으로 delta 스트리밍 규칙을 자동 적용합니다.
 *
 * @param <T> delta 및 결과 타입 (동일 타입)
 */
public class ReflectiveAssembler<T> implements FluxAssembler<T, T> {

    /**
     * 지정된 클래스 타입으로 assembler를 생성합니다.
     */
    public static <T> ReflectiveAssembler<T> of(Class<T> type);

    @Override
    public void applyDelta(T delta);

    @Override
    public T build();
}
```

### 3.3 사용 예시

```java
// DTO 정의
public class ChatCompletionChunk {
    private String id;                      // replace (기본)
    private String object;                  // replace
    private Long created;                   // replace

    @StreamIndexedList
    private List<Choice> choices;           // index 기반 merge
}

public class Choice {
    @StreamIndex
    private Integer index;                  // list item 식별자

    private String finishReason;            // replace

    @StreamAppend
    private Delta delta;                    // 중첩 객체도 재귀 처리
}

public class Delta {
    private String role;                    // replace

    @StreamAppend
    private String content;                 // append

    @StreamIndexedList
    private List<ToolCall> toolCalls;       // 중첩 indexed list
}

// 사용
ReflectiveAssembler<ChatCompletionChunk> assembler = ReflectiveAssembler.of(ChatCompletionChunk.class);

flux.doOnNext(assembler::applyDelta)
    .then()
    .block();

ChatCompletionChunk result = assembler.build();
```

---

## 4. 구현 명세

### 4.1 필드 타입별 처리 규칙

| 필드 타입 | 어노테이션 | 동작 |
|-----------|-----------|------|
| `String` | 없음 | **replace** - delta 값으로 덮어씀 |
| `String` | `@StreamAppend` | **append** - 기존 값에 이어붙임 |
| `Integer`, `Long`, `Boolean` 등 | - | **replace** - delta 값으로 덮어씀 |
| `List<E>` | 없음 | **replace** - delta 리스트로 덮어씀 |
| `List<E>` | `@StreamIndexedList` | **index merge** - index 기반 병합 |
| 중첩 객체 | 없음 | **replace** - delta 객체로 덮어씀 |
| 중첩 객체 | `@StreamAppend` | **recursive merge** - 필드별 재귀 병합 |

### 4.2 Index 기반 List 병합 알고리즘

```
1. delta list의 각 item에 대해:
   a. item에서 @StreamIndex 필드 값(index) 추출
   b. accumulated list에서 동일 index를 가진 item 검색
   c. 존재하면: 해당 item에 delta item을 재귀적으로 merge
   d. 없으면: delta item을 새로 추가
2. build() 시 index 기준으로 정렬
```

### 4.3 Null 처리 규칙

```
- delta 필드가 null이면: 해당 필드 skip (변경 없음)
- delta 필드가 non-null이면: 규칙에 따라 적용
- accumulated 필드가 null이고 delta가 non-null이면: delta 값으로 초기화
```

### 4.4 객체 생성 전략

누적 상태를 저장할 mutable holder가 필요합니다. 옵션:

**Option A: Map 기반 내부 저장소**
```java
// 내부적으로 Map<String, Object>로 상태 저장
// build() 시 target 클래스 인스턴스 생성
private Map<String, Object> state = new HashMap<>();
```

**Option B: Mutable clone 생성**
```java
// target 클래스의 인스턴스를 직접 생성하여 상태 저장
// Setter 또는 Reflection으로 필드 수정
private T accumulated;
```

**권장: Option A** - DTO가 immutable(record, @Value)인 경우에도 동작

---

## 5. 성능 고려사항

### 5.1 Reflection 오버헤드

| 작업 | 소요 시간 |
|------|-----------|
| 네트워크 I/O | ~10,000,000 ns |
| JSON 파싱 | ~100,000 ns |
| Reflection 필드 접근 | ~50 ns (캐싱 후) |

**결론**: 스트리밍 응답 조립 유스케이스에서 Reflection 오버헤드는 무시 가능

### 5.2 최적화 권장사항

```java
// Field 메타데이터 캐싱
private static final Map<Class<?>, List<FieldMetadata>> FIELD_CACHE = new ConcurrentHashMap<>();

// FieldMetadata: 어노테이션 정보, getter/setter 참조 등을 미리 분석
private record FieldMetadata(
    Field field,
    MergeStrategy strategy,    // REPLACE, APPEND, INDEXED_LIST, RECURSIVE
    Field indexField           // @StreamIndex 필드 (있는 경우)
) {}
```

---

## 6. 엣지 케이스 처리

### 6.1 지원해야 할 케이스

| 케이스 | 처리 방안 |
|--------|----------|
| `@StreamIndex` 필드 없는 `@StreamIndexedList` | 예외 발생 또는 순서대로 append |
| 중첩 depth가 깊은 경우 | 재귀 처리 (depth 제한 옵션 고려) |
| Immutable 객체 (record) | Map 기반 저장소 사용, build()에서 생성자 호출 |
| Generic 타입 `List<?>` | ParameterizedType으로 element 타입 추출 |
| 상속 구조 | 부모 클래스 필드도 스캔 |

### 6.2 지원하지 않아도 되는 케이스

- `Map<K,V>` 타입 필드 (OpenAI API에서 사용 안함)
- Array 타입 (`int[]`, `String[]`) - List 사용 권장
- Circular reference - 스트리밍 DTO에서 발생 안함

---

## 7. 참고 구현

### 7.1 OpenAI Java SDK - ChatCompletionAccumulator

**위치**: `openai-java-core/src/main/kotlin/com/openai/helpers/ChatCompletionAccumulator.kt`

**특징**:
- ChatCompletion 전용 (범용 아님)
- 필드별 하드코딩된 처리
- `mutableMap`으로 choice index → builder 관리
- String append: `(existing ?: "") + delta`

**참고 링크**: https://github.com/openai/openai-java

### 7.2 현재 프로젝트의 수동 구현 예시

**파일**: `EnhancedCompletionResponseAssembler.java`

```java
// Tool call 누적 - index 기반 Map 사용
private final Map<Integer, ToolCallBuilder> toolCallBuilders = new HashMap<>();

// delta 적용
if (delta.getToolCalls() != null) {
    for (final ToolCall call : delta.getToolCalls()) {
        if (call.getIndex() != null) {
            toolCallBuilders.computeIfAbsent(call.getIndex(), ToolCallBuilder::new)
                .merge(call);
        }
    }
}

// build 시 정렬
toolCallBuilders.values().stream()
    .sorted((a, b) -> Integer.compare(a.index, b.index))
    .map(ToolCallBuilder::build)
    .toList();
```

---

## 8. 테스트 시나리오

### 8.1 기본 케이스

```java
@Test
void shouldAppendStringFields() {
    // Given: @StreamAppend String content
    // When: delta1 = "Hello", delta2 = " world"
    // Then: result.content = "Hello world"
}

@Test
void shouldReplaceNonAnnotatedFields() {
    // Given: String id (no annotation)
    // When: delta1.id = "a", delta2.id = "b"
    // Then: result.id = "b"
}

@Test
void shouldMergeIndexedList() {
    // Given: @StreamIndexedList List<Choice> choices
    // When: delta1 = [Choice(index=0, content="Hi")]
    //       delta2 = [Choice(index=0, content=" there")]
    //       delta3 = [Choice(index=1, content="Bye")]
    // Then: result.choices = [
    //         Choice(index=0, content="Hi there"),
    //         Choice(index=1, content="Bye")
    //       ]
}
```

### 8.2 실제 OpenAI 응답 테스트

```java
@Test
void shouldAssembleRealOpenAIStreamingResponse() {
    // 실제 OpenAI streaming chunk JSON들을 파싱하여
    // ReflectiveAssembler로 조립한 결과가
    // non-streaming API 응답과 동일한지 검증
}
```

---

## 9. 향후 확장 가능성

| 기능 | 설명 | 우선순위 |
|------|------|----------|
| Builder 패턴 지원 | `@Builder` 클래스에 대한 build() 최적화 | 중 |
| Kotlin data class | copy() 메서드 활용 | 낮음 |
| 커스텀 merge 전략 | `@StreamMerge(strategy = CustomMerger.class)` | 낮음 |
| Compile-time 코드 생성 | Annotation Processor로 Reflection 제거 | 낮음 (필요시) |

---

## 10. 패키지 구조 제안

```
me.hanju.fluxhandle
├── FluxAssembler.java           // (기존) 인터페이스
├── FluxHandle.java              // (기존) 구현체
├── FluxListener.java            // (기존) 콜백
│
└── assembler/                   // (신규) 패키지
    ├── ReflectiveAssembler.java // 범용 구현체
    ├── StreamAppend.java        // 어노테이션
    ├── StreamIndexedList.java   // 어노테이션
    ├── StreamIndex.java         // 어노테이션
    └── internal/
        ├── FieldMetadata.java   // 필드 메타 캐시
        └── MergeStrategy.java   // 병합 전략 enum
```

---

## 부록: 용어 정의

| 용어 | 정의 |
|------|------|
| **Delta** | 스트리밍에서 전달되는 증분(incremental) 데이터 |
| **Chunk** | 스트리밍의 한 단위 응답 (하나의 delta 포함) |
| **Accumulate** | Delta들을 누적하여 최종 결과를 만드는 과정 |
| **Index-based merge** | List item을 index 필드 값으로 식별하여 병합하는 방식 |
