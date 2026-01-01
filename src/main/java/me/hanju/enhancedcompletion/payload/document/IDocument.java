package me.hanju.enhancedcompletion.payload.document;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

/**
 * RAG용 문서 인터페이스.
 */
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, include = JsonTypeInfo.As.PROPERTY, property = "type", defaultImpl = SimpleDocument.class)
@JsonSubTypes({
    @JsonSubTypes.Type(value = SimpleDocument.class, name = "document")
})
public interface IDocument {

  String getId();

  String getTitle();

  String getContent();

  default String getUrl() {
    return null;
  }

  default String toSerializedPrompt() {
    final StringBuilder sb = new StringBuilder();
    sb.append("<document id=\"").append(getId()).append("\">\n");
    if (getTitle() != null && !getTitle().isBlank()) {
      sb.append("<title>").append(getTitle()).append("</title>\n");
    }
    sb.append("<content>").append(getContent()).append("</content>\n");
    sb.append("</document>");
    return sb.toString();
  }
}
