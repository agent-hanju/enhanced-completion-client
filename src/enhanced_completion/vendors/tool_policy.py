"""Client-tool portability policy.

Only a JSON-schema function call has a vendor-neutral execution contract. Built-in computer,
shell, patch, approval, and similar calls carry endpoint-specific state and must stay on their
source API. OpenAI custom tools can move between its two wire protocols but not to unrelated
vendors.
"""

from __future__ import annotations

from ..blocks import ToolResultBlock, ToolUseBlock

__all__ = ["can_replay_client_tool"]

_OPENAI_WIRES = frozenset({"chat_completions", "responses"})


def can_replay_client_tool(
    block: ToolUseBlock | ToolResultBlock,
    target: str,
) -> bool:
    """Whether ``block`` can be serialized for ``target`` without changing its semantics."""
    if block.source == target:
        return True
    if block.kind == "function":
        return True
    if block.kind == "custom":
        return block.source in _OPENAI_WIRES and target in _OPENAI_WIRES
    if block.kind == "mcp_approval":
        return target == "responses"
    return False
