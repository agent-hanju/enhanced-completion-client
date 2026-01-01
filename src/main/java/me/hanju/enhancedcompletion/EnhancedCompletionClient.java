package me.hanju.enhancedcompletion;

import java.util.function.Predicate;

import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import me.hanju.enhancedcompletion.assembler.StreamingEnhancedAssembler;
import me.hanju.enhancedcompletion.exception.EnhancedCompletionClientException;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.fluxhandle.FluxHandle;
import me.hanju.fluxhandle.FluxListener;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

/**
 * LLM Chat Completion 확장 클라이언트.
 */
public class EnhancedCompletionClient {

  private static final Predicate<String> SSE_DONE = "[DONE]"::equals;

  private final WebClient client;
  private final ObjectMapper objectMapper;
  private final String baseUrl;
  private final String apiKey;

  public EnhancedCompletionClient(
      final WebClient.Builder clientBuilder,
      final ObjectMapper objectMapper,
      final String baseUrl,
      final String apiKey) {
    this.client = clientBuilder.build();
    this.objectMapper = objectMapper;
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  /**
   * LLM 스트리밍 요청.
   *
   * @param request        요청 정보
   * @param outputListener 토큰 단위 delta를 수신할 리스너
   * @return FluxHandle
   */
  public FluxHandle<ChatCompletionResponse, EnhancedCompletionResponse> stream(
      final EnhancedCompletionRequest request,
      final FluxListener<EnhancedCompletionResponse> outputListener) {

    final Flux<ChatCompletionResponse> source = client.post()
        .uri(baseUrl + "/v1/chat/completions")
        .accept(MediaType.TEXT_EVENT_STREAM)
        .headers(headers -> {
          headers.setContentType(MediaType.APPLICATION_JSON);
          if (apiKey != null && !apiKey.isBlank()) {
            headers.setBearerAuth(apiKey);
          }
        })
        .bodyValue(toRequest(request, true))
        .retrieve()
        .bodyToFlux(String.class)
        .takeUntil(SSE_DONE)
        .filter(SSE_DONE.negate())
        .map(this::parse)
        .publishOn(Schedulers.boundedElastic());

    return stream(source, outputListener);
  }

  /**
   * LLM 비스트리밍 요청.
   *
   * @param request 요청 정보
   * @return 완성된 응답
   */
  public EnhancedCompletionResponse complete(final EnhancedCompletionRequest request) {
    final String json = client.post()
        .uri(baseUrl + "/v1/chat/completions")
        .headers(headers -> {
          headers.setContentType(MediaType.APPLICATION_JSON);
          if (apiKey != null && !apiKey.isBlank()) {
            headers.setBearerAuth(apiKey);
          }
        })
        .bodyValue(toRequest(request, false))
        .retrieve()
        .bodyToMono(String.class)
        .block();

    return stream(Flux.just(parse(json)), t -> {
    }).get();
  }

  private ChatCompletionRequest toRequest(final EnhancedCompletionRequest request, final boolean stream) {
    return request.toChatCompletionRequest().toBuilder()
        .stream(stream)
        .build();
  }

  private ChatCompletionResponse parse(final String json) {
    try {
      return objectMapper.readValue(json, ChatCompletionResponse.class);
    } catch (JsonProcessingException e) {
      throw new EnhancedCompletionClientException("Failed to parse response", e);
    }
  }

  private FluxHandle<ChatCompletionResponse, EnhancedCompletionResponse> stream(
      final Flux<ChatCompletionResponse> source,
      final FluxListener<EnhancedCompletionResponse> outputListener) {

    final StreamingEnhancedAssembler assembler = new StreamingEnhancedAssembler(outputListener);
    return new FluxHandle<>(source, assembler, new FluxListener<>() {
      @Override
      public void onNext(final ChatCompletionResponse t) {
        // assembler.applyDelta()가 이미 처리함
      }

      @Override
      public void onError(final Throwable e) {
        outputListener.onError(e);
      }

      @Override
      public void onComplete() {
        outputListener.onComplete();
      }

      @Override
      public void onCancel() {
        outputListener.onCancel();
      }
    });
  }
}
