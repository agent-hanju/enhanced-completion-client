package me.hanju.enhancedcompletion.assembler;

import java.util.List;

import me.hanju.adapter.payload.TaggedToken;
import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.message.Citation;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;
import me.hanju.fluxhandle.FluxListener;

/**
 * 토큰 단위로 delta를 스트리밍하는 EnhancedCompletionResponse 어셈블러.
 */
public class StreamingEnhancedAssembler extends EnhancedCompletionResponseAssembler {

  private final FluxListener<EnhancedCompletionResponse> listener;

  public StreamingEnhancedAssembler(final FluxListener<EnhancedCompletionResponse> listener) {
    this.listener = listener;
  }

  @Override
  protected void processTaggedToken(final TaggedToken token) {
    final String path = token.path();
    final String text = token.content();
    final String event = token.event();

    if ("/".equals(path) && text != null) {
      getContent().append(text);
      setCurrentIndex(getCurrentIndex() + text.length());
      emitDelta(CitedMessage.builder().content(text).build());

    } else if ("/cite".equals(path)) {
      if ("OPEN".equals(event)) {
        setCiteStartIndex(getCurrentIndex());
        getCiteIdBuilder().setLength(0);
      } else if ("CLOSE".equals(event) && getCiteStartIndex() != null) {
        final Citation citation = Citation.builder()
            .index(getAndIncrementCitationIndex())
            .id(getCiteIdBuilder().toString())
            .startIndex(getCiteStartIndex())
            .endIndex(getCurrentIndex())
            .build();
        getCitations().add(citation);
        setCiteStartIndex(null);
        emitDelta(CitedMessage.builder().citations(List.of(citation)).build());
      } else if (text != null) {
        getContent().append(text);
        setCurrentIndex(getCurrentIndex() + text.length());
        emitDelta(CitedMessage.builder().content(text).build());
      }

    } else if ("/cite/id".equals(path) && text != null) {
      getCiteIdBuilder().append(text);
    }
  }

  private void emitDelta(final CitedMessage delta) {
    final ChatCompletionResponse source = getLastResponse();
    if (source == null) {
      return;
    }

    final EnhancedCompletionResponse response = EnhancedCompletionResponse.builder()
        .id(source.getId())
        .object(source.getObject())
        .created(source.getCreated())
        .model(source.getModel())
        .choices(List.of(BaseCompletionResponse.Choice.<CitedMessage>builder()
            .index(source.getChoices().get(0).getIndex())
            .delta(delta)
            .build()))
        .build();

    listener.onNext(response);
  }
}
