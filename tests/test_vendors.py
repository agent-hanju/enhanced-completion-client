"""Anthropic Messages, OpenAI Responses, Gemini GenerateContent 어댑터.

세 벤더가 델타를 표현하는 방식이 근본적으로 다르다. 그 차이를 어댑터가 흡수하고 허브에서는
같은 모양이 되는지 본다.

- Messages: 서버가 블록 인덱스를 준다
- Responses: 이벤트 이름에 계층이 있다
- GenerateContent: content part에 판별자가 없다
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from _dense import CITED, dense_history

from enhanced_completion import (
    Bridge,
    CitationBlock,
    CiteVocabulary,
    HubMessage,
    HubResponse,
    MappingError,
    ServerToolBlock,
    StreamMerger,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolUseBlock,
)
from enhanced_completion.vendors import generate_content, messages, responses

BASE = "http://vendor.test"


def sse(*frames: tuple[str | None, object]) -> bytes:
    out = ""
    for name, payload in frames:
        head = f"event: {name}\n" if name else ""
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        out += f"{head}data: {body}\n\n"
    return out.encode()


def text_of(turn: object) -> str:
    """wire 메시지에서 텍스트만 뽑는다.

    벤더마다 content가 문자열이거나 part 리스트다. Responses는 ``input_text``, Gemini는
    ``text`` 키를 쓴다. 시험이 그 차이에 얽매이지 않게 한 곳에서 흡수한다.
    """
    if isinstance(turn, str):
        return turn
    if not isinstance(turn, dict):
        return ""
    content = turn.get("content", turn.get("parts"))
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and "text" in p
        )
    return ""


def all_text(body: dict[str, object], key: str) -> str:
    turns = body.get(key)
    return " ".join(text_of(t) for t in turns) if isinstance(turns, list) else ""


def make(adapter: object, **kwargs: object) -> Bridge:
    return Bridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        model="m",
        http_client=httpx.AsyncClient(),
        **kwargs,  # type: ignore[arg-type]
    )


# =============================================================================
# Anthropic Messages
# =============================================================================

MSG_URL = f"{BASE}/v1/messages"


def anthropic_stream() -> bytes:
    return sse(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "model": "claude",
                    "role": "assistant",
                    "usage": {"input_tokens": 12},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "안녕"},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "하세요"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 5},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


class TestMessages:
    @respx.mock
    async def test_text_blocks_use_the_server_index(self) -> None:
        """다른 벤더는 어댑터가 인덱스를 만들지만 여기서는 그대로 옮긴다."""
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=anthropic_stream()))
        result = await make(messages).complete(["안녕"])
        assert result.text == "안녕하세요"
        assert result.id == "msg_1"
        assert result.role == "assistant"
        assert result.stop_reason == "end_turn"
        assert [b.type for b in result.content] == ["text"]
        assert result.content[0].index == 0

    @respx.mock
    async def test_usage_merges_across_two_events(self) -> None:
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=anthropic_stream()))
        usage = (await make(messages).complete(["x"])).usage
        assert usage is not None
        assert (usage.input_tokens, usage.output_tokens) == (12, 5)

    @respx.mock
    async def test_thinking_and_signature(self) -> None:
        """서명은 이어붙이지 않는다. 원문 그대로 되돌려야 하는 값이다."""
        payload = sse(
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "왜"},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig1"},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(messages).complete(["x"])
        block = result.content[0]
        assert isinstance(block, ThinkingBlock)
        assert (block.thinking, block.signature) == ("왜", "sig1")

    @respx.mock
    async def test_tool_use_arguments_accumulate(self) -> None:
        payload = sse(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "id": "t1", "name": "search"},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"q"'},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": ':"서울"}'},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(messages).complete(["x"])
        block = result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert (block.id, block.name) == ("t1", "search")
        assert json.loads(block.input_json) == {"q": "서울"}

    @respx.mock
    async def test_two_blocks_stay_separate(self) -> None:
        payload = sse(
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "생각"},
                },
            ),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "답"},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(messages).complete(["x"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        assert result.text == "답"

    @respx.mock
    async def test_server_tool_result_becomes_a_server_tool_block(self) -> None:
        payload = sse(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srv1",
                        "content": [{"url": "u"}],
                    },
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(messages).complete(["x"])).content[0]
        assert isinstance(block, ServerToolBlock)
        assert block.name == "web_search_tool_result"
        assert block.id == "srv1"
        assert json.loads(block.output) == [{"url": "u"}]

    @respx.mock
    async def test_ping_is_ignored(self) -> None:
        payload = sse(
            ("ping", {"type": "ping"}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "x"},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await make(messages).complete(["x"])).text == "x"

    @respx.mock
    async def test_error_event_raises(self) -> None:
        payload = sse(("error", {"type": "error", "error": {"type": "overloaded_error"}}))
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        with pytest.raises(MappingError, match="overloaded_error"):
            await make(messages).complete(["x"])

    @respx.mock
    async def test_version_header_is_sent(self) -> None:
        route = respx.post(MSG_URL).mock(
            return_value=httpx.Response(200, content=anthropic_stream())
        )
        await make(messages).complete(["x"])
        assert route.calls.last.request.headers["anthropic-version"] == "2023-06-01"

    def test_system_is_hoisted_and_max_tokens_defaults(self) -> None:
        body = make(messages).build_request([HubMessage.system("규칙"), "질문"])
        assert body["system"] == "규칙"
        assert body["messages"] == [{"role": "user", "content": "질문"}]
        assert body["max_tokens"] == 4096

    def test_max_tokens_can_be_overridden(self) -> None:
        assert make(messages).build_request(["x"], max_tokens=16)["max_tokens"] == 16

    def test_tools_use_input_schema(self) -> None:
        tool = ToolDefinition(name="get", description="d", input_schema={"type": "object"})
        body = make(messages).build_request(["x"], tools=[tool])
        assert body["tools"] == [
            {"name": "get", "description": "d", "input_schema": {"type": "object"}}
        ]


# =============================================================================
# OpenAI Responses
# =============================================================================

RESP_URL = f"{BASE}/v1/responses"


class TestResponses:
    @respx.mock
    async def test_output_text_deltas_accumulate(self) -> None:
        payload = sse(
            (None, {"type": "response.created", "response": {"id": "r1", "model": "gpt"}}),
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "안녕",
                },
            ),
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "하세요",
                },
            ),
            (
                None,
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "usage": {"input_tokens": 3, "output_tokens": 4},
                    },
                },
            ),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert result.text == "안녕하세요"
        assert result.id == "r1"
        assert result.stop_reason == "end_turn"
        assert result.usage is not None
        assert (result.usage.input_tokens, result.usage.output_tokens) == (3, 4)

    @respx.mock
    async def test_two_content_parts_in_one_item_stay_separate(self) -> None:
        """``output_index``만 쓰면 같은 item의 여러 part가 한 블록으로 뭉친다."""
        payload = sse(
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "첫",
                },
            ),
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 1,
                    "delta": "둘",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert [b.type for b in result.content] == ["text", "text"]
        assert result.text == "첫둘"

    @respx.mock
    async def test_reasoning_summary_becomes_thinking(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.reasoning_summary_text.delta",
                    "output_index": 0,
                    "delta": "요약",
                },
            ),
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 1,
                    "content_index": 0,
                    "delta": "답",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        assert result.text == "답"

    @respx.mock
    async def test_function_call_assembles(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"type": "function_call", "call_id": "c1", "name": "get"},
                },
            ),
            (
                None,
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '{"a"',
                },
            ),
            (
                None,
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": ":1}",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(responses).complete(["x"])).content[0]
        assert isinstance(block, ToolUseBlock)
        assert (block.id, block.name) == ("c1", "get")
        assert json.loads(block.input_json) == {"a": 1}

    @respx.mock
    async def test_refusal_is_shown_as_text(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.refusal.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "거부합니다",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await make(responses).complete(["x"])).text == "[Refused] 거부합니다"

    @respx.mock
    async def test_unknown_tool_family_is_matched_by_rule(self) -> None:
        """이름을 나열하지 않고 규칙으로 읽는다. 계열이 늘어도 스트림이 막히지 않는다."""
        payload = sse(
            (None, {"type": "response.brand_new_call.in_progress", "output_index": 0}),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(responses).complete(["x"])).content[0]
        assert isinstance(block, ServerToolBlock)
        assert block.name == "brand_new_call"
        assert block.status == "in_progress"

    @respx.mock
    async def test_incomplete_reports_its_reason(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "잘림",
                },
            ),
            (
                None,
                {
                    "type": "response.incomplete",
                    "response": {
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                    },
                },
            ),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert result.stop_reason == "max_tokens"
        assert result.text == "잘림"

    @respx.mock
    async def test_failed_sets_error_stop_reason(self) -> None:
        payload = sse((None, {"type": "response.failed", "response": {"status": "failed"}}))
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await make(responses).complete(["x"])).stop_reason == "error"

    @respx.mock
    async def test_done_events_are_not_duplicated(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "본문",
                },
            ),
            (
                None,
                {
                    "type": "response.output_text.done",
                    "output_index": 0,
                    "content_index": 0,
                    "text": "본문",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await make(responses).complete(["x"])).text == "본문"

    def test_body_uses_input_items_with_content_parts(self) -> None:
        """``input``은 ``Item`` 리스트이고 ``Item`` 안에 ``ContentPart`` 리스트가 있다."""
        body = make(responses).build_request([HubMessage.system("규칙"), "질문"])
        assert body["instructions"] == "규칙"
        assert body["input"] == [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "질문"}],
            }
        ]
        assert "messages" not in body

    def test_tools_are_flat_function_entries(self) -> None:
        tool = ToolDefinition(name="get", description="d", input_schema={"type": "object"})
        body = make(responses).build_request(["x"], tools=[tool])
        assert body["tools"] == [
            {
                "type": "function",
                "name": "get",
                "description": "d",
                "parameters": {"type": "object"},
            }
        ]


# =============================================================================
# Gemini GenerateContent
# =============================================================================

GEMINI = generate_content.for_model("gemini-2.5-flash", api_key="k")
GEMINI_URL = f"{BASE}{GEMINI.path}"


class TestGenerateContent:
    def test_path_carries_the_model_and_sse_flag(self) -> None:
        assert GEMINI.path == "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"

    @respx.mock
    async def test_text_parts_accumulate(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [{"content": {"role": "model", "parts": [{"text": "안녕"}]}}],
                    "modelVersion": "gemini",
                    "responseId": "g1",
                },
            ),
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "하세요"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])
        assert result.text == "안녕하세요"
        assert result.id == "g1"
        assert result.role == "assistant"
        assert result.stop_reason == "end_turn"
        assert result.usage is not None
        assert (result.usage.input_tokens, result.usage.output_tokens) == (5, 2)

    @respx.mock
    async def test_thought_flag_splits_the_same_field(self) -> None:
        """같은 ``text`` 필드가 두 채널을 나른다. ``thought`` 불리언이 갈림길이다."""
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "생각", "thought": True, "thoughtSignature": "sig"},
                                    {"text": "답"},
                                ]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        thinking = result.content[0]
        assert isinstance(thinking, ThinkingBlock)
        assert (thinking.thinking, thinking.signature) == ("생각", "sig")
        assert result.text == "답"

    @respx.mock
    async def test_function_call_arrives_complete(self) -> None:
        """이 API는 인수를 조각으로 쪼개지 않고 완성된 객체로 준다."""
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "get_weather",
                                            "args": {"city": "서울"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(GEMINI).complete(["x"])).content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.name == "get_weather"
        assert json.loads(block.input_json) == {"city": "서울"}

    @respx.mock
    async def test_two_calls_get_distinct_indices(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"functionCall": {"name": "a", "args": {}}},
                                    {"functionCall": {"name": "b", "args": {}}},
                                ]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])
        calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
        assert [b.name for b in calls] == ["a", "b"]

    @respx.mock
    async def test_executable_code_becomes_a_server_tool_block(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"executableCode": {"language": "PYTHON", "code": "print(1)"}}
                                ]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(GEMINI).complete(["x"])).content[0]
        assert isinstance(block, ServerToolBlock)
        assert block.name == "executableCode"
        assert block.raw == {"language": "PYTHON", "code": "print(1)"}

    @respx.mock
    async def test_max_tokens_finish_reason_normalizes(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "잘"}]}, "finishReason": "MAX_TOKENS"}
                    ]
                },
            ),
        )
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await make(GEMINI).complete(["x"])).stop_reason == "max_tokens"

    @respx.mock
    async def test_api_key_header_is_sent(self) -> None:
        route = respx.post(GEMINI_URL).mock(
            return_value=httpx.Response(
                200, content=sse((None, {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}))
            )
        )
        await make(GEMINI).complete(["x"])
        assert route.calls.last.request.headers["x-goog-api-key"] == "k"

    @respx.mock
    async def test_error_payload_raises(self) -> None:
        payload = sse((None, {"error": {"code": 429, "message": "quota"}}))
        respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, content=payload))
        with pytest.raises(MappingError, match="quota"):
            await make(GEMINI).complete(["x"])

    def test_body_uses_contents_and_model_role(self) -> None:
        body = make(GEMINI).build_request(
            [HubMessage.system("규칙"), "질문", HubMessage.assistant("답")]
        )
        assert body["systemInstruction"] == {"parts": [{"text": "규칙"}]}
        assert body["contents"] == [
            {"role": "user", "parts": [{"text": "질문"}]},
            {"role": "model", "parts": [{"text": "답"}]},
        ]

    def test_generation_params_move_into_config(self) -> None:
        body = make(GEMINI).build_request(["x"], temperature=0.2, max_tokens=16, top_p=0.9)
        assert body["generationConfig"] == {
            "temperature": 0.2,
            "maxOutputTokens": 16,
            "topP": 0.9,
        }

    def test_tools_use_function_declarations(self) -> None:
        tool = ToolDefinition(name="get", description="d", input_schema={"type": "object"})
        body = make(GEMINI).build_request(["x"], tools=[tool])
        assert body["tools"] == [
            {
                "functionDeclarations": [
                    {"name": "get", "description": "d", "parameters": {"type": "object"}}
                ]
            }
        ]


# =============================================================================
# 교차 검증
# =============================================================================


class TestHubConvergence:
    """세 벤더가 같은 대화를 각자의 모양으로 보내면 허브에서 같아지는지."""

    @respx.mock
    async def test_all_vendors_produce_the_same_hub_shape(self) -> None:
        respx.post(MSG_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text"},
                        },
                    ),
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "서울"},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ),
            )
        )
        respx.post(RESP_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (
                        None,
                        {
                            "type": "response.output_text.delta",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": "서울",
                        },
                    ),
                    (None, {"type": "response.completed", "response": {"status": "completed"}}),
                ),
            )
        )
        respx.post(GEMINI_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (None, {"candidates": [{"content": {"parts": [{"text": "서울"}]}}]}),
                ),
            )
        )

        shapes = []
        for adapter in (messages, responses, GEMINI):
            result = await make(adapter).complete(["수도?"])
            shapes.append((result.text, [b.type for b in result.content]))

        assert shapes == [("서울", ["text"])] * 3

    @respx.mock
    async def test_cite_vocabulary_works_on_every_vendor(self) -> None:
        """어휘 축과 벤더 축이 직교한다. 어휘 코드를 고치지 않는다."""
        from enhanced_completion import CitationBlock, CiteVocabulary

        tagged = '서울은 <cite id="d1">수도</cite>다.'
        respx.post(MSG_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": tagged},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ),
            )
        )
        respx.post(GEMINI_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (None, {"candidates": [{"content": {"parts": [{"text": tagged}]}}]}),
                ),
            )
        )

        for adapter in (messages, GEMINI):
            client = Bridge(
                vendor=adapter,  # type: ignore[arg-type]
                base_url=BASE,
                model="m",
                vocabularies=[CiteVocabulary()],
                http_client=httpx.AsyncClient(),
            )
            result = await client.complete(["수도?"])
            assert result.text == "서울은 수도다."
            cite = next(b for b in result.content if isinstance(b, CitationBlock))
            assert cite.id == "d1"
            assert result.text[cite.start_index : cite.end_index] == "수도"

    @respx.mock
    async def test_one_response_lowers_into_every_vendor_request(self) -> None:
        """허브 응답 하나를 세 벤더 요청으로 각각 되쓴다."""
        respx.post(MSG_URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "답변"},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ),
            )
        )
        answer = await make(messages).complete(["질문"])
        history = ["질문", HubMessage.of_response(answer)]

        assert make(messages).build_request(history)["messages"][1]["content"] == "답변"
        assert text_of(make(responses).build_request(history)["input"][1]) == "답변"
        gemini_body = make(GEMINI).build_request(history)
        assert gemini_body["contents"][1] == {"role": "model", "parts": [{"text": "답변"}]}

    @respx.mock
    async def test_thinking_is_dropped_when_crossing_vendors(self) -> None:
        """추론은 발급 벤더로만 되돌릴 수 있다. 어휘가 없으면 생략된다."""
        message = HubMessage(
            role="assistant",
            content=[ThinkingBlock(thinking="비밀", source="messages"), TextBlock(text="답")],
        )
        for adapter, key in ((messages, "messages"), (responses, "input")):
            body = make(adapter).build_request([message])
            assert text_of(body[key][0]) == "답"


class TestNativeCitations:
    """Anthropic의 네이티브 인용 채널.

    beta 헤더가 필요 없다. 예전 ``citations-2025-01-31``은 GA가 되어 사라졌다.

    이 벤더는 인용을 본문 태그가 아니라 구조 채널로 준다. 그래서 어휘의 태그 올림을 거치지
    않고 어댑터가 바로 허브 블록을 만든다. 도착지는 태그 경로와 같다.
    """

    def test_document_goes_out_on_the_native_channel(self) -> None:
        from enhanced_completion import DocumentBlock

        message = HubMessage(
            role="user",
            content=[
                DocumentBlock(id="d1", title="지리", text="서울은 수도다."),
                TextBlock(text="수도는?"),
            ],
        )
        body = make(messages).build_request([message])
        blocks = body["messages"][0]["content"]
        assert blocks[0] == {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": "서울은 수도다."},
            "citations": {"enabled": True},
            "title": "지리",
        }
        # 문서가 본문보다 앞에 와야 모델이 근거를 먼저 읽는다.
        assert blocks[1] == {"type": "text", "text": "수도는?"}

    def test_citations_are_all_or_nothing(self) -> None:
        """한 요청에서 섞으면 거절된다. 하나라도 켜져 있으면 전체를 켠다."""
        from enhanced_completion import DocumentBlock

        message = HubMessage(
            role="user",
            content=[
                DocumentBlock(id="a", text="A", citations_enabled=False),
                DocumentBlock(id="b", text="B", citations_enabled=True),
            ],
        )
        blocks = make(messages).build_request([message])["messages"][0]["content"]
        assert [b["citations"] for b in blocks] == [{"enabled": True}, {"enabled": True}]

    def test_no_beta_header_is_sent(self) -> None:
        assert messages.request_headers() == {"anthropic-version": "2023-06-01"}

    def test_document_without_native_channel_lowers_to_text(self) -> None:
        """네이티브 문서 채널이 없는 벤더에서는 본문에 태그로 내린다."""
        from enhanced_completion import DocumentBlock

        document = DocumentBlock(id="d1", title="지리", text="서울은 수도다.")
        assert document.to_prompt().splitlines() == [
            '<document id="d1">',
            "<title>지리</title>",
            "<content>서울은 수도다.</content>",
            "</document>",
        ]

    @respx.mock
    async def test_citations_delta_becomes_a_citation_block(self) -> None:
        from enhanced_completion import CitationBlock

        payload = sse(
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "서울이다."},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "citations_delta",
                        "citation": {
                            "type": "char_location",
                            "cited_text": "서울은 대한민국의 수도다.",
                            "document_index": 0,
                            "document_title": "지리",
                            "start_char_index": 0,
                            "end_char_index": 13,
                        },
                    },
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        result = await make(messages).complete(["수도?"])

        assert result.text == "서울이다."
        cite = next(b for b in result.content if isinstance(b, CitationBlock))
        assert cite.text == "서울은 대한민국의 수도다."
        assert cite.document_title == "지리"
        assert cite.document_index == 0
        assert cite.source_kind == "char_location"
        assert (cite.source_start, cite.source_end) == (0, 13)
        # 답변 좌표는 두 축이 달라 어댑터가 채우지 않는다.
        assert (cite.start_index, cite.end_index) == (0, 0)

    @respx.mock
    async def test_page_location_maps_to_the_same_fields(self) -> None:
        from enhanced_completion import CitationBlock

        payload = sse(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "citations_delta",
                        "citation": {
                            "type": "page_location",
                            "cited_text": "본문",
                            "document_index": 1,
                            "start_page_number": 3,
                            "end_page_number": 4,
                        },
                    },
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        cite = next(
            b
            for b in (await make(messages).complete(["x"])).content
            if isinstance(b, CitationBlock)
        )
        assert cite.source_kind == "page_location"
        assert (cite.source_start, cite.source_end) == (3, 4)

    @respx.mock
    async def test_both_citation_paths_reach_the_same_block_type(self) -> None:
        """태그 경로와 네이티브 경로가 같은 허브 블록에 도달한다."""
        from enhanced_completion import CitationBlock, CiteVocabulary

        native = sse(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "citations_delta",
                        "citation": {
                            "type": "char_location",
                            "cited_text": "근거",
                            "document_index": 0,
                        },
                    },
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        tagged = sse(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": '<cite id="d1">근거</cite>'},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )

        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=native))
        from_native = await make(messages).complete(["x"])

        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=tagged))
        with_vocabulary = Bridge(
            vendor=messages,
            base_url=BASE,
            model="m",
            vocabularies=[CiteVocabulary()],
            http_client=httpx.AsyncClient(),
        )
        from_tags = await with_vocabulary.complete(["x"])

        for result in (from_native, from_tags):
            cite = next(b for b in result.content if isinstance(b, CitationBlock))
            assert cite.type == "citation"
            assert cite.text == "근거"


class TestDenseLowering:
    """블록 여섯 종류가 각 벤더 wire로 내려가는지. 네트워크를 쓰지 않는다.

    벤더마다 문서와 도구 결과가 실리는 자리가 다르다. 그 차이를 어댑터가 흡수하고 허브 쪽
    이력은 하나로 유지되는 것이 요점이다.
    """

    @staticmethod
    def _bridge(adapter: object) -> Bridge:
        return Bridge(
            vendor=adapter,  # type: ignore[arg-type]
            base_url=BASE,
            model="m",
            vocabularies=[CiteVocabulary()],
        )

    def test_anthropic_uses_native_document_and_tool_channels(self) -> None:
        body = self._bridge(messages).build_request(dense_history())
        turns = body["messages"]
        assert isinstance(turns, list)

        # 문서는 네이티브 채널로, 본문보다 앞에 간다.
        first = turns[0]["content"]
        assert isinstance(first, list)
        assert [b["type"] for b in first] == ["document", "text"]
        assert first[0]["citations"] == {"enabled": True}

        # 인용은 본문에 태그로 되끼워진다.
        rendered = " ".join(str(t.get("content", "")) for t in turns)
        assert f'<cite id="d1">{CITED}</cite>' in rendered

        # 추론은 발급 벤더 표시가 다르므로 생략된다.
        assert "표를 조회해야 한다" not in rendered

    def test_responses_lowers_document_into_the_text_channel(self) -> None:
        """이 벤더에는 네이티브 문서 채널이 없다. 본문 태그로 내려간다."""
        body = self._bridge(responses).build_request(dense_history())
        rendered = all_text(body, "input")
        assert '<document id="d1">' in rendered
        assert f'<cite id="d1">{CITED}</cite>' in rendered
        assert "표를 조회해야 한다" not in rendered

    def test_gemini_renames_assistant_to_model(self) -> None:
        body = self._bridge(generate_content.for_model("m")).build_request(dense_history())
        contents = body["contents"]
        assert isinstance(contents, list)
        assert "model" in [c["role"] for c in contents]
        rendered = all_text(body, "contents")
        assert f'<cite id="d1">{CITED}</cite>' in rendered
        # 문서는 평문이라 본문 태그로 내려간다. base64나 URI면 inlineData/fileData로 간다.
        assert '<document id="d1">' in rendered

    def test_citation_indices_survive_the_round_trip(self) -> None:
        """되쓴 문자열을 다시 올리면 같은 인용이 나온다."""
        vocabulary = CiteVocabulary()
        body = self._bridge(messages).build_request(dense_history())
        turns = body["messages"]
        assert isinstance(turns, list)
        answer = next(
            str(t["content"])
            for t in turns
            if isinstance(t.get("content"), str) and "<cite" in t["content"]
        )

        mapper = vocabulary.lift_mapper()
        deltas = [
            *mapper.map(HubResponse(content=[TextBlock(text=answer, index=0)])),
            *mapper.flush(),
        ]
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        for delta in deltas:
            merger.apply(delta)
        merged = merger.build()
        cites = [b for b in merged.content if isinstance(b, CitationBlock)]
        assert [(c.id, c.text) for c in cites] == [("d1", CITED)]
        assert merged.text[cites[0].start_index : cites[0].end_index] == CITED

    def test_tool_results_land_in_each_vendor_shape(self) -> None:
        """도구 결과가 실리는 자리가 벤더마다 다르다.

        Anthropic은 ``tool_result`` 블록, Responses는 ``function_call_output`` Item,
        Gemini는 ``functionResponse`` Part다.
        """
        anthropic = self._bridge(messages).build_request(dense_history())
        turns = anthropic["messages"]
        assert isinstance(turns, list)
        results = [
            p
            for t in turns
            if isinstance(t.get("content"), list)
            for p in t["content"]
            if p.get("type") == "tool_result"
        ]
        assert [r["tool_use_id"] for r in results] == ["c1"]

        responses_body = self._bridge(responses).build_request(dense_history())
        items = responses_body["input"]
        assert isinstance(items, list)
        outputs = [i for i in items if i.get("type") == "function_call_output"]
        assert [(o["call_id"], o["output"]) for o in outputs] == [("c1", "1000만")]

        gemini = self._bridge(generate_content.for_model("m")).build_request(dense_history())
        contents = gemini["contents"]
        assert isinstance(contents, list)
        responses_parts = [p for c in contents for p in c["parts"] if "functionResponse" in p]
        function_response = responses_parts[0]["functionResponse"]
        assert function_response["name"] == "lookup"
        assert function_response["id"] == "c1"
