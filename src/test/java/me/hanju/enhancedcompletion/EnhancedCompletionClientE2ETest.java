package me.hanju.enhancedcompletion;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import com.fasterxml.jackson.databind.ObjectMapper;

import me.hanju.enhancedcompletion.augmenter.MockAugmenter;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.document.SimpleDocument;
import me.hanju.enhancedcompletion.payload.message.BaseMessage;
import me.hanju.fluxhandle.FluxListener;

/**
 * 실제 vLLM 서버를 대상으로 하는 E2E 테스트.
 * 테스트 실행 시 vLLM 서버가 실행 중이어야 합니다.
 */
// @Disabled("실제 vLLM 서버 필요 - 수동 테스트용")
@DisplayName("EnhancedCompletionClient E2E 테스트")
class EnhancedCompletionClientE2ETest {

  private static final String BASE_URL = "http://172.16.100.200:14100";
  private static final String MODEL = "luxia3-llm-32b-0901-Q";

  private EnhancedCompletionClient client;

  @BeforeEach
  void setUp() {
    EnhancedCompletionProperties properties = new EnhancedCompletionProperties(BASE_URL, null);
    client = new EnhancedCompletionClient(
        WebClient.builder(),
        new ObjectMapper(),
        properties);
  }

  @Test
  @DisplayName("스트리밍 요청이 정상적으로 동작해야 함")
  void streamingShouldWork() {
    // Given
    EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
        .model(MODEL)
        .messages(List.of(
            BaseMessage.builder()
                .role("user")
                .content("안녕하세요. 간단히 자기소개 해주세요.")
                .build()))
        .maxTokens(100)
        .build();

    StringBuilder responseContent = new StringBuilder();

    // When
    var handle = client.stream(request, new FluxListener<>() {
      @Override
      public void onNext(EnhancedCompletionResponse delta) {
        if (delta.getChoices() != null && !delta.getChoices().isEmpty()) {
          var choice = delta.getChoices().get(0);
          var message = choice.getDelta();
          if (message != null && message.getContent() != null) {
            System.out.print(message.getContent());
            responseContent.append(message.getContent());
          }
        }
      }

      @Override
      public void onComplete() {
        System.out.println("\n[완료]");
      }

      @Override
      public void onError(Throwable e) {
        System.err.println("에러: " + e.getMessage());
      }

      @Override
      public void onCancel() {
        System.out.println("[취소됨]");
      }
    });

    EnhancedCompletionResponse result = handle.get();

    // Then
    assertThat(result).isNotNull();
    assertThat(result.getChoices()).isNotEmpty();
    System.out.println("\n최종 응답: " + result.getChoices().get(0).getDelta().getContent());
  }

  @Test
  @DisplayName("비스트리밍 complete() 메서드가 정상 동작해야 함")
  void completeShouldWork() {
    // Given
    EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
        .model(MODEL)
        .messages(List.of(
            BaseMessage.builder()
                .role("user")
                .content("1+1은?")
                .build()))
        .maxTokens(50)
        .build();

    // When
    EnhancedCompletionResponse result = client.complete(request);

    // Then
    assertThat(result).isNotNull();
    assertThat(result.getChoices()).isNotEmpty();
    System.out.println("응답: " + result.getChoices().get(0).getDelta().getContent());
  }

  @Nested
  @DisplayName("MockAugmenter + vLLM 통합 테스트")
  class AugmenterIntegrationTest {

    @Test
    @DisplayName("MockAugmenter로 RAG 문서 주입 후 Completion")
    void shouldInjectDocumentsAndComplete() {
      // Given - RAG 문서 설정
      MockAugmenter augmenter = MockAugmenter.builder()
          .name("test-augmenter")
          .documents(List.of(
              SimpleDocument.builder()
                  .id("doc-1")
                  .title("회사 정보")
                  .content("우리 회사의 이름은 '한주테크'이고, 2024년에 설립되었습니다.")
                  .build()))
          .build();

      EnhancedCompletionRequest request = EnhancedCompletionRequest.builder()
          .model(MODEL)
          .messages(List.of(
              BaseMessage.builder()
                  .role("user")
                  .content("회사 이름이 뭐야?")
                  .build()))
          .augmenter(augmenter)
          .maxTokens(100)
          .build();

      // When
      var handle = client.stream(request, new FluxListener<>() {
        @Override
        public void onNext(EnhancedCompletionResponse delta) {
          if (delta.getChoices() != null && !delta.getChoices().isEmpty()) {
            var message = delta.getChoices().get(0).getDelta();
            if (message != null && message.getContent() != null) {
              System.out.print(message.getContent());
            }
          }
          // AugmentResult delta 출력
          if (delta.getAugmentResult() != null) {
            System.out.println("[RAG] 문서 수신: " + delta.getAugmentResult().getDocuments().size() + "개");
          }
        }

        @Override
        public void onComplete() {
          System.out.println("\n[완료]");
        }

        @Override
        public void onError(Throwable e) {
          System.err.println("에러: " + e.getMessage());
        }

        @Override
        public void onCancel() {
          System.out.println("[취소됨]");
        }
      });

      EnhancedCompletionResponse result = handle.get();

      // Then
      assertThat(result).isNotNull();
      assertThat(result.getAugmentResult()).isNotNull();
      assertThat(result.getAugmentResult().getDocuments()).hasSize(1);
      assertThat(result.getChoices()).isNotEmpty();

      String content = result.getChoices().get(0).getDelta().getContent();
      System.out.println("\n최종 응답: " + content);
      // RAG 문서 기반으로 "한주테크"가 포함되어야 함
      assertThat(content).containsIgnoringCase("한주테크");
    }
  }
}
