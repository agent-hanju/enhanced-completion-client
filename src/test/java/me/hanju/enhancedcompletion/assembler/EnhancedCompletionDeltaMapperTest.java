package me.hanju.enhancedcompletion.assembler;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.ToolCall;
import me.hanju.enhancedcompletion.payload.completion.ToolFunction;
import me.hanju.enhancedcompletion.payload.message.Citation;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;
import me.hanju.enhancedcompletion.payload.message.ResponseMessage;
import me.hanju.fluxhandle.FluxListener;
import me.hanju.fluxhandle.StreamHandle;
import reactor.core.publisher.Flux;

@DisplayName("EnhancedCompletionDeltaMapper")
class EnhancedCompletionDeltaMapperTest {

  private EnhancedCompletionDeltaMapper mapper;
  private List<ChatCompletionResponse> chunks;

  @BeforeEach
  void setUp() {
    mapper = new EnhancedCompletionDeltaMapper();
    chunks = new ArrayList<>();
  }

  private ChatCompletionResponse createDelta(String content) {
    return createDelta("assistant", content);
  }

  private ChatCompletionResponse createDelta(String role, String content) {
    ChatCompletionResponse response = ChatCompletionResponse.builder()
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
    chunks.add(response);
    return response;
  }

  /**
   * 문자열을 한 글자씩 스트리밍하듯이 청크 리스트에 추가.
   */
  private void feedCharByChar(String input) {
    for (char c : input.toCharArray()) {
      createDelta(String.valueOf(c));
    }
  }

  /**
   * StreamHandle을 사용하여 최종 결과를 반환.
   */
  private EnhancedCompletionResponse getMergedResult() {
    StreamHandle<EnhancedCompletionResponse> handle = new StreamHandle<>(
        EnhancedCompletionResponse.class,
        new FluxListener<>() {
          @Override public void onNext(EnhancedCompletionResponse delta) { /* no-op for test */ }
          @Override public void onComplete() { /* no-op for test */ }
          @Override public void onError(Throwable e) { /* no-op for test */ }
          @Override public void onCancel() { /* no-op for test */ }
        }
    );
    handle.subscribe(Flux.fromIterable(chunks), mapper);
    return handle.get();
  }

  /**
   * Choice에서 message 또는 delta를 가져옴.
   * DeltaMerger는 delta 필드에 병합하므로 message가 null이면 delta를 사용.
   */
  private ResponseMessage getMessageOrDelta(EnhancedCompletionResponse response) {
    var choice = response.getChoices().get(0);
    ResponseMessage msg = choice.getMessage();
    return msg != null ? msg : choice.getDelta();
  }

  @Nested
  @DisplayName("실제 스트리밍 패턴 - 태그가 청크 경계에서 분리됨")
  class RealisticStreamingPattern {

    @Test
    @DisplayName("단일 cite 태그 - 한 글자씩 스트리밍")
    void shouldParseSingleCiteTagCharByChar() {
      feedCharByChar("<cite><id>doc1</id>인용된 텍스트</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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
      createDelta("<cite><i");
      createDelta("d>do");
      createDelta("c1</id>인용");
      createDelta("텍스트</ci");
      createDelta("te>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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
      createDelta("assistant", null);
      createDelta(null, "시작 ");
      createDelta(null, "<ci");
      createDelta(null, "te><i");
      createDelta(null, "d>ref1</i");
      createDelta(null, "d>인용1</ci");
      createDelta(null, "te> 중간 ");
      createDelta(null, "<cite><i");
      createDelta(null, "d>ref2</i");
      createDelta(null, "d>인용2</ci");
      createDelta(null, "te> 끝");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("RAG 인용");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(message.getCitations().get(0).getId()).isEqualTo("doc1");
    }

    @Test
    @DisplayName("연속된 cite 태그 - 한 글자씩 스트리밍")
    void shouldHandleConsecutiveCiteTagsCharByChar() {
      feedCharByChar("<cite><id>a</id>A</cite><cite><id>b</id>B</cite><cite><id>c</id>C</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("ABC");
      assertThat(message.getCitations()).hasSize(3);
    }

    @Test
    @DisplayName("복합 태그 경계 - 닫는 태그 끝 + 텍스트 + 여는 태그 시작이 한 청크")
    void shouldHandleCompositeBoundaryInSingleChunk() {
      // e>텍스트<c 같은 복합 경계가 한 청크에 오는 경우
      createDelta("<cite><id>doc1</id>첫번째</cit");
      createDelta("e>중간텍스트<ci");  // 닫는 태그 끝 + 일반 텍스트 + 여는 태그 시작
      createDelta("te><id>doc2</id>두번째</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("첫번째중간텍스트두번째");
      assertThat(message.getCitations()).hasSize(2);

      Citation first = message.getCitations().get(0);
      assertThat(first.getId()).isEqualTo("doc1");
      assertThat(first.getStartIndex()).isEqualTo(0);
      assertThat(first.getEndIndex()).isEqualTo(3);

      Citation second = message.getCitations().get(1);
      assertThat(second.getId()).isEqualTo("doc2");
      assertThat(second.getStartIndex()).isEqualTo(8);
      assertThat(second.getEndIndex()).isEqualTo(11);
    }

    @Test
    @DisplayName("복합 태그 경계 - 연속 태그 닫힘과 열림이 한 청크")
    void shouldHandleConsecutiveTagBoundaryInSingleChunk() {
      // </cite><cite> 전체가 한 청크에 오는 경우
      createDelta("<cite><id>a</id>A");
      createDelta("</cite><cite><id>b</id>");  // 닫힘 + 열림이 한 청크
      createDelta("B</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("AB");
      assertThat(message.getCitations()).hasSize(2);

      assertThat(message.getCitations().get(0).getId()).isEqualTo("a");
      assertThat(message.getCitations().get(1).getId()).isEqualTo("b");
    }

    @Test
    @DisplayName("복합 태그 경계 - id 태그 경계가 복합적으로 분리")
    void shouldHandleIdTagCompositeBoundary() {
      // </id>텍스트</ci 같은 복합 경계
      createDelta("<cite><id>doc");
      createDelta("1</id>인용내용</ci");  // id 닫힘 + 내용 + cite 닫힘 시작
      createDelta("te>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("인용내용");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(message.getCitations().get(0).getId()).isEqualTo("doc1");
    }
  }

  @Nested
  @DisplayName("한 번에 입력되는 패턴 - 단일 토큰 내 복수 태그")
  class BulkInputPattern {

    @Test
    @DisplayName("단일 cite 태그 - 한 번에 입력")
    void shouldParseSingleCiteTagBulk() {
      createDelta("<cite><id>doc1</id>인용된 텍스트</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("인용된 텍스트");
      assertThat(message.getCitations()).hasSize(1);
    }

    @Test
    @DisplayName("여러 cite 태그 - 한 번에 입력")
    void shouldParseMultipleCiteTagsBulk() {
      createDelta("<cite><id>doc1</id>첫번째</cite> 중간 <cite><id>doc2</id>두번째</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("첫번째 중간 두번째");
      assertThat(message.getCitations()).hasSize(2);
    }

    @Test
    @DisplayName("rag 태그 - 한 번에 입력")
    void shouldParseRagTagBulk() {
      createDelta("<rag><id>doc1</id>RAG 인용</rag>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("RAG 인용");
      assertThat(message.getCitations()).hasSize(1);
    }

    @Test
    @DisplayName("복잡한 응답 - 여러 cite 태그와 일반 텍스트가 혼합된 긴 응답")
    void shouldParseComplexBulkResponse() {
      // 비스트리밍: 전체 응답이 한 번에 옴
      createDelta("assistant", "서론입니다. <cite><id>ref1</id>첫 번째 인용</cite> "
          + "중간 설명이 길게 이어집니다. "
          + "<cite><id>ref2</id>두 번째 인용</cite> "
          + "그리고 <rag><id>rag1</id>RAG 결과</rag> "
          + "마지막으로 <cite><id>ref3</id>세 번째</cite> 결론.");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getContent()).isEqualTo(
          "서론입니다. 첫 번째 인용 중간 설명이 길게 이어집니다. 두 번째 인용 그리고 RAG 결과 마지막으로 세 번째 결론.");
      assertThat(message.getCitations()).hasSize(4);

      assertThat(message.getCitations().get(0).getId()).isEqualTo("ref1");
      assertThat(message.getCitations().get(1).getId()).isEqualTo("ref2");
      assertThat(message.getCitations().get(2).getId()).isEqualTo("rag1");
      assertThat(message.getCitations().get(3).getId()).isEqualTo("ref3");
    }

    @Test
    @DisplayName("연속된 cite 태그 - 한 번에 입력")
    void shouldParseConsecutiveCiteTagsBulk() {
      // 공백 없이 연속된 태그
      createDelta("<cite><id>a</id>A</cite><cite><id>b</id>B</cite><cite><id>c</id>C</cite>");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("ABC");
      assertThat(message.getCitations()).hasSize(3);

      // 인덱스가 순차적으로 매겨져야 함
      assertThat(message.getCitations().get(0).getIndex()).isEqualTo(0);
      assertThat(message.getCitations().get(1).getIndex()).isEqualTo(1);
      assertThat(message.getCitations().get(2).getIndex()).isEqualTo(2);

      // startIndex/endIndex가 정확해야 함
      assertThat(message.getCitations().get(0).getStartIndex()).isEqualTo(0);
      assertThat(message.getCitations().get(0).getEndIndex()).isEqualTo(1);
      assertThat(message.getCitations().get(1).getStartIndex()).isEqualTo(1);
      assertThat(message.getCitations().get(1).getEndIndex()).isEqualTo(2);
      assertThat(message.getCitations().get(2).getStartIndex()).isEqualTo(2);
      assertThat(message.getCitations().get(2).getEndIndex()).isEqualTo(3);
    }

    @Test
    @DisplayName("빈 인용 텍스트를 포함한 태그 - 한 번에 입력")
    void shouldHandleEmptyCitationTextBulk() {
      createDelta("앞<cite><id>empty</id></cite>뒤");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("앞뒤");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(message.getCitations().get(0).getId()).isEqualTo("empty");
      assertThat(message.getCitations().get(0).getStartIndex()).isEqualTo(1);
      assertThat(message.getCitations().get(0).getEndIndex()).isEqualTo(1);  // 빈 범위
    }

    @Test
    @DisplayName("toolCalls가 포함된 응답 - 한 번에 입력")
    void shouldParseToolCallsBulk() {
      // 비스트리밍: toolCalls가 포함된 전체 응답이 한 번에 옴
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .toolCalls(List.of(
                      ToolCall.builder()
                          .index(0)
                          .id("call_abc123")
                          .type("function")
                          .function(ToolFunction.builder()
                              .name("get_weather")
                              .arguments("{\"location\":\"Seoul\",\"unit\":\"celsius\"}")
                              .build())
                          .build(),
                      ToolCall.builder()
                          .index(1)
                          .id("call_def456")
                          .type("function")
                          .function(ToolFunction.builder()
                              .name("get_time")
                              .arguments("{\"timezone\":\"Asia/Seoul\"}")
                              .build())
                          .build()))
                  .build())
              .finishReason("tool_calls")
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getToolCalls()).hasSize(2);

      ToolCall first = message.getToolCalls().get(0);
      assertThat(first.getId()).isEqualTo("call_abc123");
      assertThat(first.getFunction().getName()).isEqualTo("get_weather");
      assertThat(first.getFunction().getArguments()).isEqualTo("{\"location\":\"Seoul\",\"unit\":\"celsius\"}");

      ToolCall second = message.getToolCalls().get(1);
      assertThat(second.getId()).isEqualTo("call_def456");
      assertThat(second.getFunction().getName()).isEqualTo("get_time");

      assertThat(response.getChoices().get(0).getFinishReason()).isEqualTo("tool_calls");
    }

    @Test
    @DisplayName("reasoning이 포함된 응답 - 한 번에 입력")
    void shouldParseReasoningBulk() {
      // 비스트리밍: reasoning + content가 포함된 전체 응답이 한 번에 옴
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .reasoning("사용자가 날씨를 물어봤으니 서울의 현재 날씨를 확인해야 합니다. "
                      + "기온과 습도 정보를 포함해서 답변하겠습니다.")
                  .content("서울의 현재 날씨는 맑음이며, 기온은 23도입니다.")
                  .build())
              .finishReason("stop")
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getReasoning()).isEqualTo(
          "사용자가 날씨를 물어봤으니 서울의 현재 날씨를 확인해야 합니다. "
          + "기온과 습도 정보를 포함해서 답변하겠습니다.");
      assertThat(message.getContent()).isEqualTo("서울의 현재 날씨는 맑음이며, 기온은 23도입니다.");
    }

    @Test
    @DisplayName("cite + reasoning + toolCalls가 모두 포함된 복합 응답 - 한 번에 입력")
    void shouldParseComplexResponseWithAllFieldsBulk() {
      // 비스트리밍: 모든 필드가 포함된 복합 응답
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-complex")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4-turbo")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .reasoning("문서를 검색해서 답변을 작성합니다.")
                  .content("<cite><id>doc1</id>참고 문서 내용</cite>에 따르면 답변입니다.")
                  .toolCalls(List.of(ToolCall.builder()
                      .index(0)
                      .id("call_xyz")
                      .type("function")
                      .function(ToolFunction.builder()
                          .name("search_docs")
                          .arguments("{\"query\":\"test\"}")
                          .build())
                      .build()))
                  .build())
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getReasoning()).isEqualTo("문서를 검색해서 답변을 작성합니다.");
      assertThat(message.getContent()).isEqualTo("참고 문서 내용에 따르면 답변입니다.");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(message.getCitations().get(0).getId()).isEqualTo("doc1");
      assertThat(message.getToolCalls()).hasSize(1);
      assertThat(message.getToolCalls().get(0).getFunction().getName()).isEqualTo("search_docs");
    }
  }

  @Nested
  @DisplayName("일반 텍스트 처리")
  class PlainTextHandling {

    @Test
    @DisplayName("cite 태그 없는 일반 텍스트")
    void shouldHandlePlainText() {
      createDelta("일반 텍스트입니다.");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("일반 텍스트입니다.");
      assertThat(message.getCitations()).isNullOrEmpty();
    }

    @Test
    @DisplayName("여러 청크의 일반 텍스트")
    void shouldHandleMultipleChunksPlainText() {
      createDelta("Hello ");
      createDelta("world");
      createDelta("!");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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
      createDelta("");

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      // 빈 문자열은 adapter에서 토큰을 생성하지 않으므로 content가 null일 수 있음
      assertThat(message.getContent()).isNullOrEmpty();
    }

    @Test
    @DisplayName("닫히지 않은 cite 태그는 flush에서 처리 - 스트리밍 패턴")
    void shouldHandleUnclosedCiteTagOnFlush() {
      feedCharByChar("<cite><id>doc1</id>미완성 인용");

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

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

      EnhancedCompletionResponse response = getMergedResult();
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);

      assertThat(message.getContent()).isEqualTo("앞뒤");
      assertThat(message.getCitations()).hasSize(1);

      Citation citation = message.getCitations().get(0);
      assertThat(citation.getId()).isEqualTo("empty");
      assertThat(citation.getStartIndex()).isEqualTo(1);
      assertThat(citation.getEndIndex()).isEqualTo(1);
    }

    @Test
    @DisplayName("role이 없으면 기본값 없음 (DeltaMerger가 null 유지)")
    void shouldHandleNullRole() {
      createDelta(null, "텍스트");

      EnhancedCompletionResponse response = getMergedResult();
      // DeltaMerger는 null을 유지하므로 role이 null일 수 있음
      assertThat(getMessageOrDelta(response).getContent()).isEqualTo("텍스트");
    }
  }

  @Nested
  @DisplayName("응답 메타데이터")
  class ResponseMetadata {

    @Test
    @DisplayName("마지막 응답의 메타데이터가 유지됨")
    void shouldPreserveMetadataFromLastResponse() {
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().content("Hello").build())
              .build()))
          .build());

      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-456")
          .object("chat.completion.chunk")
          .created(1234567891L)
          .model("gpt-4-turbo")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().content(" world").build())
              .build()))
          .build());

      EnhancedCompletionResponse result = getMergedResult();

      // DeltaMerger가 메타데이터를 병합함 (마지막 값이 덮어쓰기)
      assertThat(result.getId()).isEqualTo("chatcmpl-456");
      assertThat(result.getModel()).isEqualTo("gpt-4-turbo");
      assertThat(result.getCreated()).isEqualTo(1234567891L);
    }
  }

  @Nested
  @DisplayName("ToolCalls 스트리밍")
  class ToolCallsStreaming {

    @Test
    @DisplayName("toolCalls가 DeltaMerger에 의해 병합됨")
    void shouldMergeToolCalls() {
      // 첫 번째 청크: tool call 시작 (id, type, function name)
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .toolCalls(List.of(ToolCall.builder()
                      .index(0)
                      .id("call_abc123")
                      .type("function")
                      .function(ToolFunction.builder()
                          .name("get_weather")
                          .arguments("")
                          .build())
                      .build()))
                  .build())
              .build()))
          .build());

      // 두 번째 청크: arguments 스트리밍
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .toolCalls(List.of(ToolCall.builder()
                      .index(0)
                      .function(ToolFunction.builder()
                          .arguments("{\"location\":")
                          .build())
                      .build()))
                  .build())
              .build()))
          .build());

      // 세 번째 청크: arguments 계속
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .toolCalls(List.of(ToolCall.builder()
                      .index(0)
                      .function(ToolFunction.builder()
                          .arguments("\"Seoul\"}")
                          .build())
                      .build()))
                  .build())
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getToolCalls()).hasSize(1);

      ToolCall toolCall = message.getToolCalls().get(0);
      assertThat(toolCall.getId()).isEqualTo("call_abc123");
      assertThat(toolCall.getType()).isEqualTo("function");
      assertThat(toolCall.getFunction().getName()).isEqualTo("get_weather");
      assertThat(toolCall.getFunction().getArguments()).isEqualTo("{\"location\":\"Seoul\"}");
    }

    @Test
    @DisplayName("여러 toolCalls 병합")
    void shouldMergeMultipleToolCalls() {
      // 두 개의 tool call
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .toolCalls(List.of(
                      ToolCall.builder()
                          .index(0)
                          .id("call_1")
                          .type("function")
                          .function(ToolFunction.builder()
                              .name("func1")
                              .arguments("{}")
                              .build())
                          .build(),
                      ToolCall.builder()
                          .index(1)
                          .id("call_2")
                          .type("function")
                          .function(ToolFunction.builder()
                              .name("func2")
                              .arguments("{}")
                              .build())
                          .build()))
                  .build())
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      assertThat(message.getToolCalls()).hasSize(2);
      assertThat(message.getToolCalls().get(0).getFunction().getName()).isEqualTo("func1");
      assertThat(message.getToolCalls().get(1).getFunction().getName()).isEqualTo("func2");
    }
  }

  @Nested
  @DisplayName("Reasoning 스트리밍")
  class ReasoningStreaming {

    @Test
    @DisplayName("reasoning이 DeltaMerger에 의해 병합됨")
    void shouldMergeReasoning() {
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .reasoning("생각 중...")
                  .build())
              .build()))
          .build());

      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .reasoning(" 계속 생각...")
                  .build())
              .build()))
          .build());

      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .content("최종 답변")
                  .build())
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();
      ResponseMessage message = getMessageOrDelta(response);

      assertThat(message.getRole()).isEqualTo("assistant");
      assertThat(message.getReasoning()).isEqualTo("생각 중... 계속 생각...");
      assertThat(message.getContent()).isEqualTo("최종 답변");
    }
  }

  @Nested
  @DisplayName("finishReason 처리")
  class FinishReasonHandling {

    @Test
    @DisplayName("마지막 청크의 finishReason이 유지됨")
    void shouldPreserveFinishReason() {
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .content("Hello")
                  .build())
              .build()))
          .build());

      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().build())
              .finishReason("stop")
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();

      assertThat(response.getChoices().get(0).getFinishReason()).isEqualTo("stop");
    }

    @Test
    @DisplayName("finishReason이 flush와 함께 전달됨 - 닫히지 않은 cite 태그")
    void shouldPreserveFinishReasonWithFlush() {
      // cite 태그가 닫히지 않은 상태에서 finishReason이 오는 경우
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .content("<cite><id>doc1</id>인용 텍스트")
                  .build())
              .build()))
          .build());

      // finishReason이 있는 마지막 청크 (cite 닫히지 않음)
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder().build())
              .finishReason("stop")
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();

      // flush에서 citation이 생성되고 finishReason도 유지되어야 함
      CitedMessage message = (CitedMessage) getMessageOrDelta(response);
      assertThat(message.getContent()).isEqualTo("인용 텍스트");
      assertThat(message.getCitations()).hasSize(1);
      assertThat(response.getChoices().get(0).getFinishReason()).isEqualTo("stop");
    }

    @Test
    @DisplayName("tool_calls finishReason")
    void shouldHandleToolCallsFinishReason() {
      chunks.add(ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .toolCalls(List.of(ToolCall.builder()
                      .index(0)
                      .id("call_1")
                      .type("function")
                      .function(ToolFunction.builder()
                          .name("test")
                          .arguments("{}")
                          .build())
                      .build()))
                  .build())
              .finishReason("tool_calls")
              .build()))
          .build());

      EnhancedCompletionResponse response = getMergedResult();

      assertThat(response.getChoices().get(0).getFinishReason()).isEqualTo("tool_calls");
    }
  }

  @Nested
  @DisplayName("DeltaMapper 스트리밍 delta 반환")
  class StreamingDeltaOutput {

    @Test
    @DisplayName("map()이 스트리밍용 delta를 반환해야 함")
    void shouldReturnStreamingDeltas() {
      ChatCompletionResponse chunk = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .content("Hello")
                  .build())
              .build()))
          .build();

      List<EnhancedCompletionResponse> deltas = mapper.map(chunk);

      // role delta + content delta
      assertThat(deltas).hasSizeGreaterThanOrEqualTo(1);
      boolean hasContentDelta = deltas.stream()
          .anyMatch(d -> "Hello".equals(d.getChoices().get(0).getDelta().getContent()));
      assertThat(hasContentDelta).isTrue();
    }

    @Test
    @DisplayName("null content일 때 role만 반환")
    void shouldReturnRoleDeltaForNullContent() {
      ChatCompletionResponse chunk = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .role("assistant")
                  .content(null)
                  .build())
              .build()))
          .build();

      List<EnhancedCompletionResponse> deltas = mapper.map(chunk);

      // role delta만 반환
      assertThat(deltas).hasSize(1);
      assertThat(deltas.get(0).getChoices().get(0).getDelta().getRole()).isEqualTo("assistant");
    }

    @Test
    @DisplayName("citation 완료 시 citation delta 반환")
    void shouldReturnCitationDelta() {
      ChatCompletionResponse chunk1 = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .content("<cite><id>doc1</id>text</cit")
                  .build())
              .build()))
          .build();

      ChatCompletionResponse chunk2 = ChatCompletionResponse.builder()
          .id("chatcmpl-123")
          .object("chat.completion.chunk")
          .created(1234567890L)
          .model("gpt-4")
          .choices(List.of(BaseCompletionResponse.Choice.<ResponseMessage>builder()
              .index(0)
              .delta(ResponseMessage.builder()
                  .content("e>")
                  .build())
              .build()))
          .build();

      mapper.map(chunk1);
      List<EnhancedCompletionResponse> deltas = mapper.map(chunk2);

      // 마지막 delta에 citation이 포함됨
      boolean hasCitationDelta = deltas.stream()
          .anyMatch(d -> d.getChoices().get(0).getDelta().getCitations() != null
              && !d.getChoices().get(0).getDelta().getCitations().isEmpty());
      assertThat(hasCitationDelta).isTrue();
    }
  }
}
