package me.hanju.enhancedcompletion.payload.completion;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;

/**
 * OpenAI Chat Completion API 응답 객체.
 */
@SuperBuilder
@Getter
@Setter
@NoArgsConstructor
@EqualsAndHashCode(callSuper = true)
@ToString(callSuper = true)
@JsonIgnoreProperties(ignoreUnknown = true)
public class ChatCompletionResponse extends BaseCompletionResponse<ResponseMessage> {
}
