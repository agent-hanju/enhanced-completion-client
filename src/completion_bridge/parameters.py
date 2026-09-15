"""각 API의 wire 필드를 그대로 선언하고 대상이 소유한 것만 고른다.

필드는 서로 독립이다. **한 필드에 넣은 값이 다른 필드로 전이되지 않는다.** 여러 벤더에 같은
설정을 보내려면 각 벤더의 필드를 각각 쓴다. 한 필드을 여러 API가 소유할 수는 있다.

wire 이름이 같아도 값 계약이 다르면 벤더 접두어로 분리한다. OpenAI 계열 ``service_tier``와
Anthropic ``anthropic_service_tier``, Gemini ``gemini_service_tier``는 서로 다른 필드다.
``logprobs``도 OpenAI 계열에서는 bool이고 Gemini에서는 개수라서 ``gemini_logprobs``로 나눈다.

각 API에서 deprecated된 필드는 선언하지 않는다. ``user``(vLLM은 무시, Responses는
deprecated), Responses의 ``prompt_cache_retention``, vLLM의 ``max_tokens``가 그렇다.
정의되지 않은 이름은 검증 오류이므로 낡은 코드가 조용히 통과하지 않고 이주 지점을 알려준다.

브리지는 모델을 보지 않는다. 대상 API가 소유한 필드는 그대로 싣고, 그 값이 그 모델에서
유효한지는 판정하지 않는다. 각 벤더의 사용 전략은 해당 API 문서를 따른다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "Hyperparameters",
    "ResponseFormat",
    "ToolChoice",
]


class ToolChoice(BaseModel):
    """공통 함수 도구 선택 정책.

    네 API가 같은 의도를 서로 다른 wire 모양으로 받는다. 입력이 두 필드짜리라 벤더별 dict를
    외우게 하는 대신 여기서 투영한다.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "required", "none", "named"] = "auto"
    name: str | None = None


class ResponseFormat(BaseModel):
    """공통 텍스트/JSON 출력 형식.

    ``ToolChoice``와 같은 이유로 타입 객체다. ``text``/``json_object`` 모드는 Messages와
    Gemini에 대응 개념이 없어 그 요청에서 빠진다.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object", "json_schema"] = "text"
    name: str = "response"
    json_schema: dict[str, Any] | None = None
    strict: bool | None = None


#: 여러 API가 공유하는 필드. 값 계약이 같아 이름을 나누지 않는다.
_SHARED = {
    "temperature": {"chat_completions", "responses", "messages", "generate_content"},
    "top_p": {"chat_completions", "responses", "messages", "generate_content"},
    "top_k": {"chat_completions", "messages", "generate_content"},
    "seed": {"chat_completions", "generate_content"},
    "presence_penalty": {"chat_completions", "generate_content"},
    "frequency_penalty": {"chat_completions", "generate_content"},
    # vLLM의 ``max_tokens``는 deprecated다. 그쪽은 ``max_completion_tokens``를 쓴다.
    "max_tokens": {"messages"},
    "max_output_tokens": {"responses", "generate_content"},
    "stop_sequences": {"messages", "generate_content"},
    "parallel_tool_calls": {"chat_completions", "responses"},
    # Responses에는 top-level ``logprobs``가 없다. `include`에
    # ``message.output_text.logprobs``를 넣고 ``top_logprobs``로 개수를 정한다.
    "logprobs": {"chat_completions"},
    "top_logprobs": {"chat_completions", "responses"},
    "stream_options": {"chat_completions", "responses"},
}

#: vLLM Chat Completions 전용. OpenAI 표준분과 vLLM이 더한 sampling/extra 파라미터다.
#: 순서가 wire body의 키 순서가 되므로 tuple이다. set은 실행마다 순서가 달라져
#: prompt cache의 prefix 일치를 깨뜨린다.
_VLLM_ONLY = (
    "max_completion_tokens",
    "stop",
    "reasoning_effort",
    "logit_bias",
    # vLLM sampling
    "min_p",
    "repetition_penalty",
    "length_penalty",
    "use_beam_search",
    "min_tokens",
    "ignore_eos",
    "stop_token_ids",
    "include_stop_str_in_output",
    "allowed_token_ids",
    "bad_words",
    "prompt_logprobs",
    "skip_special_tokens",
    "spaces_between_special_tokens",
    "truncate_prompt_tokens",
    "truncation_side",
    # vLLM extra
    "chat_template",
    "chat_template_kwargs",
    "add_generation_prompt",
    "continue_final_message",
    "add_special_tokens",
    "echo",
    "media_io_kwargs",
    "mm_processor_kwargs",
    "structured_outputs",
    "documents",
    "priority",
    "request_id",
    "return_tokens_as_token_ids",
)

_RESPONSES_ONLY = (
    "background",
    "conversation",
    "include",
    "instructions",
    "max_tool_calls",
    "metadata",
    "moderation",
    "previous_response_id",
    "prompt",
    "prompt_cache_key",
    "prompt_cache_options",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "store",
    "text",
    "truncation",
)

_MESSAGES_ONLY = (
    "anthropic_metadata",
    "anthropic_service_tier",
    "betas",
    "cache_control",
    "container",
    "context_management",
    "inference_geo",
    "mcp_servers",
    "output_config",
    "speed",
    "thinking",
)

#: Messages는 wire 이름이 파이썬 이름과 같지 않은 경우가 있다.
_MESSAGES_WIRE = {
    "anthropic_metadata": "metadata",
    "anthropic_service_tier": "service_tier",
}

#: Gemini ``generationConfig`` 안으로 들어가는 필드. 값은 wire(camelCase) 이름이다.
_GEMINI_GENERATION = {
    "temperature": "temperature",
    "top_p": "topP",
    "top_k": "topK",
    "seed": "seed",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
    "max_output_tokens": "maxOutputTokens",
    "stop_sequences": "stopSequences",
    "response_logprobs": "responseLogprobs",
    "gemini_logprobs": "logprobs",
    "response_modalities": "responseModalities",
    "gemini_response_format": "responseFormat",
    "response_mime_type": "responseMimeType",
    "response_schema": "responseSchema",
    "speech_config": "speechConfig",
    "image_config": "imageConfig",
    "media_resolution": "mediaResolution",
    "audio_transcription_config": "audioTranscriptionConfig",
    "translation_config": "translationConfig",
    "thinking_config": "thinkingConfig",
}

#: Gemini 최상위 필드.
_GEMINI_TOP = {
    "safety_settings": "safetySettings",
    "tool_config": "toolConfig",
    "cached_content": "cachedContent",
    "gemini_service_tier": "serviceTier",
}

class Hyperparameters(BaseModel):
    """네 API의 요청 옵션을 한 모델에 선언한다.

    대상이 소유하지 않은 필드는 그 요청에서 조용히 빠진다. 반면 정의되지 않은 이름은
    ``extra="forbid"`` 검증 오류가 되어 오타를 숨기지 않는다. 두 층위는 다르다.
    """

    model_config = ConfigDict(extra="forbid")

    # ---- 여러 API가 공유 ----
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    max_tokens: int | None = None
    max_output_tokens: int | None = None
    stop_sequences: list[str] | None = None
    parallel_tool_calls: bool | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    stream_options: dict[str, Any] | None = None

    # ---- 타입 객체. 대상 모양으로 투영된다 ----
    tool_choice: ToolChoice | None = None
    response_format: ResponseFormat | None = None

    # ---- vLLM Chat Completions ----
    max_completion_tokens: int | None = None
    stop: list[str] | None = None
    reasoning_effort: str | None = None
    logit_bias: dict[str, float] | None = None
    min_p: float | None = None
    repetition_penalty: float | None = None
    length_penalty: float | None = None
    use_beam_search: bool | None = None
    min_tokens: int | None = None
    ignore_eos: bool | None = None
    stop_token_ids: list[int] | None = None
    include_stop_str_in_output: bool | None = None
    allowed_token_ids: list[int] | None = None
    bad_words: list[str] | None = None
    prompt_logprobs: int | None = None
    skip_special_tokens: bool | None = None
    spaces_between_special_tokens: bool | None = None
    truncate_prompt_tokens: int | None = None
    truncation_side: Literal["left", "right"] | None = None
    chat_template: str | None = None
    chat_template_kwargs: dict[str, Any] | None = None
    add_generation_prompt: bool | None = None
    continue_final_message: bool | None = None
    add_special_tokens: bool | None = None
    echo: bool | None = None
    media_io_kwargs: dict[str, Any] | None = None
    mm_processor_kwargs: dict[str, Any] | None = None
    structured_outputs: dict[str, Any] | None = None
    documents: list[dict[str, str]] | None = None
    priority: int | None = None
    request_id: str | None = None
    return_tokens_as_token_ids: bool | None = None

    # ---- OpenAI Responses ----
    background: bool | None = None
    conversation: str | dict[str, Any] | None = None
    include: list[str] | None = None
    instructions: str | None = None
    max_tool_calls: int | None = None
    metadata: dict[str, str] | None = None
    moderation: dict[str, Any] | None = None
    previous_response_id: str | None = None
    prompt: dict[str, Any] | None = None
    prompt_cache_key: str | None = None
    prompt_cache_options: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    store: bool | None = None
    text: dict[str, Any] | None = None
    truncation: Literal["auto", "disabled"] | None = None

    # ---- Anthropic Messages ----
    anthropic_metadata: dict[str, Any] | None = None
    anthropic_service_tier: Literal["auto", "standard_only"] | None = None
    betas: list[str] | None = None
    cache_control: dict[str, Any] | None = None
    container: str | dict[str, Any] | None = None
    context_management: dict[str, Any] | None = None
    inference_geo: str | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    output_config: dict[str, Any] | None = None
    speed: Literal["fast"] | None = None
    thinking: dict[str, Any] | None = None

    # ---- Gemini GenerateContent ----
    audio_transcription_config: dict[str, Any] | None = None
    cached_content: str | None = None
    gemini_logprobs: int | None = None
    """Gemini ``generationConfig.logprobs``. 개수라서 bool인 ``logprobs``와 다른 필드다."""
    gemini_service_tier: str | None = None
    generation_config: dict[str, Any] | None = None
    image_config: dict[str, Any] | None = None
    media_resolution: str | None = None
    response_logprobs: bool | None = None
    gemini_response_format: dict[str, Any] | None = None
    """Gemini ``generationConfig.responseFormat``.

    text/audio/image 출력 형식을 한 구조에 담는 멀티모달 설정이다. Chat Completions의
    ``response_format``과 이름만 같고 다른 개념이라 접두어로 나눈다. JSON 스키마
    출력은 ``responseMimeType`` + ``responseJsonSchema``와 공존한다.
    """
    response_mime_type: str | None = None
    response_modalities: list[str] | None = None
    response_schema: dict[str, Any] | None = None
    safety_settings: list[dict[str, Any]] | None = None
    speech_config: dict[str, Any] | None = None
    thinking_config: dict[str, Any] | None = None
    tool_config: dict[str, Any] | None = None
    translation_config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _reject_exclusive_pairs(self) -> Hyperparameters:
        """같은 요청에 함께 실을 수 없는 조합을 거부한다.

        조용히 400을 받는 것보다 요청을 만들기 전에 실패하는 편이 낫다.
        """
        if self.previous_response_id is not None and self.conversation is not None:
            raise ValueError(
                "previous_response_id and conversation cannot be used together (Responses)"
            )
        return self

    @classmethod
    def coerce(cls, value: Hyperparameters | Mapping[str, Any] | None) -> Hyperparameters:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        return cls.model_validate(dict(value))

    def merged(self, override: Hyperparameters | Mapping[str, Any] | None) -> Hyperparameters:
        """생성자 기본값 위에 호출별 값을 덮는다.

        **명시한 ``None``은 그 요청에서 필드를 지운다.** Pydantic이 "명시한 ``None``"과
        "쓰지 않은 것"을 ``model_fields_set``으로 구분하므로, 오른쪽에서 ``exclude_none``을
        걸지 않는다. 그것 없이는 생성자 기본값을 호출별로 해제할 방법이 없다.

        >>> defaults = Hyperparameters(temperature=0.2)
        >>> defaults.merged(Hyperparameters(temperature=None)).temperature is None
        True
        >>> defaults.merged(Hyperparameters(top_p=0.9)).temperature
        0.2
        """
        if override is None:
            return self
        right = self.coerce(override).model_dump(exclude_unset=True)
        left = self.model_dump(exclude_none=True, exclude_unset=True)
        return type(self).model_validate(_merge(left, right))

    def for_vendor(self, family: str, *, name: str | None = None) -> dict[str, Any]:
        """대상이 소유한 필드만 wire 모양으로 만든다. 전이는 없다."""
        _ = name
        if family == "chat_completions":
            return self._chat()
        if family == "responses":
            return self._responses()
        if family == "messages":
            return self._messages()
        if family == "generate_content":
            return self._gemini()
        return {}

    # ---- 계열별 선택 ----

    def _chat(self) -> dict[str, Any]:
        out = self._owned("chat_completions")
        out.update(self._select(_VLLM_ONLY))
        _put(out, "tool_choice", _tool_choice(self.tool_choice, "chat_completions"))
        _put(out, "response_format", _response_format(self.response_format, "chat_completions"))
        return out

    def _responses(self) -> dict[str, Any]:
        out = self._owned("responses")
        out.update(self._select(_RESPONSES_ONLY))
        _put(out, "tool_choice", _tool_choice(self.tool_choice, "responses"))
        _nest(out, "text", "format", _response_format(self.response_format, "responses"))
        return out

    def _messages(self) -> dict[str, Any]:
        out = self._owned("messages")
        for field in _MESSAGES_ONLY:
            _put(out, _MESSAGES_WIRE.get(field, field), getattr(self, field))
        choice = _tool_choice(self.tool_choice, "messages")
        if isinstance(choice, dict) and self.parallel_tool_calls is not None:
            choice["disable_parallel_tool_use"] = not self.parallel_tool_calls
        _put(out, "tool_choice", choice)
        _nest(out, "output_config", "format", _response_format(self.response_format, "messages"))
        return out

    def _gemini(self) -> dict[str, Any]:
        generation: dict[str, Any] = {}
        for field, wire in _GEMINI_GENERATION.items():
            if field in _SHARED and "generate_content" not in _SHARED[field]:
                continue
            _put(generation, wire, getattr(self, field))

        out: dict[str, Any] = {}
        for field, wire in _GEMINI_TOP.items():
            _put(out, wire, getattr(self, field))

        # ``responseFormat``은 흩어져 있던 출력 형식 필드를 modality별로 모은 새 구조다.
        # 호출자가 그쪽을 명시하면 구 필드로 투영하지 않는다. 둘을 함께 보내면
        # ``responseFormat.text.mimeType``과 ``responseMimeType``이 모순될 수 있다.
        formatted = _response_format(self.response_format, "generate_content")
        if isinstance(formatted, dict) and self.gemini_response_format is None:
            generation.update(formatted)
        choice = _tool_choice(self.tool_choice, "generate_content")
        if isinstance(choice, dict):
            _nest(out, "toolConfig", "functionCallingConfig", choice["functionCallingConfig"])

        # 명시한 raw generationConfig가 마지막에 덮는다.
        if self.generation_config:
            generation = _merge(generation, self.generation_config)
        if generation:
            out["generationConfig"] = generation
        return out

    def _owned(self, family: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field, owners in _SHARED.items():
            if family in owners:
                _put(out, field, getattr(self, field))
        return out

    def _select(self, fields: tuple[str, ...]) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for field in fields:
            _put(selected, field, getattr(self, field))
        return selected


def _nest(target: dict[str, Any], outer: str, inner: str, value: Any) -> None:
    """``outer.inner``에 투영값을 넣되 호출자가 이미 정했으면 건드리지 않는다.

    벤더 raw 필드가 항상 이긴다. 다만 ``outer``의 다른 키는 보존한다.
    """
    if value is None:
        return
    nested = dict(target.get(outer) or {})
    nested.setdefault(inner, value)
    target[outer] = nested


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def _tool_choice(choice: ToolChoice | None, family: str) -> str | dict[str, Any] | None:
    if choice is None:
        return None
    if family in {"chat_completions", "responses"}:
        if choice.mode != "named":
            return choice.mode
        if not choice.name:
            raise ValueError("ToolChoice(mode='named') requires name")
        if family == "chat_completions":
            return {"type": "function", "function": {"name": choice.name}}
        return {"type": "function", "name": choice.name}
    if family == "messages":
        kinds = {"required": "any", "named": "tool"}
        result: dict[str, Any] = {"type": kinds.get(choice.mode, choice.mode)}
        if choice.mode == "named":
            if not choice.name:
                raise ValueError("ToolChoice(mode='named') requires name")
            result["name"] = choice.name
        return result
    if family == "generate_content":
        modes = {"auto": "AUTO", "required": "ANY", "none": "NONE", "named": "ANY"}
        config: dict[str, Any] = {"mode": modes[choice.mode]}
        if choice.mode == "named":
            if not choice.name:
                raise ValueError("ToolChoice(mode='named') requires name")
            config["allowedFunctionNames"] = [choice.name]
        return {"functionCallingConfig": config}
    return None


def _response_format(fmt: ResponseFormat | None, family: str) -> dict[str, Any] | None:
    if fmt is None:
        return None
    if fmt.type in {"text", "json_object"}:
        # Messages와 Gemini에는 대응 개념이 없다.
        if family in {"messages", "generate_content"}:
            return None
        return {"type": fmt.type}
    if fmt.json_schema is None:
        raise ValueError("ResponseFormat(type='json_schema') requires json_schema")
    if family == "chat_completions":
        inner: dict[str, Any] = {"name": fmt.name, "schema": fmt.json_schema}
        _put(inner, "strict", fmt.strict)
        return {"type": "json_schema", "json_schema": inner}
    if family == "responses":
        result = {"type": "json_schema", "name": fmt.name, "schema": fmt.json_schema}
        _put(result, "strict", fmt.strict)
        return result
    if family == "messages":
        return {"type": "json_schema", "schema": fmt.json_schema}
    if family == "generate_content":
        return {"responseMimeType": "application/json", "responseJsonSchema": fmt.json_schema}
    return None
