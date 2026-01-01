package me.hanju.enhancedcompletion.payload.message;

import java.util.ArrayList;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;
import lombok.experimental.SuperBuilder;
import me.hanju.enhancedcompletion.payload.completion.Message;

/**
 * 인용 정보가 포함된 응답 메시지.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PROTECTED)
@EqualsAndHashCode(callSuper = true)
@ToString(callSuper = true)
@JsonIgnoreProperties(ignoreUnknown = true)
public class CitedMessage extends ResponseMessage {

  @Builder.Default
  private List<Citation> citations = new ArrayList<>();

  @JsonIgnore
  public String getContentWithCitations() {
    if (super.getContent() == null || super.getContent().isEmpty()) {
      return "";
    }
    if (citations == null || citations.isEmpty()) {
      return super.getContent();
    }

    final List<Citation> sorted = citations.stream()
        .sorted((a, b) -> Integer.compare(a.getStartIndex(), b.getStartIndex()))
        .toList();

    final StringBuilder sb = new StringBuilder();
    int lastEnd = 0;

    for (final Citation cite : sorted) {
      if (cite.getStartIndex() > lastEnd) {
        sb.append(super.getContent(), lastEnd, cite.getStartIndex());
      }
      sb.append("<cite><id>").append(cite.getId()).append("</id>");
      sb.append(super.getContent(), cite.getStartIndex(), cite.getEndIndex());
      sb.append("</cite>");
      lastEnd = cite.getEndIndex();
    }

    if (lastEnd < super.getContent().length()) {
      sb.append(super.getContent().substring(lastEnd));
    }

    return sb.toString();
  }

  @Override
  public Message toMessage() {
    return Message.builder()
        .role(super.getRole())
        .content(getContentWithCitations())
        .build();
  }
}
