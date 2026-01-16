package me.hanju.enhancedcompletion;

public class EnhancedCompletionProperties {

  private String baseUrl;
  private String apiKey;

  public EnhancedCompletionProperties() {
  }

  public EnhancedCompletionProperties(final String baseUrl, final String apiKey) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  public String getBaseUrl() {
    if (baseUrl == null) {
      return null;
    }
    return baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
  }

  public void setBaseUrl(final String baseUrl) {
    this.baseUrl = baseUrl;
  }

  public String getApiKey() {
    return apiKey;
  }

  public void setApiKey(final String apiKey) {
    this.apiKey = apiKey;
  }
}
