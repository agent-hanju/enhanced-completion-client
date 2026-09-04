"""벤더 어댑터.

``chat_completions``로 시작한다. ``messages``, ``responses``, ``generate_content``,
사내 ``agent``는 스포크를 추가하는 일이므로 파이프라인 나머지는 그대로다.
"""

from .base import Lowerer, VendorAdapter
from .chat_completions import ChatCompletionsAdapter, chat_completions

__all__ = ["ChatCompletionsAdapter", "Lowerer", "VendorAdapter", "chat_completions"]
