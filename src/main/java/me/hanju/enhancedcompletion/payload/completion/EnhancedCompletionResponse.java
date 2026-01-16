package me.hanju.enhancedcompletion.payload.completion;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.AccessLevel;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;

/**
 * 인용 정보가 포함된 Chat Completion 응답 객체.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PUBLIC)
@EqualsAndHashCode(callSuper = true)
@ToString(callSuper = true)
@JsonIgnoreProperties(ignoreUnknown = true)
public class EnhancedCompletionResponse extends BaseCompletionResponse<CitedMessage> {

  /**
   * RAG 수행 결과 (있는 경우).
   * Augmenter가 없거나 RAG를 수행하지 않은 경우 null.
   */
  @JsonIgnore
  private SimpleAugmentResult augmentResult;
}
