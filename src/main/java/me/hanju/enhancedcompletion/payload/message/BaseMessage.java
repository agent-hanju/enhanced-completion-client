package me.hanju.enhancedcompletion.payload.message;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import me.hanju.enhancedcompletion.payload.completion.Message;

/**
 * IMessageable의 기본 구현체.
 */
@Builder
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PRIVATE)
@EqualsAndHashCode
@ToString
@JsonIgnoreProperties(ignoreUnknown = true)
public class BaseMessage implements IMessageable {

  private String role;
  private String content;

  @Override
  public Message toMessage() {
    return Message.builder()
        .role(role)
        .content(content)
        .build();
  }
}
