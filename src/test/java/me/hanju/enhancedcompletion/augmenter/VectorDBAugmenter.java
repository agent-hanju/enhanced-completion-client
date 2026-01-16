package me.hanju.enhancedcompletion.augmenter;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * 벡터 데이터베이스 연동을 위한 추상 Augmenter.
 * Pinecone, Weaviate, Qdrant, Milvus 등과 연동하려면 이 클래스를 상속합니다.
 */
public abstract class VectorDBAugmenter implements Augmenter {

  private final String name;
  private final int topK;
  private final float similarityThreshold;

  /**
   * VectorDBAugmenter를 생성합니다.
   *
   * @param name                Augmenter 이름
   * @param topK                검색할 최대 문서 수
   * @param similarityThreshold 최소 유사도 임계값 (0.0 ~ 1.0)
   */
  protected VectorDBAugmenter(
      final String name,
      final int topK,
      final float similarityThreshold) {
    this.name = name;
    this.topK = topK;
    this.similarityThreshold = similarityThreshold;
  }

  @Override
  public String getName() {
    return name;
  }

  @Override
  public Flux<AugmentResult> augment(final ChatCompletionRequest request) {
    return embedQuery(resolveQuery(request))
        .flatMapMany(embedding -> searchSimilar(embedding, topK, similarityThreshold))
        .collectList()
        .filter(docs -> !docs.isEmpty())
        .<AugmentResult>map(docs -> SimpleAugmentResult.builder()
            .documents(java.util.List.copyOf(docs))
            .build())
        .flux();
  }

  private String resolveQuery(final ChatCompletionRequest request) {
    final java.util.List<Message> messages = request.getMessages();
    if (messages != null) {
      for (int i = messages.size() - 1; i >= 0; i--) {
        final Message msg = messages.get(i);
        if ("user".equals(msg.getRole()) && msg.getContent() != null) {
          return msg.getContent();
        }
      }
    }
    return "";
  }

  /**
   * 쿼리 텍스트를 임베딩 벡터로 변환합니다.
   * OpenAI, Cohere, 로컬 모델 등 임베딩 제공자와 연동하여 구현합니다.
   *
   * @param query 쿼리 텍스트
   * @return 임베딩 벡터
   */
  protected abstract Mono<float[]> embedQuery(String query);

  /**
   * 벡터 데이터베이스에서 유사한 문서를 검색합니다.
   *
   * @param embedding 쿼리 임베딩 벡터
   * @param topK      최대 검색 결과 수
   * @param threshold 최소 유사도 임계값
   * @return 유사한 문서 스트림
   */
  protected abstract Flux<IDocument> searchSimilar(float[] embedding, int topK, float threshold);

  /**
   * topK 값을 반환합니다.
   */
  protected int getTopK() {
    return topK;
  }

  /**
   * 유사도 임계값을 반환합니다.
   */
  protected float getSimilarityThreshold() {
    return similarityThreshold;
  }
}
