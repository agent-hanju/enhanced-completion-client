package me.hanju.enhancedcompletion.payload.message;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.enhancedcompletion.payload.completion.ToolCall;
import me.hanju.streambind.annotation.StreamOverwrite;

/**
 * LLM 응답 메시지.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor(access = AccessLevel.PROTECTED)
@EqualsAndHashCode
@ToString
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonTypeInfo(use = JsonTypeInfo.Id.DEDUCTION, defaultImpl = ResponseMessage.class)
@JsonSubTypes({
    @JsonSubTypes.Type(value = ResponseMessage.class),
    @JsonSubTypes.Type(value = CitedMessage.class)
})
public class ResponseMessage implements IMessageable {

  @StreamOverwrite
  private String role;
  private String content;

  @JsonProperty("reasoning")
  @JsonAlias({ "reasoning", "reasoning_content" })
  private String reasoning;

  @JsonProperty("tool_calls")
  @JsonAlias({ "tool_calls", "toolCalls" })
  private List<ToolCall> toolCalls;

  @Override
  public Message toMessage() {
    return Message.builder()
        .role(role)
        .content(content)
        .build();
  }
}
