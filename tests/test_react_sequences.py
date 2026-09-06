"""ReAct-style repeated reasoning/tool sequences keep their semantic order."""

from __future__ import annotations

from typing import Any

from completion_bridge import (
    AnnotationBlock,
    AudioBlock,
    CitationBlock,
    DocumentBlock,
    GroundingBlock,
    HubMessage,
    HubResponse,
    ImageBlock,
    ServerToolBlock,
    StreamMerger,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from completion_bridge.vendors import (
    GenerateContentAdapter,
    MessagesAdapter,
    ResponsesAdapter,
    chat_completions,
)
from completion_bridge.vendors.base import VendorAdapter

BASE = "https://react.test"
GEMINI = GenerateContentAdapter(model="gemini-test")


def merge(adapter: VendorAdapter, events: list[dict[str, Any]]) -> HubResponse:
    mapper = adapter.to_hub()
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    for event in events:
        for delta in mapper.map(event):
            merger.apply(delta)
    for delta in mapper.flush():
        merger.apply(delta)
    return merger.build()


def build(adapter: VendorAdapter, messages: list[HubMessage]) -> dict[str, Any]:
    from completion_bridge import SyncBridge

    return SyncBridge(vendor=adapter, base_url=BASE, model="test").build_request(messages)


def test_same_type_blocks_with_distinct_indexes_never_merge() -> None:
    pairs = [
        (TextBlock(text="a", index=0), TextBlock(text="b", index=1)),
        (ThinkingBlock(thinking="a", index=0), ThinkingBlock(thinking="b", index=1)),
        (ToolUseBlock(id="a", index=0), ToolUseBlock(id="b", index=1)),
        (ToolResultBlock(tool_use_id="a", index=0), ToolResultBlock(tool_use_id="b", index=1)),
        (ImageBlock(url="https://a", index=0), ImageBlock(url="https://b", index=1)),
        (AudioBlock(data="a", index=0), AudioBlock(data="b", index=1)),
        (DocumentBlock(id="a", index=0), DocumentBlock(id="b", index=1)),
        (CitationBlock(id="a", index=0), CitationBlock(id="b", index=1)),
        (
            AnnotationBlock(id="a", index=0, annotation_index=0),
            AnnotationBlock(id="b", index=1, annotation_index=1),
        ),
        (
            GroundingBlock(index=0, candidate_index=0),
            GroundingBlock(index=1, candidate_index=1),
        ),
        (ServerToolBlock(id="a", index=0), ServerToolBlock(id="b", index=1)),
        (
            VendorBlock(type="future", raw={"id": "a"}, index=0),
            VendorBlock(type="future", raw={"id": "b"}, index=1),
        ),
    ]
    for first, second in pairs:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[first]))
        merger.apply(HubResponse(content=[second]))
        result = merger.build()
        assert len(result.content) == 2
        assert [block.index for block in result.content] == [0, 1]


def test_same_type_complete_blocks_without_indexes_are_appended() -> None:
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    merger.apply(HubResponse(content=[TextBlock(text="first")]))
    merger.apply(HubResponse(content=[TextBlock(text="second")]))

    result = merger.build()
    assert [block.text for block in result.content] == ["first", "second"]


def test_repeated_text_blocks_remain_distinct_in_every_request() -> None:
    history = [
        HubMessage(
            role="user",
            content=[TextBlock(text="first"), TextBlock(text="second")],
        )
    ]

    chat = build(chat_completions, history)["messages"][0]["content"]
    assert chat == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    anthropic = build(MessagesAdapter(), history)["messages"][0]["content"]
    assert anthropic == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    responses = build(ResponsesAdapter(), history)["input"][0]["content"]
    assert responses == [
        {"type": "input_text", "text": "first"},
        {"type": "input_text", "text": "second"},
    ]
    gemini = build(GEMINI, history)["contents"][0]["parts"]
    assert gemini == [{"text": "first"}, {"text": "second"}]


def test_anthropic_repeated_text_blocks_remain_distinct() -> None:
    adapter = MessagesAdapter()
    result = merge(
        adapter,
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "first"},
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "second"},
            },
        ],
    )
    assert [block.text for block in result.content] == ["first", "second"]
    replay = build(adapter, [HubMessage.of_response(result)])["messages"][0]["content"]
    assert replay == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


def test_responses_repeated_text_and_reasoning_parts_remain_distinct() -> None:
    adapter = ResponsesAdapter()
    message = {
        "type": "message",
        "id": "m1",
        "role": "assistant",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": "first", "annotations": []},
            {"type": "output_text", "text": "second", "annotations": []},
        ],
    }
    reasoning = {
        "type": "reasoning",
        "id": "r1",
        "summary": [
            {"type": "summary_text", "text": "think one"},
            {"type": "summary_text", "text": "think two"},
        ],
        "encrypted_content": "opaque",
    }
    result = merge(
        adapter,
        [
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "summary_index": 0,
                "delta": "think one",
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "summary_index": 1,
                "delta": "think two",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 0,
                "delta": "first",
            },
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 1,
                "delta": "second",
            },
            {"type": "response.output_item.done", "output_index": 1, "item": message},
        ],
    )
    assert [block.type for block in result.content] == [
        "thinking",
        "thinking",
        "text",
        "text",
    ]
    assert [block.thinking for block in result.content[:2]] == ["think one", "think two"]
    assert [block.text for block in result.content[2:]] == ["first", "second"]
    replay = build(adapter, [HubMessage.of_response(result)])["input"]
    assert replay == [reasoning, message]


def test_gemini_repeated_text_parts_remain_distinct() -> None:
    parts = [{"text": "first"}, {"text": "second"}]
    result = merge(GEMINI, [{"candidates": [{"content": {"role": "model", "parts": parts}}]}])
    assert [block.text for block in result.content] == ["first", "second"]
    replay = build(GEMINI, [HubMessage.of_response(result)])["contents"][0]["parts"]
    assert replay == parts


def test_chat_unindexed_text_deltas_form_one_protocol_channel() -> None:
    result = merge(
        chat_completions,
        [
            {"choices": [{"delta": {"content": "first"}}]},
            {"choices": [{"delta": {"content": "second"}}]},
        ],
    )
    assert len(result.content) == 1
    assert result.content[0].text == "firstsecond"


def test_responses_computer_session_items_are_same_api_only() -> None:
    call = {
        "type": "computer_call",
        "id": "item-call",
        "call_id": "call-1",
        "status": "completed",
        "action": {"type": "click", "x": 10, "y": 20, "button": "left"},
        "pending_safety_checks": [],
    }
    output = {
        "type": "computer_call_output",
        "id": "item-output",
        "call_id": "call-1",
        "output": {
            "type": "computer_screenshot",
            "image_url": "https://session.test/screenshot.png",
        },
    }
    adapter = ResponsesAdapter()
    result = merge(
        adapter,
        [
            {"type": "response.output_item.done", "output_index": 0, "item": call},
            {"type": "response.output_item.done", "output_index": 1, "item": output},
        ],
    )
    history = [HubMessage.of_response(result)]

    assert build(adapter, history)["input"] == [call, output]
    assert build(chat_completions, history)["messages"] == []
    assert build(MessagesAdapter(), history)["messages"] == []
    assert build(GEMINI, history)["contents"] == []


def test_anthropic_persistent_bash_call_is_same_api_only() -> None:
    adapter = MessagesAdapter()
    result = merge(
        adapter,
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "bash-1",
                    "name": "bash",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"command":"pwd"}',
                },
            },
        ],
    )
    call = result.content[0]
    assert isinstance(call, ToolUseBlock)
    assert call.kind == "anthropic_bash"
    history = [
        HubMessage.of_response(result),
        HubMessage(
            role="user",
            content=[
                ToolResultBlock(
                    source="messages",
                    kind=call.kind,
                    tool_use_id=call.id,
                    content="/workspace",
                )
            ],
        ),
    ]

    same = build(adapter, history)["messages"]
    assert same[0]["content"][0] == {
        "type": "tool_use",
        "id": "bash-1",
        "name": "bash",
        "input": {"command": "pwd"},
    }
    assert same[1]["content"][0]["type"] == "tool_result"
    assert build(chat_completions, history)["messages"] == []
    assert build(ResponsesAdapter(), history)["input"] == []
    assert build(GEMINI, history)["contents"] == []


def test_multiple_client_tool_cycles_keep_turn_order_for_every_vendor() -> None:
    history = [
        HubMessage(
            role="assistant",
            content=[ToolUseBlock(id="c1", name="lookup", input_json='{"step":1}')],
        ),
        HubMessage(role="user", content=[ToolResultBlock(tool_use_id="c1", content="one")]),
        HubMessage(
            role="assistant",
            content=[ToolUseBlock(id="c2", name="lookup", input_json='{"step":2}')],
        ),
        HubMessage(role="user", content=[ToolResultBlock(tool_use_id="c2", content="two")]),
        HubMessage(role="assistant", content=[TextBlock(text="done")]),
    ]

    chat = build(chat_completions, history)["messages"]
    assert [message["role"] for message in chat] == [
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [chat[0]["tool_calls"][0]["id"], chat[2]["tool_calls"][0]["id"]] == [
        "c1",
        "c2",
    ]

    anthropic = build(MessagesAdapter(), history)["messages"]
    assert [message["role"] for message in anthropic] == [
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [anthropic[0]["content"][0]["id"], anthropic[2]["content"][0]["id"]] == [
        "c1",
        "c2",
    ]

    responses = build(ResponsesAdapter(), history)["input"]
    assert [item["type"] for item in responses] == [
        "function_call",
        "function_call_output",
        "function_call",
        "function_call_output",
        "message",
    ]
    assert [responses[0]["call_id"], responses[2]["call_id"]] == ["c1", "c2"]

    gemini = build(GEMINI, history)["contents"]
    assert [content["role"] for content in gemini] == [
        "model",
        "user",
        "model",
        "user",
        "model",
    ]
    assert [
        gemini[0]["parts"][0]["functionCall"]["id"],
        gemini[2]["parts"][0]["functionCall"]["id"],
    ] == ["c1", "c2"]


def test_chat_repeated_reasoning_and_parallel_tool_deltas_do_not_collide() -> None:
    result = merge(
        chat_completions,
        [
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "first; ",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": '{"step":'},
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "second",
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": "1}"}},
                                {
                                    "index": 1,
                                    "id": "c2",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"step":2}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ],
    )

    assert [block.type for block in result.content] == ["thinking", "tool_use", "tool_use"]
    thinking, first, second = result.content
    assert thinking.thinking == "first; second"
    assert [(first.id, first.input_json), (second.id, second.input_json)] == [
        ("c1", '{"step":1}'),
        ("c2", '{"step":2}'),
    ]


def test_anthropic_interleaved_server_and_client_blocks_keep_order() -> None:
    adapter = MessagesAdapter()
    blocks = [
        {"type": "thinking", "thinking": "first"},
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": []},
        {"type": "thinking", "thinking": "second"},
        {"type": "tool_use", "id": "c1", "name": "lookup", "input": {}},
    ]
    events: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        events.append({"type": "content_block_start", "index": index, "content_block": block})
        if block["type"] == "thinking":
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "signature_delta", "signature": f"sig-{index}"},
                }
            )
        if block["type"] == "tool_use":
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": '{"q":"x"}'},
                }
            )

    result = merge(adapter, events)
    assert [block.type for block in result.content] == [
        "thinking",
        "server_tool",
        "server_tool",
        "thinking",
        "tool_use",
    ]
    replay = build(adapter, [HubMessage.of_response(result)])["messages"][0]["content"]
    assert [block["type"] for block in replay] == [
        "thinking",
        "server_tool_use",
        "web_search_tool_result",
        "thinking",
        "tool_use",
    ]


def test_anthropic_pause_turn_is_preserved_for_server_loop_continuation() -> None:
    result = merge(
        MessagesAdapter(),
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "server_tool_use",
                    "id": "s1",
                    "name": "web_search",
                    "input": {"query": "x"},
                },
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "pause_turn"},
            },
        ],
    )
    assert result.stop_reason == "pause_turn"


def test_responses_interleaved_reasoning_and_tools_keep_output_order() -> None:
    items = [
        {
            "type": "reasoning",
            "id": "r1",
            "summary": [{"type": "summary_text", "text": "first"}],
            "encrypted_content": "enc-1",
        },
        {"type": "web_search_call", "id": "s1", "status": "completed"},
        {
            "type": "reasoning",
            "id": "r2",
            "summary": [{"type": "summary_text", "text": "second"}],
            "encrypted_content": "enc-2",
        },
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "lookup",
            "arguments": '{"q":"x"}',
        },
    ]
    events = [
        {"type": "response.output_item.done", "output_index": index, "item": item}
        for index, item in enumerate(items)
    ]

    adapter = ResponsesAdapter()
    result = merge(adapter, events)
    assert [block.type for block in result.content] == [
        "thinking",
        "server_tool",
        "thinking",
        "tool_use",
    ]
    replay = build(adapter, [HubMessage.of_response(result)])["input"]
    assert [item["type"] for item in replay] == [
        "reasoning",
        "web_search_call",
        "reasoning",
        "function_call",
    ]


def test_gemini_interleaved_thought_and_tool_parts_keep_order() -> None:
    parts = [
        {"text": "first", "thought": True, "thoughtSignature": "sig-1"},
        {"executableCode": {"language": "PYTHON", "code": "print(1)"}},
        {"codeExecutionResult": {"outcome": "OUTCOME_OK", "output": "1"}},
        {"text": "second", "thought": True, "thoughtSignature": "sig-2"},
        {
            "functionCall": {"id": "c1", "name": "lookup", "args": {"q": "x"}},
            "thoughtSignature": "sig-call",
        },
    ]
    result = merge(
        GEMINI,
        [{"candidates": [{"content": {"role": "model", "parts": parts}}]}],
    )
    assert [block.type for block in result.content] == [
        "thinking",
        "server_tool",
        "server_tool",
        "thinking",
        "tool_use",
    ]
    replay = build(GEMINI, [HubMessage.of_response(result)])["contents"][0]["parts"]
    assert replay == parts
