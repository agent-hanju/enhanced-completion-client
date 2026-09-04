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
    AnnotationBlock,
    AudioBlock,
    Block,
    Citation,
    CitationBlock,
    ContentBlock,
    DocumentBlock,
    GroundingBlock,
    GroundingSource,
    GroundingSupport,
    ImageBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
    register_block,
    registered_blocks,
)
from .bridge import AsyncStream, Bridge, SyncBridge, SyncStream
from .contentstream import ContentSchema, Enter, Exit, ParseEvent, TagParser, TextRun
from .errors import (
    EnhancedCompletionError,
    MappingError,
    StreamNotFinished,
    TransportError,
)
from .hub import HubMessage, HubRequest, HubResponse, ToolDefinition, Usage
from .mapper import StreamMapper, compose, identity_mapper
from .merge import StreamMerger
from .parameters import (
    ChatCompletionsParameters,
    GenerateContentParameters,
    Hyperparameters,
    MessagesParameters,
    OutputFormat,
    ResponsesParameters,
    ToolChoice,
)
from .transport.sse import SseFrame, SseParser
from .vendors.base import Lowerer, VendorAdapter
from .vocabularies import CiteVocabulary, cite_schema
from .vocabulary import Vocabulary

__version__ = "0.1.0"

__all__ = [
    "AnnotationBlock",
    "AsyncStream",
    "AudioBlock",
    "Block",
    "Bridge",
    "Citation",
    "CitationBlock",
    "CiteVocabulary",
    "ContentSchema",
    "ContentBlock",
    "DocumentBlock",
    "Enter",
    "EnhancedCompletionError",
    "Exit",
    "HubMessage",
    "HubRequest",
    "HubResponse",
    "GroundingBlock",
    "GroundingSource",
    "GroundingSupport",
    "ImageBlock",
    "Lowerer",
    "MappingError",
    "ParseEvent",
    "ServerToolBlock",
    "SseFrame",
    "SseParser",
    "TagParser",
    "StreamMapper",
    "StreamMerger",
    "StreamNotFinished",
    "SyncBridge",
    "SyncStream",
    "TextBlock",
    "TextRun",
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
    "cite_schema",
    "compose",
    "identity_mapper",
    "register_block",
    "registered_blocks",
    "ChatCompletionsParameters",
    "GenerateContentParameters",
    "Hyperparameters",
    "MessagesParameters",
    "OutputFormat",
    "ResponsesParameters",
    "ToolChoice",
]
