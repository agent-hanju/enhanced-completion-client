package me.hanju.enhancedcompletion.payload.message;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import com.fasterxml.jackson.databind.ObjectMapper;

import me.hanju.enhancedcompletion.payload.completion.Message;

@DisplayName("ToolMessage")
class ToolMessageTest {

  private final ObjectMapper objectMapper = new ObjectMapper();

  @Nested
  @DisplayName("생성")
  class Creation {

    @Test
    @DisplayName("빌더로 생성")
    void builder() {
      ToolMessage message = ToolMessage.builder()
          .toolCallId("call_abc123")
          .content("{\"result\": \"success\"}")
          .build();

      assertThat(message.getToolCallId()).isEqualTo("call_abc123");
      assertThat(message.getContent()).isEqualTo("{\"result\": \"success\"}");
      assertThat(message.getRole()).isEqualTo("tool");
    }

    @Test
    @DisplayName("팩토리 메서드로 생성")
    void factoryMethod() {
      ToolMessage message = ToolMessage.of("call_xyz789", "Tool executed");

      assertThat(message.getToolCallId()).isEqualTo("call_xyz789");
      assertThat(message.getContent()).isEqualTo("Tool executed");
      assertThat(message.getRole()).isEqualTo("tool");
    }
  }

  @Nested
  @DisplayName("toMessage 변환")
  class ToMessage {

    @Test
    @DisplayName("Message로 변환 시 모든 필드 포함")
    void convertsAllFields() {
      ToolMessage toolMessage = ToolMessage.of("call_123", "result content");

      Message message = toolMessage.toMessage();

      assertThat(message.getToolCallId()).isEqualTo("call_123");
      assertThat(message.getRole()).isEqualTo("tool");
      assertThat(message.getContent()).isEqualTo("result content");
    }
  }

  @Nested
  @DisplayName("JSON 직렬화")
  class JsonSerialization {

    @Test
    @DisplayName("JSON으로 직렬화")
    void serialize() throws Exception {
      ToolMessage message = ToolMessage.of("call_abc", "result");

      String json = objectMapper.writeValueAsString(message);

      assertThat(json).contains("\"tool_call_id\":\"call_abc\"");
      assertThat(json).contains("\"content\":\"result\"");
    }

    @Test
    @DisplayName("JSON에서 역직렬화")
    void deserialize() throws Exception {
      String json = """
          {
            "tool_call_id": "call_def456",
            "content": "deserialized content"
          }
          """;

      ToolMessage message = objectMapper.readValue(json, ToolMessage.class);

      assertThat(message.getToolCallId()).isEqualTo("call_def456");
      assertThat(message.getContent()).isEqualTo("deserialized content");
      assertThat(message.getRole()).isEqualTo("tool");
    }

    @Test
    @DisplayName("IMessageable로 역직렬화")
    void deserializeAsIMessageable() throws Exception {
      String json = """
          {
            "tool_call_id": "call_polymorphic",
            "content": "polymorphic test"
          }
          """;

      IMessageable message = objectMapper.readValue(json, IMessageable.class);

      assertThat(message).isInstanceOf(ToolMessage.class);
      ToolMessage toolMessage = (ToolMessage) message;
      assertThat(toolMessage.getToolCallId()).isEqualTo("call_polymorphic");
      assertThat(toolMessage.getContent()).isEqualTo("polymorphic test");
    }
  }
}
