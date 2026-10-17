"""벤더 스트림 chunk를 비스트리밍 응답 형식 객체로 누적하는 규칙."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from completion_bridge import MappingError
from completion_bridge.vendors import chat_completions, generate_content, messages, responses
from completion_bridge.vendors.base import VendorAdapter


def accumulate(adapter: VendorAdapter, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """chunk 목록을 어댑터의 원본 응답 누적기에 차례로 넣고 결과를 돌려준다."""
    native = adapter.to_native()
    for chunk in chunks:
        native.apply(copy.deepcopy(chunk))
    return native.build().model_dump(mode="json", by_alias=True, exclude_unset=True)


class TestChatCompletions:
    def test_merges_chunks_into_chat_completion(self) -> None:
        head = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m"}
        bodies: list[dict[str, Any]] = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "logprobs": None,
                        "finish_reason": None,
                    }
                ],
                "usage": None,
            },
            {"choices": [{"index": 0, "delta": {"reasoning_content": "think"}}]},
            {"choices": [{"index": 0, "delta": {"content": "Hel"}}]},
            {"choices": [{"index": 0, "delta": {"content": "lo"}}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get", "arguments": '{"a"'},
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "type": "function",
                                    "function": {"arguments": ":1}"},
                                }
                            ]
                        },
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
        ]
        chunks = []
        for body in bodies:
            chunk = dict(head)
            chunk.update(body)
            chunks.append(chunk)

        assert accumulate(chat_completions, chunks) == {
            "id": "c1",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello",
                        "reasoning_content": "think",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get", "arguments": '{"a":1}'},
                            }
                        ],
                    },
                    "logprobs": None,
                    "finish_reason": "tool_calls",
                }
            ],
        }

    def test_keeps_choices_apart_by_index(self) -> None:
        chunks: list[dict[str, Any]] = [
            {"choices": [{"index": 1, "delta": {"content": "B"}}]},
            {"choices": [{"index": 0, "delta": {"content": "A"}}]},
            {"choices": [{"index": 1, "delta": {"content": "b"}}]},
        ]
        merged = accumulate(chat_completions, chunks)
        assert merged["choices"] == [
            {"index": 0, "message": {"content": "A"}},
            {"index": 1, "message": {"content": "Bb"}},
        ]

    def test_rejects_non_list_choices(self) -> None:
        with pytest.raises(MappingError, match="choices must be a list"):
            accumulate(chat_completions, [{"choices": {"index": 0}}])

    @pytest.mark.parametrize("same_chunk", [False, True])
    def test_accumulates_tool_fragments_like_openai_sdk(self, same_chunk: bool) -> None:
        """SDK처럼 id·함수명·인수를 연결하고 첫 목록에서도 같은 index를 병합한다."""
        first = {
            "index": 0,
            "id": "call_",
            "type": "function",
            "function": {"name": "get_", "arguments": '{"city":'},
        }
        second = {
            "index": 0,
            "id": "1",
            "function": {"name": "weather", "arguments": '"Seoul"}'},
        }
        chunks: list[dict[str, Any]] = []
        if same_chunk:
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [first, second]}}]})
        else:
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [first]}}]})
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [second]}}]})
        result = accumulate(chat_completions, chunks)
        assert result["choices"][0]["message"]["tool_calls"] == [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Seoul"}'},
            }
        ]

    def test_appends_logprobs_without_accumulating_probabilities(self) -> None:
        """logprobs의 각 토큰은 index 기반 조각이 아닌 독립 항목이다."""
        chunks = []
        for token in ["a", "b"]:
            chunks.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": token},
                            "logprobs": {
                                "content": [{"token": token, "logprob": -0.5, "top_logprobs": []}]
                            },
                        }
                    ]
                }
            )
        result = accumulate(chat_completions, chunks)
        assert result["choices"][0]["logprobs"]["content"] == [
            {"token": "a", "logprob": -0.5, "top_logprobs": []},
            {"token": "b", "logprob": -0.5, "top_logprobs": []},
        ]


class TestMessages:
    @pytest.mark.parametrize("index", [-1, True])
    def test_rejects_invalid_block_index(self, index: Any) -> None:
        """잘못된 index가 Python의 음수 인덱싱이나 bool 변환으로 해석되지 않아야 한다."""
        events = [
            {"type": "message_start", "message": {"content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": "x"},
            },
        ]
        with pytest.raises(MappingError, match="non-negative integer index"):
            accumulate(messages, events)

    def test_merges_events_into_message(self) -> None:
        events: list[dict[str, Any]] = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                },
            },
            {"type": "ping"},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "plan"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Hi"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "citations_delta",
                    "citation": {"type": "char_location", "cited_text": "x"},
                },
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "tu_1", "name": "get", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"a"'},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": ":1}"},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"input_tokens": None, "output_tokens": 20},
            },
            {"type": "message_stop"},
        ]

        assert accumulate(messages, events) == {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig"},
                {
                    "type": "text",
                    "text": "Hi",
                    "citations": [{"type": "char_location", "cited_text": "x"}],
                },
                {"type": "tool_use", "id": "tu_1", "name": "get", "input": {"a": 1}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    def test_tool_without_input_fragments_keeps_start_input(self) -> None:
        events: list[dict[str, Any]] = [
            {"type": "message_start", "message": {"id": "m", "content": []}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "t", "name": "now", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ""},
            },
        ]
        assert accumulate(messages, events)["content"] == [
            {"type": "tool_use", "id": "t", "name": "now", "input": {}}
        ]

    @pytest.mark.parametrize(
        ("events", "message"),
        [
            (
                [{"type": "content_block_start", "index": 0, "content_block": {}}],
                "before message_start",
            ),
            (
                [
                    {"type": "message_start", "message": {"content": []}},
                    {"type": "content_block_start", "index": 1, "content_block": {}},
                ],
                "index 1 != next block 0",
            ),
            (
                [
                    {"type": "message_start", "message": {"content": []}},
                    {"type": "content_block_start", "index": 0, "content_block": {}},
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "future_delta"},
                    },
                ],
                "delta type 'future_delta'",
            ),
            (
                [
                    {"type": "message_start", "message": {"content": []}},
                    {"type": "future_event"},
                ],
                "event type 'future_event'",
            ),
        ],
    )
    def test_rejects_events_without_accumulation_rule(
        self, events: list[dict[str, Any]], message: str
    ) -> None:
        native = messages.to_native()
        with pytest.raises(MappingError, match=message):
            for event in events:
                native.apply(event)

    def test_rejects_invalid_tool_input_json(self) -> None:
        events: list[dict[str, Any]] = [
            {"type": "message_start", "message": {"content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"a"'},
            },
        ]
        with pytest.raises(MappingError, match="tool input JSON of block 0 is invalid"):
            accumulate(messages, events)


class TestResponses:
    def test_uses_terminal_response_object(self) -> None:
        completed = {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Hi", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
        events: list[dict[str, Any]] = [
            {
                "type": "response.created",
                "response": {"id": "resp_1", "status": "in_progress", "output": []},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "Hi",
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": completed["output"][0],
            },
            {"type": "response.completed", "response": completed},
        ]
        assert accumulate(responses, events) == completed

    def test_rejects_stream_without_terminal_response(self) -> None:
        events: list[dict[str, Any]] = [
            {"type": "response.created", "response": {"id": "resp_1", "output": []}},
        ]
        with pytest.raises(MappingError, match="without a response object event"):
            accumulate(responses, events)


class TestGenerateContent:
    def test_preserves_parts_across_chunks_like_google_sdk(self) -> None:
        def chunk(*parts: dict[str, Any], **fields: Any) -> dict[str, Any]:
            candidate: dict[str, Any] = {"content": {"role": "model", "parts": list(parts)}}
            candidate.update(fields.pop("candidate", {}))
            body: dict[str, Any] = {"candidates": [candidate]}
            body.update(fields)
            return body

        chunks = [
            chunk(
                {"text": "Th", "thought": True},
                modelVersion="g",
                responseId="r1",
                usageMetadata={"promptTokenCount": 4},
            ),
            chunk({"text": "ink", "thought": True}),
            chunk({"text": "A"}),
            chunk({"text": "B", "thoughtSignature": "UzE="}),
            chunk({"text": "C"}, {"text": "D"}),
            chunk(
                {"functionCall": {"name": "get", "args": {}}},
                candidate={"finishReason": "STOP", "index": 0},
                usageMetadata={"promptTokenCount": 4, "candidatesTokenCount": 7},
            ),
        ]

        assert accumulate(generate_content, chunks) == {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "Th", "thought": True},
                            {"text": "ink", "thought": True},
                            {"text": "A"},
                            {"text": "B", "thoughtSignature": "UzE="},
                            {"text": "C"},
                            {"text": "D"},
                            {"functionCall": {"name": "get", "args": {}}},
                        ],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "modelVersion": "g",
            "responseId": "r1",
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 7},
        }

    def test_blocked_prompt_has_no_candidates(self) -> None:
        chunks: list[dict[str, Any]] = [{"promptFeedback": {"blockReason": "SAFETY"}}]
        assert accumulate(generate_content, chunks) == {"promptFeedback": {"blockReason": "SAFETY"}}

    def test_rejects_non_integer_candidate_index(self) -> None:
        with pytest.raises(MappingError, match="candidate index must be an integer"):
            accumulate(generate_content, [{"candidates": [{"index": "0"}]}])


def test_deep_native_merge_without_recursive_calls() -> None:
    from completion_bridge.vendors.chat_completions import _merge_value

    first: Any = {"text": "a"}
    second: Any = {"text": "b"}
    for _ in range(1200):
        first = {"items": [{"index": 0, "child": first}]}
        second = {"items": [{"index": 0, "child": second}]}
    target: dict[str, Any] = {}
    _merge_value(target, "root", first)
    _merge_value(target, "root", second)
    current = target["root"]
    for _ in range(1200):
        assert len(current["items"]) == 1
        assert current["items"][0]["index"] == 0
        current = current["items"][0]["child"]
    assert current["text"] == "ab"


def test_native_tool_lookup_preserves_order_and_choice_isolation() -> None:
    native = chat_completions.to_native()
    for index in reversed(range(300)):
        native.apply(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "type": "function",
                                    "function": {"name": "tool", "arguments": "a"},
                                }
                            ]
                        },
                    },
                    {
                        "index": 1,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "type": "function",
                                    "function": {"name": "tool", "arguments": "x"},
                                }
                            ]
                        },
                    },
                ]
            }
        )
    for index in range(300):
        native.apply(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "function": {"arguments": "b"},
                                }
                            ]
                        },
                    },
                    {
                        "index": 1,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "function": {"arguments": "y"},
                                }
                            ]
                        },
                    },
                ]
            }
        )
    result = native.build().model_dump(exclude_unset=True)
    for choice_index in range(2):
        tools = result["choices"][choice_index]["message"]["tool_calls"]
        assert len(tools) == 300
        for position in range(300):
            assert tools[position]["index"] == 299 - position
            expected = "ab" if choice_index == 0 else "xy"
            assert tools[position]["function"]["arguments"] == expected
