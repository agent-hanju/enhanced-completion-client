package me.hanju.enhancedcompletion.assembler;

import java.util.List;

import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.streambind.map.StreamMapper;

/**
 * AugmentResult를 EnhancedCompletionResponse로 변환하는 StreamMapper.
 * RAG 결과를 있는 그대로 EnhancedCompletionResponse.augmentResult에 담아 반환합니다.
 */
public class AugmentResultDeltaMapper
    implements StreamMapper<AugmentResult, EnhancedCompletionResponse> {

  @Override
  public List<EnhancedCompletionResponse> map(final AugmentResult delta) {
    if (delta == null) {
      return List.of();
    }

    final SimpleAugmentResult simpleResult;
    if (delta instanceof SimpleAugmentResult) {
      simpleResult = (SimpleAugmentResult) delta;
    } else {
      simpleResult = SimpleAugmentResult.builder()
          .documents(List.copyOf(delta.getDocuments()))
          .build();
    }

    return List.of(EnhancedCompletionResponse.builder()
        .augmentResult(simpleResult)
        .build());
  }
}
