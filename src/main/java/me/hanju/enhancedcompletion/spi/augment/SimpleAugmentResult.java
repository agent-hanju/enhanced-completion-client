package me.hanju.enhancedcompletion.spi.augment;

import java.util.ArrayList;
import java.util.List;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import me.hanju.enhancedcompletion.payload.document.IDocument;

/**
 * AugmentResult의 기본 구현체.
 * DeltaMerger가 리플렉션으로 인스턴스를 생성할 수 있도록 기본 생성자를 제공합니다.
 */
@Builder
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PUBLIC)
@AllArgsConstructor(access = AccessLevel.PRIVATE)
@EqualsAndHashCode
@ToString
public class SimpleAugmentResult implements AugmentResult {

  @Builder.Default
  private List<IDocument> documents = new ArrayList<>();

  @Override
  public List<? extends IDocument> getDocuments() {
    return documents;
  }
}
