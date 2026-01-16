package me.hanju.enhancedcompletion.spi.augment;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import reactor.core.publisher.Flux;

/**
 * RAG(Retrieval-Augmented Generation) Augmenter 인터페이스.
 * 문서 검색 및 증강 기능을 제공합니다.
 */
public interface Augmenter {

  /**
   * Augmenter 이름을 반환합니다.
   *
   * @return Augmenter 이름
   */
  String getName();

  /**
   * 문서 검색 및 증강을 수행합니다 (스트리밍).
   * 중간 결과를 순차적으로 emit하고, 마지막에 complete됩니다.
   *
   * @param request Chat Completion 요청
   * @return 검색 결과 스트림
   */
  Flux<AugmentResult> augment(ChatCompletionRequest request);
}
