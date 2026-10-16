"""모델별 URL과 인증 정보를 포함한 어댑터 초기화의 공통 사용법을 검증한다."""

import json

import httpx
import pytest

from completion_bridge import Bridge, SyncBridge, VendorAdapter
from completion_bridge.vendors import chat_completions, generate_content, messages, responses
from completion_bridge.vendors.generate_content import GenerateContentAdapter

# 읽기 전용 Gemini path 속성을 포함해 cast 없이 타입 검사를 통과해야 한다.
VENDORS: list[VendorAdapter] = [chat_completions, responses, messages, generate_content]


@pytest.mark.parametrize("vendor", VENDORS)
@pytest.mark.parametrize("sync", [False, True])
async def test_uniform_initialization(vendor: VendorAdapter, sync: bool) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if vendor is messages:
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_init",
                        "type": "message",
                        "role": "assistant",
                        "model": "chosen",
                        "content": [],
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 0},
                },
                {"type": "message_stop"},
            ]
        elif vendor is responses:
            events = [
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_init",
                        "object": "response",
                        "status": "completed",
                        "output": [],
                    },
                }
            ]
        elif vendor is generate_content:
            events = [{"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}]
        else:
            events = [
                {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": "stop"}]}
            ]
        payload = ""
        for event in events:
            payload += f"data: {json.dumps(event)}\n\n"
        return httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    if sync:
        with httpx.Client(transport=transport) as client:
            with SyncBridge(
                vendor=vendor,
                model="chosen",
                api_key="secret",
                base_url="https://llm.test",
                http_client=client,
            ) as bridge:
                bridge.complete(["hello"])
    else:
        async with httpx.AsyncClient(transport=transport) as async_client:
            async with Bridge(
                vendor=vendor,
                model="chosen",
                api_key="secret",
                base_url="https://llm.test",
                http_client=async_client,
            ) as async_bridge:
                await async_bridge.complete(["hello"])

    request = sent[0]
    if vendor is generate_content:
        assert (
            str(request.url)
            == "https://llm.test/v1beta/models/chosen:streamGenerateContent?alt=sse"
        )
        assert request.headers["x-goog-api-key"] == "secret"
        assert "authorization" not in request.headers
        assert "model" not in json.loads(request.content)
    elif vendor is messages:
        assert request.headers["x-api-key"] == "secret"
        assert "authorization" not in request.headers
        assert "anthropic-version" in request.headers
    else:
        assert request.headers["authorization"] == "Bearer secret"


@pytest.mark.parametrize("sync", [False, True])
async def test_gemini_request_model_and_key_precedence(sync: bool) -> None:
    adapter = GenerateContentAdapter(model="legacy", api_key="legacy-key", version="v1")
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 7})
        return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    if sync:
        with httpx.Client(transport=transport) as client:
            with SyncBridge(
                vendor=adapter,
                model="default",
                api_key="bridge-key",
                base_url="https://llm.test",
                http_client=client,
            ) as bridge:
                first = bridge.stream(["hello"], model="first")
                second = bridge.stream(["hello"], model="second")
                list(second)
                list(first)
                assert bridge.count_tokens(["hello"], model="count").input_tokens == 7
                bridge.complete(["hello"])
                assert bridge.count_tokens(["hello"]).input_tokens == 7
    else:
        async with httpx.AsyncClient(transport=transport) as async_client:
            async with Bridge(
                vendor=adapter,
                model="default",
                api_key="bridge-key",
                base_url="https://llm.test",
                http_client=async_client,
            ) as async_bridge:
                async_first = async_bridge.stream(["hello"], model="first")
                async_second = async_bridge.stream(["hello"], model="second")
                _ = [delta async for delta in async_second]
                _ = [delta async for delta in async_first]
                assert (await async_bridge.count_tokens(["hello"], model="count")).input_tokens == 7
                await async_bridge.complete(["hello"])
                assert (await async_bridge.count_tokens(["hello"])).input_tokens == 7

    assert [r.url.path for r in sent] == [
        "/v1/models/second:streamGenerateContent",
        "/v1/models/first:streamGenerateContent",
        "/v1/models/count:countTokens",
        "/v1/models/default:streamGenerateContent",
        "/v1/models/default:countTokens",
    ]
    assert json.loads(sent[2].content)["generateContentRequest"]["model"] == "models/count"
    assert all(r.headers["x-goog-api-key"] == "bridge-key" for r in sent)
    assert all("authorization" not in r.headers for r in sent)
    assert adapter.model == "legacy"
    assert adapter.api_key == "legacy-key"
    assert generate_content.model == ""
    assert generate_content.api_key == ""


def test_shared_adapter_keeps_bridge_models_separate() -> None:
    first = SyncBridge(vendor=generate_content, base_url="https://llm.test", model="first")
    second = SyncBridge(vendor=generate_content, base_url="https://llm.test", model="second")
    assert first.url == "https://llm.test/v1beta/models/first:streamGenerateContent?alt=sse"
    assert second.url == "https://llm.test/v1beta/models/second:streamGenerateContent?alt=sse"
    assert generate_content.model == ""


def test_custom_header_overrides_key_case_insensitively() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, text="")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with SyncBridge(
            vendor=generate_content,
            model="m",
            api_key="bridge-key",
            headers={"X-Goog-Api-Key": "custom"},
            base_url="https://llm.test",
            http_client=client,
        ) as bridge:
            bridge.complete(["hello"])
    assert sent[0].headers.get_list("x-goog-api-key") == ["custom"]
