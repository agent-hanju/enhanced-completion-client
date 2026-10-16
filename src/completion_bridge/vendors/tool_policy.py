"""클라이언트 도구 호출의 벤더 간 재사용 정책.

JSON 스키마 기반 함수 호출은 공통 실행 계약으로 재사용할 수 있다. 컴퓨터·셸·패치·승인
호출 등은 API별 상태를 사용하므로 같은 의미로 재사용할 수 있는 대상을 별도로 판정한다.
OpenAI custom 도구는 Chat Completions와 Responses 사이에서만 같은 호출 형식으로 재사용한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from completion_bridge.models.blocks import (
    ContentBlock,
    ServerToolBlock,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = ["can_replay_client_tool", "to_portable", "virtual_tool_name"]

_OPENAI_WIRES = frozenset({"chat_completions", "responses"})


def can_replay_client_tool(
    block: ToolUseBlock | ToolResultBlock,
    target: str,
) -> bool:
    """블록의 호출 의미를 유지한 채 대상 API로 직렬화할 수 있는지 판정한다."""
    if block.vendor == target:
        return True
    if block.kind == "function":
        return True
    if block.kind == "custom":
        return block.vendor in _OPENAI_WIRES and target in _OPENAI_WIRES
    if block.kind == "mcp_approval":
        return target == "responses"
    return False


#: 어댑터 계열 -> 가상 도구 이름에 붙일 브랜드 접두어.
#: ``ToolUseBlock.kind``가 이미 ``anthropic_bash`` 형태를 쓰므로 같은 규칙이다.
_BRAND = {
    "messages": "anthropic",
    "responses": "openai",
    "chat_completions": "openai",
    "generate_content": "gemini",
}


def virtual_tool_name(name: str, vendor: str | None) -> str:
    """벤더 브랜드 접두어를 붙인다. 대상 API의 실제 도구와 충돌하지 않는다."""
    brand = _BRAND.get(vendor or "")
    if not brand or name.startswith(f"{brand}_"):
        return name
    return f"{brand}_{name}"


def to_portable(blocks: Sequence[ContentBlock], target: str) -> list[ContentBlock]:
    """다른 벤더로 옮길 이력을 재생 가능한 형태로 바꾼다.

    실행 가능한 도구를 만드는 것이 아니라 **호출 기록을 옮기는 것**이다. 가상 도구는 요청
    ``tools``에 등록되지 않으므로 모델이 호출할 수 없다. 좌표계나 working directory를 옮길 수
    없다는 제약은 그 도구를 다시 호출 가능하게 만들 때의 이야기이고, 여기서는 "이렇게 호출되어
    이런 결과가 나왔다"는 사실만 옮긴다.

    쌍을 만들 수 없는 것은 그대로 두고 어휘/직렬화 경로에 맡긴다.
    """
    out: list[ContentBlock] = []
    for block in blocks:
        if isinstance(block, ServerToolBlock) and block.vendor not in (None, target):
            pair = _virtual_pair(block)
            # 옮길 기록이 없으면 빈 쌍을 만들지 않는다. 원격 참조에 갇힌 결과가 그렇다.
            out.extend(pair)
            continue
        if isinstance(block, ToolUseBlock | ToolResultBlock) and not can_replay_client_tool(
            block, target
        ):
            out.append(_as_function(block))
            continue
        out.append(block)
    return out


def _as_function(block: ToolUseBlock | ToolResultBlock) -> ContentBlock:
    """환경 의존 client tool을 브랜드 접두어가 붙은 일반 함수 호출로 바꾼다."""
    name = virtual_tool_name(block.name or block.kind, block.vendor)
    return block.model_copy(update={"kind": "function", "name": name, "native": {}})


def _virtual_pair(block: ServerToolBlock) -> list[ContentBlock]:
    """서버 도구 기록 하나를 assistant 호출과 user 결과 쌍으로 편다.

    id는 원본을 쓰거나 없으면 만든다. 쌍의 참조 무결성은 우리가 발급하므로 통제할 수 있다.
    """
    if not block.input_json and not block.output:
        # 호출 인수도 결과도 없다. 원격 참조에 갇혀 옮길 내용이 없는 경우다.
        return []
    call_id = block.id or f"{block.name or 'server_tool'}_{block.index or 0}"
    name = virtual_tool_name(block.name or "server_tool", block.vendor)
    use = ToolUseBlock(
        id=call_id,
        name=name,
        kind="function",
        input_json=block.input_json,
        index=block.index,
    )
    result = ToolResultBlock(
        tool_use_id=call_id,
        name=name,
        kind="function",
        content=block.output,
        is_error=block.is_error,
        index=block.index,
    )
    return [use, result]
