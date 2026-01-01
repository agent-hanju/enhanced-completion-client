package me.hanju.enhancedcompletion.assembler;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.message.Citation;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;

@DisplayName("EnhancedCompletionResponseAssembler")
class EnhancedCompletionResponseAssemblerTest {

  private EnhancedCompletionResponseAssembler assembler;

  @BeforeEach
  void setUp() {
    assembler = new EnhancedCompletionResponseAssembler();
  }

  private ChatCompletionResponse createDelta(String content) {
    return createDelta("assistant", content);
  }

  private ChatCompletionResponse createDelta(String role, String content) {
    return ChatCompletionResponse.builder()
        .id("chatcmpl-123")
        .object("chat.completion.chunk")
        .created(1234567890L)
        .model("gpt-4")
        .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
            .index(0)
            .delta(ResponseMessage.builder()
                .role(role)
                .content(content)
                .build())
            .build()))
        .build();
  }

  /**
   * 문자열을 한 글자씩 스트리밍하듯이 어셈블러에 전달.
   */
  private void feedCharByChar(String input) {
    for (char c : input.toCharArray()) {
      assembler.applyDelta(createDelta(String.valueOf(c)));
    }
  }

  @Nested
  @DisplayName("실제 스트리밍 패턴 - 태그가 청크 경계에서 분리됨")
  class RealisticStreamingPattern {

    @Test
    @DisplayName("단일 cite 태그 - 한 글자씩 스트리밍")
    void shouldParseSingleCiteTagCharByChar() {
      feedCharByChar("<cite><id>doc1</id>인용된 텍스트</cite>");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("인용된 텍스트");
      assertThat(message.getCitations()).hasSize(1);

      Citation citation = message.getCitations().get(0);
      assertThat(citation.getIndex()).isEqualTo(0);
      assertThat(citation.getId()).isEqualTo("doc1");
      assertThat(citation.getStartIndex()).isEqualTo(0);
      assertThat(citation.getEndIndex()).isEqualTo(7);
      assertThat(message.getContent().substring(citation.getStartIndex(), citation.getEndIndex()))
          .isEqualTo("인용된 텍스트");
    }

    @Test
    @DisplayName("여러 cite 태그 - 한 글자씩 스트리밍")
    void shouldParseMultipleCiteTagsCharByChar() {
      feedCharByChar("<cite><id>doc1</id>첫번째</cite> 중간 <cite><id>doc2</id>두번째</cite>");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("첫번째 중간 두번째");
      assertThat(message.getCitations()).hasSize(2);

      Citation first = message.getCitations().get(0);
      assertThat(first.getId()).isEqualTo("doc1");
      assertThat(first.getStartIndex()).isEqualTo(0);
      assertThat(first.getEndIndex()).isEqualTo(3);

      Citation second = message.getCitations().get(1);
      assertThat(second.getId()).isEqualTo("doc2");
      assertThat(second.getStartIndex()).isEqualTo(7);
      assertThat(second.getEndIndex()).isEqualTo(10);
    }

    @Test
    @DisplayName("청크로 나뉜 cite 태그 - 태그 경계에서 분리")
    void shouldParseCiteTagAcrossChunks() {
      assembler.applyDelta(createDelta("<cite><i"));
      assembler.applyDelta(createDelta("d>do"));
      assembler.applyDelta(createDelta("c1</id>인용"));
      assembler.applyDelta(createDelta("텍스트</ci"));
      assembler.applyDelta(createDelta("te>"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("인용텍스트");
      assertThat(message.getCitations()).hasSize(1);

      Citation citation = message.getCitations().get(0);
      assertThat(citation.getId()).isEqualTo("doc1");
      assertThat(citation.getStartIndex()).isEqualTo(0);
      assertThat(citation.getEndIndex()).isEqualTo(5);
    }

    @Test
    @DisplayName("여러 청크에 걸친 복잡한 응답 - 태그 경계에서 분리")
    void shouldHandleComplexStreamingResponse() {
      assembler.applyDelta(createDelta("assistant", null));
      assembler.applyDelta(createDelta(null, "시작 "));
      assembler.applyDelta(createDelta(null, "<ci"));
      assembler.applyDelta(createDelta(null, "te><i"));
      assembler.applyDelta(createDelta(null, "d>ref1</i"));
      assembler.applyDelta(createDelta(null, "d>인용1</ci"));
      assembler.applyDelta(createDelta(null, "te> 중간 "));
      assembler.applyDelta(createDelta(null, "<cite><i"));
      assembler.applyDelta(createDelta(null, "d>ref2</i"));
      assembler.applyDelta(createDelta(null, "d>인용2</ci"));
      assembler.applyDelta(createDelta(null, "te> 끝"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getContent()).isEqualTo("시작 인용1 중간 인용2 끝");
      assertThat(message.getCitations()).hasSize(2);

      Citation first = message.getCitations().get(0);
      assertThat(first.getId()).isEqualTo("ref1");
      assertThat(first.getStartIndex()).isEqualTo(3);
      assertThat(first.getEndIndex()).isEqualTo(6);

      Citation second = message.getCitations().get(1);
      assertThat(second.getId()).isEqualTo("ref2");
      assertThat(second.getStartIndex()).isEqualTo(10);
      assertThat(second.getEndIndex()).isEqualTo(13);
    }

    @Test
    @DisplayName("rag 태그 - 한 글자씩 스트리밍")
    void shouldParseRagTagCharByChar() {
      feedCharByChar("<rag><id>doc1</id>RAG 인용</rag>");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("RAG 인용");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(message.getCitations().get(0).getId()).isEqualTo("doc1");
    }

    @Test
    @DisplayName("연속된 cite 태그 - 한 글자씩 스트리밍")
    void shouldHandleConsecutiveCiteTagsCharByChar() {
      feedCharByChar("<cite><id>a</id>A</cite><cite><id>b</id>B</cite><cite><id>c</id>C</cite>");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("ABC");
      assertThat(message.getCitations()).hasSize(3);
    }
  }

  @Nested
  @DisplayName("한 번에 입력되는 패턴 - 단일 토큰 내 복수 태그")
  class BulkInputPattern {

    @Test
    @DisplayName("단일 cite 태그 - 한 번에 입력")
    void shouldParseSingleCiteTagBulk() {
      assembler.applyDelta(createDelta("<cite><id>doc1</id>인용된 텍스트</cite>"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("인용된 텍스트");
      assertThat(message.getCitations()).hasSize(1);
    }

    @Test
    @DisplayName("여러 cite 태그 - 한 번에 입력")
    void shouldParseMultipleCiteTagsBulk() {
      assembler.applyDelta(createDelta("<cite><id>doc1</id>첫번째</cite> 중간 <cite><id>doc2</id>두번째</cite>"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("첫번째 중간 두번째");
      assertThat(message.getCitations()).hasSize(2);
    }

    @Test
    @DisplayName("rag 태그 - 한 번에 입력")
    void shouldParseRagTagBulk() {
      assembler.applyDelta(createDelta("<rag><id>doc1</id>RAG 인용</rag>"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("RAG 인용");
      assertThat(message.getCitations()).hasSize(1);
    }
  }

  @Nested
  @DisplayName("일반 텍스트 처리")
  class PlainTextHandling {

    @Test
    @DisplayName("cite 태그 없는 일반 텍스트")
    void shouldHandlePlainText() {
      assembler.applyDelta(createDelta("일반 텍스트입니다."));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("일반 텍스트입니다.");
      assertThat(message.getCitations()).isNullOrEmpty();
    }

    @Test
    @DisplayName("여러 청크의 일반 텍스트")
    void shouldHandleMultipleChunksPlainText() {
      assembler.applyDelta(createDelta("Hello "));
      assembler.applyDelta(createDelta("world"));
      assembler.applyDelta(createDelta("!"));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("Hello world!");
      assertThat(message.getCitations()).isNullOrEmpty();
    }
  }

  @Nested
  @DisplayName("엣지 케이스")
  class EdgeCases {

    @Test
    @DisplayName("빈 content")
    void shouldHandleEmptyContent() {
      assembler.applyDelta(createDelta(""));

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEmpty();
      assertThat(message.getCitations()).isNullOrEmpty();
    }

    @Test
    @DisplayName("null response 무시")
    void shouldIgnoreNullResponse() {
      assembler.applyDelta(null);
      assembler.applyDelta(createDelta("텍스트"));

      EnhancedCompletionResponse response = assembler.build();
      assertThat(response.getChoices().get(0).getMessage().getContent()).isEqualTo("텍스트");
    }

    @Test
    @DisplayName("빈 choices 무시")
    void shouldIgnoreEmptyChoices() {
      ChatCompletionResponse emptyChoices = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .choices(List.of())
          .build();

      assembler.applyDelta(emptyChoices);
      assembler.applyDelta(createDelta("텍스트"));

      EnhancedCompletionResponse response = assembler.build();
      assertThat(response.getChoices().get(0).getMessage().getContent()).isEqualTo("텍스트");
    }

    @Test
    @DisplayName("닫히지 않은 cite 태그는 flush에서 처리 - 스트리밍 패턴")
    void shouldHandleUnclosedCiteTagOnFlush() {
      feedCharByChar("<cite><id>doc1</id>미완성 인용");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("미완성 인용");
      assertThat(message.getCitations()).hasSize(1);

      Citation citation = message.getCitations().get(0);
      assertThat(citation.getId()).isEqualTo("doc1");
      assertThat(citation.getStartIndex()).isEqualTo(0);
      assertThat(citation.getEndIndex()).isEqualTo(6);
    }

    @Test
    @DisplayName("빈 cite 태그 - 스트리밍 패턴")
    void shouldHandleEmptyCiteTag() {
      feedCharByChar("앞<cite><id>empty</id></cite>뒤");

      EnhancedCompletionResponse response = assembler.build();
      CitedMessage message = response.getChoices().get(0).getMessage();

      assertThat(message.getContent()).isEqualTo("앞뒤");
      assertThat(message.getCitations()).hasSize(1);

      Citation citation = message.getCitations().get(0);
      assertThat(citation.getId()).isEqualTo("empty");
      assertThat(citation.getStartIndex()).isEqualTo(1);
      assertThat(citation.getEndIndex()).isEqualTo(1);
    }

    @Test
    @DisplayName("role이 없으면 기본값 assistant")
    void shouldUseDefaultRole() {
      assembler.applyDelta(createDelta(null, "텍스트"));

      EnhancedCompletionResponse response = assembler.build();
      assertThat(response.getChoices().get(0).getMessage().getRole()).isEqualTo("assistant");
    }
  }

  @Nested
  @DisplayName("응답 메타데이터")
  class ResponseMetadata {

    @Test
    @DisplayName("마지막 응답의 메타데이터가 유지됨")
    void shouldPreserveMetadataFromLastResponse() {
      ChatCompletionResponse response1 = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().content("Hello").build())
              .build()))
          .build();

      ChatCompletionResponse response2 = ChatCompletionResponse.builder()
          .id("chatcmpl-456")
          .object("chat.completion.chunk")
          .created(1234567891L)
          .model("gpt-4-turbo")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().content(" world").build())
              .build()))
          .build();

      assembler.applyDelta(response1);
      assembler.applyDelta(response2);

      EnhancedCompletionResponse result = assembler.build();

      assertThat(result.getId()).isEqualTo("chatcmpl-456");
      assertThat(result.getModel()).isEqualTo("gpt-4-turbo");
      assertThat(result.getCreated()).isEqualTo(1234567891L);
    }
  }
}
