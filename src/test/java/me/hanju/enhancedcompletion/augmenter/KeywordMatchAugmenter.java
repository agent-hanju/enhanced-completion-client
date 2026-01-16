package me.hanju.enhancedcompletion.augmenter;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.Message;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.spi.augment.SimpleAugmentResult;
import reactor.core.publisher.Flux;

/**
 * 키워드 기반 간단한 Augmenter.
 * 외부 의존성 없이 키워드 매칭으로 문서를 검색합니다.
 */
public class KeywordMatchAugmenter implements Augmenter {

  private final String name;
  private final Map<String, List<IDocument>> keywordIndex;
  private final int maxResults;

  /**
   * KeywordMatchAugmenter를 생성합니다.
   *
   * @param name       Augmenter 이름
   * @param maxResults 최대 반환 문서 수
   */
  public KeywordMatchAugmenter(final String name, final int maxResults) {
    this.name = name;
    this.keywordIndex = new ConcurrentHashMap<>();
    this.maxResults = maxResults;
  }

  /**
   * 문서를 키워드와 함께 인덱싱합니다.
   *
   * @param document 인덱싱할 문서
   * @param keywords 연관 키워드 목록
   */
  public void indexDocument(final IDocument document, final List<String> keywords) {
    for (final String keyword : keywords) {
      keywordIndex.computeIfAbsent(
          keyword.toLowerCase(Locale.ROOT),
          k -> new ArrayList<>()).add(document);
    }
  }

  /**
   * 문서를 제목과 내용에서 자동으로 키워드를 추출하여 인덱싱합니다.
   *
   * @param document 인덱싱할 문서
   */
  public void indexDocument(final IDocument document) {
    final List<String> keywords = extractKeywords(document);
    indexDocument(document, keywords);
  }

  private List<String> extractKeywords(final IDocument document) {
    final List<String> keywords = new ArrayList<>();

    // 제목에서 키워드 추출 (공백 기준 분리)
    if (document.getTitle() != null) {
      for (final String word : document.getTitle().split("\\s+")) {
        final String cleaned = word.replaceAll("[^a-zA-Z0-9가-힣]", "").toLowerCase(Locale.ROOT);
        if (cleaned.length() >= 2) {
          keywords.add(cleaned);
        }
      }
    }

    return keywords;
  }

  @Override
  public String getName() {
    return name;
  }

  @Override
  public Flux<AugmentResult> augment(final ChatCompletionRequest request) {
    final String query = resolveQuery(request).toLowerCase(Locale.ROOT);
    final List<IDocument> matchedDocs = new ArrayList<>();

    for (final Map.Entry<String, List<IDocument>> entry : keywordIndex.entrySet()) {
      if (query.contains(entry.getKey())) {
        for (final IDocument doc : entry.getValue()) {
          if (!containsDoc(matchedDocs, doc) && matchedDocs.size() < maxResults) {
            matchedDocs.add(doc);
          }
        }
      }
    }

    if (matchedDocs.isEmpty()) {
      return Flux.empty();
    }

    return Flux.just(SimpleAugmentResult.builder()
        .documents(List.copyOf(matchedDocs))
        .build());
  }

  private boolean containsDoc(final List<IDocument> docs, final IDocument target) {
    if (target.getId() == null) {
      return false;
    }
    return docs.stream().anyMatch(d -> target.getId().equals(d.getId()));
  }

  private String resolveQuery(final ChatCompletionRequest request) {
    final List<Message> messages = request.getMessages();
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
   * 인덱싱된 키워드 수를 반환합니다.
   */
  public int getIndexedKeywordCount() {
    return keywordIndex.size();
  }

  /**
   * 인덱스를 초기화합니다.
   */
  public void clearIndex() {
    keywordIndex.clear();
  }
}
