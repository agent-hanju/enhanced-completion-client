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

from .agent import AgentToolExecutor
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
from .bridge import AsyncStream, Bridge, SyncBridge, SyncStream
from .contentstream import ContentSchema, Enter, Exit, ParseEvent, TagParser, TextRun
from .errors import (
    CompletionBridgeError,
    ExtractionError,
    MappingError,
    StreamNotFinished,
    TransportError,
)
from .hub import (
    HubMessage,
    HubRequest,
    HubResponse,
    TokenCount,
    TokenizedPrompt,
    ToolDefinition,
    Usage,
)
from .mapper import StreamMapper, compose, identity_mapper
from .merge import StreamMerger
from .native import (
    ChatCompletionsResponse,
    GenerateContentResponse,
    MessagesResponse,
    ResponsesResponse,
)
from .parameters import (
    Hyperparameters,
    ResponseFormat,
    ToolChoice,
)
from .runtime import (
    CompletionRuntime,
    HitlTask,
    Run,
    RuntimeCancelled,
    RuntimeErrorWithPartial,
    RuntimeExecution,
)
from .runtime_models import (
    Execution,
    HitlDecision,
    HitlDraft,
    PolicyEvaluator,
    ToolCall,
    ToolExecutor,
    ToolOutput,
    ToolPolicy,
)
from .transport.sse import SseEvent, SseParser
from .vendors.base import Lowerer, NativeAccumulator, VendorAdapter
from .vocabularies import CiteVocabulary, cite_schema
from .vocabulary import Vocabulary

__version__ = "0.4.1"

__all__ = [
    "AgentToolExecutor",
    "CompletionRuntime",
    "HitlTask",
    "Run",
    "RuntimeCancelled",
    "RuntimeErrorWithPartial",
    "RuntimeExecution",
    "Execution",
    "HitlDecision",
    "HitlDraft",
    "PolicyEvaluator",
    "ToolCall",
    "ToolExecutor",
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
    "ExtractionError",
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
