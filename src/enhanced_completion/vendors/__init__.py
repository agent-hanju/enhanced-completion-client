"""벤더 어댑터.

``chat_completions``가 OpenAI 호환 규약을, ``single_agent``와 ``code_agent``가 사내
agent-studio를 담당한다. ``messages``, ``responses``, ``generate_content``는 스포크를
추가하는 일이므로 파이프라인 나머지는 그대로다.
"""

from .agent import (
    AgentActivityBlock,
    AgentAdapter,
    AgentErrorBlock,
    AgentEvent,
    AgentSourcesBlock,
    code_agent,
    single_agent,
)
from .base import Lowerer, VendorAdapter
from .chat_completions import ChatCompletionsAdapter, chat_completions

__all__ = [
    "AgentActivityBlock",
    "AgentAdapter",
    "AgentErrorBlock",
    "AgentEvent",
    "AgentSourcesBlock",
    "ChatCompletionsAdapter",
    "Lowerer",
    "VendorAdapter",
    "chat_completions",
    "code_agent",
    "single_agent",
]
