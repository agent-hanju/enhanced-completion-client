package me.hanju.enhancedcompletion.augmenter;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.payload.document.SimpleDocument;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;

@DisplayName("Augmenter 테스트")
class AugmenterTest {

  private static ChatCompletionRequest createRequest(String userMessage) {
    return ChatCompletionRequest.builder()
        .messages(List.of(Message.builder().role("user").content(userMessage).build()))
        .build();
  }

  @Nested
  @DisplayName("MockAugmenter")
  class MockAugmenterTest {

    @Test
    @DisplayName("기본 생성자로 생성 시 기본 문서 반환")
    void shouldReturnDefaultDocuments() {
      // Given
      MockAugmenter augmenter = new MockAugmenter();
      ChatCompletionRequest request = createRequest("test query");

      // When
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(augmenter.getName()).isEqualTo("mock-augmenter");
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(2);
    }

    @Test
    @DisplayName("빌더로 커스텀 문서 설정")
    void shouldReturnCustomDocuments() {
      // Given
      List<IDocument> customDocs = List.of(
          SimpleDocument.builder().id("custom-1").title("Custom Doc").content("Custom content").build());

      MockAugmenter augmenter = MockAugmenter.builder()
          .name("custom-mock")
          .documents(customDocs)
          .build();

      ChatCompletionRequest request = createRequest("query");

      // When
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(augmenter.getName()).isEqualTo("custom-mock");
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(1);
      assertThat(results.get(0).getDocuments().get(0).getId()).isEqualTo("custom-1");
    }

    @Test
    @DisplayName("streamIndividually=true 시 문서를 개별 스트리밍")
    void shouldStreamDocumentsIndividually() {
      // Given
      List<IDocument> docs = List.of(
          SimpleDocument.builder().id("doc-1").title("Doc 1").content("Content 1").build(),
          SimpleDocument.builder().id("doc-2").title("Doc 2").content("Content 2").build(),
          SimpleDocument.builder().id("doc-3").title("Doc 3").content("Content 3").build());

      MockAugmenter augmenter = MockAugmenter.builder()
          .documents(docs)
          .streamIndividually(true)
          .build();

      ChatCompletionRequest request = createRequest("query");
      AtomicInteger streamCount = new AtomicInteger(0);

      // When
      List<AugmentResult> results = augmenter.augment(request)
          .doOnNext(r -> streamCount.incrementAndGet())
          .collectList()
          .block();

      // Then - 3개의 개별 결과가 스트리밍됨
      assertThat(results).hasSize(3);
      assertThat(streamCount.get()).isEqualTo(3);
      assertThat(results.get(0).getDocuments()).hasSize(1);
      assertThat(results.get(0).getDocuments().get(0).getId()).isEqualTo("doc-1");
    }

    @Test
    @DisplayName("delay 설정 시 지연 후 반환")
    void shouldDelayResults() {
      // Given
      MockAugmenter augmenter = MockAugmenter.builder()
          .delay(Duration.ofMillis(100))
          .build();

      ChatCompletionRequest request = createRequest("query");
      long startTime = System.currentTimeMillis();

      // When
      augmenter.augment(request).blockLast();
      long elapsed = System.currentTimeMillis() - startTime;

      // Then
      assertThat(elapsed).isGreaterThanOrEqualTo(100);
    }
  }

  @Nested
  @DisplayName("KeywordMatchAugmenter")
  class KeywordMatchAugmenterTest {

    @Test
    @DisplayName("키워드로 인덱싱 후 검색")
    void shouldSearchByKeyword() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);

      IDocument doc1 = SimpleDocument.builder()
          .id("doc-1")
          .title("Java Guide")
          .content("Java programming guide")
          .build();

      IDocument doc2 = SimpleDocument.builder()
          .id("doc-2")
          .title("Python Guide")
          .content("Python programming guide")
          .build();

      augmenter.indexDocument(doc1, List.of("java", "programming"));
      augmenter.indexDocument(doc2, List.of("python", "programming"));

      // When - "java" 키워드로 검색
      ChatCompletionRequest request = createRequest("I want to learn java");
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(1);
      assertThat(results.get(0).getDocuments().get(0).getId()).isEqualTo("doc-1");
    }

    @Test
    @DisplayName("여러 키워드 매칭 시 중복 제거")
    void shouldDeduplicateResults() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);

      IDocument doc = SimpleDocument.builder()
          .id("doc-1")
          .title("Java Spring Guide")
          .content("Java Spring programming guide")
          .build();

      // 같은 문서를 여러 키워드로 인덱싱
      augmenter.indexDocument(doc, List.of("java", "spring", "programming"));

      // When - 여러 키워드가 쿼리에 포함됨
      ChatCompletionRequest request = createRequest("java spring tutorial");
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then - 중복 제거되어 1개만 반환
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(1);
    }

    @Test
    @DisplayName("자동 키워드 추출")
    void shouldExtractKeywordsAutomatically() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);

      IDocument doc = SimpleDocument.builder()
          .id("doc-1")
          .title("Machine Learning Basics")
          .content("Introduction to ML")
          .build();

      augmenter.indexDocument(doc); // 자동 키워드 추출

      // When
      ChatCompletionRequest request = createRequest("teach me machine learning");
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments().get(0).getId()).isEqualTo("doc-1");
    }

    @Test
    @DisplayName("매칭되는 키워드 없으면 빈 결과")
    void shouldReturnEmptyWhenNoMatch() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);
      augmenter.indexDocument(
          SimpleDocument.builder().id("doc-1").title("Java Guide").content("Java").build(),
          List.of("java"));

      // When
      ChatCompletionRequest request = createRequest("python tutorial");
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(results).isEmpty();
    }

    @Test
    @DisplayName("maxResults 제한 적용")
    void shouldLimitResults() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 2);

      for (int i = 1; i <= 5; i++) {
        augmenter.indexDocument(
            SimpleDocument.builder()
                .id("doc-" + i)
                .title("Doc " + i)
                .content("Content")
                .build(),
            List.of("common"));
      }

      // When
      ChatCompletionRequest request = createRequest("common keyword");
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then - maxResults=2로 제한됨
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(2);
    }

    @Test
    @DisplayName("대소문자 구분 없이 검색")
    void shouldSearchCaseInsensitively() {
      // Given
      KeywordMatchAugmenter augmenter = new KeywordMatchAugmenter("keyword-aug", 5);
      augmenter.indexDocument(
          SimpleDocument.builder().id("doc-1").title("Test").content("Test").build(),
          List.of("Java") // 대문자
      );

      // When
      ChatCompletionRequest request = createRequest("JAVA programming"); // 대문자 쿼리
      List<AugmentResult> results = augmenter.augment(request).collectList().block();

      // Then
      assertThat(results).hasSize(1);
    }
  }

  @Nested
  @DisplayName("CompositeAugmenter")
  class CompositeAugmenterTest {

    @Test
    @DisplayName("여러 Augmenter 결과 병합")
    void shouldMergeResultsFromMultipleAugmenters() {
      // Given
      MockAugmenter mock1 = MockAugmenter.builder()
          .name("mock1")
          .documents(List.of(
              SimpleDocument.builder().id("m1-doc1").title("Mock1 Doc1").content("Content").build()))
          .build();

      MockAugmenter mock2 = MockAugmenter.builder()
          .name("mock2")
          .documents(List.of(
              SimpleDocument.builder().id("m2-doc1").title("Mock2 Doc1").content("Content").build()))
          .build();

      CompositeAugmenter composite = CompositeAugmenter.builder()
          .name("composite")
          .addAugmenter(mock1)
          .addAugmenter(mock2)
          .maxTotalDocuments(10)
          .build();

      // When
      ChatCompletionRequest request = createRequest("query");
      List<AugmentResult> results = composite.augment(request).collectList().block();

      // Then
      assertThat(composite.getName()).isEqualTo("composite");
      assertThat(composite.getAugmenterCount()).isEqualTo(2);
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(2);
    }

    @Test
    @DisplayName("중복 ID 제거")
    void shouldDeduplicateById() {
      // Given - 같은 ID를 가진 문서를 반환하는 두 Augmenter
      IDocument sharedDoc = SimpleDocument.builder()
          .id("shared-id")
          .title("Shared Doc")
          .content("Content")
          .build();

      MockAugmenter mock1 = MockAugmenter.builder()
          .documents(List.of(sharedDoc))
          .build();

      MockAugmenter mock2 = MockAugmenter.builder()
          .documents(List.of(sharedDoc))
          .build();

      CompositeAugmenter composite = CompositeAugmenter.builder()
          .addAugmenter(mock1)
          .addAugmenter(mock2)
          .maxTotalDocuments(10)
          .build();

      // When
      ChatCompletionRequest request = createRequest("query");
      List<AugmentResult> results = composite.augment(request).collectList().block();

      // Then - 중복 제거되어 1개만 반환
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(1);
    }

    @Test
    @DisplayName("maxTotalDocuments 제한")
    void shouldLimitTotalDocuments() {
      // Given
      MockAugmenter mock1 = MockAugmenter.builder()
          .documents(List.of(
              SimpleDocument.builder().id("m1-1").title("1").content("1").build(),
              SimpleDocument.builder().id("m1-2").title("2").content("2").build(),
              SimpleDocument.builder().id("m1-3").title("3").content("3").build()))
          .build();

      MockAugmenter mock2 = MockAugmenter.builder()
          .documents(List.of(
              SimpleDocument.builder().id("m2-1").title("1").content("1").build(),
              SimpleDocument.builder().id("m2-2").title("2").content("2").build()))
          .build();

      CompositeAugmenter composite = CompositeAugmenter.builder()
          .addAugmenter(mock1)
          .addAugmenter(mock2)
          .maxTotalDocuments(3) // 총 5개 중 3개만
          .build();

      // When
      ChatCompletionRequest request = createRequest("query");
      List<AugmentResult> results = composite.augment(request).collectList().block();

      // Then
      assertThat(results).hasSize(1);
      assertThat(results.get(0).getDocuments()).hasSize(3);
    }

    @Test
    @DisplayName("모든 Augmenter가 빈 결과면 빈 Flux 반환")
    void shouldReturnEmptyWhenAllAugmentersReturnEmpty() {
      // Given
      KeywordMatchAugmenter keyword1 = new KeywordMatchAugmenter("k1", 5);
      KeywordMatchAugmenter keyword2 = new KeywordMatchAugmenter("k2", 5);
      // 아무것도 인덱싱하지 않음

      CompositeAugmenter composite = CompositeAugmenter.builder()
          .addAugmenter(keyword1)
          .addAugmenter(keyword2)
          .build();

      // When
      ChatCompletionRequest request = createRequest("query");
      List<AugmentResult> results = composite.augment(request).collectList().block();

      // Then
      assertThat(results).isEmpty();
    }
  }

  @Nested
  @DisplayName("IDocument")
  class IDocumentTest {

    @Test
    @DisplayName("toSerializedPrompt 형식 확인")
    void shouldSerializeToPromptFormat() {
      // Given
      IDocument doc = SimpleDocument.builder()
          .id("doc-123")
          .title("Test Document")
          .content("This is the content.")
          .url("https://example.com")
          .build();

      // When
      String prompt = doc.toSerializedPrompt();

      // Then
      assertThat(prompt).contains("<document id=\"doc-123\">");
      assertThat(prompt).contains("<title>Test Document</title>");
      assertThat(prompt).contains("<content>This is the content.</content>");
      assertThat(prompt).contains("</document>");
    }

    @Test
    @DisplayName("제목이 없으면 title 태그 생략")
    void shouldOmitTitleWhenNull() {
      // Given
      IDocument doc = SimpleDocument.builder()
          .id("doc-123")
          .content("Content only")
          .build();

      // When
      String prompt = doc.toSerializedPrompt();

      // Then
      assertThat(prompt).doesNotContain("<title>");
      assertThat(prompt).contains("<content>Content only</content>");
    }
  }
}
