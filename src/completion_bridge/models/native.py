"""벤더 스트림 누적에 사용하는 응답 모델.

누적 중 아직 받지 않은 필드는 기본값으로 유지한다. ``model_dump(exclude_unset=True)``로
직렬화하면 받은 필드와 누적기가 명시적으로 만든 필드만 출력한다. 모델에 선언하지 않은
확장 필드도 보존하며, 중첩된 미디어·도구별 고유 payload는 원본 JSON 구조를 사용한다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatCompletionsChoice",
    "ChatCompletionsFunction",
    "ChatCompletionsMessage",
    "ChatCompletionsResponse",
    "ChatCompletionsToolCall",
    "ChatCompletionsUsage",
    "GenerateContentCandidate",
    "GenerateContentContent",
    "GenerateContentFunctionCall",
    "GenerateContentPart",
    "GenerateContentResponse",
    "GenerateContentUsage",
    "MessagesContentBlock",
    "MessagesResponse",
    "MessagesUsage",
    "ResponsesContentPart",
    "ResponsesOutputItem",
    "ResponsesResponse",
    "ResponsesUsage",
]


class _NativeModel(BaseModel):
    """확장 필드를 보존하고 생성 시점과 속성 대입 시점에 선언된 타입을 검증한다."""

    model_config = ConfigDict(
        extra="allow", strict=True, validate_assignment=True, hide_input_in_errors=True
    )


class ChatCompletionsFunction(_NativeModel):
    """Chat Completions 도구 호출의 함수명과 JSON 인수 문자열."""

    name: str | None = None
    arguments: str | None = None


class ChatCompletionsToolCall(_NativeModel):
    """스트림 index별로 누적한 Chat Completions 도구 호출."""

    index: int | None = None
    id: str | None = None
    type: str | None = None
    function: ChatCompletionsFunction | None = None


class ChatCompletionsMessage(_NativeModel):
    """Chat Completions choice의 assistant 메시지."""

    role: str | None = None
    content: str | list[dict[str, Any]] | None = None
    refusal: str | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ChatCompletionsToolCall] | None = None
    function_call: ChatCompletionsFunction | None = None
    annotations: list[dict[str, Any]] | None = None
    audio: dict[str, Any] | None = None


class ChatCompletionsChoice(_NativeModel):
    """index가 같은 delta를 병합한 Chat Completions 선택지."""

    index: int
    message: ChatCompletionsMessage = Field(default_factory=ChatCompletionsMessage)
    finish_reason: str | None = None
    logprobs: dict[str, Any] | None = None


class ChatCompletionsUsage(_NativeModel):
    """Chat Completions 토큰 수와 서버가 추가한 사용량 필드."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_tokens_details: dict[str, Any] | None = None
    completion_tokens_details: dict[str, Any] | None = None


class ChatCompletionsResponse(_NativeModel):
    """Chat Completions 스트림을 누적한 응답."""

    id: str | None = None
    object: Literal["chat.completion"] = "chat.completion"
    created: int | None = None
    model: str | None = None
    choices: list[ChatCompletionsChoice] = Field(default_factory=list)
    usage: ChatCompletionsUsage | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None


class ResponsesContentPart(_NativeModel):
    """Responses 메시지·추론 항목의 본문 part와 고유 확장 필드."""

    type: str | None = None
    text: str | None = None
    refusal: str | None = None
    annotations: list[dict[str, Any]] | None = None
    logprobs: list[dict[str, Any]] | None = None


class ResponsesOutputItem(_NativeModel):
    """Responses 출력의 메시지·추론·도구 호출 항목."""

    type: str | None = None
    id: str | None = None
    status: str | None = None
    role: str | None = None
    content: list[ResponsesContentPart] | None = None
    summary: list[ResponsesContentPart] | None = None
    name: str | None = None
    call_id: str | None = None
    arguments: str | None = None


class ResponsesUsage(_NativeModel):
    """Responses 토큰 사용량."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    input_tokens_details: dict[str, Any] | None = None
    output_tokens_details: dict[str, Any] | None = None


class ResponsesResponse(_NativeModel):
    """Responses 종료 이벤트의 최종 응답."""

    id: str | None = None
    object: Literal["response"] | None = None
    created_at: int | float | None = None
    model: str | None = None
    status: str | None = None
    output: list[ResponsesOutputItem] = Field(default_factory=list)
    usage: ResponsesUsage | None = None
    error: dict[str, Any] | None = None
    incomplete_details: dict[str, Any] | None = None


class MessagesContentBlock(_NativeModel):
    """Messages 본문의 text·thinking·도구 블록과 고유 payload."""

    type: str | None = None
    text: str | None = None
    thinking: str | None = None
    signature: str | None = None
    citations: list[dict[str, Any]] | None = None
    id: str | None = None
    name: str | None = None
    input: Any = None


class MessagesUsage(_NativeModel):
    """Messages 누적 토큰 사용량과 캐시·서버 도구별 확장 필드."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    server_tool_use: dict[str, Any] | None = None


class MessagesResponse(_NativeModel):
    """Messages 시작 객체에 블록과 delta를 누적한 응답."""

    id: str | None = None
    type: Literal["message"] | None = None
    role: str | None = None
    model: str | None = None
    content: list[MessagesContentBlock] = Field(default_factory=list)
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: MessagesUsage | None = None


class GenerateContentFunctionCall(_NativeModel):
    """GenerateContent 함수 호출명·인수와 호출 식별자."""

    id: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None


class GenerateContentPart(_NativeModel):
    """GenerateContent의 text·서명·함수 호출·미디어 part."""

    text: str | None = None
    thought: bool | None = None
    thoughtSignature: str | None = None
    functionCall: GenerateContentFunctionCall | None = None
    functionResponse: dict[str, Any] | None = None
    inlineData: dict[str, Any] | None = None
    fileData: dict[str, Any] | None = None
    executableCode: dict[str, Any] | None = None
    codeExecutionResult: dict[str, Any] | None = None


class GenerateContentContent(_NativeModel):
    """GenerateContent candidate의 역할과 수신 순서를 유지한 part 목록."""

    role: str | None = None
    parts: list[GenerateContentPart] = Field(default_factory=list)


class GenerateContentCandidate(_NativeModel):
    """index별 GenerateContent 출력과 종료·안전성·근거 메타데이터."""

    index: int | None = None
    content: GenerateContentContent | None = None
    finishReason: str | None = None
    finishMessage: str | None = None
    safetyRatings: list[dict[str, Any]] | None = None
    groundingMetadata: dict[str, Any] | None = None
    citationMetadata: dict[str, Any] | None = None
    logprobsResult: dict[str, Any] | None = None


class GenerateContentUsage(_NativeModel):
    """GenerateContent 토큰 사용량. 필드명은 REST JSON 형식을 따른다."""

    promptTokenCount: int | None = None
    candidatesTokenCount: int | None = None
    totalTokenCount: int | None = None
    cachedContentTokenCount: int | None = None
    thoughtsTokenCount: int | None = None
    toolUsePromptTokenCount: int | None = None


class GenerateContentResponse(_NativeModel):
    """GenerateContent chunk의 candidate·part·메타데이터를 누적한 응답."""

    candidates: list[GenerateContentCandidate] = Field(default_factory=list)
    usageMetadata: GenerateContentUsage | None = None
    promptFeedback: dict[str, Any] | None = None
    modelVersion: str | None = None
    responseId: str | None = None
