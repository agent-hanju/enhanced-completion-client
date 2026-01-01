package me.hanju.enhancedcompletion.assembler;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import me.hanju.adapter.ContentStreamAdapter;
import me.hanju.adapter.payload.TaggedToken;
import me.hanju.adapter.transition.TransitionSchema;
import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ToolCall;
import me.hanju.enhancedcompletion.payload.completion.ToolFunction;
import me.hanju.enhancedcompletion.payload.message.Citation;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;
import me.hanju.fluxhandle.FluxAssembler;

/**
 * 스트리밍 ChatCompletionResponse를 조립하여 EnhancedCompletionResponse를 생성하는 어셈블러.
 * cite 태그를 파싱하여 Citation을 추출한다.
 */
public class EnhancedCompletionResponseAssembler
    implements FluxAssembler<ChatCompletionResponse, EnhancedCompletionResponse> {

  private static final TransitionSchema CITE_SCHEMA = TransitionSchema.root()
      .tag("cite", cite -> cite.tag("id")).alias("rag");

  private final ContentStreamAdapter adapter = new ContentStreamAdapter(CITE_SCHEMA);
  private final StringBuilder content = new StringBuilder();
  private final StringBuilder reasoning = new StringBuilder();
  private final StringBuilder citeIdBuilder = new StringBuilder();
  private final List<Citation> citations = new ArrayList<>();
  private final Map<Integer, ToolCallBuilder> toolCallBuilders = new HashMap<>();

  private String role;
  private ChatCompletionResponse lastResponse;
  private int currentIndex = 0;
  private int citationIndex = 0;
  private Integer citeStartIndex = null;

  @Override
  public void applyDelta(final ChatCompletionResponse response) {
    if (response == null || response.getChoices() == null || response.getChoices().isEmpty()) {
      return;
    }
    lastResponse = response;

    final var choice = response.getChoices().get(0);
    final ResponseMessage delta = choice.getDelta() != null ? choice.getDelta() : choice.getMessage();
    if (delta == null) {
      return;
    }

    if (delta.getRole() != null) {
      this.role = delta.getRole();
    }
    if (delta.getContent() != null) {
      processContent(delta.getContent());
    }
    if (delta.getReasoning() != null) {
      reasoning.append(delta.getReasoning());
    }
    if (delta.getToolCalls() != null) {
      for (final ToolCall call : delta.getToolCalls()) {
        if (call.getIndex() != null) {
          toolCallBuilders.computeIfAbsent(call.getIndex(), ToolCallBuilder::new).merge(call);
        }
      }
    }
  }

  private void processContent(final String text) {
    for (final TaggedToken token : adapter.feedToken(text)) {
      processTaggedToken(token);
    }
  }

  protected ChatCompletionResponse getLastResponse() {
    return lastResponse;
  }

  protected StringBuilder getContent() {
    return content;
  }

  protected List<Citation> getCitations() {
    return citations;
  }

  protected int getCurrentIndex() {
    return currentIndex;
  }

  protected void setCurrentIndex(final int currentIndex) {
    this.currentIndex = currentIndex;
  }

  protected StringBuilder getCiteIdBuilder() {
    return citeIdBuilder;
  }

  protected Integer getCiteStartIndex() {
    return citeStartIndex;
  }

  protected void setCiteStartIndex(final Integer citeStartIndex) {
    this.citeStartIndex = citeStartIndex;
  }

  protected int getAndIncrementCitationIndex() {
    return citationIndex++;
  }

  protected void processTaggedToken(final TaggedToken token) {
    final String path = token.path();
    final String text = token.content();
    final String event = token.event();

    if ("/".equals(path) && text != null) {
      content.append(text);
      currentIndex += text.length();
    } else if ("/cite".equals(path)) {
      if ("OPEN".equals(event)) {
        citeStartIndex = currentIndex;
        citeIdBuilder.setLength(0);
      } else if ("CLOSE".equals(event) && citeStartIndex != null) {
        citations.add(Citation.builder()
            .index(citationIndex++)
            .id(citeIdBuilder.toString())
            .startIndex(citeStartIndex)
            .endIndex(currentIndex)
            .build());
        citeStartIndex = null;
      } else if (text != null) {
        content.append(text);
        currentIndex += text.length();
      }
    } else if ("/cite/id".equals(path) && text != null) {
      citeIdBuilder.append(text);
    }
  }

  private void flush() {
    for (final TaggedToken token : adapter.flush()) {
      processTaggedToken(token);
    }
    if (citeStartIndex != null) {
      citations.add(Citation.builder()
          .index(citationIndex++)
          .id(citeIdBuilder.toString())
          .startIndex(citeStartIndex)
          .endIndex(currentIndex)
          .build());
      citeStartIndex = null;
    }
  }

  @Override
  public EnhancedCompletionResponse build() {
    flush();

    final List<ToolCall> toolCalls = toolCallBuilders.isEmpty() ? null
        : toolCallBuilders.values().stream()
            .sorted((a, b) -> Integer.compare(a.index, b.index))
            .map(ToolCallBuilder::build)
            .toList();

    final CitedMessage message = CitedMessage.builder()
        .role(role != null ? role : "assistant")
        .content(content.toString())
        .reasoning(reasoning.isEmpty() ? null : reasoning.toString())
        .toolCalls(toolCalls)
        .citations(citations.isEmpty() ? null : new ArrayList<>(citations))
        .build();

    final var builder = EnhancedCompletionResponse.builder()
        .choices(List.of(BaseCompletionResponse.Choice.<CitedMessage>builder()
            .index(0)
            .message(message)
            .finishReason("stop")
            .build()));

    if (lastResponse != null) {
      builder.id(lastResponse.getId())
          .object(lastResponse.getObject())
          .created(lastResponse.getCreated())
          .model(lastResponse.getModel())
          .usage(lastResponse.getUsage());
    }

    return builder.build();
  }

  private static class ToolCallBuilder {
    final int index;
    String id;
    String type;
    String name;
    final StringBuilder arguments = new StringBuilder();

    ToolCallBuilder(int index) {
      this.index = index;
    }

    void merge(ToolCall chunk) {
      if (chunk.getId() != null)
        this.id = chunk.getId();
      if (chunk.getType() != null)
        this.type = chunk.getType();
      if (chunk.getFunction() != null) {
        if (chunk.getFunction().getName() != null)
          this.name = chunk.getFunction().getName();
        if (chunk.getFunction().getArguments() != null)
          this.arguments.append(chunk.getFunction().getArguments());
      }
    }

    ToolCall build() {
      return ToolCall.builder()
          .index(index)
          .id(id)
          .type(type)
          .function(ToolFunction.builder()
              .name(name)
              .arguments(arguments.toString())
              .build())
          .build();
    }
  }
}
