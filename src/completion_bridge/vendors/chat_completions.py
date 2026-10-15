"""OpenAI 호환 Chat Completions 어댑터.

vLLM이 이 규약을 쓴다. 첫 번째로 구현하는 스포크다.

이 벤더의 응답은 ``choices[0].delta``에 조각이 담기고 필드가 셋으로 갈린다. ``content``는
본문, ``reasoning``은 추론, ``tool_calls``는 도구 호출이다. 추론 필드 이름은 vLLM 버전에 따라
``reasoning``과 ``reasoning_content``로 갈리므로 둘 다 본다.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from pydantic import BaseModel

from ..blocks import (
    AnnotationBlock,
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from ..errors import MappingError
from ..hub import (
    HubMessage,
    HubRequest,
    HubResponse,
    TokenCount,
    TokenizedPrompt,
    ToolDefinition,
    Usage,
)
from ..mapper import StreamMapper
from ..native import (
    ChatCompletionsChoice,
    ChatCompletionsMessage,
    ChatCompletionsResponse,
    ChatCompletionsToolCall,
)
from ..seq import Seq, SeqAllocator
from ..transport.sse import SseEvent
from .base import JsonCall, Lowerer, NativeAccumulator
from .normalize import REFUSAL_PREFIX, stop_reason_from_chat
from .parts import as_chat_completions_part, has_opaque_media_reference
from .tool_policy import can_replay_client_tool

__all__ = ["ChatCompletionsAdapter", "chat_completions"]

DONE = "[DONE]"

_RESERVED = frozenset({"model", "messages", "tools", "stream"})

# 원본 응답 누적에서 조각마다 같은 값이 다시 올 수 있는 식별 필드. 연결하지 않고 나중 값을 쓴다.
_REPLACED_FIELDS = frozenset({"index", "type", "role", "finish_reason", "stop_reason"})


class _ToHub:
    """Chat Completions chunk를 허브 델타로 바꾼다.

    블록 최초 등장 순서로 0부터 번호 n을 부여하고 ``seq=(n, 0)``을 쓴다. 연속 채널은 같은
    블록에 누적하고 채널이 바뀌면 새 블록을 만든다. 도구 호출은 원본 index별로 같은 블록을
    유지한다. annotation은 대상 text 블록 바로 뒤의 seq를 받는다.
    """

    def __init__(self, vendor: str) -> None:
        self._vendor = vendor
        self._seqs = SeqAllocator(1)
        self._next_index = 0
        self._channel: str | None = None
        self._channel_seq: Seq = ()
        self._tool_seqs: dict[int, Seq] = {}
        self._text_ranges: dict[Seq, tuple[int, int]] = {}
        self._text_length = 0

    def _allocate(self) -> Seq:
        """다음 블록 번호로 seq를 발급한다."""
        index = self._next_index
        self._next_index += 1
        return self._seqs.coord((index,))

    def _segment(self, channel: str) -> Seq:
        """채널이 바뀌었으면 새 seq를, 같은 채널이면 이어 쓰는 블록의 seq를 돌려준다."""
        if self._channel != channel:
            self._channel = channel
            self._channel_seq = self._allocate()
        return self._channel_seq

    def map(self, chunk: dict[str, Any]) -> list[HubResponse]:
        choices = chunk.get("choices") or []
        head: dict[str, Any] = choices[0] if choices else {}
        delta = head.get("delta") or head.get("message") or {}

        blocks: list[ContentBlock] = []

        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            blocks.append(
                ThinkingBlock(
                    thinking=reasoning, seq=self._segment("reasoning"), vendor=self._vendor
                )
            )

        content = delta.get("content")
        if isinstance(content, str) and content:
            seq = self._segment("content")
            start = self._text_ranges.get(seq, (self._text_length, self._text_length))[0]
            self._text_length += len(content)
            self._text_ranges[seq] = (start, self._text_length)
            blocks.append(TextBlock(text=content, seq=seq, vendor=self._vendor))

        # 거부도 사용자가 봐야 하는 본문이다. 다만 답변과 구분되게 표시를 붙인다.
        refusal = delta.get("refusal")
        if isinstance(refusal, str) and refusal:
            prefix = "" if self._channel == "refusal" else REFUSAL_PREFIX
            blocks.append(
                TextBlock(
                    text=prefix + refusal,
                    seq=self._segment("refusal"),
                    vendor=self._vendor,
                )
            )

        audio = delta.get("audio")
        if isinstance(audio, dict):
            blocks.append(
                AudioBlock(
                    seq=self._segment("audio"),
                    vendor=self._vendor,
                    native=dict(audio),
                    file_id=audio.get("id"),
                    data=audio.get("data"),
                    transcript=audio.get("transcript"),
                    expires_at=audio.get("expires_at"),
                )
            )

        for annotation in delta.get("annotations") or []:
            if isinstance(annotation, dict):
                blocks.append(self._annotation(annotation))

        for position, call in enumerate(delta.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            blocks.append(self._tool_block(call, position))

        usage = self._usage(chunk.get("usage"))
        finish = head.get("finish_reason")

        response = HubResponse(
            id=chunk.get("id"),
            model=chunk.get("model"),
            role=delta.get("role"),
            content=blocks,
            stop_reason=stop_reason_from_chat(finish if isinstance(finish, str) else None),
            usage=usage,
        )
        return [response]

    def flush(self) -> list[HubResponse]:
        return []

    def _tool_block(self, call: dict[str, Any], position: int) -> ToolUseBlock:
        """도구 호출 조각 하나를 블록으로.

        벤더가 보내지 않은 필드는 넣지 않는다. 빈 문자열로 채워 넣으면 병합기가 그것을
        "이 델타에 실린 값"으로 보고 앞서 받은 ``id``와 ``name``을 지운다. 델타 스트림에서
        ``id``와 ``name``은 첫 조각에만 오고 이후에는 ``arguments``만 온다.
        """
        kind = call.get("type")
        fn = call.get("function") or call.get("custom") or {}
        raw_index = call.get("index")
        key = raw_index if isinstance(raw_index, int) else position
        if key not in self._tool_seqs:
            self._tool_seqs[key] = self._allocate()
        self._channel = None
        fields: dict[str, Any] = {
            "seq": self._tool_seqs[key],
            "vendor": self._vendor,
            "native": dict(call),
        }
        if isinstance(kind, str) and kind:
            fields["kind"] = kind
        if call.get("id"):
            fields["id"] = call["id"]
        if fn.get("name"):
            fields["name"] = fn["name"]
        arguments = fn.get("arguments")
        if isinstance(arguments, str) and arguments:
            fields["input_json"] = arguments
        return ToolUseBlock(**fields)

    def _annotation(self, annotation: dict[str, Any]) -> AnnotationBlock:
        """annotation 하나를 대상 text 블록 뒤에 올 블록으로 만든다.

        wire의 문자 범위가 이미 받은 text 블록 하나 안에 있으면 그 블록을 대상으로 삼고 범위를
        블록 기준 오프셋으로 바꾼다. 받은 text 블록이 하나뿐이고 범위가 없으면 그 블록을
        대상으로 삼는다. 대상이 없으면 지금까지 받은 블록 중 맨 끝 블록 뒤에 둔다.
        """
        detail = annotation.get("url_citation")
        if not isinstance(detail, dict):
            detail = annotation
        fields: dict[str, Any] = {
            "vendor": self._vendor,
            "kind": str(annotation.get("type") or "annotation"),
            "native": dict(annotation),
        }
        url = detail.get("url")
        title = detail.get("title")
        if isinstance(url, str):
            fields["id"] = url
            fields["uri"] = url
        if isinstance(title, str):
            fields["title"] = title
        file_id = detail.get("file_id")
        if isinstance(file_id, str) and file_id:
            fields["file_id"] = file_id
            fields.setdefault("id", file_id)
        for key in ("start_index", "end_index"):
            if isinstance(detail.get(key), int):
                fields[key] = detail[key]
        # Wire offsets address concatenated content; hub offsets address the target block.
        start, end = fields.get("start_index"), fields.get("end_index")
        if isinstance(start, int) and isinstance(end, int):
            for seq, (left, right) in self._text_ranges.items():
                if left <= start < end <= right:
                    fields.update(target_seq=seq, start_index=start - left, end_index=end - left)
                    break
        elif len(self._text_ranges) == 1:
            fields["target_seq"] = next(iter(self._text_ranges))
        fields["seq"] = self._seqs.after(fields.get("target_seq"))
        return AnnotationBlock(**fields)

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        return Usage(
            input_tokens=raw.get("prompt_tokens"),
            output_tokens=raw.get("completion_tokens"),
            native=dict(raw),
        )


def _merge_value(target: BaseModel | dict[str, Any], key: str, value: Any) -> None:
    """chunk 필드 하나를 누적 중인 객체의 같은 ``key``에 반영한다.

    객체와 리스트는 첫 값부터 재귀 병합해 같은 index의 조각을 누적한다. ``None``은 기존 값을
    유지하고 :data:`_REPLACED_FIELDS`는 나중 값을 쓴다. 문자열 조각은 연결하고 숫자는 합산한다.
    객체는 key별로 재귀 반영하고 리스트는 :func:`_merge_items`로 반영한다. 그 밖의 값은 나중
    값으로 바꾼다.
    """
    if isinstance(target, BaseModel):
        present = key in target.model_fields_set or key in (target.model_extra or {})
        current = getattr(target, key, None)
    else:
        present = key in target
        current = target.get(key)
    if not present or current is None:
        # 첫 chunk 안에도 같은 tool index의 조각이 여러 개 있을 수 있다.
        if isinstance(value, dict):
            initial: Any = {}
        elif isinstance(value, list):
            initial = []
        else:
            initial = value
        if isinstance(target, BaseModel):
            setattr(target, key, initial)
            current = getattr(target, key)
        else:
            target[key] = initial
            current = initial
        if not isinstance(value, (dict, list)):
            return
    if value is None:
        return
    if current is None or key in _REPLACED_FIELDS:
        merged = value
    elif isinstance(current, str) and isinstance(value, str):
        merged = current + value
    elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
        merged = current + value
    elif isinstance(current, (BaseModel, dict)) and isinstance(value, dict):
        for field in value:
            _merge_value(current, field, value[field])
        return
    elif isinstance(current, list) and isinstance(value, list):
        typed_tools = isinstance(target, ChatCompletionsMessage) and key == "tool_calls"
        _merge_items(current, value, typed_tools=typed_tools)
        return
    else:
        merged = value
    if isinstance(target, BaseModel):
        setattr(target, key, merged)
    else:
        target[key] = merged


def _merge_items(current: list[Any], items: list[Any], *, typed_tools: bool = False) -> None:
    """리스트 조각의 원소를 누적 중인 리스트에 반영한다.

    정수 ``index``를 가진 객체는 같은 ``index``의 기존 객체에 :func:`_merge_value`로 반영한다.
    같은 ``index``의 객체가 없거나 ``index``가 없는 원소는 끝에 추가한다. ``tool_calls``가 앞의
    경우이고 ``logprobs.content``가 뒤의 경우다. ``typed_tools``이면 새 항목을
    :class:`ChatCompletionsToolCall` 객체로 만든다.
    """
    for item in items:
        if typed_tools and not isinstance(item, dict):
            raise MappingError("chat completions tool call must be a JSON object")
        existing: BaseModel | dict[str, Any] | None = None
        if isinstance(item, dict) and isinstance(item.get("index"), int):
            for candidate in current:
                candidate_index = None
                if isinstance(candidate, BaseModel):
                    candidate_index = getattr(candidate, "index", None)
                elif isinstance(candidate, dict):
                    candidate_index = candidate.get("index")
                if candidate_index == item["index"]:
                    existing = candidate
                    break
        if existing is None:
            if not isinstance(item, dict):
                current.append(item)
                continue
            if typed_tools:
                existing = ChatCompletionsToolCall()
            else:
                existing = {}
            current.append(existing)
        for field in item:
            _merge_value(existing, field, item[field])


class _ToNative:
    """Chat Completions chunk를 ``ChatCompletion`` 객체 하나로 누적한다.

    ``choices`` 외 최상위 필드는 ``None``이 아닌 나중 값을 쓴다. choice는 ``index``별로 모으고
    ``index``가 없으면 chunk 안 위치를 쓴다. choice의 ``delta``는 ``message``에 반영하며 choice
    필드의 반영 규칙은 :func:`_merge_value`다. 결과의 ``object``는 ``chat.completion``이다.
    """

    def __init__(self) -> None:
        self._completion = ChatCompletionsResponse(object="chat.completion", choices=[])
        self._choices: dict[int, ChatCompletionsChoice] = {}

    def apply(self, chunk: dict[str, Any]) -> None:
        """chunk 하나를 누적한다.

        Raises:
            MappingError: ``choices``가 리스트가 아니거나, choice나 ``delta``가 객체가 아닐 때.
        """
        # 최상위 필드: id, model, usage 등은 None이 아닌 나중 값을 쓴다.
        for key in chunk:
            if key in ("choices", "object"):
                continue
            value = chunk[key]
            present = key in self._completion.model_fields_set or key in (
                self._completion.model_extra or {}
            )
            if value is not None or not present:
                setattr(self._completion, key, value)

        # choices: index별 choice에 delta를 message로 반영한다.
        choices = chunk.get("choices")
        if choices is None:
            return
        if not isinstance(choices, list):
            raise MappingError("chat completions chunk choices must be a list")
        for position in range(len(choices)):
            choice = choices[position]
            if not isinstance(choice, dict):
                raise MappingError(f"chat completions choice {position} must be a JSON object")
            raw_index = choice.get("index")
            index = raw_index if isinstance(raw_index, int) else position
            merged = self._choices.get(index)
            if merged is None:
                merged = ChatCompletionsChoice(index=index, message=ChatCompletionsMessage())
                self._choices[index] = merged
            for key in choice:
                value = choice[key]
                if key == "delta":
                    if value is not None and not isinstance(value, dict):
                        raise MappingError(f"chat completions choice {index} delta is not object")
                    _merge_value(merged, "message", value)
                else:
                    _merge_value(merged, key, value)

    def build(self) -> ChatCompletionsResponse:
        """누적한 ``ChatCompletion`` 객체를 돌려준다. ``choices``는 ``index`` 오름차순이다."""
        choices: list[ChatCompletionsChoice] = []
        for index in sorted(self._choices):
            choices.append(self._choices[index])
        self._completion.choices = choices
        return self._completion


class ChatCompletionsAdapter:
    """``POST {base_url}/v1/chat/completions``."""

    path = "/v1/chat/completions"
    parameter_family = "chat_completions"

    def __init__(
        self,
        *,
        name: str = "chat_completions",
        reasoning_input_field: str | None = None,
    ) -> None:
        self.name = name
        self.reasoning_input_field = reasoning_input_field

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request.messages, lowerer),
            "stream": True,
        }
        tools = [
            definition
            for tool in request.tools
            if (definition := self._tool_definition(tool)) is not None
        ]
        if tools:
            body["tools"] = tools
        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        for key, value in params.items():
            if key in _RESERVED:
                continue
            body[key] = value
        return body

    def is_terminal(self, frame: SseEvent) -> bool:
        return frame.data.strip() == DONE

    def decode(self, frame: SseEvent) -> dict[str, Any] | None:
        payload = frame.data.strip()
        if not payload or payload == DONE:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MappingError("chat completions chunk is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MappingError("chat completions chunk must be a JSON object")
        return parsed

    def to_hub(self) -> StreamMapper[Any, Any]:
        return _ToHub(self.name)

    def to_native(self) -> NativeAccumulator[ChatCompletionsResponse]:
        return _ToNative()

    def token_count_calls(
        self, body: dict[str, Any]
    ) -> Generator[JsonCall, dict[str, Any], TokenCount]:
        """vLLM ``/tokenize``와 ``/detokenize``로 토큰 수와 chat template 적용 결과를 얻는다.

        ``/tokenize``는 생성 전용 필드를 무시하므로 생성 body를 그대로 보낸다. 서버가 chat
        template을 적용해 토큰화한 ``count``와 ``tokens``를 받고, 그 ``tokens``를
        ``/detokenize``로 보내 특수 토큰을 포함한 프롬프트 문자열을 받는다.

        Raises:
            MappingError: ``/tokenize`` 응답에 정수 ``count``와 목록 ``tokens``가 없거나,
                ``/detokenize`` 응답에 문자열 ``prompt``가 없을 때.
        """
        tokenized = yield JsonCall("/tokenize", body)
        count = tokenized.get("count")
        tokens = tokenized.get("tokens")
        if not isinstance(count, int) or not isinstance(tokens, list):
            raise MappingError(
                "vLLM /tokenize response must have an integer count and a tokens list"
            )

        detokenized = yield JsonCall("/detokenize", {"model": body["model"], "tokens": tokens})
        prompt = detokenized.get("prompt")
        if not isinstance(prompt, str):
            raise MappingError("vLLM /detokenize response must have a string prompt")
        return TokenCount(
            input_tokens=count,
            tokenized=TokenizedPrompt(text=prompt, token_ids=tokens),
        )

    # ---- 요청 쪽 내림 ----

    def _messages(self, messages: list[HubMessage], lowerer: Lowerer) -> list[dict[str, Any]]:
        """허브 메시지를 wire 메시지로 펼친다.

        한 허브 메시지가 여러 wire 메시지가 될 수 있다. ``tool_result`` 블록이 role ``tool``의
        별도 메시지로 나가야 하기 때문이다. Java 초기 구현의 ``toMessage()``가 단일 반환이라
        이 경우를 표현할 수 없었고, ``streambind-base``에서 ``toMessages()``로 바뀐 이유가 이것이다.
        """
        out: list[dict[str, Any]] = []
        for message in messages:
            pending: list[ContentBlock] = []
            blocks = list(message.content)
            # Only dense order indices are sortable; vocabulary/vendor keys are opaque.
            if blocks and {block.index for block in blocks} == set(range(len(blocks))):
                blocks.sort(key=lambda block: block.index if block.index is not None else 0)
            for block in blocks:
                if not isinstance(block, ToolResultBlock):
                    pending.append(block)
                    continue
                if not can_replay_client_tool(block, self.name):
                    continue
                wire = self._message(message.role, pending, lowerer)
                if wire is not None:
                    out.append(wire)
                pending = []
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": self._tool_result_text(block, lowerer),
                    }
                )
            wire = self._message(message.role, pending, lowerer)
            if wire is not None:
                out.append(wire)
        return out

    def _message(
        self, role: str, blocks: list[ContentBlock], lowerer: Lowerer
    ) -> dict[str, Any] | None:
        tool_calls = [
            b
            for b in blocks
            if isinstance(b, ToolUseBlock) and can_replay_client_tool(b, self.name)
        ]
        reasoning = [
            b.thinking
            for b in blocks
            if isinstance(b, ThinkingBlock) and b.vendor == self.name and self.reasoning_input_field
        ]
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []

        def flush_plain() -> None:
            text_parts = lowerer.lower_text_parts(plain)
            plain.clear()
            for text in text_parts:
                parts.append({"type": "text", "text": text})

        for block in blocks:
            if has_opaque_media_reference(block):
                continue
            if isinstance(block, ToolUseBlock):
                continue
            if isinstance(block, ThinkingBlock):
                if block.vendor == self.name and self.reasoning_input_field:
                    continue
                plain.append(block)
                continue
            if isinstance(block, AudioBlock) and role == "assistant":
                # Chat의 이전 assistant audio는 입력 Part가 아니라 audio ID 참조다.
                continue
            if isinstance(block, ImageBlock) and role == "assistant":
                # assistant content는 text/refusal만 허용한다. image_url은 user 입력 전용이다.
                continue
            if isinstance(block, DocumentBlock) and role == "assistant":
                # file Part도 user 입력 전용이다. 남길 수 있는 설명만 text fallback으로 보낸다.
                plain.append(block)
                continue
            if (
                isinstance(block, ServerToolBlock | VendorBlock)
                and block.vendor == self.name
                and block.raw
            ):
                flush_plain()
                parts.append(dict(block.raw))
                continue
            part = as_chat_completions_part(block)
            if part is None:
                plain.append(block)
                continue
            flush_plain()
            parts.append(part)
        flush_plain()

        if not parts and not tool_calls and not reasoning:
            return None
        wire: dict[str, Any] = {"role": role}
        if parts:
            if all(part.get("type") == "text" for part in parts) and (
                role == "assistant" or len(parts) == 1
            ):
                wire["content"] = "".join(part["text"] for part in parts)
            else:
                wire["content"] = parts
        elif tool_calls:
            wire["content"] = None
        if tool_calls:
            wire["tool_calls"] = [self._tool_call(block) for block in tool_calls]
        if reasoning and self.reasoning_input_field:
            wire[self.reasoning_input_field] = "".join(reasoning)
        return wire

    def _tool_call(self, block: ToolUseBlock) -> dict[str, Any]:
        if block.vendor == self.name and block.native:
            call = dict(block.native)
            # ``index`` identifies a tool-call delta inside a streamed response.
            # It is not part of an assistant tool call accepted in request history.
            call.pop("index", None)
            # A streamed tool call carries its ``id`` on the first delta only, and merging
            # keeps the last ``native`` seen, so the id is usually absent here. The server
            # rejects an assistant tool call without one.
            if not call.get("id") and block.id:
                call["id"] = block.id
        else:
            call = {"id": block.id, "type": block.kind}
        if block.kind == "custom":
            call["custom"] = {"name": block.name, "input": block.input_json}
        else:
            call["type"] = "function"
            call["function"] = {"name": block.name, "arguments": block.input_json or "{}"}
        return call

    @staticmethod
    def _tool_result_text(block: ToolResultBlock, lowerer: Lowerer) -> str:
        """tool 메시지 본문. 이 API의 tool 메시지는 문자열만 받는다.

        ``blocks``를 text로 바꾼 결과가 있으면 ``content`` 뒤에 그것을 쓰고
        ``structured_content``는 보내지 않는다. 없으면 ``structured_content`` JSON, 그것도
        없으면 ``content``를 쓴다.
        """
        nested = lowerer.lower_text(
            b
            for b in block.blocks
            if not isinstance(b, ContentBlock) or not has_opaque_media_reference(b)
        )
        if nested:
            return f"{block.content}\n{nested}" if block.content else nested
        if block.structured_content is not None:
            return json.dumps(block.structured_content, ensure_ascii=False)
        return block.content

    def _tool_definition(self, tool: ToolDefinition) -> dict[str, Any] | None:
        native = tool.native_for(self.name)
        if native is not None:
            return native
        if tool.vendor is not None:
            return None
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }


chat_completions = ChatCompletionsAdapter()
"""기본 인스턴스. 어댑터가 상태를 갖지 않으므로 공유해도 된다."""
