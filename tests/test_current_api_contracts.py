"""현재 벤더 계약과 손실 허용 경계를 고정하는 회귀 테스트."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from enhanced_completion import (
    AudioBlock,
    Bridge,
    DocumentBlock,
    HubMessage,
    ImageBlock,
    ServerToolBlock,
    SyncBridge,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from enhanced_completion.vendors import (
    MessagesAdapter,
    chat_completions,
    generate_content,
    messages,
    responses,
)

BASE = "http://contracts.test"
GEMINI = generate_content.for_model("gemini-test")


def build(adapter: object, history: list[HubMessage], **kwargs: object) -> dict[str, object]:
    bridge = SyncBridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        model="test-model",
    )
    return bridge.build_request(history, **kwargs)


def sse(*payloads: object) -> bytes:
    return "".join(
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n" for payload in payloads
    ).encode()


class TestToolCallResultBridge:
    def history(self) -> list[HubMessage]:
        return [
            HubMessage(
                role="assistant",
                content=[ToolUseBlock(id="call-1", name="lookup", input_json='{"key":"x"}')],
            ),
            HubMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="call-1", content="42")],
            ),
        ]

    def test_chat_completions_uses_assistant_call_and_tool_message(self) -> None:
        body = build(chat_completions, self.history())
        assert body["messages"] == [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"key":"x"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "42"},
        ]

    def test_messages_uses_tool_use_and_user_tool_result(self) -> None:
        body = build(messages, self.history())
        assert body["messages"] == [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "lookup",
                        "input": {"key": "x"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "42"}],
            },
        ]

    def test_responses_uses_sibling_input_items(self) -> None:
        body = build(responses, self.history())
        assert body["input"] == [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": '{"key":"x"}',
            },
            {"type": "function_call_output", "call_id": "call-1", "output": "42"},
        ]

    def test_gemini_uses_model_call_and_named_user_response(self) -> None:
        body = build(GEMINI, self.history())
        assert body["contents"] == [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "lookup", "args": {"key": "x"}, "id": "call-1"}}
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "lookup",
                            "id": "call-1",
                            "response": {"result": "42"},
                        }
                    }
                ],
            },
        ]


class TestResponsesReplay:
    @respx.mock
    async def test_audio_and_transcript_deltas_accumulate(self) -> None:
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.audio.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "AA",
                    },
                    {
                        "type": "response.audio.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "BB",
                    },
                    {
                        "type": "response.audio_transcript.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "서울",
                    },
                    {"type": "response.completed", "response": {"status": "completed"}},
                ),
            )
        )
        bridge = Bridge(
            vendor=responses,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        audio = (await bridge.complete(["질문"])).content[0]
        assert isinstance(audio, AudioBlock)
        assert audio.data == "AABB"
        assert audio.transcript == "서울"
        await bridge.aclose()

    @respx.mock
    async def test_top_level_citations_and_server_usage_are_preserved(self) -> None:
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "citations": ["https://example.test/source"],
                            "server_side_tool_usage": {"web_searches": 1},
                        },
                    }
                ),
            )
        )
        bridge = Bridge(
            vendor=responses,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert result.content[0].id == "https://example.test/source"
        assert result.server_side_tool_usage == {"web_searches": 1}
        await bridge.aclose()

    @respx.mock
    async def test_reasoning_text_delta_is_not_dropped(self) -> None:
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.reasoning_text.delta",
                        "output_index": 0,
                        "delta": "reasoning text",
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {"type": "reasoning", "id": "rs_1", "summary": []},
                    },
                    {"type": "response.completed", "response": {"status": "completed"}},
                ),
            )
        )
        bridge = Bridge(
            vendor=responses,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert result.content[0].thinking == "reasoning text"
        assert result.content[0].native["id"] == "rs_1"
        await bridge.aclose()

    @respx.mock
    async def test_reasoning_item_is_replayed_with_encrypted_content(self) -> None:
        route = respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {"type": "reasoning", "id": "rs_1", "summary": []},
                    },
                    {
                        "type": "response.reasoning_summary_text.delta",
                        "output_index": 0,
                        "delta": "간단한 요약",
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "reasoning",
                            "id": "rs_1",
                            "summary": [{"type": "summary_text", "text": "간단한 요약"}],
                            "encrypted_content": "opaque-token",
                        },
                    },
                    {"type": "response.completed", "response": {"status": "completed"}},
                ),
            )
        )
        bridge = Bridge(
            vendor=responses,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert result.content[0].thinking == "간단한 요약"
        assert result.content[0].encrypted_content == "opaque-token"

        persisted = HubMessage.model_validate(HubMessage.of_response(result).model_dump())
        replay = build(responses, [persisted])
        assert replay["input"][0] == {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "간단한 요약"}],
            "encrypted_content": "opaque-token",
        }
        assert route.called
        await bridge.aclose()

    def test_nested_tool_result_uses_input_content_parts(self) -> None:
        history = [
            HubMessage(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call-1",
                        content="화면",
                        blocks=[ImageBlock(url="https://example.test/screenshot.png")],
                    )
                ],
            )
        ]
        output = build(responses, history)["input"][0]
        assert output == {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": [
                {"type": "input_text", "text": "화면"},
                {"type": "input_image", "image_url": "https://example.test/screenshot.png"},
            ],
        }

    @respx.mock
    async def test_server_tool_item_is_preserved_for_same_vendor(self) -> None:
        item = {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "current"},
        }
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {"type": "response.completed", "response": {"status": "completed"}},
                ),
            )
        )
        bridge = Bridge(
            vendor=responses,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert isinstance(result.content[0], ServerToolBlock)
        assert build(responses, [HubMessage.of_response(result)])["input"] == [item]
        await bridge.aclose()


class TestGeminiReplay:
    @respx.mock
    async def test_thought_signatures_on_call_and_text_are_replayed(self) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call-1",
                                    "name": "lookup",
                                    "args": {"key": "x"},
                                },
                                "thoughtSignature": "sig-call",
                            },
                            {"text": "답", "thoughtSignature": "sig-text"},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        respx.post(f"{BASE}{GEMINI.path}").mock(
            return_value=httpx.Response(200, content=sse(response))
        )
        bridge = Bridge(
            vendor=GEMINI,
            base_url=BASE,
            model="gemini-test",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert [block.signature for block in result.content] == ["sig-call", "sig-text"]

        parts = build(GEMINI, [HubMessage.of_response(result)])["contents"][0]["parts"]
        assert parts[0]["thoughtSignature"] == "sig-call"
        assert parts[1] == {"text": "답", "thoughtSignature": "sig-text"}
        await bridge.aclose()


class TestAnthropicReplay:
    @respx.mock
    async def test_native_citation_is_attached_to_its_text_block_on_replay(self) -> None:
        citation = {
            "type": "char_location",
            "cited_text": "서울",
            "document_index": 0,
            "document_title": "문서",
            "start_char_index": 0,
            "end_char_index": 2,
        }
        respx.post(f"{BASE}/v1/messages").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "서울"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "citations_delta", "citation": citation},
                    },
                    {"type": "message_stop"},
                ),
            )
        )
        bridge = Bridge(
            vendor=messages,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        persisted = HubMessage.model_validate(HubMessage.of_response(result).model_dump())
        part = build(messages, [persisted])["messages"][0]["content"][0]
        assert part["text"] == "서울"
        assert part["citations"] == [citation]
        await bridge.aclose()

    @respx.mock
    async def test_mcp_beta_header_is_opt_in(self) -> None:
        adapter = MessagesAdapter(betas=["mcp-client-2025-11-20"])
        route = respx.post(f"{BASE}/v1/messages").mock(
            return_value=httpx.Response(200, content=sse({"type": "message_stop"}))
        )
        bridge = Bridge(
            vendor=adapter,
            base_url=BASE,
            model="test-model",
            api_key="secret",
            http_client=httpx.AsyncClient(),
        )
        await bridge.complete(["질문"])
        headers = route.calls.last.request.headers
        assert headers["anthropic-beta"] == "mcp-client-2025-11-20"
        assert headers["x-api-key"] == "secret"
        await bridge.aclose()


class TestGeminiMediaReplay:
    @pytest.mark.parametrize(
        ("part", "expected_type"),
        [
            (
                {"inlineData": {"mimeType": "application/pdf", "data": "PDF"}},
                DocumentBlock,
            ),
            (
                {
                    "fileData": {
                        "mimeType": "audio/mpeg",
                        "fileUri": "gs://bucket/audio.mp3",
                    }
                },
                AudioBlock,
            ),
        ],
    )
    @respx.mock
    async def test_non_image_media_is_not_mislabeled_as_image(
        self,
        part: dict[str, object],
        expected_type: type[object],
    ) -> None:
        respx.post(f"{BASE}{GEMINI.path}").mock(
            return_value=httpx.Response(
                200,
                content=sse({"candidates": [{"content": {"parts": [part]}}]}),
            )
        )
        bridge = Bridge(
            vendor=GEMINI,
            base_url=BASE,
            model="gemini-test",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert isinstance(result.content[0], expected_type)
        await bridge.aclose()

    @respx.mock
    async def test_new_tool_part_is_preserved(self) -> None:
        part = {"toolCall": {"name": "remote_search", "args": {"q": "x"}}}
        respx.post(f"{BASE}{GEMINI.path}").mock(
            return_value=httpx.Response(
                200,
                content=sse({"candidates": [{"content": {"parts": [part]}}]}),
            )
        )
        bridge = Bridge(
            vendor=GEMINI,
            base_url=BASE,
            model="gemini-test",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert isinstance(result.content[0], ServerToolBlock)
        replay = build(GEMINI, [HubMessage.of_response(result)])
        assert replay["contents"][0]["parts"] == [part]
        await bridge.aclose()


class TestVendorNativeToolsAndUnknownParts:
    @pytest.mark.parametrize(
        ("adapter", "vendor", "wire", "container"),
        [
            (messages, "messages", {"type": "web_search_20250305", "name": "web_search"}, "tools"),
            (responses, "responses", {"type": "web_search_preview"}, "tools"),
            (GEMINI, "generate_content", {"googleSearch": {}}, "tools"),
        ],
    )
    def test_native_tool_definition_is_not_forced_into_function_schema(
        self,
        adapter: object,
        vendor: str,
        wire: dict[str, object],
        container: str,
    ) -> None:
        tool = ToolDefinition.native(vendor, wire)
        assert wire in build(adapter, [HubMessage.user("x")], tools=[tool])[container]

    def test_unknown_gemini_part_can_round_trip_same_vendor(self) -> None:
        part = {"futureMedia": {"id": "f1"}}
        message = HubMessage(
            role="assistant",
            content=[
                VendorBlock(
                    type="gemini_part",
                    source="generate_content",
                    raw=part,
                    native=part,
                )
            ],
        )
        assert build(GEMINI, [message])["contents"][0]["parts"] == [part]


class TestChatCurrentFields:
    def test_assistant_audio_replays_by_id_not_as_input_audio(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                AudioBlock(
                    source="chat_completions",
                    file_id="audio-1",
                    data="base64-output",
                )
            ],
        )
        assert build(chat_completions, [message])["messages"] == [
            {"role": "assistant", "content": None, "audio": {"id": "audio-1"}}
        ]

    @respx.mock
    async def test_nested_url_citation_annotation_is_normalized(self) -> None:
        payload = (
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url_citation": {
                                            "url": "https://example.test/source",
                                            "title": "Source",
                                            "start_index": 1,
                                            "end_index": 4,
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
            + b"data: [DONE]\n\n"
        )
        respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=payload)
        )
        bridge = Bridge(
            vendor=chat_completions,
            base_url=BASE,
            model="test-model",
            http_client=httpx.AsyncClient(),
        )
        citation = (await bridge.complete(["질문"])).content[0]
        assert citation.id == "https://example.test/source"
        assert citation.document_title == "Source"
        assert (citation.start_index, citation.end_index) == (1, 4)
        await bridge.aclose()
