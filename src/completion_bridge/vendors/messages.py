"""Anthropic Messages API 어댑터.

허브 모델이 이 API 모양을 따르므로 변환이 거의 항등이다. 그래서 이 어댑터가 허브 설계의
시험대이기도 하다. 여기서 억지가 필요하면 허브 모양이 잘못 잡힌 것이다.

**이 벤더의 특별한 점은 블록 인덱스를 서버가 준다는 것이다.** ``content_block_start``에
``index``가 있고 이후 델타가 그것을 참조한다. 허브 블록의 ``seq``는 ``(index, 0)``이다.

이름 붙은 SSE 이벤트를 쓴다. 종료 표지가 ``[DONE]``이 아니라 ``message_stop`` 이벤트다.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Mapping, Sequence
from typing import Any

from completion_bridge.errors import MappingError, VendorStreamError
from completion_bridge.http.sse import SseEvent
from completion_bridge.models.blocks import (
    Citation,
    CitationBlock,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    SearchResultBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from completion_bridge.models.hub import (
    HubMessage,
    HubRequest,
    HubResponse,
    TokenCount,
    ToolDefinition,
    Usage,
)
from completion_bridge.models.native import MessagesContentBlock, MessagesResponse, MessagesUsage
from completion_bridge.streaming.mapping import StreamMapper
from completion_bridge.streaming.sequence import Seq, normalize_seq
from completion_bridge.vendors.base import JsonCall, Lowerer, NativeAccumulator
from completion_bridge.vendors.normalize import INSTRUCTION_ROLES
from completion_bridge.vendors.parts import as_anthropic_part, has_opaque_media_reference
from completion_bridge.vendors.tool_adaptation import ToolAdaptation

__all__ = ["MessagesAdapter", "messages"]

SOURCE = "messages"

# 서버가 실행하는 도구. tool_use와 달리 클라이언트가 결과를 되보내지 않는다.
_SERVER_TOOL_USE = frozenset({"server_tool_use", "mcp_tool_use"})
_STATEFUL_CLIENT_TOOL_KINDS = {
    "bash": "anthropic_bash",
    "computer": "anthropic_computer",
    "str_replace_based_edit_tool": "anthropic_text_editor",
    "memory": "anthropic_memory",
    "browser": "anthropic_browser",
}
_SERVER_TOOL_RESULT = frozenset(
    {
        "web_search_tool_result",
        "web_fetch_tool_result",
        "mcp_tool_result",
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
        "tool_search_tool_result",
    }
)
TERMINAL_EVENTS = frozenset({"message_stop"})
IGNORED_EVENTS = frozenset({"ping"})

# 이 API가 쓰는 기본 버전 헤더. 호출자가 headers로 덮을 수 있다.
DEFAULT_VERSION = "2023-06-01"


_SEQ_WIDTH = 1


class _ToHub:
    """Anthropic 이벤트를 허브 델타로 바꾼다.

    서버가 준 ``index``를 좌표로 ``seq=(index,)``을 만든다. 병합기가 그 키로 조각을 짝짓는다.
    """

    def __init__(self) -> None:
        self._kinds: dict[int, str] = {}

    def map(self, delta: dict[str, Any]) -> list[HubResponse]:
        """벤더 스트림 이벤트 하나를 허브 델타 목록으로 변환한다."""
        event = delta
        name = event.get("type")
        if name == "message_start":
            return self._message_start(event.get("message") or {})
        if name == "content_block_start":
            return self._block_start(event)
        if name == "content_block_delta":
            return self._block_delta(event)
        if name == "message_delta":
            return self._message_delta(event)
        if name in ("content_block_stop", *IGNORED_EVENTS, *TERMINAL_EVENTS):
            return []
        if name == "error":
            raise VendorStreamError(SOURCE, event.get("error") or {})
        return []

    def flush(self) -> list[HubResponse]:
        """이 변환기는 종료 시 보관하는 잔여 델타가 없으므로 빈 목록을 반환한다."""
        return []

    # ---- 이벤트별 ----

    def _message_start(self, message: dict[str, Any]) -> list[HubResponse]:
        """메시지 시작 이벤트의 식별자·모델·역할·초기 사용량을 허브 델타로 변환한다."""
        return [
            HubResponse(
                id=message.get("id"),
                model=message.get("model"),
                role=message.get("role"),
                usage=self._usage(message.get("usage")),
            )
        ]

    def _block_seq(self, event: dict[str, Any]) -> Seq:
        """블록 이벤트의 ``index``로 seq를 만든다.

        Raises:
            MappingError: 이벤트에 정수 ``index``가 없을 때. 이 벤더는 모든 블록 이벤트에
                ``index``를 준다.
        """
        index = event.get("index")
        if not isinstance(index, int):
            raise MappingError(f"messages {event.get('type')} event has no integer index")
        return normalize_seq((index,), _SEQ_WIDTH)

    def _block_start(self, event: dict[str, Any]) -> list[HubResponse]:
        """content block의 좌표와 종류를 기억하고 시작 내용을 허브 블록으로 변환한다."""
        seq = self._block_seq(event)
        block = event.get("content_block") or {}
        kind = block.get("type") or ""
        self._kinds[seq[0]] = kind
        return [HubResponse(content=[self._seed(kind, block, seq)])]

    def _seed(self, kind: str, block: dict[str, Any], seq: Seq) -> ContentBlock:
        """content block 종류에 맞는 초기 허브 블록을 생성하고 원본 필드를 보존한다."""
        common: dict[str, Any] = {
            "seq": seq,
            "vendor": SOURCE,
            "native": dict(block),
        }
        if kind == "text":
            text = block.get("text")
            citations = []
            for citation in block.get("citations") or []:
                if isinstance(citation, dict):
                    citations.append(self._citation_model(citation))
            fields: dict[str, Any] = {"citations": citations} if citations else {}
            if text:
                fields["text"] = text
            return TextBlock(**common, **fields)
        if kind == "thinking":
            thinking = block.get("thinking")
            return ThinkingBlock(**common, **({"thinking": thinking} if thinking else {}))
        if kind == "image":
            source = block.get("source") or {}
            image_fields: dict[str, Any] = {}
            if source.get("media_type"):
                image_fields["media_type"] = source["media_type"]
            if source.get("data"):
                image_fields["data"] = source["data"]
            if source.get("url"):
                image_fields["url"] = source["url"]
            return ImageBlock(**common, **image_fields)
        if kind == "redacted_thinking":
            # 내용이 암호화되어 온다. 원문 그대로 되돌려야 하므로 보존만 한다.
            return VendorBlock(type=kind, raw=dict(block), **common)
        if kind == "tool_use":
            fields = {}
            if block.get("id"):
                fields["id"] = block["id"]
            if block.get("name"):
                fields["name"] = block["name"]
                special_kind = _STATEFUL_CLIENT_TOOL_KINDS.get(str(block["name"]))
                if special_kind:
                    fields["kind"] = special_kind
            return ToolUseBlock(**common, **fields)
        if kind in _SERVER_TOOL_USE:
            # 서버가 실행하는 호출이다. 클라이언트가 결과를 되보낼 필요가 없다.
            fields = {"name": block.get("name") or kind}
            if block.get("id"):
                fields["id"] = block["id"]
            if block.get("server_name"):
                fields["raw"] = dict(block)
            else:
                fields["raw"] = dict(block)
            return ServerToolBlock(**common, **fields)
        if kind in _SERVER_TOOL_RESULT:
            return ServerToolBlock(
                name=kind,
                id=block.get("tool_use_id") or "",
                output=json.dumps(block.get("content"), ensure_ascii=False)
                if block.get("content") is not None
                else "",
                raw=dict(block),
                **common,
            )
        # 허브에 대응물이 없는 블록은 원본을 보존한다. 같은 벤더로 되돌릴 때 무손실이다.
        return VendorBlock(type=kind or "unknown", raw=dict(block), **common)

    def _block_delta(self, event: dict[str, Any]) -> list[HubResponse]:
        """기존 블록 좌표에 텍스트·추론·도구 인자·인용의 증분을 적용할 델타를 만든다."""
        seq = self._block_seq(event)
        delta = event.get("delta") or {}
        kind = delta.get("type")
        common: dict[str, Any] = {"seq": seq, "vendor": SOURCE}

        if kind == "text_delta":
            return self._one(TextBlock(text=delta.get("text") or "", **common))
        if kind == "thinking_delta":
            return self._one(ThinkingBlock(thinking=delta.get("thinking") or "", **common))
        if kind == "signature_delta":
            # 서명은 이어붙이지 않고 마지막 값을 쓴다. 원문 그대로 되돌려야 하는 값이다.
            return self._one(ThinkingBlock(signature=delta.get("signature") or "", **common))
        if kind == "input_json_delta":
            fragment = delta.get("partial_json") or ""
            return self._one(ToolUseBlock(input_json=fragment, **common))
        if kind == "citations_delta":
            return self._citation(delta.get("citation") or {}, seq)
        return []

    def _citation(self, citation: dict[str, Any], seq: Seq) -> list[HubResponse]:
        """네이티브 인용을 해당 허브 text block에 붙인다.

        이 벤더는 인용을 본문 태그가 아니라 구조 채널로 준다. beta 헤더도 필요 없다. 그래서
        어휘의 태그 올림을 거치지 않는다. 좌표는 근거 문서 안의 위치이고 citation이 붙은
        ``TextBlock`` 전체가 생성 답변의 인용 구간이다.
        """
        return self._one(
            TextBlock(
                seq=seq,
                vendor=SOURCE,
                citations=[self._citation_model(citation)],
            )
        )

    @staticmethod
    def _citation_model(citation: dict[str, Any]) -> Citation:
        """벤더 인용의 위치와 출처를 공통 인용 모델로 변환하고 원본을 보존한다."""
        fields: dict[str, Any] = {
            "type": str(citation.get("type") or "citation"),
            "source": SOURCE,
            "native": dict(citation),
        }
        cited = citation.get("cited_text")
        if isinstance(cited, str) and cited:
            fields["cited_text"] = cited

        index = citation.get("document_index")
        if isinstance(index, int):
            fields["document_index"] = index
            fields["id"] = str(index)
        title = citation.get("document_title")
        if isinstance(title, str) and title:
            fields["document_title"] = title
            fields["id"] = title

        uri = citation.get("url")
        if isinstance(uri, str) and uri:
            fields["uri"] = uri
            fields["id"] = uri
        file_id = citation.get("file_id")
        if isinstance(file_id, str) and file_id:
            fields["file_id"] = file_id
            fields.setdefault("id", file_id)
        if citation.get("type") == "search_result_location":
            # search result의 출처를 uri와 id에, 제목과 요청 안 순번을 문서 필드에 둔다.
            title = citation.get("title")
            if isinstance(title, str) and title:
                fields["document_title"] = title
            result_index = citation.get("search_result_index")
            if isinstance(result_index, int):
                fields["document_index"] = result_index
            source = citation.get("source")
            if isinstance(source, str) and source:
                fields["uri"] = source
                fields["id"] = source
        encrypted_index = citation.get("encrypted_index")
        if isinstance(encrypted_index, str) and encrypted_index:
            fields["encrypted_index"] = encrypted_index
        field_names = {
            "start_char_index": "source_start",
            "end_char_index": "source_end",
            "start_page_number": "source_start",
            "end_page_number": "source_end",
            "start_block_index": "source_start",
            "end_block_index": "source_end",
        }
        for src in field_names:
            dst = field_names[src]
            value = citation.get(src)
            if isinstance(value, int):
                fields[dst] = value

        return Citation(**fields)

    @staticmethod
    def _one(block: ContentBlock) -> list[HubResponse]:
        """블록 하나를 포함하는 허브 델타 하나를 목록으로 반환한다."""
        return [HubResponse(content=[block])]

    def _message_delta(self, event: dict[str, Any]) -> list[HubResponse]:
        """메시지 종료 사유와 증분 사용량을 허브 델타로 변환한다."""
        delta = event.get("delta") or {}
        fields: dict[str, Any] = {}
        reason = delta.get("stop_reason")
        if isinstance(reason, str):
            fields["stop_reason"] = reason
        usage = self._usage(event.get("usage"))
        if usage is not None:
            fields["usage"] = usage
        return [HubResponse(**fields)] if fields else []

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        """벤더 사용량을 허브 토큰 사용량과 원본 사용량으로 변환한다."""
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("input_tokens"), int):
            fields["input_tokens"] = raw["input_tokens"]
        if isinstance(raw.get("output_tokens"), int):
            fields["output_tokens"] = raw["output_tokens"]
        return Usage(**fields, native=dict(raw))


class _ToNative:
    """Messages 스트림 이벤트를 ``Message`` 객체 하나로 누적한다.

    ``message_start``의 ``message``를 기본 객체로 쓴다. ``content_block_start``의 블록은 서버가
    준 ``index`` 순서로 ``content``에 추가하고, ``content_block_delta``는 그 블록에 반영한다.
    ``input_json_delta``의 ``partial_json``은 블록별로 연결해 두었다가 :meth:`build`에서 JSON으로
    파싱해 ``input``을 바꾼다. ``message_delta``는 ``delta`` 필드를 ``Message``에, ``usage``
    필드를 ``Message.usage``에 최상위 key 단위로 반영한다.
    """

    def __init__(self) -> None:
        self._message: MessagesResponse | None = None
        self._input_json: dict[int, str] = {}

    def apply(self, chunk: dict[str, Any]) -> None:
        """이벤트 하나를 ``Message`` 객체에 반영한다.

        Raises:
            MappingError: ``message_start`` 전에 다른 이벤트가 오거나, 블록 ``index``가 시작 순서와
                맞지 않거나, 누적 규칙이 없는 이벤트·delta 종류가 올 때.
        """
        name = chunk.get("type")
        if name in ("content_block_stop", *IGNORED_EVENTS, *TERMINAL_EVENTS):
            return

        # message_start: 이후 이벤트를 반영할 Message 기본 객체를 정한다.
        if name == "message_start":
            message = chunk.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                raise MappingError("messages message_start event has no message content list")
            self._message = MessagesResponse.model_validate(message)
            return
        if self._message is None:
            raise MappingError(f"messages {name} event arrived before message_start")
        content = self._message.content

        # content_block_start: 서버가 준 index 순서로 블록을 추가한다.
        if name == "content_block_start":
            index = self._index(chunk)
            block = chunk.get("content_block")
            if not isinstance(block, dict):
                raise MappingError(f"messages content_block_start {index} has no content_block")
            if index != len(content):
                raise MappingError(
                    f"messages content_block_start index {index} != next block {len(content)}"
                )
            content.append(MessagesContentBlock.model_validate(block))
            return

        # content_block_delta: 이미 시작한 블록에 조각을 반영한다.
        if name == "content_block_delta":
            index = self._index(chunk)
            if index >= len(content):
                raise MappingError(f"messages content_block_delta references unstarted {index}")
            self._apply_delta(index, content[index], chunk.get("delta"))
            return

        # message_delta: 종료 정보와 누적 usage를 Message에 반영한다.
        if name == "message_delta":
            delta = chunk.get("delta")
            if not isinstance(delta, dict):
                raise MappingError("messages message_delta event has no delta object")
            for field in delta:
                setattr(self._message, field, delta[field])
            usage = chunk.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise MappingError("messages message_delta usage is not an object")
                current: dict[str, Any] = {}
                if self._message.usage is not None:
                    current = self._message.usage.model_dump(exclude_unset=True)
                for field in usage:
                    # null은 message_start에서 받은 값을 지우지 않는다.
                    if usage[field] is not None or field not in current:
                        current[field] = usage[field]
                self._message.usage = MessagesUsage.model_validate(current)
            for key in chunk:
                if key not in ("type", "delta", "usage"):
                    setattr(self._message, key, chunk[key])
            return

        raise MappingError(f"messages event type {name!r} has no accumulation rule")

    def build(self) -> MessagesResponse:
        """누적한 ``Message`` 객체를 돌려준다. 도구 입력 JSON 조각을 파싱해 ``input``에 둔다.

        ``partial_json`` 조각이 하나도 없거나 빈 문자열뿐인 블록은 ``content_block_start``의
        ``input``을 그대로 둔다.

        Raises:
            MappingError: ``message_start``를 받지 못했거나 연결한 도구 입력이 JSON이 아닐 때.
        """
        if self._message is None:
            raise MappingError("messages stream ended without message_start")
        content = self._message.content
        for index in self._input_json:
            raw = self._input_json[index]
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise MappingError(f"messages tool input JSON of block {index} is invalid") from exc
            content[index].input = parsed
        return self._message

    @staticmethod
    def _index(chunk: dict[str, Any]) -> int:
        """블록 이벤트의 ``index``를 돌려준다.

        Raises:
            MappingError: 정수 ``index``가 없을 때.
        """
        index = chunk.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise MappingError(
                f"messages {chunk.get('type')} event has no non-negative integer index"
            )
        return index

    def _apply_delta(self, index: int, block: MessagesContentBlock, delta: Any) -> None:
        """``content_block_delta``의 delta 하나를 ``index`` 블록에 반영한다.

        text·thinking 조각은 연결하고, signature는 나중 값으로 바꾸고, citation은 목록 끝에
        추가한다. ``partial_json``은 블록별 버퍼에 연결만 한다.

        Raises:
            MappingError: delta가 객체가 아니거나, 누적 규칙이 없는 종류이거나, 조각 값의 타입이
                규약과 다를 때.
        """
        if not isinstance(delta, dict):
            raise MappingError(f"messages content_block_delta {index} has no delta object")
        kind = delta.get("type")
        if kind == "text_delta":
            self._append_text(block, delta, "text")
        elif kind == "thinking_delta":
            self._append_text(block, delta, "thinking")
        elif kind == "signature_delta":
            signature = delta.get("signature")
            if not isinstance(signature, str):
                raise MappingError(f"messages signature_delta {index} has no string signature")
            block.signature = signature
        elif kind == "input_json_delta":
            fragment = delta.get("partial_json")
            if not isinstance(fragment, str):
                raise MappingError(f"messages input_json_delta {index} has no partial_json")
            self._input_json[index] = self._input_json.get(index, "") + fragment
        elif kind == "citations_delta":
            citation = delta.get("citation")
            if not isinstance(citation, dict):
                raise MappingError(f"messages citations_delta {index} has no citation object")
            if block.citations is None:
                block.citations = []
            block.citations.append(citation)
        else:
            raise MappingError(f"messages delta type {kind!r} has no accumulation rule")

    @staticmethod
    def _append_text(block: MessagesContentBlock, delta: dict[str, Any], field: str) -> None:
        """delta의 문자열 ``field`` 조각을 블록의 같은 필드 끝에 연결한다.

        Raises:
            MappingError: 조각이나 블록의 기존 값이 문자열이 아닐 때.
        """
        piece = delta.get(field)
        current = getattr(block, field)
        if field not in block.model_fields_set:
            current = ""
        if not isinstance(piece, str) or not isinstance(current, str):
            raise MappingError(f"messages {delta.get('type')} cannot append {field}")
        setattr(block, field, current + piece)


class MessagesAdapter:
    """``POST {base_url}/v1/messages``.

    이 API는 ``x-api-key``를 쓰므로 ``Bridge(api_key=...)``가 어댑터의
    :attr:`api_key_header`를 읽어 올바른 헤더로 보낸다.
    """

    name = SOURCE
    path = "/v1/messages"
    parameter_family = "messages"

    api_key_header = "x-api-key"

    def __init__(
        self,
        *,
        version: str = DEFAULT_VERSION,
        max_tokens: int = 4096,
        betas: Sequence[str] = (),
    ) -> None:
        """기본 출력 토큰 수와 beta·확장 헤더 설정을 보관한다."""
        self.version = version
        self.max_tokens = max_tokens
        self.betas = tuple(betas)

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 Messages body로.

        ``system``이 별도 최상위 필드라 messages 배열에서 빼낸다. ``max_tokens``가 필수라
        호출자가 주지 않으면 기본값을 채운다.
        """
        system: list[str] = []
        turns: list[dict[str, Any]] = []
        documents = []
        for message in request.messages:
            for block in message.content:
                if isinstance(block, DocumentBlock):
                    documents.append(block)
        citations_enabled = False
        for document in documents:
            if document.citations_enabled:
                citations_enabled = True
                break
        search_citations_enabled = False
        for result in self._search_results(request.messages):
            if result.citations_enabled:
                search_citations_enabled = True
                break
        for message in request.messages:
            if message.role in INSTRUCTION_ROLES:
                text = lowerer.lower_text(message.content)
                if text:
                    system.append(text)
                continue
            turns.extend(
                self._message_turns(
                    message,
                    lowerer,
                    citations_enabled=citations_enabled,
                    search_citations_enabled=search_citations_enabled,
                )
            )

        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": turns,
            "stream": True,
            "max_tokens": params.pop("max_tokens", self.max_tokens),
        }
        if system:
            body["system"] = "\n\n".join(system)
        tools = []
        for tool in request.tools:
            definition = self._tool_definition(tool)
            if definition is not None:
                tools.append(definition)
        if tools:
            body["tools"] = tools
        for key in params:
            value = params[key]
            if key not in ("model", "messages", "stream", "tools", "system"):
                body[key] = value
        self._reject_invalid_combinations(
            body, citations_enabled=citations_enabled or search_citations_enabled
        )
        return body

    @staticmethod
    def _search_results(messages: Sequence[HubMessage]) -> list[SearchResultBlock]:
        """요청의 모든 search result. tool_result ``blocks`` 안에 있는 것도 포함한다.

        Anthropic은 한 요청의 search result가 모두 같은 인용 설정이어야 하므로, 설정을 정할 때
        위치와 관계없이 전부 본다.
        """
        found: list[SearchResultBlock] = []
        for message in messages:
            for block in message.content:
                if isinstance(block, SearchResultBlock):
                    found.append(block)
                elif isinstance(block, ToolResultBlock):
                    for candidate_block in block.blocks:
                        if isinstance(candidate_block, SearchResultBlock):
                            found.append(candidate_block)
        return found

    @staticmethod
    def _reject_invalid_combinations(body: dict[str, Any], *, citations_enabled: bool) -> None:
        """이 API가 함께 받지 않는 조합을 요청 생성 시점에 막는다.

        조용히 400을 받는 것보다 낫다. 어느 필드가 문제인지 여기서는 알지만 서버 오류
        본문에서는 되짚어야 한다.
        """
        output_config = body.get("output_config")
        if citations_enabled and isinstance(output_config, Mapping) and "format" in output_config:
            raise MappingError(
                "document or search_result citations and output_config.format cannot be used "
                "together"
            )

        if body.get("mcp_servers"):
            declared = set()
            for tool in body.get("tools") or []:
                if isinstance(tool, Mapping) and tool.get("type") == "mcp_toolset":
                    declared.add(tool.get("mcp_server_name"))
            missing = []
            for server in body["mcp_servers"]:
                if isinstance(server, Mapping) and server.get("name") not in declared:
                    missing.append(str(server.get("name")))
            if missing:
                raise MappingError(
                    "mcp_servers requires a matching mcp_toolset tool for each server: "
                    + ", ".join(missing)
                )

    def _message_turns(
        self,
        message: Any,
        lowerer: Lowerer,
        *,
        citations_enabled: bool,
        search_citations_enabled: bool,
    ) -> list[dict[str, Any]]:
        """블록이 요구하는 role을 지키며 한 허브 턴을 0..N wire 턴으로 펼친다."""
        turns: list[dict[str, Any]] = []
        role = message.role
        pending: list[ContentBlock] = []
        pending_role: str | None = None

        def flush() -> None:
            """보관한 같은 역할의 일반 블록을 메시지로 변환하고 임시 상태를 초기화한다."""
            nonlocal pending
            if not pending or pending_role is None:
                return
            content = self._content(
                pending,
                lowerer,
                citations_enabled=citations_enabled,
                search_citations_enabled=search_citations_enabled,
            )
            pending = []
            if content is not None:
                turns.append({"role": pending_role, "content": content})

        for block in message.content:
            if isinstance(
                block, ToolUseBlock | ToolResultBlock
            ) and not ToolAdaptation.can_replay_client_tool(block, self.name):
                continue
            block_role = "user" if isinstance(block, ToolResultBlock) else role
            if pending_role is not None and block_role != pending_role:
                flush()
            pending_role = block_role
            pending.append(block)
        flush()
        return turns

    def _content(
        self,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
        *,
        citations_enabled: bool,
        search_citations_enabled: bool,
    ) -> str | list[dict[str, Any]] | None:
        """허브 블록을 Messages content 목록으로 변환하며 문서와 도구 결과를 처리한다."""
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []
        last_text_part: dict[str, Any] | None = None
        text_parts_by_index: dict[int, dict[str, Any]] = {}

        def flush_plain() -> None:
            """일반 블록을 텍스트로 변환하고 마지막 텍스트 part 및 임시 목록을 갱신한다."""
            nonlocal last_text_part
            text_parts = lowerer.lower_text_parts(plain)
            plain.clear()
            for text in text_parts:
                last_text_part = {"type": "text", "text": text}
                parts.append(last_text_part)

        for block in blocks:
            if has_opaque_media_reference(block):
                continue
            if isinstance(block, CitationBlock) and block.vendor == SOURCE and block.native:
                flush_plain()
                native_citation = block.native.get("citation", block.native)
                block_index = block.native.get("block_index")
                target = (
                    text_parts_by_index.get(block_index)
                    if isinstance(block_index, int)
                    else last_text_part
                )
                if target is not None and isinstance(native_citation, dict):
                    target.setdefault("citations", []).append(dict(native_citation))
                continue
            if isinstance(block, ToolResultBlock):
                flush_plain()
                parts.append(
                    self._tool_result_part(block, search_citations_enabled=search_citations_enabled)
                )
                continue
            part = as_anthropic_part(block)
            if part is None:
                plain.append(block)
                continue
            flush_plain()
            if isinstance(block, DocumentBlock):
                part["citations"] = {"enabled": citations_enabled}
            elif isinstance(block, SearchResultBlock):
                part["citations"] = {"enabled": search_citations_enabled}
            parts.append(part)
            if part.get("type") == "text":
                last_text_part = part
                if isinstance(block.index, int):
                    text_parts_by_index[block.index] = part
        flush_plain()

        if not parts:
            return None
        if len(parts) == 1 and parts[0] == {"type": "text", "text": parts[0].get("text")}:
            return str(parts[0]["text"])
        return parts

    @staticmethod
    def _tool_result_part(
        block: ToolResultBlock, *, search_citations_enabled: bool
    ) -> dict[str, Any]:
        """``tool_result``의 ``content``는 블록 리스트다.

        이미지를 돌려주는 도구가 그 경로를 쓴다. 평문만 있으면 문자열로 싣는다.

        ``blocks``에 :class:`SearchResultBlock`이 있으면 search result만 보낸다. 이 API는
        search result가 있는 tool_result의 모든 블록이 search result이기를 요구한다.

        Raises:
            MappingError: search result와 ``content``나 다른 블록이 함께 있을 때.
        """
        part: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.tool_use_id}
        searches = []
        for candidate_block in block.blocks:
            if isinstance(candidate_block, SearchResultBlock):
                searches.append(candidate_block)
        if searches:
            if block.content or len(searches) != len(block.blocks):
                raise MappingError(
                    f"tool_result {block.tool_use_id!r} mixes search results with other "
                    "content; messages requires every tool_result block to be a search_result"
                )
            rendered = []
            for result in searches:
                rendered.append(as_anthropic_part(result))
            inner = []
            for search in rendered:
                if search is not None:
                    inner.append(search)
            for search in inner:
                search["citations"] = {"enabled": search_citations_enabled}
            part["content"] = inner
            if block.is_error:
                part["is_error"] = True
            return part
        if block.content and not block.blocks:
            part["content"] = block.content
            if block.is_error:
                part["is_error"] = True
            return part
        if block.structured_content is not None and not block.blocks:
            part["content"] = json.dumps(block.structured_content, ensure_ascii=False)
            if block.is_error:
                part["is_error"] = True
            return part
        inner = []
        rendered_part: dict[str, Any] | None
        for nested_block in block.blocks:
            if isinstance(nested_block, ContentBlock) and has_opaque_media_reference(nested_block):
                continue
            if isinstance(nested_block, TextBlock):
                rendered_part = {"type": "text", "text": nested_block.text}
            else:
                rendered_part = as_anthropic_part(nested_block)
            if rendered_part is not None:
                inner.append(rendered_part)
        if block.content:
            inner.insert(0, {"type": "text", "text": block.content})
        part["content"] = inner if inner else ""
        if block.is_error:
            part["is_error"] = True
        return part

    def _tool_definition(self, tool: ToolDefinition) -> dict[str, Any] | None:
        """대상 벤더의 원본 도구 정의 또는 공통 함수 도구 정의를 반환한다."""
        native = tool.native_for(self.name)
        if native is not None:
            return native
        if tool.vendor is not None:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def request_headers(self) -> dict[str, str]:
        """이 벤더가 요구하는 버전 헤더.

        인용에는 beta 헤더가 필요 없다. 버전 헤더만 요구한다.
        """
        headers = {"anthropic-version": self.version}
        if self.betas:
            headers["anthropic-beta"] = ",".join(self.betas)
        return headers

    def is_terminal(self, frame: SseEvent) -> bool:
        """SSE 프레임이 이 API의 종료 이벤트인지 판정한다."""
        if frame.event.strip() in TERMINAL_EVENTS:
            return True
        return self._peek_type(frame) in TERMINAL_EVENTS

    def decode(self, frame: SseEvent) -> dict[str, Any] | None:
        """빈 SSE data를 제외하고 JSON 객체를 해석하며 잘못된 형식은 MappingError로 전달한다."""
        payload = frame.data.strip()
        if not payload:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MappingError("messages chunk is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MappingError("messages chunk must be a JSON object")
        # 이름은 본문 type이 권위다. event 필드는 같은 값을 중복해 싣는다.
        if not parsed.get("type") and frame.event.strip():
            parsed["type"] = frame.event.strip()
        return parsed

    @staticmethod
    def _peek_type(frame: SseEvent) -> str:
        """SSE data에서 이벤트 종류를 확인하며 해석할 수 없으면 빈 값을 반환한다."""
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
        """스트림마다 독립적인 벤더 응답→허브 델타 변환기를 생성한다."""
        return _ToHub()

    def to_native(self) -> NativeAccumulator[MessagesResponse]:
        """스트림마다 독립적인 원본 Response 누적기를 생성한다."""
        return _ToNative()

    def token_count_calls(
        self, body: dict[str, Any]
    ) -> Generator[JsonCall, dict[str, Any], TokenCount]:
        """``POST /v1/messages/count_tokens``로 입력 토큰 수를 얻는다.

        생성 body에서 이 엔드포인트가 받지 않는 필드만 빼고 보낸다. 나머지 필드는 그대로
        보내므로 ``thinking``, ``tools``처럼 토큰 수에 영향을 주는 설정이 반영된다.

        Raises:
            MappingError: 응답에 정수 ``input_tokens``가 없을 때.
        """
        # 토큰 수 측정에서만 쓰는 제외 목록이다. 생성 body에 들어갈 수 있지만 count_tokens가
        # "Extra inputs are not permitted"로 거부하는 필드다.
        # 각 어댑터 별로 특수한 사정 때문에 제한적으로 추가했다. 적용할 스키마가 다르기 때문에
        # 공통화시킬 수요가 낮다.
        excluded = frozenset(
            {
                "stream",
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stop_sequences",
                "metadata",
                "service_tier",
                "inference_geo",
                "container",
                "betas",
            }
        )
        selected_fields = {}
        for key in body:
            value = body[key]
            if key not in excluded:
                selected_fields[key] = value
        payload = yield JsonCall(
            "/v1/messages/count_tokens",
            selected_fields,
        )
        input_tokens = payload.get("input_tokens")
        if not isinstance(input_tokens, int):
            raise MappingError("messages count_tokens response must have an integer input_tokens")
        return TokenCount(input_tokens=input_tokens, tokenized=None)


messages = MessagesAdapter()
"""기본 인스턴스."""
