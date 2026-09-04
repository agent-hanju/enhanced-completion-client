"""LLM 스트리밍 브리지.

여러 벤더 API의 스트리밍 응답을 하나의 허브 타입으로 받고, 그것을 그대로 다음 요청의 대화
이력으로 되쓴다. custom content type을 등록해 확장한다.

    from enhanced_completion import Bridge
    from enhanced_completion.vendors import chat_completions

    bridge = Bridge(vendor=chat_completions, base_url="http://...", model="...")

    stream = bridge.stream(["안녕하세요"])
    async for delta in stream:
        print(delta.text, end="")
    final = stream.result
"""

from __future__ import annotations

from .blocks import (
    Block,
    ContentBlock,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
    register_block,
    registered_blocks,
)
from .bridge import AsyncStream, Bridge, SyncBridge, SyncStream
from .errors import (
    EnhancedCompletionError,
    MappingError,
    StreamNotFinished,
    TransportError,
)
from .hub import HubMessage, HubRequest, HubResponse, ToolDefinition, Usage
from .mapper import StreamMapper, compose, identity_mapper
from .merge import StreamMerger
from .transport.sse import SseFrame, SseParser
from .vendors.base import Lowerer, VendorAdapter
from .vocabulary import Vocabulary

__version__ = "0.1.0"

__all__ = [
    "AsyncStream",
    "Block",
    "Bridge",
    "ContentBlock",
    "EnhancedCompletionError",
    "HubMessage",
    "HubRequest",
    "HubResponse",
    "ImageBlock",
    "Lowerer",
    "MappingError",
    "SseFrame",
    "SseParser",
    "StreamMapper",
    "StreamMerger",
    "StreamNotFinished",
    "SyncBridge",
    "SyncStream",
    "TextBlock",
    "ThinkingBlock",
    "ToolDefinition",
    "ToolResultBlock",
    "ToolUseBlock",
    "TransportError",
    "Usage",
    "VendorAdapter",
    "VendorBlock",
    "Vocabulary",
    "__version__",
    "compose",
    "identity_mapper",
    "register_block",
    "registered_blocks",
]
