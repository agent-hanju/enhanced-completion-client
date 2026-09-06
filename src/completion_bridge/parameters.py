"""평평한 요청 옵션을 각 API가 지원하는 wire 필드로 투영한다.

알려진 필드는 하나의 :class:`Hyperparameters`에 둔다. 각 어댑터는 자기 API가 지원하는 필드만
골라 쓰므로 같은 객체를 다른 Bridge에 넣어도 관계없는 값은 조용히 빠진다. 반면 정의되지 않은
필드는 Pydantic 검증 오류로 처리해 오타까지 조용히 사라지는 일은 막는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Hyperparameters",
    "OutputFormat",
    "ToolChoice",
]


class ToolChoice(BaseModel):
    """공통 함수 도구 선택 정책."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "required", "none", "named"] = "auto"
    name: str | None = None


class OutputFormat(BaseModel):
    """공통 텍스트/JSON 출력 형식."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object", "json_schema"] = "text"
    name: str = "response"
    json_schema: dict[str, Any] | None = None
    strict: bool | None = None


class Hyperparameters(BaseModel):
    """공통 및 API 고유 요청 옵션의 평평한 합집합.

    필드가 어느 API에서 쓰이는지는 :meth:`for_vendor`가 결정한다. 예를 들어
    ``previous_response_id``는 Responses에서만, ``inference_geo``는 Messages에서만,
    ``safety_settings``는 Gemini에서만 나간다. 중첩 wire 객체는 공통 변환 결과 위에 deep-merge
    되므로 세부 옵션만 직접 지정할 수도 있다.
    """

    model_config = ConfigDict(extra="forbid")

    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    stop_sequences: list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    reasoning_effort: str | None = None
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None
    output_format: OutputFormat | None = None

    # Chat Completions
    audio: dict[str, Any] | None = None
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    max_completion_tokens: int | None = None
    modalities: list[str] | None = None
    prediction: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    verbosity: str | None = None
    web_search_options: dict[str, Any] | None = None

    # Responses
    background: bool | None = None
    conversation: str | dict[str, Any] | None = None
    include: list[str] | None = None
    previous_response_id: str | None = None
    prompt: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    text: dict[str, Any] | None = None
    truncation: str | None = None

    # Anthropic Messages
    cache_control: dict[str, Any] | None = None
    container: str | dict[str, Any] | None = None
    context_management: dict[str, Any] | None = None
    inference_geo: str | None = None
    max_tokens: int | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    anthropic_metadata: dict[str, Any] | None = None
    anthropic_service_tier: Literal["auto", "standard_only"] | None = None
    output_config: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None

    # Gemini GenerateContent
    cached_content: str | None = None
    generation_config: dict[str, Any] | None = None
    gemini_service_tier: str | None = None
    safety_settings: list[dict[str, Any]] | None = None
    tool_config: dict[str, Any] | None = None

    # 둘 이상의 API가 같은 wire 이름으로 지원하는 옵션
    metadata: dict[str, str] | None = None
    prompt_cache_key: str | None = None
    prompt_cache_retention: str | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    store: bool | None = None
    stream_options: dict[str, Any] | None = None
    top_logprobs: int | None = None
    user: str | None = None

    # 알려진 표준 필드와 달리 검증·필터링하지 않는 명시적 escape hatch. 기본 어댑터에서도
    # 호출자가 의도적으로 사용한 값이므로 현재 대상 요청에만 마지막으로 적용한다.
    extensions: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def coerce(cls, value: Hyperparameters | Mapping[str, Any] | None) -> Hyperparameters:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        return cls.model_validate(dict(value))

    def merged(self, override: Hyperparameters | Mapping[str, Any] | None) -> Hyperparameters:
        if override is None:
            return self
        right = self.coerce(override).model_dump(exclude_none=True, exclude_unset=True)
        left = self.model_dump(exclude_none=True, exclude_unset=True)
        return type(self).model_validate(_merge(left, right))

    def for_vendor(self, family: str, *, name: str | None = None) -> dict[str, Any]:
        """대상 계열이 지원하는 값만 선택하고 명시적 extension을 마지막에 적용한다."""
        selected = self._common_for(family)
        if self.extensions:
            selected = _merge(selected, self.extensions)
        _ = name
        return selected

    def _common_for(self, family: str) -> dict[str, Any]:
        if family == "chat_completions":
            return self._chat()
        if family == "responses":
            return self._responses()
        if family == "messages":
            return self._messages()
        if family == "generate_content":
            return self._gemini()
        return {}

    def _chat(self) -> dict[str, Any]:
        out = self._sampling(top_k=False)
        _put(out, "max_completion_tokens", self.max_output_tokens)
        _put(out, "stop", self.stop_sequences)
        _put(out, "reasoning_effort", self.reasoning_effort)
        _put(out, "parallel_tool_calls", self.parallel_tool_calls)
        _put(out, "tool_choice", _tool_choice(self.tool_choice, "chat_completions"))
        _put(out, "response_format", _output_format(self.output_format, "chat_completions"))
        return _merge(
            out,
            self._select(
                "audio",
                "logit_bias",
                "logprobs",
                "max_completion_tokens",
                "metadata",
                "modalities",
                "prediction",
                "prompt_cache_key",
                "prompt_cache_retention",
                "response_format",
                "safety_identifier",
                "service_tier",
                "store",
                "stream_options",
                "top_logprobs",
                "user",
                "verbosity",
                "web_search_options",
            ),
        )

    def _responses(self) -> dict[str, Any]:
        out = self._sampling(seed=False, penalties=False, top_k=False)
        _put(out, "max_output_tokens", self.max_output_tokens)
        _put(out, "parallel_tool_calls", self.parallel_tool_calls)
        _put(out, "tool_choice", _tool_choice(self.tool_choice, "responses"))
        if self.reasoning_effort is not None:
            out["reasoning"] = {"effort": self.reasoning_effort}
        formatted = _output_format(self.output_format, "responses")
        if formatted is not None:
            out["text"] = {"format": formatted}
        direct = self._select(
            "background",
            "conversation",
            "include",
            "metadata",
            "previous_response_id",
            "prompt",
            "prompt_cache_key",
            "prompt_cache_retention",
            "reasoning",
            "safety_identifier",
            "service_tier",
            "store",
            "stream_options",
            "text",
            "top_logprobs",
            "truncation",
            "user",
        )
        return _merge(out, direct)

    def _messages(self) -> dict[str, Any]:
        out = self._sampling(seed=False, penalties=False)
        _put(out, "max_tokens", self.max_output_tokens)
        _put(out, "stop_sequences", self.stop_sequences)
        choice = _tool_choice(self.tool_choice, "messages")
        if isinstance(choice, dict) and self.parallel_tool_calls is not None:
            choice["disable_parallel_tool_use"] = not self.parallel_tool_calls
        elif self.parallel_tool_calls is not None:
            choice = {
                "type": "auto",
                "disable_parallel_tool_use": not self.parallel_tool_calls,
            }
        _put(out, "tool_choice", choice)
        output_config: dict[str, Any] = {}
        _put(output_config, "effort", self.reasoning_effort)
        formatted = _output_format(self.output_format, "messages")
        _put(output_config, "format", formatted)
        if output_config:
            out["output_config"] = output_config
        direct = self._select(
            "cache_control",
            "container",
            "context_management",
            "inference_geo",
            "max_tokens",
            "mcp_servers",
            "output_config",
            "thinking",
            anthropic_metadata="metadata",
            anthropic_service_tier="service_tier",
        )
        return _merge(out, direct)

    def _gemini(self) -> dict[str, Any]:
        config = self._sampling()
        renamed = {
            "temperature": config.get("temperature"),
            "topP": config.get("top_p"),
            "topK": config.get("top_k"),
            "seed": config.get("seed"),
            "presencePenalty": config.get("presence_penalty"),
            "frequencyPenalty": config.get("frequency_penalty"),
            "maxOutputTokens": self.max_output_tokens,
            "stopSequences": self.stop_sequences,
        }
        generation = {key: value for key, value in renamed.items() if value is not None}
        formatted = _output_format(self.output_format, "generate_content")
        if isinstance(formatted, dict):
            generation.update(formatted)
        out: dict[str, Any] = {"generationConfig": generation} if generation else {}
        choice = _tool_choice(self.tool_choice, "generate_content")
        if choice is not None:
            out["toolConfig"] = choice
        direct = self._select(
            cached_content="cachedContent",
            generation_config="generationConfig",
            safety_settings="safetySettings",
            gemini_service_tier="serviceTier",
            store="store",
            tool_config="toolConfig",
        )
        return _merge(out, direct)

    def _select(self, *fields: str, **aliases: str) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for field in fields:
            _put(selected, field, getattr(self, field))
        for field, wire_name in aliases.items():
            _put(selected, wire_name, getattr(self, field))
        return selected

    def _sampling(
        self,
        *,
        seed: bool = True,
        penalties: bool = True,
        top_k: bool = True,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        _put(out, "temperature", self.temperature)
        _put(out, "top_p", self.top_p)
        if top_k:
            _put(out, "top_k", self.top_k)
        if seed:
            _put(out, "seed", self.seed)
        if penalties:
            _put(out, "presence_penalty", self.presence_penalty)
            _put(out, "frequency_penalty", self.frequency_penalty)
        return out


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


def _output_format(fmt: OutputFormat | None, family: str) -> dict[str, Any] | None:
    if fmt is None:
        return None
    if fmt.type == "text":
        if family in {"messages", "generate_content"}:
            return None
        return {"type": "text"}
    if fmt.type == "json_object":
        if family in {"messages", "generate_content"}:
            return None
        return {"type": "json_object"}
    if fmt.json_schema is None:
        raise ValueError("OutputFormat(type='json_schema') requires json_schema")
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
