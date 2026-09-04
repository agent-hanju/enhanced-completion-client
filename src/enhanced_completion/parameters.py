"""벤더 중립 생성 옵션과 벤더별 요청 확장.

공통 필드는 의미가 실제로 같은 API에만 투영한다. 이름만 비슷하고 계약이 다른 옵션은 각
벤더 섹션에 둔다. 벤더 섹션은 ``extra="allow"``라 API가 새 필드를 추가해도 즉시 사용할 수
있지만, 다른 벤더 요청으로는 절대 새지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatCompletionsParameters",
    "GenerateContentParameters",
    "Hyperparameters",
    "MessagesParameters",
    "OutputFormat",
    "ResponsesParameters",
    "ToolChoice",
]


class _VendorParameters(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)


class ChatCompletionsParameters(_VendorParameters):
    """Chat Completions 전용 필드. 미선언 확장도 이 섹션 안에서만 허용한다."""

    audio: dict[str, Any] | None = None
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    max_completion_tokens: int | None = None
    metadata: dict[str, str] | None = None
    modalities: list[str] | None = None
    prediction: dict[str, Any] | None = None
    prompt_cache_key: str | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    store: bool | None = None
    stream_options: dict[str, Any] | None = None
    top_logprobs: int | None = None
    user: str | None = None
    verbosity: str | None = None
    web_search_options: dict[str, Any] | None = None


class ResponsesParameters(_VendorParameters):
    """Responses 전용 필드."""

    background: bool | None = None
    conversation: str | dict[str, Any] | None = None
    include: list[str] | None = None
    metadata: dict[str, str] | None = None
    previous_response_id: str | None = None
    prompt: dict[str, Any] | None = None
    prompt_cache_key: str | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    store: bool | None = None
    stream_options: dict[str, Any] | None = None
    text: dict[str, Any] | None = None
    top_logprobs: int | None = None
    truncation: str | None = None
    user: str | None = None


class MessagesParameters(_VendorParameters):
    """Anthropic Messages 전용 필드.

    ``temperature``/``top_p``/``top_k``는 최신 모델에서 폐기되었지만 구형 모델 호환을 위해
    명시적 벤더 섹션에는 남긴다. 공통 sampling 옵션에서는 자동 투영하지 않는다.
    """

    cache_control: dict[str, Any] | None = None
    container: str | dict[str, Any] | None = None
    context_management: dict[str, Any] | None = None
    inference_geo: str | None = None
    max_tokens: int | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    output_config: dict[str, Any] | None = None
    service_tier: str | None = None
    stop_sequences: list[str] | None = None
    temperature: float | None = None
    thinking: dict[str, Any] | None = None
    tool_choice: dict[str, Any] | None = None
    top_k: int | None = None
    top_p: float | None = None


class GenerateContentParameters(_VendorParameters):
    """Gemini GenerateContent 전용 필드. JSON 이름은 REST wire 이름으로 직렬화한다."""

    generation_config: dict[str, Any] | None = Field(default=None, alias="generationConfig")
    tool_config: dict[str, Any] | None = Field(default=None, alias="toolConfig")
    safety_settings: list[dict[str, Any]] | None = Field(default=None, alias="safetySettings")
    cached_content: str | None = Field(default=None, alias="cachedContent")
    service_tier: str | None = Field(default=None, alias="serviceTier")
    store: bool | None = None


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
    """요청마다 쓰는 공통 생성 옵션과 격리된 벤더 확장.

    공통 필드는 지원되는 API에만 들어간다. 예를 들어 ``top_k``는 Gemini에만,
    ``stop_sequences``는 Chat/Messages/Gemini에만 들어간다. ``vendor``는 사용자 정의 어댑터의
    정확한 ``name``을 키로 하는 마지막 확장점이다.
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

    chat_completions: ChatCompletionsParameters = Field(
        default_factory=ChatCompletionsParameters
    )
    responses: ResponsesParameters = Field(default_factory=ResponsesParameters)
    messages: MessagesParameters = Field(default_factory=MessagesParameters)
    generate_content: GenerateContentParameters = Field(
        default_factory=GenerateContentParameters
    )
    vendor: dict[str, dict[str, Any]] = Field(default_factory=dict)

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
        common = self._common_for(family)
        section = getattr(self, family, None)
        if isinstance(section, _VendorParameters):
            common = _merge(common, section.wire())
        if name and name in self.vendor:
            common = _merge(common, self.vendor[name])
        return common

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
        return out

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
        return out

    def _messages(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
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
        return out

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
        return out

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
