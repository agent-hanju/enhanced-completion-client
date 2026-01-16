package me.hanju.enhancedcompletion.spi.augment;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonTypeInfo;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;

import me.hanju.enhancedcompletion.payload.document.IDocument;

/**
 * Augmenter 수행 결과 인터페이스.
 * 검색된 문서 목록을 제공합니다.
 */
@JsonTypeInfo(use = JsonTypeInfo.Id.DEDUCTION, defaultImpl = SimpleAugmentResult.class)
@JsonDeserialize(as = SimpleAugmentResult.class)
public interface AugmentResult {

  /**
   * 검색된 문서 목록을 반환합니다.
   *
   * @return 문서 목록
   */
  List<? extends IDocument> getDocuments();
}
