"""Anthropic Messages API 어댑터.

허브 모델이 이 API 모양을 따르므로 변환이 거의 항등이다. 그래서 이 어댑터가 허브 설계의
시험대이기도 하다. 여기서 억지가 필요하면 허브 모양이 잘못 잡힌 것이다.

**이 벤더의 특별한 점은 블록 인덱스를 서버가 준다는 것이다.** ``content_block_start``에
``index``가 실려 오고 이후 델타가 그것을 참조한다. 다른 벤더에서는 어댑터가 인덱스를 만들어
붙여야 하는데 여기서는 그대로 옮기면 된다.

이름 붙은 SSE 이벤트를 쓴다. 종료 표지가 ``[DONE]``이 아니라 ``message_stop`` 이벤트다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks import (
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    VendorBlock,
)
from ..errors import MappingError
from ..hub import HubRequest, HubResponse, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer

__all__ = ["MessagesAdapter", "messages"]

SOURCE = "messages"
TERMINAL_EVENTS = frozenset({"message_stop"})
IGNORED_EVENTS = frozenset({"ping"})

# 이 API가 쓰는 기본 버전 헤더. 호출자가 headers로 덮을 수 있다.
DEFAULT_VERSION = "2023-06-01"


class _ToHub:
    """Anthropic 이벤트를 허브 델타로 바꾼다.

    서버가 준 ``index``를 그대로 블록 인덱스로 쓴다. 병합기가 그 키로 조각을 짝짓는다.
    """

    def __init__(self) -> None:
        self._kinds: dict[int, str] = {}

    def map(self, event: dict[str, Any]) -> list[HubResponse]:
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
            detail = json.dumps(event.get("error") or {}, ensure_ascii=False)
            raise MappingError(f"messages stream reported an error: {detail[:200]}")
        return []

    def flush(self) -> list[HubResponse]:
        return []

    # ---- 이벤트별 ----

    def _message_start(self, message: dict[str, Any]) -> list[HubResponse]:
        return [
            HubResponse(
                id=message.get("id"),
                model=message.get("model"),
                role=message.get("role"),
                usage=self._usage(message.get("usage")),
            )
        ]

    def _block_start(self, event: dict[str, Any]) -> list[HubResponse]:
        index = event.get("index")
        block = event.get("content_block") or {}
        kind = block.get("type") or ""
        if isinstance(index, int):
            self._kinds[index] = kind
        return [HubResponse(content=[self._seed(kind, block, index)])]

    def _seed(self, kind: str, block: dict[str, Any], index: Any) -> ContentBlock:
        common: dict[str, Any] = {"index": index, "source": SOURCE}
        if kind == "text":
            text = block.get("text")
            return TextBlock(**common, **({"text": text} if text else {}))
        if kind == "thinking":
            thinking = block.get("thinking")
            return ThinkingBlock(**common, **({"thinking": thinking} if thinking else {}))
        if kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
            fields: dict[str, Any] = {}
            if block.get("id"):
                fields["id"] = block["id"]
            if block.get("name"):
                fields["name"] = block["name"]
            return ToolUseBlock(**common, **fields)
        # 허브에 대응물이 없는 블록은 원본을 보존한다. 같은 벤더로 되돌릴 때 무손실이다.
        raw = {k: v for k, v in block.items() if k != "type"}
        return VendorBlock(type=kind or "unknown", raw=raw, **common)

    def _block_delta(self, event: dict[str, Any]) -> list[HubResponse]:
        index = event.get("index")
        delta = event.get("delta") or {}
        kind = delta.get("type")
        common: dict[str, Any] = {"index": index, "source": SOURCE}

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
            citation = delta.get("citation") or {}
            block_kind = self._kinds.get(index if isinstance(index, int) else -1, "text")
            return self._one(
                VendorBlock(type=f"{block_kind}_citation", raw=citation, source=SOURCE)
            )
        return []

    @staticmethod
    def _one(block: ContentBlock) -> list[HubResponse]:
        return [HubResponse(content=[block])]

    def _message_delta(self, event: dict[str, Any]) -> list[HubResponse]:
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
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("input_tokens"), int):
            fields["input_tokens"] = raw["input_tokens"]
        if isinstance(raw.get("output_tokens"), int):
            fields["output_tokens"] = raw["output_tokens"]
        return Usage(**fields) if fields else None


class MessagesAdapter:
    """``POST {base_url}/v1/messages``.

    API key는 ``Bridge(api_key=...)``가 ``Authorization: Bearer``로 보낸다. 이 API는
    ``x-api-key``를 쓰므로 그쪽을 원하면 ``headers``로 넘긴다.
    """

    name = SOURCE
    path = "/v1/messages"

    def __init__(self, *, version: str = DEFAULT_VERSION, max_tokens: int = 4096) -> None:
        self.version = version
        self.max_tokens = max_tokens

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 Messages body로.

        ``system``이 별도 최상위 필드라 messages 배열에서 빼낸다. ``max_tokens``가 필수라
        호출자가 주지 않으면 기본값을 채운다.
        """
        system: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            text = lowerer.lower_text(message.content)
            if message.role == "system":
                if text:
                    system.append(text)
                continue
            if text:
                turns.append({"role": message.role, "content": text})

        params = dict(request.params)
        body: dict[str, Any] = {
            "model": request.model,
            "messages": turns,
            "stream": True,
            "max_tokens": params.pop("max_tokens", self.max_tokens),
        }
        if system:
            body["system"] = "\n\n".join(system)
        if request.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        for key, value in params.items():
            if key not in ("model", "messages", "stream", "tools", "system"):
                body[key] = value
        return body

    def request_headers(self) -> dict[str, str]:
        """이 벤더가 요구하는 버전 헤더."""
        return {"anthropic-version": self.version}

    def is_terminal(self, frame: SseFrame) -> bool:
        if frame.event.strip() in TERMINAL_EVENTS:
            return True
        return self._peek_type(frame) in TERMINAL_EVENTS

    def decode(self, frame: SseFrame) -> dict[str, Any] | None:
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


messages = MessagesAdapter()
"""기본 인스턴스."""
