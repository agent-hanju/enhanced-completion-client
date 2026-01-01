package me.hanju.enhancedcompletion.payload.completion;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;

/**
 * OpenAI Chat Completion API 요청 기본 객체.
 *
 * @param <T> 메시지 타입
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PROTECTED)
@EqualsAndHashCode
@ToString
@JsonInclude(JsonInclude.Include.NON_NULL)
public abstract class BaseCompletionRequest<T extends IMessage> {

  private String model;

  @lombok.Builder.Default
  private List<T> messages = new ArrayList<>();

  private Double temperature;

  @JsonProperty("top_p")
  private Double topP;

  private Integer n;
  private Boolean stream;
  private List<String> stop;

  @JsonProperty("max_tokens")
  private Integer maxTokens;

  @JsonProperty("presence_penalty")
  private Double presencePenalty;

  @JsonProperty("frequency_penalty")
  private Double frequencyPenalty;

  private Boolean logprobs;

  @JsonProperty("top_logprobs")
  private Integer topLogprobs;

  private String user;
  private List<Map<String, Object>> tools;

  @JsonProperty("tool_choice")
  private Object toolChoice;

  @JsonProperty("response_format")
  private Map<String, Object> responseFormat;

  private Integer seed;
}
