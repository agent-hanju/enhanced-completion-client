package me.hanju.enhancedcompletion.payload.completion;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;

/**
 * OpenAI Chat Completion API 응답 기본 객체.
 *
 * @param <T> 응답 메시지 타입
 */
@SuperBuilder
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PROTECTED)
@JsonIgnoreProperties(ignoreUnknown = true)
public abstract class BaseCompletionResponse<T extends ResponseMessage> {

  private String id;
  private String object;
  private Long created;
  private String model;
  private List<Choice<T>> choices;
  private Usage usage;

  /**
   * 응답 선택지.
   *
   * @param <T> 응답 메시지 타입
   */
  @Builder
  @Getter
  @Setter
  @NoArgsConstructor(access = AccessLevel.PACKAGE)
  @AllArgsConstructor(access = AccessLevel.PRIVATE)
  @EqualsAndHashCode
  @ToString
  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class Choice<T extends ResponseMessage> {
    private Integer index;
    private T message;
    private T delta;

    @JsonProperty("finish_reason")
    @JsonAlias({ "finish_reason", "finishReason" })
    private String finishReason;
  }

  /**
   * 토큰 사용량 정보.
   */
  @Builder
  @Getter
  @Setter
  @NoArgsConstructor(access = AccessLevel.PACKAGE)
  @AllArgsConstructor(access = AccessLevel.PRIVATE)
  @EqualsAndHashCode
  @ToString
  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class Usage {
    @JsonProperty("prompt_tokens")
    @JsonAlias({ "prompt_tokens", "promptTokens" })
    private Integer promptTokens;

    @JsonProperty("completion_tokens")
    @JsonAlias({ "completion_tokens", "completionTokens" })
    private Integer completionTokens;

    @JsonProperty("total_tokens")
    @JsonAlias({ "total_tokens", "totalTokens" })
    private Integer totalTokens;
  }
}
