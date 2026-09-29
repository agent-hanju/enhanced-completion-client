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
from collections.abc import Generator
from typing import Any

from ..blocks import (
    AnnotationBlock,
    AudioBlock,
    CitationBlock,
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
from ..hub import HubRequest, HubResponse, TokenCount, ToolDefinition, Usage
from ..mapper import StreamMapper
from ..seq import Seq, SeqAllocator
from ..transport.sse import SseEvent
from .base import JsonCall, Lowerer
from .normalize import REFUSAL_PREFIX, stop_reason_from_responses
from .parts import as_responses_part, has_opaque_media_reference
from .tool_policy import can_replay_client_tool

__all__ = ["ResponsesAdapter", "responses"]

SOURCE = "responses"
TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.failed", "response.incomplete", "error"}
)

# seq 좌표는 (output_index, part_kind, part_index)다. reasoning item의 원문 추론 part는 같은
# item의 summary part와 구분되도록 part_kind 1을 쓰고, 나머지 part와 item 수준 블록은 0을 쓴다.
_PART = 0
_REASONING_TEXT = 1

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


def _slot(event: dict[str, Any]) -> Seq:
    """``output_index``와 ``content_index``로 part 좌표를 만든다. 없는 값은 0이다."""
    output = event.get("output_index")
    content = event.get("content_index")
    return (
        output if isinstance(output, int) else 0,
        _PART,
        content if isinstance(content, int) else 0,
    )


def _item_slot(event: dict[str, Any]) -> Seq:
    """``output_index``로 item 수준 좌표를 만든다. 없는 값은 0이다."""
    output = event.get("output_index")
    return (output if isinstance(output, int) else 0, _PART, 0)


def _summary_slot(event: dict[str, Any]) -> Seq:
    """reasoning summary part의 좌표.

    이 이벤트는 ``content_index`` 대신 ``summary_index``를 준다. 없는 값은 0이다.
    """
    output = event.get("output_index")
    summary = event.get("summary_index")
    return (
        output if isinstance(output, int) else 0,
        _PART,
        summary if isinstance(summary, int) else 0,
    )


class _ToHub:
    """Responses 이벤트를 허브 델타로 바꾼다."""

    def __init__(self) -> None:
        self._seqs = SeqAllocator(3)
        self._refusal_started = False
        self._text_streamed: set[Seq] = set()
        self._refusal_streamed: set[Seq] = set()
        self._reasoning_streamed: set[Seq] = set()
        self._reasoning_item_slots: dict[Seq, Seq] = {}
        self._arguments_streamed: set[Seq] = set()
        self._audio_streamed: set[Seq] = set()
        self._audio_transcript_streamed: set[Seq] = set()
        self._annotations_seen: set[tuple[Seq, int]] = set()
        self._done_items: set[int] = set()

    def map(self, event: dict[str, Any]) -> list[HubResponse]:
        name = event.get("type")
        if not isinstance(name, str):
            return []

        # 본문
        if name == "response.output_text.delta":
            seq = self._seqs.coord(_slot(event))
            self._text_streamed.add(seq)
            return self._one(TextBlock(text=event.get("delta") or "", seq=seq))
        if name == "response.refusal.delta":
            seq = self._seqs.coord(_slot(event))
            self._refusal_streamed.add(seq)
            prefix = "" if self._refusal_started else REFUSAL_PREFIX
            self._refusal_started = True
            return self._one(TextBlock(text=prefix + (event.get("delta") or ""), seq=seq))

        # 추론 요약. 원문 추론은 암호화되어 오므로 요약만 텍스트로 쓸 수 있다.
        if name == "response.reasoning_summary_text.delta":
            seq = self._seqs.coord(_summary_slot(event))
            self._reasoning_streamed.add(seq)
            self._reasoning_item_slots.setdefault(_item_slot(event), seq)
            return self._one(ThinkingBlock(thinking=event.get("delta") or "", seq=seq))
        if name == "response.reasoning_text.delta":
            output, _, part = _slot(event)
            seq = self._seqs.coord((output, _REASONING_TEXT, part))
            self._reasoning_streamed.add(seq)
            self._reasoning_item_slots.setdefault(_item_slot(event), seq)
            return self._one(ThinkingBlock(thinking=event.get("delta") or "", seq=seq))

        if name == "response.audio.delta":
            seq = self._audio_seq(event)
            self._audio_streamed.add(seq)
            return self._one(AudioBlock(data=event.get("delta") or "", seq=seq))
        if name in ("response.audio.transcript.delta", "response.audio_transcript.delta"):
            seq = self._audio_seq(event)
            self._audio_transcript_streamed.add(seq)
            return self._one(AudioBlock(transcript=event.get("delta") or "", seq=seq))

        # 도구 인수
        if name in (
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
        ):
            seq = self._seqs.coord(_slot(event))
            self._arguments_streamed.add(seq)
            kind = "custom" if ".custom_tool_" in name else "function"
            return self._one(
                ToolUseBlock(
                    input_json=event.get("delta") or "",
                    seq=seq,
                    kind=kind,
                )
            )
        if name == "response.mcp_call_arguments.delta":
            return self._one(
                ServerToolBlock(
                    name="mcp_call",
                    input_json=event.get("delta") or "",
                    seq=self._seqs.coord(_slot(event)),
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
                    seq=self._seqs.coord(_slot(event)),
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

    def _audio_seq(self, event: dict[str, Any]) -> Seq:
        """audio 이벤트의 seq.

        이벤트에 ``output_index``나 ``content_index``가 있으면 그 part의 seq를 쓴다. 현재 audio
        stream 이벤트에는 두 값이 없으므로, 처음 받은 시점의 맨 끝 블록 바로 뒤에 seq를 한 번
        발급하고 이후의 audio/transcript 조각에도 같은 seq를 쓴다.
        """
        if isinstance(event.get("output_index"), int) or isinstance(
            event.get("content_index"), int
        ):
            return self._seqs.coord(_slot(event))
        return self._seqs.after(key=("audio",))

    def _annotation(self, event: dict[str, Any]) -> list[HubResponse]:
        """output text annotation을 독립 허브 블록으로.

        이 벤더는 인용을 annotation이라 부른다. ``url_citation``은 웹 근거,
        ``file_citation``은 업로드한 파일 근거, ``file_path``는 코드 실행이 만든 파일 참조다.
        앞의 둘은 인용이고 마지막은 산출물 경로라 성질이 다르다.

        ``start_index``/``end_index``는 답변 문자열 안의 위치다. text보다 늦게 올 수 있으므로
        이미 전달한 text block을 소급 분할하지 않는다.

        ``.added`` 접미를 일괄 무시하면 이 이벤트가 함께 사라진다. 그래서 위에서 먼저 걸러야
        한다.

        블록은 대상 part 블록 바로 뒤의 seq를 받는다. 대상 part와 벤더 ``annotation_index``가
        같은 annotation을 다시 받으면 아무것도 만들지 않는다.
        """
        annotation = event.get("annotation") or {}
        kind = annotation.get("type")
        target_seq = self._seqs.coord(_slot(event))
        if kind == "file_path":
            # 인용이 아니라 산출물 경로다. 원본을 보존한다.
            return self._one(
                VendorBlock(
                    type="responses_file_path",
                    raw=dict(annotation),
                    native={"level": "annotation"},
                    seq=self._seqs.after(target_seq),
                )
            )

        raw_annotation_index = event.get("annotation_index")
        annotation_index = (
            raw_annotation_index if isinstance(raw_annotation_index, int) else 0
        )
        seen_key = (target_seq, annotation_index)
        if seen_key in self._annotations_seen:
            return []
        self._annotations_seen.add(seen_key)
        fields: dict[str, Any] = {
            "source": SOURCE,
            "seq": self._seqs.after(target_seq),
            "target_seq": target_seq,
            "kind": kind or "annotation",
            "native": dict(annotation),
        }
        identifier = annotation.get("url") or annotation.get("file_id")
        if isinstance(identifier, str) and identifier:
            fields["id"] = identifier
        title = annotation.get("title") or annotation.get("filename")
        if isinstance(title, str) and title:
            fields["title"] = title
        url = annotation.get("url")
        if isinstance(url, str) and url:
            fields["uri"] = url
        file_id = annotation.get("file_id")
        if isinstance(file_id, str) and file_id:
            fields["file_id"] = file_id
        for key, dst in (("start_index", "start_index"), ("end_index", "end_index")):
            value = annotation.get(key)
            if isinstance(value, int):
                fields[dst] = value
        return [HubResponse(content=[AnnotationBlock(**fields)])]

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
        coords = _slot(event)
        if kind in _CLIENT_TOOL_ITEMS:
            seq = self._seqs.coord(coords)
            fields: dict[str, Any] = {
                "seq": seq,
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
            if seq not in self._arguments_streamed and raw_input not in (None, "", {}):
                fields["input_json"] = (
                    raw_input
                    if isinstance(raw_input, str)
                    else json.dumps(raw_input, ensure_ascii=False)
                )
                self._arguments_streamed.add(seq)
            return [HubResponse(content=[ToolUseBlock(**fields)])]
        if kind == "mcp_approval_request":
            return self._one(
                ToolUseBlock(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or "mcp_approval"),
                    kind="mcp_approval",
                    input=item,
                    seq=self._seqs.coord(coords),
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
                    seq=self._seqs.coord(coords),
                )
            )
        if kind == "message":
            return self._message_item(item, event, final=final)
        if kind == "reasoning":
            return self._reasoning_item(item, coords)
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
                    seq=self._seqs.coord(coords),
                    native=raw,
                    raw=raw,
                )
            )
        if isinstance(kind, str) and kind:
            return self._one(
                VendorBlock(
                    type=f"responses_{kind}",
                    raw=dict(item),
                    native={"level": "item"},
                    seq=self._seqs.coord(coords),
                )
            )
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
            seq = self._seqs.coord(_slot(part_event))
            native = {"item": item_meta, "part": dict(part)}
            kind = part.get("type")
            if kind == "output_text":
                fields: dict[str, Any] = {"seq": seq, "native": native}
                if seq not in self._text_streamed and isinstance(part.get("text"), str):
                    fields["text"] = part["text"]
                    self._text_streamed.add(seq)
                blocks.append(TextBlock(source=SOURCE, **fields))
                for annotation_index, annotation in enumerate(part.get("annotations") or []):
                    if not isinstance(annotation, dict):
                        continue
                    annotation_event = dict(part_event)
                    annotation_event.update(
                        {
                            "annotation_index": annotation_index,
                            "annotation": annotation,
                        }
                    )
                    for delta in self._annotation(annotation_event):
                        blocks.extend(delta.content)
            elif kind == "refusal":
                fields = {"seq": seq, "native": native}
                if seq not in self._refusal_streamed and isinstance(part.get("refusal"), str):
                    prefix = "" if self._refusal_started else REFUSAL_PREFIX
                    self._refusal_started = True
                    fields["text"] = prefix + part["refusal"]
                    self._refusal_streamed.add(seq)
                blocks.append(TextBlock(source=SOURCE, **fields))
            elif kind in ("output_audio", "audio"):
                fields = {"seq": seq, "native": native}
                if seq not in self._audio_streamed and isinstance(part.get("data"), str):
                    fields["data"] = part["data"]
                    self._audio_streamed.add(seq)
                transcript = part.get("transcript")
                if seq not in self._audio_transcript_streamed and isinstance(transcript, str):
                    fields["transcript"] = transcript
                    self._audio_transcript_streamed.add(seq)
                blocks.append(AudioBlock(source=SOURCE, **fields))
            else:
                blocks.append(
                    VendorBlock(
                        type=f"responses_{kind or 'content'}",
                        raw=dict(part),
                        native=native,
                        source=SOURCE,
                        seq=seq,
                    )
                )
        if not blocks and final and isinstance(output_index, int):
            blocks.append(
                VendorBlock(
                    type="responses_message",
                    raw=dict(item),
                    source=SOURCE,
                    seq=self._seqs.coord(_item_slot(event)),
                )
            )
        return [HubResponse(content=blocks)] if blocks else []

    def _reasoning_item(self, item: dict[str, Any], coords: Seq) -> list[HubResponse]:
        """reasoning item을 summary part와 원문 추론 part별 thinking 블록으로 바꾼다.

        part마다 ``(output_index, part_kind, part_index)`` 좌표의 seq를 쓴다. 이미 스트리밍한
        part는 본문을 다시 싣지 않는다. 첫 블록에만 item 원본과 ``encrypted_content``를 둔다.
        part가 하나도 없으면 이 item에서 처음 스트리밍한 추론 블록, 없으면 item 좌표의 블록 하나에
        item 원본을 둔다.

        Args:
            item: ``output_item.added``/``done`` 이벤트의 reasoning item.
            coords: item 수준 좌표 ``(output_index, 0, 0)``.
        """
        blocks: list[ContentBlock] = []
        native_pending = True
        encrypted = item.get("encrypted_content")
        output = coords[0]

        def append_parts(entries: Any, *, part_kind: int) -> None:
            nonlocal native_pending
            if not isinstance(entries, list):
                return
            for part_index, entry in enumerate(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                    continue
                seq = self._seqs.coord((output, part_kind, part_index))
                fields: dict[str, Any] = {
                    "seq": seq,
                    "source": SOURCE,
                }
                if seq not in self._reasoning_streamed:
                    fields["thinking"] = entry["text"]
                    self._reasoning_streamed.add(seq)
                if native_pending:
                    fields["native"] = dict(item)
                    if isinstance(encrypted, str) and encrypted:
                        fields["encrypted_content"] = encrypted
                    native_pending = False
                blocks.append(ThinkingBlock(**fields))

        append_parts(item.get("summary"), part_kind=_PART)
        append_parts(item.get("content"), part_kind=_REASONING_TEXT)
        if not blocks:
            streamed = self._reasoning_item_slots.get(coords)
            fields: dict[str, Any] = {
                "seq": streamed if streamed is not None else self._seqs.coord(coords),
                "source": SOURCE,
                "native": dict(item),
            }
            if isinstance(encrypted, str) and encrypted:
                fields["encrypted_content"] = encrypted
            blocks.append(ThinkingBlock(**fields))
        return [HubResponse(content=blocks)]

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
                            AnnotationBlock(
                                seq=self._seqs.after(),
                                id=citation,
                                uri=citation,
                                kind="response_citation",
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
                            AnnotationBlock(
                                seq=self._seqs.after(),
                                id=str(identifier),
                                uri=citation.get("url"),
                                title=citation.get("title"),
                                kind=str(citation.get("type") or "response_citation"),
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
    parameter_family = "responses"

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 Responses body로.

        이 API는 ``messages``가 아니라 ``input``을 받고 ``system``을 ``instructions``로 받는다.

        **``input``은 ``Item`` 리스트이고 ``Item`` 안에 다시 ``ContentPart`` 리스트가 있다.**
        두 층이다. 문자열 하나로 누르면 이미지, 음성, 파일 part를 실을 수 없다.

        도구 결과와 MCP 승인은 content part가 아니라 **별개 Item**이다.
        ``function_call_output``과 ``mcp_approval_response``가 그것이고, 클라이언트가 되보내는
        입력 항목이다.
        """
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            phase = getattr(message, "phase", None)
            turns.extend(self._items(message.role, message.content, lowerer, phase=phase))

        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        body: dict[str, Any] = {
            "model": request.model,
            "input": turns,
            "stream": True,
        }
        tools = [
            definition
            for tool in request.tools
            if (definition := self._tool_definition(tool)) is not None
        ]
        if tools:
            body["tools"] = tools
        for key, value in params.items():
            if key not in ("model", "input", "stream", "tools", "instructions"):
                body[key] = value
        return body

    def _items(
        self,
        role: str,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
        *,
        phase: str | None = None,
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
            item = self._message_item(role, pending, lowerer, phase=phase)
            pending = []
            if item is not None:
                items.append(item)

        for block in blocks:
            item: dict[str, Any] | None = None
            if isinstance(block, ToolResultBlock):
                if not can_replay_client_tool(block, self.name):
                    continue
                item = self._tool_result_item(block, lowerer)
            elif isinstance(block, ToolUseBlock):
                if not can_replay_client_tool(block, self.name):
                    continue
                item = self._tool_use_item(block)
            elif isinstance(block, ThinkingBlock) and block.source == SOURCE:
                item = self._reasoning_input(block)
            elif isinstance(block, ServerToolBlock) and block.source == SOURCE:
                candidate = dict(block.raw or block.native)
                if not str(candidate.get("type") or "").startswith("response."):
                    item = candidate
            elif (
                isinstance(block, VendorBlock)
                and block.source == SOURCE
                and block.native.get("level") == "item"
            ):
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
        *,
        phase: str | None = None,
    ) -> dict[str, Any] | None:
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []
        item_meta: dict[str, Any] = {}
        for block in blocks:
            if block.source != SOURCE:
                continue
            native_item = block.native.get("item")
            if isinstance(native_item, dict):
                item_meta = dict(native_item)
                break
        replaying_output = (
            role == "assistant"
            and item_meta.get("role") == "assistant"
            and isinstance(item_meta.get("id"), str)
            and isinstance(item_meta.get("status"), str)
        )
        if role == "assistant" and not replaying_output:
            # EasyInputMessageParam permits prior assistant turns without server-issued
            # id/status. The live Responses endpoint accepts those as string content; its
            # role-sensitive part union rejects input_text/input_image/input_file here.
            portable = [
                block
                for block in blocks
                if not isinstance(block, ImageBlock | AudioBlock)
                and not (
                    isinstance(block, DocumentBlock)
                    and bool(block.data)
                    and not block.text
                    and not block.uri
                )
            ]
            text = lowerer.lower_text(portable)
            if not text:
                return None
            item: dict[str, Any] = {
                "type": "message",
                "role": "assistant",
                "content": text,
            }
            if phase in ("commentary", "final_answer"):
                item["phase"] = phase
            return item
        annotations_by_target: dict[int, list[dict[str, Any]]] = {}
        legacy_annotations: list[dict[str, Any]] = []
        for candidate in blocks:
            if isinstance(candidate, AnnotationBlock) and candidate.source == SOURCE:
                if isinstance(candidate.target_index, int) and candidate.native:
                    annotations_by_target.setdefault(candidate.target_index, []).append(
                        dict(candidate.native)
                    )
            elif (
                isinstance(candidate, CitationBlock)
                and candidate.source == SOURCE
                and candidate.native
            ):
                legacy_annotations.append(dict(candidate.native))

        def flush_plain() -> None:
            text_parts = lowerer.lower_text_parts(plain)
            plain.clear()
            for text in text_parts:
                kind = "output_text" if replaying_output else "input_text"
                parts.append({"type": kind, "text": text})

        for block in blocks:
            if has_opaque_media_reference(block):
                continue
            if isinstance(block, AnnotationBlock) and block.source == SOURCE:
                continue
            if isinstance(block, CitationBlock) and block.source == SOURCE and block.native:
                continue
            if isinstance(block, VendorBlock) and block.source == SOURCE:
                native_part = block.native.get("part")
                if isinstance(native_part, dict):
                    flush_plain()
                    parts.append(dict(native_part))
                    continue
            if isinstance(block, TextBlock) and block.source == SOURCE and block.native:
                native = block.native
                native_part = native.get("part")
                if isinstance(native_part, dict):
                    flush_plain()
                    part = dict(native_part)
                    if part.get("type") == "output_text":
                        part["text"] = block.text
                        target_index = block.index
                        annotations = (
                            annotations_by_target.get(target_index, [])
                            if isinstance(target_index, int)
                            else []
                        )
                        if not annotations:
                            annotations = legacy_annotations
                        if annotations and not part.get("annotations"):
                            part["annotations"] = annotations
                    elif part.get("type") == "refusal":
                        part["refusal"] = block.text.removeprefix(REFUSAL_PREFIX)
                    parts.append(part)
                    continue
            # Responses의 assistant output message는 output_text/refusal만 허용한다. 출력 오디오
            # stream이나 다른 벤더의 assistant media를 input part로 위조하지 않는다. 문서는
            # lowerer가 읽을 수 있는 텍스트로 내릴 수 있으므로 plain 경로에 둔다.
            if replaying_output and isinstance(block, AudioBlock | ImageBlock):
                continue
            if replaying_output and isinstance(block, DocumentBlock):
                plain.append(block)
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
            if isinstance(nested, ContentBlock) and has_opaque_media_reference(nested):
                continue
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

    def _tool_definition(self, tool: ToolDefinition) -> dict[str, Any] | None:
        native = tool.native_for(self.name)
        if native is not None:
            return native
        if tool.vendor is not None:
            return None
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    def is_terminal(self, frame: SseEvent) -> bool:
        name = frame.event.strip()
        if name in TERMINAL_EVENTS:
            return True
        return self._peek_type(frame) in TERMINAL_EVENTS

    def decode(self, frame: SseEvent) -> dict[str, Any] | None:
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
    def _peek_type(frame: SseEvent) -> str:
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

    def token_count_calls(
        self, body: dict[str, Any]
    ) -> Generator[JsonCall, dict[str, Any], TokenCount]:
        """``POST /v1/responses/input_tokens``로 입력 토큰 수를 얻는다.

        생성 body에서 이 엔드포인트가 받지 않는 필드만 빼고 보낸다. 나머지 필드는 그대로
        보내므로 ``instructions``, ``tools``, ``reasoning``처럼 토큰 수에 영향을 주는 설정이
        반영된다.

        Raises:
            MappingError: 응답에 정수 ``input_tokens``가 없을 때.
        """
        # 토큰 수 측정에서만 쓰는 제외 목록이다. 생성 body에 들어갈 수 있지만 input_tokens가
        # "Unknown parameter"로 거부하는 필드다.
        # 각 어댑터 별로 특수한 사정 때문에 제한적으로 추가했다. 적용할 스키마가 다르기 때문에
        # 공통화시킬 수요가 낮다.
        excluded = frozenset(
            {
                "stream",
                "stream_options",
                "temperature",
                "top_p",
                "top_logprobs",
                "max_output_tokens",
                "max_tool_calls",
                "background",
                "include",
                "store",
                "metadata",
                "service_tier",
                "safety_identifier",
                "prompt_cache_key",
                "prompt_cache_options",
                "moderation",
            }
        )
        payload = yield JsonCall(
            "/v1/responses/input_tokens",
            {key: value for key, value in body.items() if key not in excluded},
        )
        input_tokens = payload.get("input_tokens")
        if not isinstance(input_tokens, int):
            raise MappingError("responses input_tokens response must have an integer input_tokens")
        return TokenCount(input_tokens=input_tokens, tokenized=None)


responses = ResponsesAdapter()
"""기본 인스턴스."""
