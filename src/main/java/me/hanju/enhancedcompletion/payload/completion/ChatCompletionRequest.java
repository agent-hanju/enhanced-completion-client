package me.hanju.enhancedcompletion.payload.completion;

import com.fasterxml.jackson.annotation.JsonInclude;

import lombok.Getter;
import lombok.Setter;
import lombok.experimental.SuperBuilder;

/**
 * OpenAI Chat Completion API 요청 객체.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ChatCompletionRequest extends BaseCompletionRequest<Message> {
}
