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

from enhanced_completion import (
    Bridge,
    HubMessage,
    MappingError,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolUseBlock,
    VendorBlock,
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
    async def test_unknown_block_type_is_preserved(self) -> None:
        payload = sse(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "web_search_tool_result", "content": [{"url": "u"}]},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        respx.post(MSG_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(messages).complete(["x"])).content[0]
        assert isinstance(block, VendorBlock)
        assert block.type == "web_search_tool_result"
        assert block.raw == {"content": [{"url": "u"}]}

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
        assert result.stop_reason == "stop"
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
        assert (await make(responses).complete(["x"])).text == "거부합니다"

    @respx.mock
    async def test_unknown_tool_family_is_matched_by_rule(self) -> None:
        """이름을 나열하지 않고 규칙으로 읽는다. 계열이 늘어도 스트림이 막히지 않는다."""
        payload = sse(
            (None, {"type": "response.brand_new_call.in_progress", "output_index": 0}),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(RESP_URL).mock(return_value=httpx.Response(200, content=payload))
        block = (await make(responses).complete(["x"])).content[0]
        assert isinstance(block, VendorBlock)
        assert block.type == "responses_in_progress"

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
        assert result.stop_reason == "max_output_tokens"
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

    def test_body_uses_input_and_instructions(self) -> None:
        body = make(responses).build_request([HubMessage.system("규칙"), "질문"])
        assert body["instructions"] == "규칙"
        assert body["input"] == [{"role": "user", "content": "질문"}]
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
        assert result.stop_reason == "stop"
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
    async def test_vendor_only_parts_are_preserved(self) -> None:
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
        assert isinstance(block, VendorBlock)
        assert block.type == "executableCode"
        assert block.raw == {"language": "PYTHON", "code": "print(1)"}

    @respx.mock
    async def test_max_tokens_finish_reason_maps_to_length(self) -> None:
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
        assert (await make(GEMINI).complete(["x"])).stop_reason == "length"

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
        assert make(responses).build_request(history)["input"][1]["content"] == "답변"
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
            assert body[key][0]["content"] == "답"
