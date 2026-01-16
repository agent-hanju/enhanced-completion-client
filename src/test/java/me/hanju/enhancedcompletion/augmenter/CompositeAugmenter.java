package me.hanju.enhancedcompletion.augmenter;

import java.util.List;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import reactor.core.publisher.Flux;

/**
 * 여러 Augmenter를 조합하여 병렬로 실행하고 결과를 병합하는 Augmenter.
 */
public class CompositeAugmenter implements Augmenter {

  private final String name;
  private final List<Augmenter> augmenters;
  private final int maxTotalDocuments;

  /**
   * CompositeAugmenter를 생성합니다.
   *
   * @param name              Augmenter 이름
   * @param augmenters        조합할 Augmenter 목록
   * @param maxTotalDocuments 최대 총 문서 수
   */
  public CompositeAugmenter(
      final String name,
      final List<Augmenter> augmenters,
      final int maxTotalDocuments) {
    this.name = name;
    this.augmenters = List.copyOf(augmenters);
    this.maxTotalDocuments = maxTotalDocuments;
  }

  @Override
  public String getName() {
    return name;
  }

  @Override
  public Flux<AugmentResult> augment(final ChatCompletionRequest request) {
    // 모든 Augmenter를 병렬로 실행
    return Flux.fromIterable(augmenters)
        .flatMap(augmenter -> augmenter.augment(request))
        .flatMapIterable(AugmentResult::getDocuments)
        .distinct(IDocument::getId) // ID 기반 중복 제거
        .take(maxTotalDocuments)
        .collectList()
        .filter(docs -> !docs.isEmpty())
        .<AugmentResult>map(docs -> SimpleAugmentResult.builder()
            .documents(List.copyOf(docs))
            .build())
        .flux();
  }

  /**
   * 조합된 Augmenter 수를 반환합니다.
   */
  public int getAugmenterCount() {
    return augmenters.size();
  }

  /**
   * Builder를 반환합니다.
   */
  public static Builder builder() {
    return new Builder();
  }

  /**
   * CompositeAugmenter Builder.
   */
  public static class Builder {
    private String name = "composite-augmenter";
    private final java.util.ArrayList<Augmenter> augmenters = new java.util.ArrayList<>();
    private int maxTotalDocuments = 10;

    public Builder name(final String name) {
      this.name = name;
      return this;
    }

    public Builder addAugmenter(final Augmenter augmenter) {
      this.augmenters.add(augmenter);
      return this;
    }

    public Builder augmenters(final List<Augmenter> augmenters) {
      this.augmenters.clear();
      this.augmenters.addAll(augmenters);
      return this;
    }

    public Builder maxTotalDocuments(final int maxTotalDocuments) {
      this.maxTotalDocuments = maxTotalDocuments;
      return this;
    }

    public CompositeAugmenter build() {
      return new CompositeAugmenter(name, augmenters, maxTotalDocuments);
    }
  }
}
