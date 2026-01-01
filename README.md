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
    implementation 'com.github.agent-hanju:enhanced-completion-client:0.1.0'
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
    <version>0.1.0</version>
</dependency>
```

## Usage

### Basic Streaming

```java
import me.hanju.enhancedcompletion.EnhancedCompletionClient;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.fluxhandle.FluxListener;

// 클라이언트 생성
EnhancedCompletionClient client = new EnhancedCompletionClient(
    WebClient.builder(),
    new ObjectMapper(),
    "https://api.openai.com",
    "your-api-key"
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

## Message Types

### 기본 제공 메시지 타입

| 타입 | 설명 |
|------|------|
| `BaseMessage` | 기본 메시지 (role, content) |
| `AttachedMessage` | 문서 첨부 메시지 (documents 포함) |
| `ResponseMessage` | LLM 응답 메시지 (reasoning, tool_calls 포함) |
| `CitedMessage` | 인용 정보가 포함된 응답 메시지 (citations 포함) |

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
EnhancedCompletionClient client = new EnhancedCompletionClient(
    WebClient.builder(),
    mapper,
    "https://api.openai.com",
    "your-api-key"
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

## License

MIT License
