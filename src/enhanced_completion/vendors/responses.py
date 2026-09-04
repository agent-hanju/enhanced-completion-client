"""OpenAI Responses API 어댑터.

**이 벤더는 이벤트 이름에 계층이 있다.** ``response.output_text.delta`` 같은 점 구분 이름이
마흔 개를 넘는다. 이름을 통째로 나열하지 않고 규칙으로 읽는다. 접두가 무엇의 이벤트인지를,
접미가 어떤 단계인지를 말한다.

그래서 서버가 새 도구 계열을 추가해도 어댑터를 고칠 일이 적다. ``response.<something>_call.*``
꼴이면 도구 진행 보고로 흘려보낸다. 지금 존재하는 것만 나열했다면 계열이 늘 때마다 스트림이
알 수 없는 이벤트로 막혔을 것이다.

블록 인덱스는 ``output_index``와 ``content_index``를 합쳐 만든다. 서버가 두 축으로 위치를
주기 때문이다. 하나만 쓰면 같은 item 안의 여러 content part가 한 블록으로 뭉친다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks import (
    AudioBlock,
    CitationBlock,
    ContentBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from ..errors import MappingError
from ..hub import HubRequest, HubResponse, ToolDefinition, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer
from .normalize import REFUSAL_PREFIX, stop_reason_from_responses
from .parts import as_responses_part

__all__ = ["ResponsesAdapter", "responses"]

SOURCE = "responses"
TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.failed", "response.incomplete", "error"}
)

# 한 item 안의 여러 content part를 구분하려면 두 축이 필요하다.
_CONTENT_STRIDE = 1 << 32
_REASONING_TEXT_OFFSET = 1 << 31

_CLIENT_TOOL_ITEMS = frozenset(
    {
        "function_call",
        "custom_tool_call",
        "computer_call",
        "local_shell_call",
        "shell_call",
        "apply_patch_call",
    }
)
_SERVER_TOOL_ITEMS = frozenset(
    {
        "web_search_call",
        "file_search_call",
        "code_interpreter_call",
        "image_generation_call",
        "mcp_call",
        "mcp_list_tools",
    }
)
_TOOL_RESULT_ITEMS = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "computer_call_output",
        "local_shell_call_output",
        "shell_call_output",
        "apply_patch_call_output",
    }
)


def _slot(event: dict[str, Any]) -> int:
    """``output_index``와 ``content_index``를 하나의 블록 키로 접는다."""
    output = event.get("output_index")
    content = event.get("content_index")
    base = output * _CONTENT_STRIDE if isinstance(output, int) else 0
    return base + (content if isinstance(content, int) else 0)


class _ToHub:
    """Responses 이벤트를 허브 델타로 바꾼다."""

    def __init__(self) -> None:
        self._refusal_started = False
        self._text_streamed: set[int] = set()
        self._refusal_streamed: set[int] = set()
        self._reasoning_streamed: set[int] = set()
        self._reasoning_item_slots: dict[int, int] = {}
        self._arguments_streamed: set[int] = set()
        self._audio_streamed: set[int] = set()
        self._audio_transcript_streamed: set[int] = set()
        self._done_items: set[int] = set()

    def map(self, event: dict[str, Any]) -> list[HubResponse]:
        name = event.get("type")
        if not isinstance(name, str):
            return []

        # 본문
        if name == "response.output_text.delta":
            index = _slot(event)
            self._text_streamed.add(index)
            return self._one(TextBlock(text=event.get("delta") or "", index=index))
        if name == "response.refusal.delta":
            index = _slot(event)
            self._refusal_streamed.add(index)
            prefix = "" if self._refusal_started else REFUSAL_PREFIX
            self._refusal_started = True
            return self._one(TextBlock(text=prefix + (event.get("delta") or ""), index=index))

        # 추론 요약. 원문 추론은 암호화되어 오므로 요약만 텍스트로 쓸 수 있다.
        if name == "response.reasoning_summary_text.delta":
            index = _slot(event)
            self._reasoning_streamed.add(index)
            self._reasoning_item_slots[index] = index
            return self._one(ThinkingBlock(thinking=event.get("delta") or "", index=index))
        if name == "response.reasoning_text.delta":
            item_index = _slot(event)
            index = item_index + _REASONING_TEXT_OFFSET
            self._reasoning_streamed.add(index)
            self._reasoning_item_slots.setdefault(item_index, index)
            return self._one(ThinkingBlock(thinking=event.get("delta") or "", index=index))

        if name == "response.audio.delta":
            index = _slot(event)
            self._audio_streamed.add(index)
            return self._one(AudioBlock(data=event.get("delta") or "", index=index))
        if name == "response.audio_transcript.delta":
            index = _slot(event)
            self._audio_transcript_streamed.add(index)
            return self._one(AudioBlock(transcript=event.get("delta") or "", index=index))

        # 도구 인수
        if name in (
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
        ):
            index = _slot(event)
            self._arguments_streamed.add(index)
            kind = "custom" if ".custom_tool_" in name else "function"
            return self._one(
                ToolUseBlock(
                    input_json=event.get("delta") or "",
                    index=index,
                    kind=kind,
                )
            )
        if name == "response.mcp_call_arguments.delta":
            return self._one(
                ServerToolBlock(
                    name="mcp_call",
                    input_json=event.get("delta") or "",
                    index=_slot(event),
                )
            )

        # 인용. 이 벤더는 annotation이라 부른다.
        if name == "response.output_text.annotation.added":
            return self._annotation(event)

        # item 등장. 도구 호출의 식별자와 이름이 여기 실린다.
        if name == "response.output_item.added":
            return self._item_added(event)
        if name == "response.output_item.done":
            return self._item_done(event)

        # 수명주기
        if name in ("response.created", "response.in_progress", "response.queued"):
            return self._response_meta(event)
        if name in TERMINAL_EVENTS:
            return self._terminal(name, event)

        # content/argument 완료 통보는 델타의 중복이다. item.done만 원본 갱신에 쓴다.
        if name.endswith(".done") or name.endswith(".added"):
            return []

        # 도구 진행 보고. 계열이 늘어도 규칙으로 걸린다.
        if "_call" in name or ".mcp_" in name:
            stage = name.rsplit(".", 1)[-1]
            family = name.removeprefix("response.").rsplit(".", 1)[0]
            return self._one(
                ServerToolBlock(
                    name=family,
                    status=stage,
                    index=_slot(event),
                    raw=dict(event),
                )
            )
        return []

    def flush(self) -> list[HubResponse]:
        return []

    @staticmethod
    def _one(block: ContentBlock) -> list[HubResponse]:
        block.source = SOURCE
        return [HubResponse(content=[block])]

    def _annotation(self, event: dict[str, Any]) -> list[HubResponse]:
        """annotation을 허브 :class:`CitationBlock`으로.

        이 벤더는 인용을 annotation이라 부른다. ``url_citation``은 웹 근거,
        ``file_citation``은 업로드한 파일 근거, ``file_path``는 코드 실행이 만든 파일 참조다.
        앞의 둘은 인용이고 마지막은 산출물 경로라 성질이 다르다.

        ``start_index``/``end_index``는 답변 문자열 안의 위치다. Anthropic이 원문 좌표를 주는
        것과 축이 다르므로 이쪽은 답변 좌표를 채운다.

        ``.added`` 접미를 일괄 무시하면 이 이벤트가 함께 사라진다. 그래서 위에서 먼저 걸러야
        한다.
        """
        annotation = event.get("annotation") or {}
        kind = annotation.get("type")
        if kind == "file_path":
            # 인용이 아니라 산출물 경로다. 원본을 보존한다.
            return self._one(VendorBlock(type="responses_file_path", raw=dict(annotation)))

        fields: dict[str, Any] = {
            "source": SOURCE,
            "source_kind": kind or "annotation",
            "native": dict(annotation),
        }
        identifier = annotation.get("url") or annotation.get("file_id")
        if isinstance(identifier, str) and identifier:
            fields["id"] = identifier
        title = annotation.get("title") or annotation.get("filename")
        if isinstance(title, str) and title:
            fields["document_title"] = title
        index = annotation.get("index")
        if isinstance(index, int):
            fields["document_index"] = index
        for key, dst in (("start_index", "start_index"), ("end_index", "end_index")):
            value = annotation.get(key)
            if isinstance(value, int):
                fields[dst] = value
        return [HubResponse(content=[CitationBlock(**fields)])]

    def _item_added(self, event: dict[str, Any]) -> list[HubResponse]:
        item = event.get("item") or {}
        return self._item(item, event, final=False)

    def _item_done(self, event: dict[str, Any]) -> list[HubResponse]:
        item = event.get("item") or {}
        output_index = event.get("output_index")
        if isinstance(output_index, int):
            self._done_items.add(output_index)
        return self._item(item, event, final=True)

    def _item(
        self,
        item: dict[str, Any],
        event: dict[str, Any],
        *,
        final: bool,
    ) -> list[HubResponse]:
        kind = item.get("type")
        index = _slot(event)
        if kind in _CLIENT_TOOL_ITEMS:
            fields: dict[str, Any] = {
                "index": index,
                "source": SOURCE,
                "kind": "custom" if kind == "custom_tool_call" else str(kind).removesuffix("_call"),
                "native": dict(item),
            }
            call_id = item.get("call_id") or item.get("id")
            if call_id:
                fields["id"] = call_id
            if item.get("name"):
                fields["name"] = item["name"]
            raw_input = item.get("arguments")
            if raw_input is None:
                raw_input = item.get("input")
            if raw_input is None:
                raw_input = item.get("action")
            if index not in self._arguments_streamed and raw_input not in (None, "", {}):
                fields["input_json"] = (
                    raw_input
                    if isinstance(raw_input, str)
                    else json.dumps(raw_input, ensure_ascii=False)
                )
                self._arguments_streamed.add(index)
            return [HubResponse(content=[ToolUseBlock(**fields)])]
        if kind == "mcp_approval_request":
            return self._one(
                ToolUseBlock(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or "mcp_approval"),
                    kind="mcp_approval",
                    input=item,
                    index=index,
                    native=dict(item),
                )
            )
        if kind in _TOOL_RESULT_ITEMS:
            raw_output = item.get("output")
            content = raw_output if isinstance(raw_output, str) else ""
            structured = None if isinstance(raw_output, str) else raw_output
            result_kind = str(kind).removesuffix("_call_output")
            if result_kind == "custom_tool":
                result_kind = "custom"
            return self._one(
                ToolResultBlock(
                    tool_use_id=str(item.get("call_id") or item.get("id") or ""),
                    kind=result_kind,
                    content=content,
                    structured_content=structured,
                    native=dict(item),
                    index=index,
                )
            )
        if kind == "message":
            return self._message_item(item, event, final=final)
        if kind == "reasoning":
            return self._reasoning_item(item, index)
        if kind in _SERVER_TOOL_ITEMS:
            raw = dict(item)
            return self._one(
                ServerToolBlock(
                    name=str(kind),
                    id=str(item.get("id") or item.get("call_id") or ""),
                    status=item.get("status") if isinstance(item.get("status"), str) else None,
                    input_json=(item.get("arguments") or "")
                    if isinstance(item.get("arguments"), str)
                    else "",
                    output=str(item.get("output") or item.get("result") or ""),
                    index=index,
                    native=raw,
                    raw=raw,
                )
            )
        if isinstance(kind, str) and kind:
            return self._one(VendorBlock(type=f"responses_{kind}", raw=dict(item), index=index))
        return []

    def _message_item(
        self,
        item: dict[str, Any],
        event: dict[str, Any],
        *,
        final: bool,
    ) -> list[HubResponse]:
        blocks: list[ContentBlock] = []
        item_meta = {key: value for key, value in item.items() if key != "content"}
        output_index = event.get("output_index")
        for content_index, part in enumerate(item.get("content") or []):
            if not isinstance(part, dict):
                continue
            part_event = dict(event)
            part_event["content_index"] = content_index
            index = _slot(part_event)
            native = {"item": item_meta, "part": dict(part)}
            kind = part.get("type")
            if kind == "output_text":
                fields: dict[str, Any] = {"index": index, "native": native}
                if index not in self._text_streamed and isinstance(part.get("text"), str):
                    fields["text"] = part["text"]
                    self._text_streamed.add(index)
                blocks.append(TextBlock(source=SOURCE, **fields))
            elif kind == "refusal":
                fields = {"index": index, "native": native}
                if index not in self._refusal_streamed and isinstance(part.get("refusal"), str):
                    prefix = "" if self._refusal_started else REFUSAL_PREFIX
                    self._refusal_started = True
                    fields["text"] = prefix + part["refusal"]
                    self._refusal_streamed.add(index)
                blocks.append(TextBlock(source=SOURCE, **fields))
            elif kind in ("output_audio", "audio"):
                fields = {"index": index, "native": native}
                if index not in self._audio_streamed and isinstance(part.get("data"), str):
                    fields["data"] = part["data"]
                    self._audio_streamed.add(index)
                transcript = part.get("transcript")
                if index not in self._audio_transcript_streamed and isinstance(transcript, str):
                    fields["transcript"] = transcript
                    self._audio_transcript_streamed.add(index)
                blocks.append(AudioBlock(source=SOURCE, **fields))
            else:
                blocks.append(
                    VendorBlock(
                        type=f"responses_{kind or 'content'}",
                        raw=dict(part),
                        native=native,
                        source=SOURCE,
                        index=index,
                    )
                )
        if not blocks and final and isinstance(output_index, int):
            blocks.append(
                VendorBlock(
                    type="responses_message",
                    raw=dict(item),
                    source=SOURCE,
                    index=output_index * _CONTENT_STRIDE,
                )
            )
        return [HubResponse(content=blocks)] if blocks else []

    def _reasoning_item(self, item: dict[str, Any], index: int) -> list[HubResponse]:
        summaries = item.get("summary") or []
        summary = "\n".join(
            str(entry.get("text"))
            for entry in summaries
            if isinstance(entry, dict) and isinstance(entry.get("text"), str)
        )
        target_index = self._reasoning_item_slots.get(index, index)
        fields: dict[str, Any] = {"index": target_index, "native": dict(item)}
        if target_index not in self._reasoning_streamed and summary:
            fields["thinking"] = summary
            self._reasoning_streamed.add(target_index)
        encrypted = item.get("encrypted_content")
        if isinstance(encrypted, str) and encrypted:
            fields["encrypted_content"] = encrypted
        return self._one(ThinkingBlock(**fields))

    def _response_meta(self, event: dict[str, Any]) -> list[HubResponse]:
        response = event.get("response") or {}
        fields: dict[str, Any] = {}
        if isinstance(response.get("id"), str):
            fields["id"] = response["id"]
        if isinstance(response.get("model"), str):
            fields["model"] = response["model"]
        fields["role"] = "assistant"
        return [HubResponse(**fields)]

    def _terminal(self, name: str, event: dict[str, Any]) -> list[HubResponse]:
        if name == "error":
            detail = json.dumps(event, ensure_ascii=False)[:200]
            raise MappingError(f"responses stream reported an error: {detail}")

        response = event.get("response") or {}
        out: list[HubResponse] = []
        for output_index, item in enumerate(response.get("output") or []):
            if output_index in self._done_items or not isinstance(item, dict):
                continue
            out.extend(self._item_done({"output_index": output_index, "item": item}))
        for citation in response.get("citations") or []:
            if isinstance(citation, str):
                out.append(
                    HubResponse(
                        content=[
                            CitationBlock(
                                id=citation,
                                source_kind="response_citation",
                                source=SOURCE,
                                native={"citation": citation},
                            )
                        ]
                    )
                )
            elif isinstance(citation, dict):
                identifier = citation.get("url") or citation.get("id") or ""
                out.append(
                    HubResponse(
                        content=[
                            CitationBlock(
                                id=str(identifier),
                                document_title=citation.get("title"),
                                source_kind=str(citation.get("type") or "response_citation"),
                                source=SOURCE,
                                native=dict(citation),
                            )
                        ]
                    )
                )
        fields: dict[str, Any] = {}
        status = response.get("status")
        if name == "response.failed":
            fields["stop_reason"] = "error"
        elif name == "response.incomplete":
            reason = (response.get("incomplete_details") or {}).get("reason")
            fields["stop_reason"] = (
                "max_tokens"
                if reason == "max_output_tokens"
                else reason
                if isinstance(reason, str)
                else "max_tokens"
            )
        elif isinstance(status, str):
            fields["stop_reason"] = stop_reason_from_responses(status)
        usage = self._usage(response.get("usage"))
        if usage is not None:
            fields["usage"] = usage
        if response.get("server_side_tool_usage") is not None:
            fields["server_side_tool_usage"] = response["server_side_tool_usage"]
        if fields:
            out.append(HubResponse(**fields))
        return out

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("input_tokens"), int):
            fields["input_tokens"] = raw["input_tokens"]
        if isinstance(raw.get("output_tokens"), int):
            fields["output_tokens"] = raw["output_tokens"]
        return Usage(**fields) if fields else None


class ResponsesAdapter:
    """``POST {base_url}/v1/responses``."""

    name = SOURCE
    path = "/v1/responses"

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 Responses body로.

        이 API는 ``messages``가 아니라 ``input``을 받고 ``system``을 ``instructions``로 받는다.

        **``input``은 ``Item`` 리스트이고 ``Item`` 안에 다시 ``ContentPart`` 리스트가 있다.**
        두 층이다. 문자열 하나로 누르면 이미지, 음성, 파일 part를 실을 수 없다.

        도구 결과와 MCP 승인은 content part가 아니라 **별개 Item**이다.
        ``function_call_output``과 ``mcp_approval_response``가 그것이고, 클라이언트가 되보내는
        입력 항목이다.
        """
        instructions: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                text = lowerer.lower_text(message.content)
                if text:
                    instructions.append(text)
                continue
            turns.extend(self._items(message.role, message.content, lowerer))

        params = dict(request.params)
        body: dict[str, Any] = {
            "model": request.model,
            "input": turns,
            "stream": True,
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        if request.tools:
            body["tools"] = [self._tool_definition(tool) for tool in request.tools]
        for key, value in params.items():
            if key not in ("model", "input", "stream", "tools", "instructions"):
                body[key] = value
        return body

    def _items(
        self,
        role: str,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
    ) -> list[dict[str, Any]]:
        """한 허브 메시지를 Responses Item 0..N개로 펼친다.

        도구 호출과 결과, reasoning은 message의 content part가 아니다. 응답의 ``output``을
        수동 이력으로 되보낼 때도 Item 계층을 유지해야 한다.
        """
        items: list[dict[str, Any]] = []
        pending: list[ContentBlock] = []

        def flush() -> None:
            nonlocal pending
            if not pending:
                return
            item = self._message_item(role, pending, lowerer)
            pending = []
            if item is not None:
                items.append(item)

        for block in blocks:
            item: dict[str, Any] | None = None
            if isinstance(block, ToolResultBlock):
                item = self._tool_result_item(block, lowerer)
            elif isinstance(block, ToolUseBlock):
                item = self._tool_use_item(block)
            elif isinstance(block, ThinkingBlock) and block.source == SOURCE:
                item = self._reasoning_input(block)
            elif isinstance(block, ServerToolBlock) and block.source == SOURCE:
                item = dict(block.raw or block.native)
            elif isinstance(block, VendorBlock) and block.source == SOURCE:
                item = dict(block.raw)

            if item is None:
                pending.append(block)
                continue
            flush()
            items.append(item)
        flush()
        return items

    @staticmethod
    def _message_item(
        role: str,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
    ) -> dict[str, Any] | None:
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []
        item_meta: dict[str, Any] = {}
        annotations = [
            dict(block.native)
            for block in blocks
            if isinstance(block, CitationBlock) and block.source == SOURCE and block.native
        ]

        def flush_plain() -> None:
            text = lowerer.lower_text(plain)
            plain.clear()
            if text:
                kind = "output_text" if role == "assistant" else "input_text"
                parts.append({"type": kind, "text": text})

        for block in blocks:
            if isinstance(block, CitationBlock) and block.source == SOURCE and block.native:
                continue
            if isinstance(block, TextBlock) and block.source == SOURCE and block.native:
                native = block.native
                native_item = native.get("item")
                native_part = native.get("part")
                if isinstance(native_part, dict):
                    flush_plain()
                    part = dict(native_part)
                    if part.get("type") == "output_text":
                        part["text"] = block.text
                        if annotations and not part.get("annotations"):
                            part["annotations"] = annotations
                    parts.append(part)
                    if isinstance(native_item, dict) and not item_meta:
                        item_meta = dict(native_item)
                    continue
            if isinstance(block, AudioBlock) and block.source == SOURCE and block.native:
                native = block.native
                native_item = native.get("item")
                native_part = native.get("part")
                if isinstance(native_part, dict):
                    flush_plain()
                    part = dict(native_part)
                    if block.data is not None:
                        part["data"] = block.data
                    if block.transcript is not None:
                        part["transcript"] = block.transcript
                    parts.append(part)
                    if isinstance(native_item, dict) and not item_meta:
                        item_meta = dict(native_item)
                    continue
            rendered = as_responses_part(block)
            if rendered is None:
                plain.append(block)
                continue
            flush_plain()
            parts.append(rendered)
        flush_plain()

        if not parts:
            return None
        item = item_meta
        item.update({"type": "message", "role": role, "content": parts})
        return item

    @staticmethod
    def _tool_use_item(block: ToolUseBlock) -> dict[str, Any]:
        if block.source == SOURCE and block.native:
            item = dict(block.native)
            if block.kind == "mcp_approval":
                return item
        else:
            kind = block.kind or "function"
            wire_type = {
                "function": "function_call",
                "custom": "custom_tool_call",
            }.get(kind, f"{kind}_call")
            item = {"type": wire_type}
        if block.kind in ("function", "custom"):
            item["call_id"] = block.id
            if block.name:
                item["name"] = block.name
        elif block.id and block.source != SOURCE:
            item["id"] = block.id
        if block.kind == "custom":
            item["input"] = block.input_json
        elif block.kind == "function":
            item["arguments"] = block.input_json or "{}"
        elif block.input is not None:
            item["action"] = block.input
        return item

    @staticmethod
    def _tool_result_item(block: ToolResultBlock, lowerer: Lowerer) -> dict[str, Any]:
        if block.source == SOURCE and block.native:
            return dict(block.native)
        if block.kind == "mcp_approval":
            item: dict[str, Any] = {
                "type": "mcp_approval_response",
                "approval_request_id": block.tool_use_id,
                "approve": not bool(block.is_error),
            }
            if block.content:
                item["reason"] = block.content
            return item

        output: Any
        rendered_parts: list[dict[str, Any]] = []
        if block.content:
            rendered_parts.append({"type": "input_text", "text": block.content})
        for nested in block.blocks:
            if isinstance(nested, TextBlock):
                rendered_parts.append({"type": "input_text", "text": nested.text})
                continue
            if isinstance(nested, ContentBlock):
                part = as_responses_part(nested)
                if part is not None:
                    rendered_parts.append(part)
        if rendered_parts and (block.blocks or len(rendered_parts) > 1):
            output = rendered_parts
        elif block.structured_content is not None:
            output = json.dumps(block.structured_content, ensure_ascii=False)
        elif rendered_parts:
            output = rendered_parts[0]["text"]
        else:
            output = lowerer.lower_text(block.blocks)

        kind = block.kind or "function"
        wire_type = {
            "function": "function_call_output",
            "custom": "custom_tool_call_output",
        }.get(kind, f"{kind}_call_output")
        return {"type": wire_type, "call_id": block.tool_use_id, "output": output}

    @staticmethod
    def _reasoning_input(block: ThinkingBlock) -> dict[str, Any] | None:
        if block.native:
            item = dict(block.native)
            if block.encrypted_content:
                item["encrypted_content"] = block.encrypted_content
            return item
        if block.encrypted_content:
            return {
                "type": "reasoning",
                "encrypted_content": block.encrypted_content,
                "summary": [],
            }
        return None

    def _tool_definition(self, tool: ToolDefinition) -> dict[str, Any]:
        native = tool.native_for(self.name)
        if native is not None:
            return native
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    def is_terminal(self, frame: SseFrame) -> bool:
        name = frame.event.strip()
        if name in TERMINAL_EVENTS:
            return True
        return self._peek_type(frame) in TERMINAL_EVENTS

    def decode(self, frame: SseFrame) -> dict[str, Any] | None:
        payload = frame.data.strip()
        if not payload:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MappingError("responses chunk is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MappingError("responses chunk must be a JSON object")
        if not parsed.get("type") and frame.event.strip():
            parsed["type"] = frame.event.strip()
        return parsed

    @staticmethod
    def _peek_type(frame: SseFrame) -> str:
        payload = frame.data.strip()
        if not payload:
            return ""
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, dict):
            kind = parsed.get("type")
            if isinstance(kind, str):
                return kind
        return ""

    def to_hub(self) -> StreamMapper[Any, Any]:
        return _ToHub()


responses = ResponsesAdapter()
"""기본 인스턴스."""
