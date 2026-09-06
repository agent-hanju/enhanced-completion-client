"""브리지 end-to-end. SSE 바이트에서 병합된 허브 응답까지."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from completion_bridge import (
    Bridge,
    HubMessage,
    StreamNotFinished,
    SyncBridge,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    TransportError,
)
from completion_bridge.vendors import chat_completions

BASE = "http://llm.test"
URL = f"{BASE}/v1/chat/completions"


def sse(*chunks: dict[str, object], done: bool = True) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    if done:
        body += "data: [DONE]\n\n"
    return body.encode()


def delta(**fields: object) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "model": "luxia",
        "choices": [{"index": 0, "delta": fields}],
    }


def make_bridge() -> Bridge:
    return Bridge(
        vendor=chat_completions,
        base_url=BASE,
        model="luxia",
        api_key="secret",
        http_client=httpx.AsyncClient(),
    )


class TestRequestBuilding:
    def test_string_becomes_user_turn(self) -> None:
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        body = bridge.build_request(["안녕"])
        assert body["messages"] == [{"role": "user", "content": "안녕"}]
        assert body["model"] == "luxia"
        assert body["stream"] is True

    def test_params_pass_through_unchanged(self) -> None:
        """서버가 새 필드를 추가해도 라이브러리를 다시 배포하지 않기 위해서다."""
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        body = bridge.build_request(["x"], temperature=0.2, chat_template_kwargs={"a": 1})
        assert body["temperature"] == 0.2
        assert body["chat_template_kwargs"] == {"a": 1}

    def test_reserved_names_cannot_be_overridden(self) -> None:
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        body = bridge.build_request(["x"], model="ignored-here", stream=False)
        assert body["stream"] is True

    def test_model_argument_wins_over_default(self) -> None:
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        assert bridge.build_request(["x"], model="other")["model"] == "other"

    def test_tools_become_function_definitions(self) -> None:
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        tool = ToolDefinition(name="get", description="d", input_schema={"type": "object"})
        body = bridge.build_request(["x"], tools=[tool])
        assert body["tools"] == [
            {
                "type": "function",
                "function": {"name": "get", "description": "d", "parameters": {"type": "object"}},
            }
        ]

    def test_tool_result_expands_into_its_own_message(self) -> None:
        """한 허브 메시지가 여러 wire 메시지가 될 수 있다."""
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        message = HubMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="c1", content="42"), TextBlock(text="확인")],
        )
        body = bridge.build_request([message])
        assert body["messages"] == [
            {"role": "tool", "tool_call_id": "c1", "content": "42"},
            {"role": "user", "content": "확인"},
        ]

    def test_assistant_tool_calls_are_rendered(self) -> None:
        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        message = HubMessage(
            role="assistant",
            content=[ToolUseBlock(id="c1", name="get", input_json='{"q":1}')],
        )
        body = bridge.build_request([message])
        assert body["messages"][0]["tool_calls"] == [
            {"id": "c1", "type": "function", "function": {"name": "get", "arguments": '{"q":1}'}}
        ]

    def test_thinking_block_is_dropped_when_lowering(self) -> None:
        """추론 블록은 발급 벤더로만 되돌릴 수 있다. 어휘가 없으면 생략된다."""
        from completion_bridge import ThinkingBlock

        bridge = SyncBridge(vendor=chat_completions, base_url=BASE, model="luxia")
        message = HubMessage(
            role="assistant",
            content=[ThinkingBlock(thinking="secret"), TextBlock(text="answer")],
        )
        body = bridge.build_request([message])
        assert body["messages"] == [{"role": "assistant", "content": "answer"}]


class TestAsyncStreaming:
    @respx.mock
    async def test_deltas_flow_and_merge(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    delta(role="assistant"),
                    delta(content="안녕"),
                    delta(content="하세요"),
                ),
            )
        )
        bridge = make_bridge()
        stream = bridge.stream(["hi"])
        seen = [d.text async for d in stream]
        assert seen == ["", "안녕", "하세요"]
        assert stream.result.text == "안녕하세요"
        assert stream.result.role == "assistant"
        await bridge.aclose()

    @respx.mock
    async def test_complete_returns_merged_only(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(content="a"), delta(content="b")))
        )
        bridge = make_bridge()
        assert (await bridge.complete(["hi"])).text == "ab"
        await bridge.aclose()

    @respx.mock
    async def test_reasoning_becomes_thinking_block(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200, content=sse(delta(reasoning="생각"), delta(content="답"))
            )
        )
        bridge = make_bridge()
        result = await bridge.complete(["hi"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        assert result.text == "답"
        await bridge.aclose()

    @respx.mock
    async def test_reasoning_content_alias_is_accepted(self) -> None:
        """vLLM 버전에 따라 필드 이름이 갈린다."""
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(reasoning_content="생각")))
        )
        bridge = make_bridge()
        result = await bridge.complete(["hi"])
        assert result.content[0].type == "thinking"
        await bridge.aclose()

    @respx.mock
    async def test_tool_call_fragments_assemble(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    delta(
                        tool_calls=[
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "get", "arguments": '{"a'},
                            }
                        ]
                    ),
                    delta(tool_calls=[{"index": 0, "function": {"arguments": '":1}'}}]),
                ),
            )
        )
        bridge = make_bridge()
        result = await bridge.complete(["hi"])
        block = result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert (block.id, block.name, block.input_json) == ("c1", "get", '{"a":1}')
        await bridge.aclose()

    @respx.mock
    async def test_heartbeat_comments_are_skipped(self) -> None:
        payload = b": keep-alive\n\n" + sse(delta(content="x"))
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        bridge = make_bridge()
        assert (await bridge.complete(["hi"])).text == "x"
        await bridge.aclose()

    @respx.mock
    async def test_stream_without_done_marker_still_finishes(self) -> None:
        """[DONE]을 보내지 않는 서버가 있다."""
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(content="x"), done=False))
        )
        bridge = make_bridge()
        assert (await bridge.complete(["hi"])).text == "x"
        await bridge.aclose()

    @respx.mock
    async def test_usage_is_merged(self) -> None:
        chunk = delta(content="x")
        chunk["usage"] = {"prompt_tokens": 7, "completion_tokens": 2}
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse(chunk)))
        bridge = make_bridge()
        usage = (await bridge.complete(["hi"])).usage
        assert usage is not None
        assert (usage.input_tokens, usage.output_tokens) == (7, 2)
        await bridge.aclose()

    @respx.mock
    async def test_result_before_completion_raises(self) -> None:
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse(delta(content="x"))))
        bridge = make_bridge()
        stream = bridge.stream(["hi"])
        with pytest.raises(StreamNotFinished):
            _ = stream.result
        await stream.aclose()
        await bridge.aclose()

    @respx.mock
    async def test_partial_is_available_after_close(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(content="a"), delta(content="b")))
        )
        bridge = make_bridge()
        stream = bridge.stream(["hi"])
        async for _ in stream:
            break
        await stream.aclose()
        assert stream.partial.text == "a"
        await bridge.aclose()

    @respx.mock
    async def test_bearer_token_is_sent(self) -> None:
        route = respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(content="x")))
        )
        bridge = make_bridge()
        await bridge.complete(["hi"])
        assert route.calls.last.request.headers["authorization"] == "Bearer secret"
        assert route.calls.last.request.headers["accept"] == "text/event-stream"
        await bridge.aclose()


class TestErrors:
    @respx.mock
    async def test_http_error_raises_transport_error(self) -> None:
        respx.post(URL).mock(return_value=httpx.Response(503, text="upstream busy"))
        bridge = make_bridge()
        with pytest.raises(TransportError) as info:
            await bridge.complete(["hi"])
        assert info.value.status_code == 503
        assert "upstream busy" in info.value.detail
        await bridge.aclose()


class TestSyncStreaming:
    @respx.mock
    def test_sync_bridge_mirrors_async(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(content="a"), delta(content="b")))
        )
        with SyncBridge(
            vendor=chat_completions, base_url=BASE, model="luxia", http_client=httpx.Client()
        ) as bridge:
            stream = bridge.stream(["hi"])
            assert [d.text for d in stream] == ["a", "b"]
            assert stream.result.text == "ab"

    @respx.mock
    def test_sync_complete(self) -> None:
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse(delta(content="z"))))
        with SyncBridge(
            vendor=chat_completions, base_url=BASE, model="luxia", http_client=httpx.Client()
        ) as bridge:
            assert bridge.complete(["hi"]).text == "z"


class TestRoundTrip:
    @respx.mock
    async def test_response_becomes_next_request_history(self) -> None:
        """응답의 블록을 그대로 이력에 넣는다. 허브를 Messages 모양으로 잡은 이유다."""
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse(delta(role="assistant", content="네")))
        )
        bridge = make_bridge()
        answer = await bridge.complete(["질문"])

        body = bridge.build_request(["질문", HubMessage.of_response(answer), "추가 질문"])
        assert body["messages"] == [
            {"role": "user", "content": "질문"},
            {"role": "assistant", "content": "네"},
            {"role": "user", "content": "추가 질문"},
        ]
        await bridge.aclose()
