# Enhanced Completion Client

OpenAI-compatible Chat Completion API 클라이언트로, 스트리밍 응답과 인용(Citation) 태그 파싱을 지원합니다.

## Features

- **OpenAI-compatible API**: `/v1/chat/completions` 엔드포인트 호환
- **Streaming Support**: SSE 기반 실시간 스트리밍 응답 처리
- **Citation Parsing**: `<cite>` / `<rag>` 태그 자동 파싱 및 Citation 추출
- **Document Attachment**: 문서 첨부 메시지 지원
- **Reactive Streams**: Spring WebFlux 기반 비동기 처리

## Installation

### Gradle (JitPack)

```groovy
repositories {
    maven { url 'https://jitpack.io' }
}

dependencies {
    implementation 'com.github.agent-hanju:enhanced-completion-client:0.2.0'
}
```

### Maven (JitPack)

```xml
<repositories>
    <repository>
        <id>jitpack.io</id>
        <url>https://jitpack.io</url>
    </repository>
</repositories>

<dependency>
    <groupId>com.github.agent-hanju</groupId>
    <artifactId>enhanced-completion-client</artifactId>
    <version>0.2.0</version>
</dependency>
```

## Usage

### Basic Streaming

```java
import me.hanju.enhancedcompletion.EnhancedCompletionClient;
import me.hanju.enhancedcompletion.EnhancedCompletionProperties;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.fluxhandle.FluxListener;

// 클라이언트 생성
EnhancedCompletionProperties properties = new EnhancedCompletionProperties(
    "https://api.openai.com",
    "your-api-key"
);
EnhancedCompletionClient client = new EnhancedCompletionClient(
    WebClient.builder(),
    new ObjectMapper(),
    properties
);

// 요청 생성
EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
    .model("gpt-4")
    .messages(List.of(
        Message.builder().role("user").content("Hello!").build()
    ))
    .build();

// 스트리밍 요청
client.stream(request, new FluxListener<>() {
    @Override
    public void onNext(EnhancedCompletionResponse response) {
        // 토큰 단위로 delta 수신
        CitedMessage delta = response.getChoices().get(0).getDelta();
        if (delta.getContent() != null) {
            System.out.print(delta.getContent());
        }
        if (delta.getCitations() != null) {
            // Citation 정보 처리
            delta.getCitations().forEach(cite ->
                System.out.println("Citation: " + cite.getId())
            );
        }
    }

    @Override
    public void onComplete() {
        System.out.println("\nDone!");
    }

    @Override
    public void onError(Throwable e) {
        e.printStackTrace();
    }
});
```

### Non-Streaming Request

```java
EnhancedCompletionResponse response = client.complete(request);
CitedMessage message = response.getChoices().get(0).getMessage();

System.out.println("Content: " + message.getContent());
System.out.println("Citations: " + message.getCitations());
```

### Document Attachment

```java
import me.hanju.enhancedcompletion.payload.message.AttachedMessage;
import me.hanju.enhancedcompletion.payload.document.SimpleDocument;

AttachedMessage userMessage = AttachedMessage.builder()
    .role("user")
    .content("이 문서들을 참고해서 답변해주세요.")
    .documents(List.of(
        SimpleDocument.builder()
            .id("doc1")
            .title("문서 제목")
            .content("문서 내용...")
            .build()
    ))
    .build();

EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
    .model("gpt-4")
    .messages(List.of(userMessage))
    .build();
```

### Tool Use

```java
import me.hanju.enhancedcompletion.payload.message.BaseMessage;
import me.hanju.enhancedcompletion.payload.message.ToolMessage;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;

// 1. Tool 호출을 포함한 응답 수신
EnhancedCompletionResponse response = client.complete(request);
ResponseMessage assistantMessage = response.getChoices().get(0).getMessage();

if (assistantMessage.getToolCalls() != null) {
    List<IMessageable> messages = new ArrayList<>(request.getMessages());
    messages.add(assistantMessage);

    // 2. 각 Tool 호출에 대한 결과 추가
    for (ToolCall toolCall : assistantMessage.getToolCalls()) {
        String result = executeToolCall(toolCall);  // Tool 실행
        messages.add(ToolMessage.of(toolCall.getId(), result));
    }

    // 3. Tool 결과와 함께 후속 요청
    EnhancedCompletionRequest followUp = request.toBuilder()
        .messages(messages)
        .build();

    EnhancedCompletionResponse finalResponse = client.complete(followUp);
}
```

## Message Types

### 기본 제공 메시지 타입

| 타입              | 설명                                                            |
| ----------------- | --------------------------------------------------------------- |
| `BaseMessage`     | 기본 메시지 (role, content)                                     |
| `ToolMessage`     | Tool 호출 결과 메시지 (tool_call_id 필수, role은 "tool"로 고정) |
| `AttachedMessage` | 문서 첨부 메시지 (documents 포함)                               |
| `ResponseMessage` | LLM 응답 메시지 (reasoning, tool_calls 포함)                    |
| `CitedMessage`    | 인용 정보가 포함된 응답 메시지 (citations 포함)                 |

### Custom Message 타입 정의

`IMessageable` 인터페이스를 구현하여 커스텀 메시지 타입을 정의할 수 있습니다.

```java
import me.hanju.enhancedcompletion.payload.message.IMessageable;
import me.hanju.enhancedcompletion.payload.completion.Message;

public class MyCustomMessage implements IMessageable {
    private String role;
    private String content;
    private String customField;  // 커스텀 필드

    @Override
    public String getRole() {
        return role;
    }

    @Override
    public String getContent() {
        return content;
    }

    @Override
    public Message toMessage() {
        // LLM API로 전송될 형식으로 변환
        return Message.builder()
            .role(role)
            .content(content + "\n[Custom: " + customField + "]")
            .build();
    }
}
```

### ObjectMapper에 커스텀 타입 등록

Jackson ObjectMapper에 커스텀 타입을 등록하면 JSON 역직렬화 시 자동으로 인식됩니다.

```java
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.jsontype.NamedType;

ObjectMapper mapper = new ObjectMapper();
mapper.registerSubtypes(new NamedType(MyCustomMessage.class, "my-custom-type"));

// 클라이언트 생성 시 등록된 ObjectMapper 사용
EnhancedCompletionProperties properties = new EnhancedCompletionProperties(
    "https://api.openai.com",
    "your-api-key"
);
EnhancedCompletionClient client = new EnhancedCompletionClient(
    WebClient.builder(),
    mapper,
    properties
);
```

### 커스텀 메시지 사용

```java
MyCustomMessage customMessage = new MyCustomMessage();
customMessage.setRole("user");
customMessage.setContent("질문입니다.");
customMessage.setCustomField("추가 정보");

EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
    .model("gpt-4")
    .messages(List.of(customMessage))
    .build();
```

## Augmenter (RAG)

RAG(Retrieval-Augmented Generation) 기능을 위한 Augmenter 인터페이스를 제공합니다.

### 기본 제공 Augmenter

| 타입                    | 설명                                      |
| ----------------------- | ----------------------------------------- |
| `MockAugmenter`         | 테스트/개발용, 미리 정의된 문서 반환      |
| `KeywordMatchAugmenter` | 키워드 기반 간단한 검색, 외부 의존성 없음 |
| `VectorDBAugmenter`     | 벡터 DB 연동용 추상 클래스                |
| `CompositeAugmenter`    | 여러 Augmenter 병렬 실행 후 결과 병합     |

### Augmenter 사용

```java
import me.hanju.enhancedcompletion.augmenter.MockAugmenter;
import me.hanju.enhancedcompletion.spi.augment.AugmentRequest;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;

// Augmenter 생성
MockAugmenter augmenter = MockAugmenter.builder()
    .name("my-augmenter")
    .documents(List.of(
        SimpleDocument.builder()
            .id("doc1")
            .title("문서 제목")
            .content("문서 내용")
            .build()
    ))
    .build();

// 검색 요청
AugmentRequest request = new AugmentRequest(conversationHistory, "검색 쿼리");

// 결과 수신 (Reactive)
augmenter.augment(request)
    .flatMapIterable(AugmentResult::getDocuments)
    .subscribe(doc -> System.out.println("Found: " + doc.getTitle()));
```

### KeywordMatchAugmenter 사용

```java
import me.hanju.enhancedcompletion.augmenter.KeywordMatchAugmenter;

KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);

// 문서 인덱싱 (수동 키워드)
augmenter.indexDocument(doc1, List.of("java", "spring", "programming"));

// 또는 자동 키워드 추출 (제목에서)
augmenter.indexDocument(doc2);

// 검색
AugmentRequest request = new AugmentRequest(List.of(), "java programming guide");
List<IDocument> results = augmenter.augment(request)
    .flatMapIterable(AugmentResult::getDocuments)
    .collectList()
    .block();
```

### CompositeAugmenter로 여러 소스 병합

```java
import me.hanju.enhancedcompletion.augmenter.CompositeAugmenter;

CompositeAugmenter composite = CompositeAugmenter.builder()
    .name("multi-source")
    .addAugmenter(keywordAugmenter)
    .addAugmenter(vectorAugmenter)
    .maxTotalDocuments(10)  // 전체 최대 문서 수
    .build();

// 모든 Augmenter를 병렬 실행하고 결과 병합 (ID 기반 중복 제거)
augmenter.augment(request).subscribe(...);
```

### 커스텀 Augmenter 구현

`Augmenter` 인터페이스를 구현하여 커스텀 검색 로직을 정의할 수 있습니다.

```java
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.spi.augment.AugmentRequest;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;

public class MyCustomAugmenter implements Augmenter {

    @Override
    public String getName() {
        return "my-custom-augmenter";
    }

    @Override
    public Flux<AugmentResult> augment(AugmentRequest request) {
        // 검색 로직 구현
        List<IDocument> docs = searchDocuments(request.query());

        if (docs.isEmpty()) {
            return Flux.empty();
        }

        // 단일 결과 반환
        return Flux.just(() -> docs);

        // 또는 스트리밍 (문서를 개별적으로 emit)
        // return Flux.fromIterable(docs)
        //     .map(doc -> (AugmentResult) () -> List.of(doc));
    }
}
```

### VectorDBAugmenter 확장

벡터 DB 연동을 위해 `VectorDBAugmenter`를 확장합니다.

```java
import me.hanju.enhancedcompletion.augmenter.VectorDBAugmenter;

public class PineconeAugmenter extends VectorDBAugmenter {

    public PineconeAugmenter() {
        super("pinecone", 5, 0.7f);  // name, topK, threshold
    }

    @Override
    protected Mono<float[]> embedQuery(String query) {
        // 임베딩 API 호출 (예: OpenAI Embeddings)
        return openAiClient.embeddings(query);
    }

    @Override
    protected Flux<IDocument> searchSimilar(float[] embedding, int topK, float threshold) {
        // 벡터 DB 검색 API 호출
        return pineconeClient.query(embedding, topK, threshold);
    }
}
```

### IDocument 인터페이스

검색 결과 문서는 `IDocument` 인터페이스를 구현해야 합니다.

```java
public interface IDocument {
    String getId();       // 문서 고유 ID
    String getTitle();    // 제목
    String getContent();  // 내용
    default String getUrl() { return null; }  // URL (선택)

    // LLM 프롬프트용 직렬화
    default String toSerializedPrompt() {
        // <document id="..."><title>...</title><content>...</content></document>
    }
}
```

기본 구현체 `SimpleDocument`를 제공합니다:

```java
SimpleDocument doc = SimpleDocument.builder()
    .id("doc-123")
    .title("문서 제목")
    .content("문서 내용")
    .url("https://example.com/doc")
    .build();
```

## Citation Format

LLM 응답에서 `<cite>` 또는 `<rag>` 태그를 자동으로 파싱합니다.

**입력 (LLM 응답):**

```
서울은 대한민국의 수도입니다<cite><id>doc1</id>수도 정보</cite>.
```

**출력:**

- `content`: "서울은 대한민국의 수도입니다수도 정보."
- `citations`: `[{index: 0, id: "doc1", startIndex: 14, endIndex: 19}]`

## Dependencies

- Java 21+
- Spring WebFlux 6.2.x
- Jackson Databind 2.18.x
- [content-stream-adapter](https://github.com/agent-hanju/content-stream-adapter)
- [fluxhandle](https://github.com/agent-hanju/fluxhandle)
- [streambind](https://github.com/agent-hanju/streambind)

## License

MIT License
