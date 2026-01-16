package me.hanju.enhancedcompletion.augmenter;

import java.time.Duration;
import java.util.List;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.payload.document.SimpleDocument;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import reactor.core.publisher.Flux;

/**
 * 테스트 및 개발용 Mock Augmenter.
 * 미리 정의된 문서를 반환하며, 네트워크 지연을 시뮬레이션할 수 있습니다.
 */
public class MockAugmenter implements Augmenter {

  private final String name;
  private final List<IDocument> documents;
  private final Duration delayBetweenDocuments;
  private final boolean streamIndividually;

  /**
   * 기본 설정으로 MockAugmenter를 생성합니다.
   */
  public MockAugmenter() {
    this("mock-augmenter", createDefaultDocuments(), Duration.ZERO, false);
  }

  /**
   * 커스텀 설정으로 MockAugmenter를 생성합니다.
   *
   * @param name                  Augmenter 이름
   * @param documents             반환할 문서 목록
   * @param delayBetweenDocuments 문서 간 지연 시간
   * @param streamIndividually    true면 문서를 개별적으로 스트리밍
   */
  public MockAugmenter(
      final String name,
      final List<IDocument> documents,
      final Duration delayBetweenDocuments,
      final boolean streamIndividually) {
    this.name = name;
    this.documents = List.copyOf(documents);
    this.delayBetweenDocuments = delayBetweenDocuments;
    this.streamIndividually = streamIndividually;
  }

  @Override
  public String getName() {
    return name;
  }

  @Override
  public Flux<AugmentResult> augment(final ChatCompletionRequest request) {
    if (streamIndividually) {
      // 각 문서를 개별 결과로 스트리밍 (스트리밍 테스트용)
      Flux<IDocument> docFlux = Flux.fromIterable(documents);
      if (!delayBetweenDocuments.isZero()) {
        docFlux = docFlux.delayElements(delayBetweenDocuments);
      }
      return docFlux.map(doc -> SimpleAugmentResult.builder()
          .documents(List.of(doc))
          .build());
    } else {
      // 모든 문서를 단일 결과로 반환
      final AugmentResult result = SimpleAugmentResult.builder()
          .documents(List.copyOf(documents))
          .build();
      if (delayBetweenDocuments.isZero()) {
        return Flux.just(result);
      }
      return Flux.just(result).delayElements(delayBetweenDocuments);
    }
  }

  private static List<IDocument> createDefaultDocuments() {
    return List.of(
        SimpleDocument.builder()
            .id("doc-1")
            .title("Introduction to RAG")
            .content("Retrieval-Augmented Generation (RAG) combines retrieval with generation "
                + "to enhance LLM responses with external knowledge.")
            .url("https://example.com/rag-intro")
            .build(),
        SimpleDocument.builder()
            .id("doc-2")
            .title("Vector Databases")
            .content("Vector databases store embeddings for efficient semantic search. "
                + "Popular options include Pinecone, Weaviate, and Qdrant.")
            .url("https://example.com/vector-db")
            .build());
  }

  /**
   * Builder를 반환합니다.
   */
  public static Builder builder() {
    return new Builder();
  }

  /**
   * MockAugmenter Builder.
   */
  public static class Builder {
    private String name = "mock-augmenter";
    private List<IDocument> documents = createDefaultDocuments();
    private Duration delay = Duration.ZERO;
    private boolean streamIndividually = false;

    public Builder name(final String name) {
      this.name = name;
      return this;
    }

    public Builder documents(final List<IDocument> documents) {
      this.documents = documents;
      return this;
    }

    public Builder delay(final Duration delay) {
      this.delay = delay;
      return this;
    }

    public Builder streamIndividually(final boolean streamIndividually) {
      this.streamIndividually = streamIndividually;
      return this;
    }

    public MockAugmenter build() {
      return new MockAugmenter(name, documents, delay, streamIndividually);
    }
  }
}
