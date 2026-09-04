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

from ..blocks import ContentBlock, TextBlock, ThinkingBlock, ToolUseBlock, VendorBlock
from ..errors import MappingError
from ..hub import HubRequest, HubResponse, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer

__all__ = ["ResponsesAdapter", "responses"]

SOURCE = "responses"
TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.failed", "response.incomplete", "error"}
)

# 한 item 안의 여러 content part를 구분하려면 두 축이 필요하다.
_CONTENT_STRIDE = 1000


def _slot(event: dict[str, Any]) -> int:
    """``output_index``와 ``content_index``를 하나의 블록 키로 접는다."""
    output = event.get("output_index")
    content = event.get("content_index")
    base = output * _CONTENT_STRIDE if isinstance(output, int) else 0
    return base + (content if isinstance(content, int) else 0)


class _ToHub:
    """Responses 이벤트를 허브 델타로 바꾼다."""

    def map(self, event: dict[str, Any]) -> list[HubResponse]:
        name = event.get("type")
        if not isinstance(name, str):
            return []

        # 본문
        if name == "response.output_text.delta":
            return self._one(TextBlock(text=event.get("delta") or "", index=_slot(event)))
        if name == "response.refusal.delta":
            # 거부도 사용자에게 보여야 하는 본문이다.
            return self._one(TextBlock(text=event.get("delta") or "", index=_slot(event)))

        # 추론 요약. 원문 추론은 암호화되어 오므로 요약만 텍스트로 쓸 수 있다.
        if name == "response.reasoning_summary_text.delta":
            return self._one(ThinkingBlock(thinking=event.get("delta") or "", index=_slot(event)))

        # 도구 인수
        if name in (
            "response.function_call_arguments.delta",
            "response.mcp_call_arguments.delta",
        ):
            return self._one(ToolUseBlock(input_json=event.get("delta") or "", index=_slot(event)))

        # item 등장. 도구 호출의 식별자와 이름이 여기 실린다.
        if name == "response.output_item.added":
            return self._item_added(event)

        # 수명주기
        if name in ("response.created", "response.in_progress", "response.queued"):
            return self._response_meta(event)
        if name in TERMINAL_EVENTS:
            return self._terminal(name, event)

        # 완료 통보는 델타의 중복이라 흘려보낸다.
        if name.endswith(".done") or name.endswith(".added"):
            return []

        # 도구 진행 보고. 계열이 늘어도 규칙으로 걸린다.
        if "_call" in name:
            return self._one(
                VendorBlock(type=f"responses_{name.rsplit('.', 1)[-1]}", raw=dict(event))
            )
        return []

    def flush(self) -> list[HubResponse]:
        return []

    @staticmethod
    def _one(block: ContentBlock) -> list[HubResponse]:
        block.source = SOURCE
        return [HubResponse(content=[block])]

    def _item_added(self, event: dict[str, Any]) -> list[HubResponse]:
        item = event.get("item") or {}
        kind = item.get("type")
        index = _slot(event)
        if kind in ("function_call", "mcp_call"):
            fields: dict[str, Any] = {"index": index, "source": SOURCE}
            call_id = item.get("call_id") or item.get("id")
            if call_id:
                fields["id"] = call_id
            if item.get("name"):
                fields["name"] = item["name"]
            arguments = item.get("arguments")
            if isinstance(arguments, str) and arguments:
                fields["input_json"] = arguments
            return [HubResponse(content=[ToolUseBlock(**fields)])]
        if kind in ("message", "reasoning"):
            # 본문과 추론은 델타로 따라온다. 여기서 자리만 잡으면 중복이 된다.
            return []
        if isinstance(kind, str) and kind:
            raw = {k: v for k, v in item.items() if k != "type"}
            return [
                HubResponse(content=[VendorBlock(type=kind, raw=raw, index=index, source=SOURCE)])
            ]
        return []

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
        fields: dict[str, Any] = {}
        status = response.get("status")
        if name == "response.failed":
            fields["stop_reason"] = "error"
        elif name == "response.incomplete":
            reason = (response.get("incomplete_details") or {}).get("reason")
            fields["stop_reason"] = reason if isinstance(reason, str) else "incomplete"
        elif isinstance(status, str):
            fields["stop_reason"] = "stop" if status == "completed" else status
        usage = self._usage(response.get("usage"))
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


class ResponsesAdapter:
    """``POST {base_url}/v1/responses``."""

    name = SOURCE
    path = "/v1/responses"

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 Responses body로.

        이 API는 ``messages``가 아니라 ``input``을 받고 ``system``을 ``instructions``로 받는다.
        """
        instructions: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            text = lowerer.lower_text(message.content)
            if message.role == "system":
                if text:
                    instructions.append(text)
                continue
            if text:
                turns.append({"role": message.role, "content": text})

        params = dict(request.params)
        body: dict[str, Any] = {
            "model": request.model,
            "input": turns,
            "stream": True,
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in request.tools
            ]
        for key, value in params.items():
            if key not in ("model", "input", "stream", "tools", "instructions"):
                body[key] = value
        return body

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
