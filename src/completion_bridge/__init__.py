"""LLM 스트리밍 브리지.

여러 벤더 API의 스트리밍 응답을 하나의 허브 타입으로 받고, 그것을 그대로 다음 요청의 대화
이력으로 되쓴다. custom content type을 등록해 확장한다.

    from completion_bridge import Bridge
    from completion_bridge.vendors import chat_completions

    bridge = Bridge(vendor=chat_completions, base_url="http://...", model="...")

    stream = bridge.stream(["안녕하세요"])
    async for delta in stream:
        print(delta.text, end="")
    final = stream.result
"""

from __future__ import annotations

from completion_bridge.bridge import AsyncStream, Bridge, SyncBridge, SyncStream
from completion_bridge.errors import (
    CompletionBridgeError,
    ExecutionStateError,
    MappingError,
    StreamNotFinished,
    TransportError,
    VendorStreamError,
)
from completion_bridge.models.blocks import (
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
    SearchResultBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
    register_block,
    registered_blocks,
)
from completion_bridge.models.hub import (
    HubMessage,
    HubRequest,
    HubResponse,
    TokenCount,
    TokenizedPrompt,
    ToolDefinition,
    Usage,
)
from completion_bridge.models.native import (
    ChatCompletionsResponse,
    GenerateContentResponse,
    MessagesResponse,
    ResponsesResponse,
)
from completion_bridge.models.parameters import (
    Hyperparameters,
    ResponseFormat,
    ToolChoice,
)
from completion_bridge.models.tools import (
    Execution,
    HitlDecision,
    HitlDraft,
    ToolCall,
    ToolCallback,
    ToolOutput,
    ToolPolicy,
)
from completion_bridge.runtime.execution import (
    Run,
    RuntimeCancelled,
    RuntimeErrorWithPartial,
    RuntimeExecution,
)
from completion_bridge.streaming.content import (
    ContentSchema,
    Enter,
    Exit,
    ParseEvent,
    TagParser,
    TextRun,
)
from completion_bridge.streaming.mapping import StreamMapper, Vocabulary, compose, identity_mapper
from completion_bridge.streaming.merge import StreamMerger
from completion_bridge.transport.sse import SseEvent, SseParser
from completion_bridge.vendors.base import Lowerer, NativeAccumulator, VendorAdapter
from completion_bridge.vocabularies import CiteVocabulary, cite_schema

__version__ = "0.4.1"

__all__ = [
    "Run",
    "RuntimeCancelled",
    "RuntimeErrorWithPartial",
    "RuntimeExecution",
    "Execution",
    "HitlDecision",
    "HitlDraft",
    "ToolCall",
    "ToolCallback",
    "ToolOutput",
    "ToolPolicy",
    "AnnotationBlock",
    "AsyncStream",
    "AudioBlock",
    "Block",
    "Bridge",
    "ChatCompletionsResponse",
    "Citation",
    "CitationBlock",
    "CiteVocabulary",
    "ContentSchema",
    "ContentBlock",
    "DocumentBlock",
    "Enter",
    "CompletionBridgeError",
    "ExecutionStateError",
    "Exit",
    "HubMessage",
    "HubRequest",
    "HubResponse",
    "GroundingBlock",
    "GroundingSource",
    "GroundingSupport",
    "GenerateContentResponse",
    "ImageBlock",
    "Lowerer",
    "MappingError",
    "NativeAccumulator",
    "MessagesResponse",
    "ParseEvent",
    "SearchResultBlock",
    "ResponsesResponse",
    "ServerToolBlock",
    "SseEvent",
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
    "TokenCount",
    "TokenizedPrompt",
    "ToolDefinition",
    "ToolResultBlock",
    "ToolUseBlock",
    "TransportError",
    "VendorStreamError",
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
    "Hyperparameters",
    "ResponseFormat",
    "ToolChoice",
]
