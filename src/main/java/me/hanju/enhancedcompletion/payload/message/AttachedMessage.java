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
import me.hanju.enhancedcompletion.payload.document.IDocument;

/**
 * 문서가 첨부된 메시지.
 */
@SuperBuilder(toBuilder = true)
@Getter
@Setter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor(access = AccessLevel.PROTECTED)
@EqualsAndHashCode
@ToString
@JsonIgnoreProperties(ignoreUnknown = true)
public class AttachedMessage implements IMessageable {

  private String role;

  @Builder.Default
  private List<IDocument> documents = new ArrayList<>();

  private String content;

  @Override
  @JsonIgnore
  public Message toMessage() {
    final String documentsText = serializeDocuments();

    String mergedContent;
    if (documentsText.isEmpty()) {
      mergedContent = content;
    } else if (content == null || content.isEmpty()) {
      mergedContent = documentsText;
    } else {
      mergedContent = content + "\n\n" + documentsText;
    }

    return Message.builder()
        .role(role)
        .content(mergedContent)
        .build();
  }

  @JsonIgnore
  public String serializeDocuments() {
    if (this.documents == null || this.documents.isEmpty()) {
      return "";
    }
    final StringBuilder sb = new StringBuilder();
    sb.append("<documents>\n");
    for (final IDocument doc : this.documents) {
      sb.append(doc.toSerializedPrompt()).append("\n");
    }
    sb.append("</documents>");
    return sb.toString();
  }
}
