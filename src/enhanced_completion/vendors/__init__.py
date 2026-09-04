"""벤더 어댑터.

다섯 스포크가 하나의 허브로 수렴한다. 벤더 축과 어휘 축이 직교하므로 어댑터를 갈아끼워도
어휘와 파이프라인 나머지는 그대로다.

| 어댑터 | API | 특이점 |
|---|---|---|
| ``chat_completions`` | OpenAI 호환, 사내 vLLM | 표준. ``[DONE]``으로 끝난다 |
| ``messages`` | Anthropic Messages | 서버가 블록 인덱스를 준다 |
| ``responses`` | OpenAI Responses | 이벤트 이름에 계층이 있다 |
| ``generate_content`` | Gemini | content part에 판별자가 없다 |
| ``single_agent`` / ``code_agent`` | 사내 agent-studio | 이름과 본문 위치가 흔들린다 |
"""

from .agent import (
    AgentAdapter,
    AgentErrorBlock,
    AgentEvent,
    AgentSourcesBlock,
    code_agent,
    single_agent,
)
from .base import Lowerer, VendorAdapter
from .chat_completions import ChatCompletionsAdapter, chat_completions
from .generate_content import GenerateContentAdapter, generate_content
from .messages import MessagesAdapter, messages
from .responses import ResponsesAdapter, responses

__all__ = [
    "AgentAdapter",
    "AgentErrorBlock",
    "AgentEvent",
    "AgentSourcesBlock",
    "ChatCompletionsAdapter",
    "GenerateContentAdapter",
    "Lowerer",
    "MessagesAdapter",
    "ResponsesAdapter",
    "VendorAdapter",
    "chat_completions",
    "code_agent",
    "generate_content",
    "messages",
    "responses",
    "single_agent",
]
