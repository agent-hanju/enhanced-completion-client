package me.hanju.enhancedcompletion.payload.message;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.NonNull;
import lombok.Setter;
import lombok.ToString;
import me.hanju.enhancedcompletion.payload.completion.Message;

/**
 * Tool 호출 결과를 담는 메시지.
 * OpenAI API의 tool role 메시지에 해당.
 */
@Builder
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PRIVATE)
@EqualsAndHashCode
@ToString
@JsonIgnoreProperties(ignoreUnknown = true)
public class ToolMessage implements IMessageable {

  private static final String ROLE_TOOL = "tool";

  @NonNull
  @JsonProperty("tool_call_id")
  private String toolCallId;

  private String content;

  @Override
  public String getRole() {
    return ROLE_TOOL;
  }

  @Override
  public Message toMessage() {
    return Message.builder()
        .toolCallId(toolCallId)
        .role(ROLE_TOOL)
        .content(content)
        .build();
  }

  /**
   * ToolMessage 생성 편의 메서드.
   *
   * @param toolCallId tool call ID
   * @param content    tool 실행 결과
   * @return ToolMessage
   */
  public static ToolMessage of(String toolCallId, String content) {
    return ToolMessage.builder()
        .toolCallId(toolCallId)
        .content(content)
        .build();
  }
}
