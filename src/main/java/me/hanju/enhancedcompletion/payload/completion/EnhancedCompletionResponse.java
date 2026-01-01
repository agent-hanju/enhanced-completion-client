package me.hanju.enhancedcompletion.payload.completion;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;

/**
 * 인용 정보가 포함된 Chat Completion 응답 객체.
 */
@SuperBuilder
@Getter
@Setter
@EqualsAndHashCode(callSuper = true)
@ToString(callSuper = true)
@JsonIgnoreProperties(ignoreUnknown = true)
public class EnhancedCompletionResponse extends BaseCompletionResponse<CitedMessage> {
}
