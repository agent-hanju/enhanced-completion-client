"""사내 agent-studio 어댑터.

계약을 `/d/반입용/agent-studio-v1.4.1.tar`의 프론트 이미지에서 확인했다. 소비 코드가 곧
스펙이다. 문서는 낡을 수 있지만 배포된 파서는 낡을 수 없다.

- 이미지: ``saltlux-agent-studio-front-1.4.1.tar``
- 파일: ``usr/share/nginx/html/assets/sse-*.js``, ``SingleAgentService-*.js``,
  ``SingleAgentChat-*.js``, ``CodeAgentChat-*.js``

**이 벤더가 표준과 다른 네 가지.**

1. 이벤트 이름이 SSE ``event:`` 필드에 없으면 본문 JSON의 ``event`` 또는 ``type``에 있다
2. 종료가 ``[DONE]`` 페이로드와 ``done`` 이벤트 둘 다다
3. 본문 텍스트의 위치가 고정이 아니다. 프론트가 아홉 개 경로를 순서대로 훑는다
4. JSON이 아닌 프레임이 있다. 파싱 실패는 원문 문자열로 폴백한다

본문 이벤트 별칭이 일곱 개이고 code agent는 도구 이벤트를 더 갖는다. 어댑터가 그것을 하나로
정규화한다.

**요청 방향이 축약된다.** ``execute``가 ``message`` 문자열 하나만 받고 messages 배열을 받지
않는다. 이력은 서버가 ``sessionId``로 관리한다. 그래서 허브 -> 요청은 마지막 사용자 메시지를
평탄화하는 것이 전부이고, 사실상 읽기 전용 스포크다.

취소는 ``POST /{kind}-agents/{id}/tasks/{taskId}/stop``이다. 연결 종료가 취소가 아니다.
:meth:`AgentAdapter.stop_path`가 그 경로를 만들어 준다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from ..blocks import (
    ContentBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
    register_block,
)
from ..hub import HubRequest, HubResponse
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer

__all__ = [
    "AgentAdapter",
    "AgentErrorBlock",
    "AgentEvent",
    "AgentSourcesBlock",
    "code_agent",
    "single_agent",
]

DONE = "[DONE]"
SOURCE = "agent"

# 본문 조각을 뜻하는 이벤트 이름. 하나로 정규화한다.
TEXT_EVENTS = frozenset(
    {"agent_message", "message", "message_delta", "answer", "chunk", "delta", "text_chunk"}
)

#: 프론트가 본문을 꺼내려고 훑는 경로. 순서가 계약이다.
#: 자체 모양과 OpenAI 모양이 한 스트림에 섞여 온다.
TEXT_PATHS: tuple[tuple[str, ...], ...] = (
    ("content",),
    ("body",),
    ("answer",),
    ("delta",),
    ("text",),
    ("message", "content"),
    ("choices", "0", "delta", "content"),
    ("choices", "0", "message", "content"),
    ("data",),
)

# code agent가 자기가 쓴 도구를 알리는 이벤트. 클라이언트가 실행할 호출이 아니라 진행 보고다.
TOOL_ACTIVITY_EVENTS = frozenset(
    {
        "bash",
        "edit",
        "read",
        "write",
        "glob",
        "grep",
        "web_search",
        "web_fetch",
        "memory_read",
        "memory_write",
        "skill_read",
        "skill_run",
        "agent",
        "agent_wait",
    }
)

PROGRESS_EVENTS = frozenset({"activity", "activity_done"})
LIFECYCLE_EVENTS = frozenset({"run_start", "session", "task", "title"})
ERROR_EVENTS = frozenset({"error", "failed"})
TERMINAL_EVENTS = frozenset({"done"})


class AgentSourcesBlock(ContentBlock):
    """agent가 붙인 근거 목록.

    :class:`~enhanced_completion.blocks.Citation`과 합칠 수 있는지는 실제 페이로드가 근거와
    답변 구간의 관계를 제공하는지 확인한 뒤 정해야 한다. 지금은 원본을 보존한다.
    """

    type: Literal["agent_sources"] = "agent_sources"
    data: dict[str, Any] | None = None


class AgentErrorBlock(ContentBlock):
    """스트림 도중 서버가 알린 실패.

    예외로 올리지 않는다. 여기까지 모인 본문을 잃지 않아야 하고, 무엇을 사용자에게 보일지는
    소비 앱이 정한다. ``stop_reason``도 함께 ``error``로 바뀐다.
    """

    type: Literal["agent_error"] = "agent_error"
    detail: str = ""


for _cls in (AgentSourcesBlock, AgentErrorBlock):
    register_block(_cls)


class AgentEvent:
    """정규화된 agent 이벤트 하나."""

    __slots__ = ("name", "payload", "raw")

    def __init__(self, name: str, payload: dict[str, Any] | None, raw: str) -> None:
        self.name = name
        self.payload = payload
        self.raw = raw

    def __repr__(self) -> str:
        return f"AgentEvent(name={self.name!r})"


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    """경로를 따라 값을 꺼낸다. 숫자 조각은 리스트 인덱스로 본다."""
    cursor = payload
    for key in path:
        if isinstance(cursor, dict):
            cursor = cursor.get(key)
        elif isinstance(cursor, list) and key.isdigit():
            index = int(key)
            cursor = cursor[index] if index < len(cursor) else None
        else:
            return None
        if cursor is None:
            return None
    return cursor


def extract_text(payload: Any) -> str:
    """본문 텍스트를 꺼낸다. 아홉 개 경로를 순서대로 훑는다.

    필드 이름이 고정돼 있지 않은 것이 이 벤더의 핵심 특이점이다. 프론트가 같은 순서로
    훑으므로 그 순서를 계약으로 본다.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if not isinstance(payload, dict):
        return ""
    for path in TEXT_PATHS:
        value = _dig(payload, path)
        if isinstance(value, str) and value:
            return value
    return ""


class _ToHub:
    """agent 이벤트를 허브 델타로 바꾼다.

    본문과 추론에 고정 인덱스를 주어 조각이 같은 블록에 누적되게 한다. 진행 보고와 근거는
    각각 독립된 블록이라 인덱스를 주지 않고 도착 순서대로 덧붙는다.
    """

    TEXT_INDEX = 0
    THINKING_INDEX = -1

    def __init__(self) -> None:
        self._tool_index = 1
        self._tool_slots: dict[str, int] = {}

    def map(self, event: AgentEvent) -> list[HubResponse]:
        name = event.name
        payload = event.payload if isinstance(event.payload, dict) else {}
        blocks: list[ContentBlock] = []
        fields: dict[str, Any] = {}

        if name in TEXT_EVENTS:
            text = extract_text(event.payload if event.payload is not None else event.raw)
            if text:
                blocks.append(TextBlock(text=text, index=self.TEXT_INDEX, source=SOURCE))

        elif name == "reasoning_delta":
            text = extract_text(event.payload if event.payload is not None else event.raw)
            if text:
                blocks.append(
                    ThinkingBlock(thinking=text, index=self.THINKING_INDEX, source=SOURCE)
                )

        elif name == "tool_call":
            blocks.append(self._tool_call(payload))

        elif name == "tool_result":
            blocks.append(self._tool_result(payload))

        elif name in TOOL_ACTIVITY_EVENTS or name in PROGRESS_EVENTS:
            # 서버가 실행한 도구의 사후 보고다. 다른 벤더의 서버 도구와 같은 자리다.
            blocks.append(
                ServerToolBlock(
                    name=name,
                    output=extract_text(event.payload) if event.payload else event.raw,
                    raw=payload,
                    source=SOURCE,
                )
            )

        elif name == "sources":
            blocks.append(AgentSourcesBlock(data=payload or None, source=SOURCE))

        elif name in ERROR_EVENTS:
            detail = extract_text(event.payload) or event.raw
            blocks.append(AgentErrorBlock(detail=detail[:500], source=SOURCE))
            fields["stop_reason"] = "error"

        elif name in LIFECYCLE_EVENTS:
            # 식별자는 응답 메타로 올린다. 나머지는 화면 표시용이라 흘려보낸다.
            identifier = payload.get("taskId") or payload.get("sessionId") or payload.get("id")
            if isinstance(identifier, str) and identifier:
                fields["id"] = identifier
            if name == "run_start":
                fields["role"] = "assistant"

        elif name not in TERMINAL_EVENTS:
            # 알려지지 않은 이벤트도 버리지 않는다. 서버가 어휘를 늘려도 스트림이 깨지지 않는다.
            blocks.append(VendorBlock(type=f"agent_{name}", raw=payload, source=SOURCE))

        if not blocks and not fields:
            return []
        return [HubResponse(content=blocks, **fields)]

    def flush(self) -> list[HubResponse]:
        return []

    def _slot(self, call_id: str) -> int:
        """도구 호출 식별자마다 안정된 인덱스를 준다. 조각이 같은 블록에 모이게 한다."""
        if call_id not in self._tool_slots:
            self._tool_slots[call_id] = self._tool_index
            self._tool_index += 1
        return self._tool_slots[call_id]

    def _tool_call(self, payload: dict[str, Any]) -> ToolUseBlock:
        call_id = str(payload.get("id") or payload.get("toolCallId") or payload.get("callId") or "")
        name = payload.get("name") or payload.get("tool") or ""
        args = payload.get("arguments")
        if args is None:
            args = payload.get("input") or payload.get("args")
        fields: dict[str, Any] = {"index": self._slot(call_id), "source": SOURCE}
        if call_id:
            fields["id"] = call_id
        if isinstance(name, str) and name:
            fields["name"] = name
        if isinstance(args, str) and args:
            fields["input_json"] = args
        elif isinstance(args, (dict, list)):
            fields["input_json"] = json.dumps(args, ensure_ascii=False)
        return ToolUseBlock(**fields)

    def _tool_result(self, payload: dict[str, Any]) -> ToolResultBlock:
        call_id = str(payload.get("id") or payload.get("toolCallId") or payload.get("callId") or "")
        content = extract_text(payload)
        if not content:
            result = payload.get("result") or payload.get("output")
            if result is not None:
                content = json.dumps(result, ensure_ascii=False)
        fields: dict[str, Any] = {"source": SOURCE}
        if call_id:
            fields["tool_use_id"] = call_id
        if content:
            fields["content"] = content
        if payload.get("isError") or payload.get("is_error"):
            fields["is_error"] = True
        return ToolResultBlock(**fields)


class AgentAdapter:
    """``POST {base_url}{prefix}/{kind}-agents/{id}/execute``.

    CSRF 토큰은 ``Bridge(headers={"X-CSRF-Token": ...})``로 넘긴다. 브라우저가 쿠키에서
    읽어 헤더로 옮기는 값이고, 이 라이브러리는 쿠키를 다루지 않는다.
    """

    parameter_family = "agent"

    def __init__(
        self,
        *,
        kind: Literal["single", "code"] = "single",
        agent_id: str = "",
        prefix: str = "/console/api",
        session_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.agent_id = agent_id
        self.prefix = prefix.rstrip("/")
        self.session_id = session_id
        self.name = f"{kind}_agent"

    @property
    def path(self) -> str:
        return f"{self.prefix}/{self.kind}-agents/{self.agent_id}/execute"

    def stop_path(self, task_id: str) -> str:
        """취소 경로. 연결 종료가 취소가 아니므로 소비 앱이 이것을 따로 호출한다."""
        return f"{self.prefix}/{self.kind}-agents/{self.agent_id}/tasks/{task_id}/stop"

    def for_agent(self, agent_id: str, *, session_id: str | None = None) -> AgentAdapter:
        """같은 설정으로 다른 agent를 가리키는 어댑터를 만든다."""
        return AgentAdapter(
            kind=self.kind,
            agent_id=agent_id,
            prefix=self.prefix,
            session_id=session_id if session_id is not None else self.session_id,
        )

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 execute body로 만든다.

        messages 배열을 받지 않으므로 마지막 사용자 메시지만 평탄화한다. 이력은 서버가
        ``sessionId``로 관리한다.
        """
        message = ""
        for turn in reversed(request.messages):
            if turn.role == "user":
                message = lowerer.lower_text(turn.content)
                break

        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        body: dict[str, Any] = {
            "message": message,
            "sessionId": params.pop("sessionId", None) or self.session_id,
            "fileIds": params.pop("fileIds", []),
            "attachmentIds": params.pop("attachmentIds", []),
            "webSearchEnabled": bool(params.pop("webSearchEnabled", False)),
            "responseMode": "streaming",
        }
        task_id = params.pop("taskId", None)
        if task_id:
            body["taskId"] = task_id
        # 남은 파라미터는 그대로 싣는다. 서버가 필드를 늘려도 다시 배포하지 않기 위해서다.
        body.update(params)
        return body

    def is_terminal(self, frame: SseFrame) -> bool:
        if frame.data.strip() == DONE:
            return True
        return self.resolve_event(frame).name in TERMINAL_EVENTS

    def decode(self, frame: SseFrame) -> AgentEvent | None:
        payload = frame.data.strip()
        if not payload or payload == DONE:
            return None
        return self.resolve_event(frame)

    def resolve_event(self, frame: SseFrame) -> AgentEvent:
        """프레임에서 이벤트 이름과 페이로드를 정한다.

        이름이 ``event:`` 필드에 없거나 ``message``면 본문 JSON의 ``event`` 또는 ``type``을
        본다. 프론트가 그렇게 한다. JSON이 아니면 원문 문자열로 폴백한다.
        """
        raw = frame.data.strip()
        parsed: dict[str, Any] | None = None
        try:
            candidate = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate

        name = frame.event.strip()
        if (not name or name == "message") and parsed is not None:
            inner = parsed.get("event") or parsed.get("type")
            if isinstance(inner, str) and inner:
                name = inner
        if not name:
            name = "message"
        return AgentEvent(name, parsed, raw)

    def to_hub(self) -> StreamMapper[Any, Any]:
        return _ToHub()


single_agent = AgentAdapter(kind="single")
"""single agent 기본 인스턴스. ``for_agent(id)``로 대상을 지정한다."""

code_agent = AgentAdapter(kind="code")
"""code agent 기본 인스턴스."""
