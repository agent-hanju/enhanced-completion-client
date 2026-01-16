package me.hanju.enhancedcompletion;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;

import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import me.hanju.enhancedcompletion.assembler.AugmentResultDeltaMapper;
import me.hanju.enhancedcompletion.assembler.EnhancedCompletionDeltaMapper;
import me.hanju.enhancedcompletion.exception.EnhancedCompletionClientException;
import me.hanju.enhancedcompletion.spi.augment.AugmentResult;
import me.hanju.enhancedcompletion.spi.augment.Augmenter;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.ChatCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionRequest;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.document.IDocument;
import me.hanju.enhancedcompletion.payload.message.AttachedMessage;
import me.hanju.enhancedcompletion.payload.message.IMessageable;
import me.hanju.fluxhandle.FluxListener;
import me.hanju.fluxhandle.StreamHandle;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

/**
 * LLM Chat Completion API에 대한 커스텀 가능 확장 클라이언트
 */
public class EnhancedCompletionClient {

  private static final Predicate<String> SSE_DONE = "[DONE]"::equals;

  private final WebClient client;
  private final ObjectMapper objectMapper;
  private final EnhancedCompletionProperties properties;

  public EnhancedCompletionClient(
      final WebClient.Builder clientBuilder,
      final ObjectMapper objectMapper,
      final EnhancedCompletionProperties properties) {
    this.client = clientBuilder.build();
    this.objectMapper = objectMapper;
    this.properties = properties;
  }

  /**
   * LLM 스트리밍 요청.
   * Augmenter가 있으면 RAG 스트리밍 후 Completion 스트리밍을 수행합니다.
   * RAG 결과와 Completion 결과 모두 handle.get()으로 병합된 결과를 얻을 수 있습니다.
   *
   * @param request        요청 정보
   * @param outputListener 토큰 단위 delta를 수신할 리스너
   * @return StreamHandle
   */
  public StreamHandle<EnhancedCompletionResponse> stream(
      final EnhancedCompletionRequest request,
      final FluxListener<EnhancedCompletionResponse> outputListener) {

    final StreamHandle<EnhancedCompletionResponse> handle = new StreamHandle<>(
        EnhancedCompletionResponse.class,
        outputListener);

    final Augmenter augmenter = request.getAugmenter();

    // Augmenter가 없으면 completion만 subscribe
    if (augmenter == null) {
      final EnhancedCompletionDeltaMapper mapper = new EnhancedCompletionDeltaMapper();
      handle.subscribe(createCompletionFlux(request), mapper);
      return handle;
    }

    // RAG 스트리밍
    final ChatCompletionRequest chatRequest = request.toChatCompletionRequest();
    final AugmentResultDeltaMapper augmentMapper = new AugmentResultDeltaMapper();
    final StreamHandle<EnhancedCompletionResponse> augmentHandle = new StreamHandle<>(
        EnhancedCompletionResponse.class,
        new FluxListener<>() {
          @Override
          public void onNext(EnhancedCompletionResponse delta) {
            handle.emitNext(delta);
          }

          @Override
          public void onComplete() {
            // get()에서 blocking 대기하므로 여기선 처리 불필요
          }

          @Override
          public void onError(Throwable e) {
            handle.emitError(e);
          }

          @Override
          public void onCancel() {
            handle.cancel();
          }
        });
    augmentHandle.subscribe(augmenter.augment(chatRequest), augmentMapper);

    // RAG 완료 대기 후 Completion 시작 (비동기)
    Schedulers.boundedElastic().schedule(() -> {
      final EnhancedCompletionResponse merged = augmentHandle.get();
      final AugmentResult augmentResult = merged != null ? merged.getAugmentResult() : null;
      final EnhancedCompletionRequest augmentedRequest = applyAugmentResult(request, augmentResult);

      final EnhancedCompletionDeltaMapper completionMapper = new EnhancedCompletionDeltaMapper();
      handle.subscribe(createCompletionFlux(augmentedRequest), completionMapper);
    });

    return handle;
  }

  private Flux<ChatCompletionResponse> createCompletionFlux(final EnhancedCompletionRequest request) {
    final String apiKey = properties.getApiKey();
    return client.post()
        .uri(properties.getBaseUrl() + "/v1/chat/completions")
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
  }

  /**
   * LLM 비스트리밍 요청.
   * 내부적으로 스트리밍 요청을 사용하여 handle.get()으로 결과를 반환합니다.
   *
   * @param request 요청 정보
   * @return 완성된 응답
   */
  public EnhancedCompletionResponse complete(final EnhancedCompletionRequest request) {
    final StreamHandle<EnhancedCompletionResponse> handle = stream(request, new FluxListener<>() {
      @Override
      public void onNext(EnhancedCompletionResponse delta) {
      }

      @Override
      public void onComplete() {
      }

      @Override
      public void onError(Throwable e) {
      }

      @Override
      public void onCancel() {
      }
    });

    return handle.get();
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

  /**
   * RAG 결과를 요청에 적용합니다.
   * documents를 마지막 user message에 주입합니다.
   */
  private EnhancedCompletionRequest applyAugmentResult(
      final EnhancedCompletionRequest request,
      final AugmentResult augmentResult) {

    if (augmentResult == null || augmentResult.getDocuments() == null
        || augmentResult.getDocuments().isEmpty()) {
      return request;
    }

    final List<IMessageable> messages = request.getMessages();
    if (messages == null || messages.isEmpty()) {
      return request;
    }

    // 마지막 user message를 찾아서 documents 주입
    final List<IMessageable> newMessages = new ArrayList<>(messages.size());
    int lastUserIndex = -1;

    for (int i = messages.size() - 1; i >= 0; i--) {
      if ("user".equals(messages.get(i).getRole())) {
        lastUserIndex = i;
        break;
      }
    }

    for (int i = 0; i < messages.size(); i++) {
      if (i == lastUserIndex) {
        final IMessageable original = messages.get(i);
        final List<IDocument> docs = new ArrayList<>(augmentResult.getDocuments());
        final AttachedMessage attached = AttachedMessage.builder()
            .role(original.getRole())
            .content(original.getContent())
            .documents(docs)
            .build();
        newMessages.add(attached);
      } else {
        newMessages.add(messages.get(i));
      }
    }

    return request.toBuilder()
        .messages(newMessages)
        .build();
  }
}
