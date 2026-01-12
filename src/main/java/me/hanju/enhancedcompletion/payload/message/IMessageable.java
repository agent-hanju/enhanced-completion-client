package me.hanju.enhancedcompletion.payload.message;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

import me.hanju.enhancedcompletion.payload.completion.IMessage;
import me.hanju.enhancedcompletion.payload.completion.Message;

/**
 * Message로 변환 가능한 객체를 나타내는 인터페이스.
 */
@JsonTypeInfo(use = JsonTypeInfo.Id.DEDUCTION, defaultImpl = BaseMessage.class)
@JsonSubTypes({
    @JsonSubTypes.Type(value = BaseMessage.class),
    @JsonSubTypes.Type(value = ToolMessage.class),
    @JsonSubTypes.Type(value = AttachedMessage.class),
    @JsonSubTypes.Type(value = ResponseMessage.class),
    @JsonSubTypes.Type(value = CitedMessage.class)
})
public interface IMessageable extends IMessage {
  Message toMessage();
}
