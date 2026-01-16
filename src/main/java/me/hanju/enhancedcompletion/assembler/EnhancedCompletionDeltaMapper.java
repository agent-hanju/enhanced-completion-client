package me.hanju.enhancedcompletion.assembler;

import java.util.ArrayList;
import java.util.List;

import me.hanju.adapter.ContentStreamAdapter;
import me.hanju.adapter.payload.TaggedToken;
import me.hanju.adapter.transition.TransitionSchema;
import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;
import me.hanju.enhancedcompletion.payload.message.Citation;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;
import me.hanju.streambind.map.StreamMapper;

/**
 * ChatCompletionResponse 스트림을 EnhancedCompletionResponse로 변환하는 StreamMapper.
 * cite 태그를 파싱하여 Citation을 추출하고, 각 delta마다 스트리밍 응답을 반환합니다.
 * StreamMerger가 delta들을 병합하여 최종 결과를 생성합니다.
 */
public class EnhancedCompletionDeltaMapper
    implements StreamMapper<ChatCompletionResponse, EnhancedCompletionResponse> {

  private static final TransitionSchema CITE_SCHEMA = TransitionSchema.root()
      .tag("cite", cite -> cite.tag("id")).alias("rag");

  private final ContentStreamAdapter adapter = new ContentStreamAdapter(CITE_SCHEMA);
  private final StringBuilder citeIdBuilder = new StringBuilder();

  private ChatCompletionResponse lastResponse;
  private int currentIndex = 0;
  private int citationIndex = 0;
  private Integer citeStartIndex = null;

  @Override
  public List<EnhancedCompletionResponse> map(final ChatCompletionResponse response) {
    if (response == null || response.getChoices() == null || response.getChoices().isEmpty()) {
      return List.of();
    }
    lastResponse = response;

    final var choice = response.getChoices().get(0);
    final ResponseMessage delta = choice.getDelta() != null ? choice.getDelta() : choice.getMessage();
    if (delta == null) {
      return List.of();
    }

    // content 처리 - cite 태그 파싱으로 여러 delta가 될 수 있음 (상태 기반 버퍼링)
    final List<EnhancedCompletionResponse> results = delta.getContent() != null
        ? new ArrayList<>(processContent(delta.getContent()))
        : new ArrayList<>();

    // 기본 1:1 변환 - role, reasoning, toolCalls를 하나의 delta로 반환
    // content delta가 있으면 그것을, 없으면 새로 생성
    if (results.isEmpty()) {
      results.add(createDelta(CitedMessage.builder()
          .role(delta.getRole())
          .reasoning(delta.getReasoning())
          .toolCalls(delta.getToolCalls())
          .build()));
    } else {
      // 첫 번째 content delta에 role/reasoning/toolCalls 포함
      final EnhancedCompletionResponse first = results.get(0);
      final CitedMessage firstDelta = first.getChoices().get(0).getDelta();
      results.set(0, createDelta(CitedMessage.builder()
          .role(delta.getRole())
          .content(firstDelta.getContent())
          .reasoning(delta.getReasoning())
          .toolCalls(delta.getToolCalls())
          .citations(firstDelta.getCitations())
          .build()));
    }

    return results;
  }

  private List<EnhancedCompletionResponse> processContent(final String text) {
    final List<EnhancedCompletionResponse> results = new ArrayList<>();

    for (final TaggedToken token : adapter.feedToken(text)) {
      final EnhancedCompletionResponse delta = processTaggedToken(token);
      if (delta != null) {
        results.add(delta);
      }
    }

    return results;
  }

  private EnhancedCompletionResponse processTaggedToken(final TaggedToken token) {
    final String path = token.path();
    final String text = token.content();
    final String event = token.event();

    if ("/".equals(path) && text != null) {
      currentIndex += text.length();
      return createDelta(CitedMessage.builder().content(text).build());

    } else if ("/cite".equals(path)) {
      if ("OPEN".equals(event)) {
        citeStartIndex = currentIndex;
        citeIdBuilder.setLength(0);
      } else if ("CLOSE".equals(event) && citeStartIndex != null) {
        final Citation citation = Citation.builder()
            .index(citationIndex++)
            .id(citeIdBuilder.toString())
            .startIndex(citeStartIndex)
            .endIndex(currentIndex)
            .build();
        citeStartIndex = null;
        return createDelta(CitedMessage.builder().citations(List.of(citation)).build());
      } else if (text != null) {
        currentIndex += text.length();
        return createDelta(CitedMessage.builder().content(text).build());
      }

    } else if ("/cite/id".equals(path) && text != null) {
      citeIdBuilder.append(text);
    }

    return null;
  }

  private EnhancedCompletionResponse createDelta(final CitedMessage delta) {
    if (lastResponse == null) {
      return null;
    }

    final var sourceChoice = lastResponse.getChoices().get(0);
    return EnhancedCompletionResponse.builder()
        .id(lastResponse.getId())
        .object(lastResponse.getObject())
        .created(lastResponse.getCreated())
        .model(lastResponse.getModel())
        .choices(List.of(BaseCompletionResponse.Choice.<CitedMessage>builder()
            .index(sourceChoice.getIndex())
            .delta(delta)
            .finishReason(sourceChoice.getFinishReason())
            .build()))
        .build();
  }

  /**
   * 스트림 완료 후 flush 처리를 수행합니다.
   * 닫히지 않은 cite 태그가 있으면 마지막 citation을 반환합니다.
   */
  @Override
  public List<EnhancedCompletionResponse> flush() {
    final List<EnhancedCompletionResponse> results = new ArrayList<>();

    for (final TaggedToken token : adapter.flush()) {
      final EnhancedCompletionResponse delta = processTaggedToken(token);
      if (delta != null) {
        results.add(delta);
      }
    }

    // 닫히지 않은 cite 태그 처리
    if (citeStartIndex != null) {
      final Citation citation = Citation.builder()
          .index(citationIndex++)
          .id(citeIdBuilder.toString())
          .startIndex(citeStartIndex)
          .endIndex(currentIndex)
          .build();
      citeStartIndex = null;
      results.add(createDelta(CitedMessage.builder().citations(List.of(citation)).build()));
    }

    return results;
  }
}
