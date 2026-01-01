package me.hanju.enhancedcompletion.payload.completion;

import java.util.ArrayList;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonInclude;

import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.message.IMessageable;

/**
 * 확장된 Chat Completion 요청 객체.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class EnhancedCompletionRequest extends BaseCompletionRequest<IMessageable> {

  @JsonIgnore
  public ChatCompletionRequest toChatCompletionRequest() {
    final List<Message> convertedMessages = new ArrayList<>();
    if (this.getMessages() != null) {
      for (final IMessageable msg : this.getMessages()) {
        convertedMessages.add(msg.toMessage());
      }
    }

    return ChatCompletionRequest.builder()
        .model(this.getModel())
        .messages(convertedMessages)
        .temperature(this.getTemperature())
        .topP(this.getTopP())
        .n(this.getN())
        .stream(this.getStream())
        .stop(this.getStop())
        .maxTokens(this.getMaxTokens())
        .presencePenalty(this.getPresencePenalty())
        .frequencyPenalty(this.getFrequencyPenalty())
        .logprobs(this.getLogprobs())
        .topLogprobs(this.getTopLogprobs())
        .user(this.getUser())
        .tools(this.getTools())
        .toolChoice(this.getToolChoice())
        .responseFormat(this.getResponseFormat())
        .seed(this.getSeed())
        .build();
  }
}
