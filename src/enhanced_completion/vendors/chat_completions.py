"""OpenAI 호환 Chat Completions 어댑터.

사내 vLLM(LUXIA)이 이 규약을 쓴다. 첫 번째로 구현하는 스포크다.

이 벤더의 응답은 ``choices[0].delta``에 조각이 담기고 필드가 셋으로 갈린다. ``content``는
본문, ``reasoning``은 추론, ``tool_calls``는 도구 호출이다. 추론 필드 이름은 vLLM 버전에 따라
``reasoning``과 ``reasoning_content``로 갈리므로 둘 다 본다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks import ContentBlock, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock
from ..errors import MappingError
from ..hub import HubMessage, HubRequest, HubResponse, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer
from .parts import as_chat_completions_part

__all__ = ["ChatCompletionsAdapter", "chat_completions"]

DONE = "[DONE]"

# 서버 소관이라 호출자가 params로 덮어쓰지 못하게 막는 이름.
_RESERVED = frozenset({"model", "messages", "tools", "stream"})


class _ToHub:
    """Chat Completions chunk를 허브 델타로 바꾼다.

    도구 호출 조각의 ``index``를 그대로 블록 ``index``로 옮긴다. 병합기가 그 키로 조각을
    짝지어 붙인다. 본문과 추론 블록에도 고정 인덱스를 주어 같은 블록에 누적되게 한다.
    """

    # 본문과 추론은 choice 하나에 각각 하나씩이다. 도구 인덱스와 겹치지 않게 떨어뜨린다.
    TEXT_INDEX = 0
    THINKING_INDEX = -1
    TOOL_INDEX_BASE = 1

    def __init__(self, source: str) -> None:
        self._source = source

    def map(self, chunk: dict[str, Any]) -> list[HubResponse]:
        choices = chunk.get("choices") or []
        head: dict[str, Any] = choices[0] if choices else {}
        delta = head.get("delta") or head.get("message") or {}

        blocks: list[ContentBlock] = []

        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            blocks.append(
                ThinkingBlock(thinking=reasoning, index=self.THINKING_INDEX, source=self._source)
            )

        content = delta.get("content")
        if isinstance(content, str) and content:
            blocks.append(TextBlock(text=content, index=self.TEXT_INDEX, source=self._source))

        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            blocks.append(self._tool_block(call))

        usage = self._usage(chunk.get("usage"))
        finish = head.get("finish_reason")

        response = HubResponse(
            id=chunk.get("id"),
            model=chunk.get("model"),
            role=delta.get("role"),
            content=blocks,
            stop_reason=finish if isinstance(finish, str) else None,
            usage=usage,
        )
        return [response]

    def flush(self) -> list[HubResponse]:
        return []

    def _tool_block(self, call: dict[str, Any]) -> ToolUseBlock:
        """도구 호출 조각 하나를 블록으로.

        벤더가 보내지 않은 필드는 넣지 않는다. 빈 문자열로 채워 넣으면 병합기가 그것을
        "이 델타에 실린 값"으로 보고 앞서 받은 ``id``와 ``name``을 지운다. 델타 스트림에서
        ``id``와 ``name``은 첫 조각에만 오고 이후에는 ``arguments``만 온다.
        """
        fn = call.get("function") or {}
        raw_index = call.get("index")
        fields: dict[str, Any] = {
            "index": self.TOOL_INDEX_BASE + (raw_index if isinstance(raw_index, int) else 0),
            "source": self._source,
        }
        if call.get("id"):
            fields["id"] = call["id"]
        if fn.get("name"):
            fields["name"] = fn["name"]
        arguments = fn.get("arguments")
        if isinstance(arguments, str) and arguments:
            fields["input_json"] = arguments
        return ToolUseBlock(**fields)

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        return Usage(
            input_tokens=raw.get("prompt_tokens"),
            output_tokens=raw.get("completion_tokens"),
        )


class ChatCompletionsAdapter:
    """``POST {base_url}/v1/chat/completions``."""

    name = "chat_completions"
    path = "/v1/chat/completions"

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request.messages, lowerer),
            "stream": True,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        for key, value in request.params.items():
            if key in _RESERVED:
                continue
            body[key] = value
        return body

    def is_terminal(self, frame: SseFrame) -> bool:
        return frame.data.strip() == DONE

    def decode(self, frame: SseFrame) -> dict[str, Any] | None:
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

    # ---- 요청 쪽 내림 ----

    def _messages(self, messages: list[HubMessage], lowerer: Lowerer) -> list[dict[str, Any]]:
        """허브 메시지를 wire 메시지로 펼친다.

        한 허브 메시지가 여러 wire 메시지가 될 수 있다. ``tool_result`` 블록이 role ``tool``의
        별도 메시지로 나가야 하기 때문이다. Java 초기 구현의 ``toMessage()``가 단일 반환이라
        이 경우를 표현할 수 없었고, ``streambind-base``에서 ``toMessages()``로 바뀐 이유가 이것이다.
        """
        out: list[dict[str, Any]] = []
        for message in messages:
            tool_results = [b for b in message.content if isinstance(b, ToolResultBlock)]
            tool_calls = [b for b in message.content if isinstance(b, ToolUseBlock)]

            # tool 결과가 먼저 나가야 직전 assistant 턴의 tool_calls와 짝이 맞는다.
            for block in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.tool_use_id,
                        "content": block.content,
                    }
                )

            # 멀티모달 part. streambind-base의 RequestContentPart는 text와 image_url 둘만
            # permit하지만 실제 API는 input_audio와 file도 받는다.
            parts: list[dict[str, Any]] = []
            rest: list[ContentBlock] = []
            for item in message.content:
                if isinstance(item, ToolResultBlock | ToolUseBlock):
                    continue
                part = as_chat_completions_part(item)
                if part is not None:
                    parts.append(part)
                else:
                    rest.append(item)

            text = lowerer.lower_text(rest)
            if not text and not tool_calls and not parts:
                continue

            content: str | list[dict[str, Any]] = text
            if parts:
                # part 리스트를 쓰면 본문도 part가 되어야 한다. 문자열과 섞을 수 없다.
                if text:
                    parts.append({"type": "text", "text": text})
                content = parts

            wire: dict[str, Any] = {"role": message.role, "content": content}
            if tool_calls:
                wire["tool_calls"] = [
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {"name": b.name, "arguments": b.input_json or "{}"},
                    }
                    for b in tool_calls
                ]
            out.append(wire)
        return out


chat_completions = ChatCompletionsAdapter()
"""기본 인스턴스. 어댑터가 상태를 갖지 않으므로 공유해도 된다."""
