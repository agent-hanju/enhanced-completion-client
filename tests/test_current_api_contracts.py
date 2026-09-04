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
    MappingError,
    ServerToolBlock,
    SyncBridge,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from enhanced_completion.vendors import (
    ChatCompletionsAdapter,
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

    def test_chat_stream_index_is_not_replayed_as_request_field(self) -> None:
        adapter = ChatCompletionsAdapter(reasoning_input_field="reasoning_content")
        mapper = adapter.to_hub()
        deltas = mapper.map(
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"key":"x"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
        history = [HubMessage.of_response(deltas[0])]

        body = build(adapter, history)

        assert body["messages"][0]["tool_calls"] == [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"key":"x"}'},
            }
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
                        "delta": "AA",
                        "sequence_number": 0,
                    },
                    {
                        "type": "response.audio.delta",
                        "delta": "BB",
                        "sequence_number": 1,
                    },
                    {
                        "type": "response.audio.transcript.delta",
                        "delta": "서울",
                        "sequence_number": 2,
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
    async def test_global_audio_stream_does_not_merge_into_first_text_part(self) -> None:
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.audio.delta",
                        "delta": "WAV",
                        "sequence_number": 0,
                    },
                    {
                        "type": "response.output_text.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "답",
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
        assert [block.type for block in result.content] == ["audio", "text"]
        assert result.content[0].data == "WAV"
        assert result.content[1].text == "답"
        await bridge.aclose()

    def test_foreign_assistant_uses_easy_input_text_and_omits_input_only_media(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="답"),
                ImageBlock(url="https://example.test/image.png"),
                AudioBlock(data="WAV", format="wav"),
                DocumentBlock(id="doc", data="PDF", media_type="application/pdf"),
            ],
            phase="final_answer",
        )

        assert build(responses, [message])["input"] == [
            {
                "type": "message",
                "role": "assistant",
                "content": "답",
                "phase": "final_answer",
            }
        ]

    def test_native_output_message_keeps_output_union_and_metadata(self) -> None:
        native_item = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
        }
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(
                    text="답",
                    source="responses",
                    native={
                        "item": native_item,
                        "part": {"type": "output_text", "text": "답", "annotations": []},
                    },
                ),
                AudioBlock(source="responses", data="WAV"),
            ],
        )

        assert build(responses, [message])["input"] == [
            {
                **native_item,
                "content": [{"type": "output_text", "text": "답", "annotations": []}],
            }
        ]

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

    @respx.mock
    async def test_unknown_content_part_stays_inside_its_message(self) -> None:
        item = {
            "type": "message",
            "id": "msg_future",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "future_output", "payload": {"value": 1}}],
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
        assert isinstance(result.content[0], VendorBlock)
        assert build(responses, [HubMessage.of_response(result)])["input"] == [item]
        await bridge.aclose()

    def test_progress_event_is_not_replayed_as_an_input_item(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                ServerToolBlock(
                    source="responses",
                    name="web_search_call",
                    raw={
                        "type": "response.web_search_call.in_progress",
                        "output_index": 0,
                    },
                ),
                TextBlock(text="진행 중"),
            ],
        )
        assert build(responses, [message])["input"] == [
            {
                "type": "message",
                "role": "assistant",
                "content": "진행 중",
            }
        ]


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
    async def test_new_server_results_are_preserved_but_file_replay_is_rejected(self) -> None:
        blocks = [
            {
                "type": "code_execution_tool_result",
                "tool_use_id": "srv-code-1",
                "content": {
                    "type": "code_execution_result",
                    "content": [{"type": "code_execution_output", "file_id": "file_1"}],
                    "return_code": 0,
                    "stderr": "",
                    "stdout": "25\n",
                },
            },
            {
                "type": "tool_search_tool_result",
                "tool_use_id": "srv-search-1",
                "content": {
                    "type": "tool_search_tool_search_result",
                    "tool_references": [],
                },
            },
            {"type": "container_upload", "file_id": "file_1"},
        ]
        respx.post(f"{BASE}/v1/messages").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    *[
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": block,
                        }
                        for index, block in enumerate(blocks)
                    ],
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
        assert [type(block) for block in result.content] == [
            ServerToolBlock,
            ServerToolBlock,
            VendorBlock,
        ]
        with pytest.raises(MappingError, match="remote file/container references"):
            build(messages, [HubMessage.of_response(result)])
        await bridge.aclose()

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

    @respx.mock
    async def test_audio_transcription_deltas_merge_and_replay(self) -> None:
        chunks = (
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "audioTranscription": {
                                        "text": "안녕 ",
                                        "finished": False,
                                        "languageCode": "ko-KR",
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"audioTranscription": {"text": "하세요", "finished": True}}
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(
            return_value=httpx.Response(200, content=sse(*chunks))
        )
        bridge = Bridge(
            vendor=GEMINI,
            base_url=BASE,
            model="gemini-test",
            http_client=httpx.AsyncClient(),
        )
        result = await bridge.complete(["질문"])
        assert len(result.content) == 1
        assert isinstance(result.content[0], AudioBlock)
        assert result.content[0].transcript == "안녕 하세요"

        replay = build(GEMINI, [HubMessage.of_response(result)])
        assert replay["contents"][0]["parts"] == [
            {"audioTranscription": {"text": "안녕 하세요", "finished": True}}
        ]
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

    @pytest.mark.parametrize(
        "adapter",
        [chat_completions, messages, responses, GEMINI],
    )
    def test_tools_are_absent_unless_explicitly_supplied(self, adapter: object) -> None:
        assert "tools" not in build(adapter, [HubMessage.user("x")])

    @pytest.mark.parametrize(
        "adapter",
        [chat_completions, messages, GEMINI],
    )
    def test_native_tool_is_not_exposed_to_a_different_vendor(self, adapter: object) -> None:
        web_search = ToolDefinition.native("responses", {"type": "web_search_preview"})
        assert "tools" not in build(adapter, [HubMessage.user("x")], tools=[web_search])

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
    def test_assistant_audio_id_is_not_accepted_as_request_content(self) -> None:
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
        with pytest.raises(MappingError, match="file_id based content is not supported"):
            build(chat_completions, [message])


class TestRemoteContentReferences:
    @pytest.mark.parametrize(
        "block",
        [
            ImageBlock(file_id="file-image"),
            AudioBlock(file_id="file-audio"),
            DocumentBlock(file_id="file-document"),
        ],
    )
    @pytest.mark.parametrize(
        "adapter",
        [chat_completions, messages, responses, GEMINI],
    )
    def test_file_id_is_rejected_for_every_request_adapter(
        self,
        block: ImageBlock | AudioBlock | DocumentBlock,
        adapter: object,
    ) -> None:
        with pytest.raises(MappingError, match="use a URL or inline base64 data"):
            build(adapter, [HubMessage(role="user", content=[block])])

    def test_same_vendor_server_file_reference_is_not_replayed(self) -> None:
        block = ServerToolBlock(
            source="responses",
            raw={
                "type": "code_interpreter_call",
                "id": "item-1",
                "container_id": "container-1",
                "outputs": [{"type": "file", "file_id": "file-1"}],
            },
        )
        history = [HubMessage(role="assistant", content=[block])]

        with pytest.raises(MappingError, match="remote file/container references"):
            build(responses, history)
        assert build(messages, history)["messages"] == []

    def test_vendor_file_uri_is_rejected(self) -> None:
        message = HubMessage(
            role="user",
            content=[DocumentBlock(uri="gs://bucket/report.pdf")],
        )
        with pytest.raises(MappingError, match="vendor file URIs are not supported"):
            build(GEMINI, [message])

    def test_responses_document_url_uses_file_url(self) -> None:
        message = HubMessage(
            role="user",
            content=[
                DocumentBlock(
                    id="report",
                    title="report.pdf",
                    uri="https://example.test/report.pdf",
                    media_type="application/pdf",
                )
            ],
        )
        assert build(responses, [message])["input"][0]["content"] == [
            {
                "type": "input_file",
                "file_url": "https://example.test/report.pdf",
                "filename": "report.pdf",
            }
        ]

    @pytest.mark.parametrize(
        "block",
        [
            ImageBlock(url="https://example.test/image.png"),
            ImageBlock(data="PNG", media_type="image/png"),
            AudioBlock(data="WAV", format="wav", media_type="audio/wav"),
            DocumentBlock(data="PDF", media_type="application/pdf"),
        ],
    )
    def test_url_and_inline_data_remain_request_inputs(
        self,
        block: ImageBlock | AudioBlock | DocumentBlock,
    ) -> None:
        body = build(chat_completions, [HubMessage(role="user", content=[block])])
        assert body["messages"]

    def test_foreign_assistant_input_only_media_is_not_emitted_as_chat_parts(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                ImageBlock(
                    source="generate_content",
                    media_type="image/png",
                    data="PNG",
                ),
                AudioBlock(source="responses", data="WAV", format="wav"),
                DocumentBlock(
                    source="generate_content",
                    id="result",
                    uri="https://example.test/result.pdf",
                    media_type="application/pdf",
                ),
            ],
        )

        assert build(chat_completions, [message])["messages"] == [
            {
                "role": "assistant",
                "content": (
                    '<documents>\n<document id="result">\n'
                    '<content media-type="application/pdf">https://example.test/result.pdf</content>\n'
                    "</document>\n</documents>"
                ),
            }
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
        annotation = (await bridge.complete(["질문"])).content[0]
        assert annotation.id == "https://example.test/source"
        assert annotation.title == "Source"
        assert (annotation.start_index, annotation.end_index) == (1, 4)
        await bridge.aclose()
