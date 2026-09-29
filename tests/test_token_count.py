"""토큰 수 측정. 어댑터의 요청 순서와 브리지의 전송을 확인한다."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest
import respx

from completion_bridge import (
    Bridge,
    HubMessage,
    Hyperparameters,
    MappingError,
    SyncBridge,
    TokenCount,
    TokenizedPrompt,
    ToolChoice,
    ToolDefinition,
    TransportError,
)
from completion_bridge.vendors import (
    JsonCall,
    VendorAdapter,
    chat_completions,
    generate_content,
    messages,
    responses,
)

BASE = "http://llm.test"
WEATHER = ToolDefinition(
    name="weather",
    description="날씨를 조회한다",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
)
HISTORY: list[HubMessage | str] = [HubMessage.system("짧게 답해"), "안녕 세계"]


def generation_body(vendor: VendorAdapter, hyperparameters: Hyperparameters) -> dict[str, Any]:
    """실제 생성 요청과 같은 경로로 body를 만든다."""
    bridge = SyncBridge(vendor=vendor, base_url=BASE, model="m")
    return bridge.build_request(HISTORY, tools=[WEATHER], hyperparameters=hyperparameters)


def drive(
    calls: Generator[JsonCall, dict[str, Any], TokenCount], *payloads: dict[str, Any]
) -> tuple[list[JsonCall], TokenCount]:
    """어댑터가 내놓는 요청을 모으고 준비한 응답을 순서대로 돌려준다."""
    sent: list[JsonCall] = []
    try:
        call = next(calls)
        for payload in payloads:
            sent.append(call)
            call = calls.send(payload)
    except StopIteration as finished:
        return sent, finished.value
    raise AssertionError(f"adapter asked for an unprepared call: {call}")


class TestChatCompletionsCalls:
    def test_tokenize_then_detokenize(self) -> None:
        body = generation_body(
            chat_completions,
            Hyperparameters(
                temperature=0.2,
                max_completion_tokens=64,
                chat_template_kwargs={"enable_thinking": False},
            ),
        )

        sent, result = drive(
            chat_completions.token_count_calls(body),
            {"count": 3, "max_model_len": 8192, "tokens": [2, 105, 107]},
            {"prompt": "<bos><|turn>user\n"},
        )

        assert sent == [
            JsonCall("/tokenize", body),
            JsonCall("/detokenize", {"model": "m", "tokens": [2, 105, 107]}),
        ]
        assert body["stream"] is True
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert result == TokenCount(
            input_tokens=3,
            tokenized=TokenizedPrompt(text="<bos><|turn>user\n", token_ids=[2, 105, 107]),
        )

    def test_tokenize_response_without_tokens_is_rejected(self) -> None:
        calls = chat_completions.token_count_calls({"model": "m", "messages": []})
        next(calls)
        with pytest.raises(MappingError, match="/tokenize"):
            calls.send({"count": 3})

    def test_detokenize_response_without_prompt_is_rejected(self) -> None:
        calls = chat_completions.token_count_calls({"model": "m", "messages": []})
        next(calls)
        calls.send({"count": 1, "tokens": [2]})
        with pytest.raises(MappingError, match="/detokenize"):
            calls.send({"text": "x"})


class TestMessagesCalls:
    def test_generation_only_fields_are_excluded(self) -> None:
        body = generation_body(
            messages,
            Hyperparameters(
                max_tokens=64,
                temperature=0.2,
                top_p=0.9,
                top_k=5,
                stop_sequences=["끝"],
                anthropic_metadata={"user_id": "u"},
                anthropic_service_tier="auto",
                inference_geo="us",
                container="container_1",
                betas=["context-management-2025-06-27"],
                thinking={"type": "enabled", "budget_tokens": 1024},
                context_management={"edits": []},
                cache_control={"type": "ephemeral"},
                speed="fast",
                tool_choice=ToolChoice(mode="auto"),
            ),
        )

        sent, result = drive(messages.token_count_calls(body), {"input_tokens": 43})

        assert [call.path for call in sent] == ["/v1/messages/count_tokens"]
        assert sorted(sent[0].body) == [
            "cache_control",
            "context_management",
            "messages",
            "model",
            "speed",
            "system",
            "thinking",
            "tool_choice",
            "tools",
        ]
        assert sent[0].body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        assert result == TokenCount(input_tokens=43, tokenized=None)

    def test_response_without_input_tokens_is_rejected(self) -> None:
        calls = messages.token_count_calls({"model": "m", "messages": []})
        next(calls)
        with pytest.raises(MappingError, match="input_tokens"):
            calls.send({"tokens": 3})


class TestResponsesCalls:
    def test_generation_only_fields_are_excluded(self) -> None:
        body = generation_body(
            responses,
            Hyperparameters(
                max_output_tokens=64,
                temperature=0.2,
                top_p=0.9,
                top_logprobs=2,
                stream_options={"include_obfuscation": False},
                max_tool_calls=3,
                background=False,
                include=["message.output_text.logprobs"],
                store=False,
                metadata={"k": "v"},
                service_tier="auto",
                safety_identifier="s",
                prompt_cache_key="p",
                prompt_cache_options={"k": "v"},
                moderation={"mode": "none"},
                reasoning={"effort": "low"},
                text={"format": {"type": "text"}},
                truncation="auto",
                parallel_tool_calls=True,
                tool_choice=ToolChoice(mode="auto"),
            ),
        )

        sent, result = drive(responses.token_count_calls(body), {"input_tokens": 19})

        assert [call.path for call in sent] == ["/v1/responses/input_tokens"]
        assert sorted(sent[0].body) == [
            "input",
            "model",
            "parallel_tool_calls",
            "reasoning",
            "text",
            "tool_choice",
            "tools",
            "truncation",
        ]
        assert result == TokenCount(input_tokens=19, tokenized=None)


class TestGenerateContentCalls:
    def test_body_is_wrapped_with_model(self) -> None:
        adapter = generate_content.for_model("gemini-x", api_key="k")
        body = generation_body(adapter, Hyperparameters(max_output_tokens=64, temperature=0.2))

        sent, result = drive(adapter.token_count_calls(body), {"totalTokens": 8})

        assert sent == [
            JsonCall(
                "/v1beta/models/gemini-x:countTokens",
                {"generateContentRequest": {"model": "models/gemini-x", **body}},
            )
        ]
        assert body["generationConfig"] == {"maxOutputTokens": 64, "temperature": 0.2}
        assert result == TokenCount(input_tokens=8, tokenized=None)

    def test_adapter_without_model_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="for_model"):
            next(generate_content.token_count_calls({"contents": []}))


class TestBridgeTransport:
    @respx.mock
    async def test_async_bridge_sends_vllm_calls_in_order(self) -> None:
        tokenize = respx.post(f"{BASE}/tokenize").mock(
            return_value=httpx.Response(200, json={"count": 2, "tokens": [2, 105]})
        )
        detokenize = respx.post(f"{BASE}/detokenize").mock(
            return_value=httpx.Response(200, json={"prompt": "<bos><|turn>"})
        )
        async with Bridge(
            vendor=chat_completions,
            base_url=BASE,
            model="m",
            api_key="secret",
            http_client=httpx.AsyncClient(),
        ) as bridge:
            result = await bridge.count_tokens(["안녕"])
            expected_body = bridge.build_request(["안녕"])

        assert result == TokenCount(
            input_tokens=2, tokenized=TokenizedPrompt(text="<bos><|turn>", token_ids=[2, 105])
        )
        tokenize_request = tokenize.calls.last.request
        assert json.loads(tokenize_request.content) == expected_body
        assert tokenize_request.headers["accept"] == "application/json"
        assert tokenize_request.headers["authorization"] == "Bearer secret"
        assert json.loads(detokenize.calls.last.request.content) == {
            "model": "m",
            "tokens": [2, 105],
        }

    @respx.mock
    def test_sync_bridge_uses_vendor_headers(self) -> None:
        route = respx.post(f"{BASE}/v1/messages/count_tokens").mock(
            return_value=httpx.Response(200, json={"input_tokens": 11})
        )
        with SyncBridge(
            vendor=messages,
            base_url=BASE,
            model="claude",
            api_key="secret",
            http_client=httpx.Client(),
        ) as bridge:
            result = bridge.count_tokens(["안녕"], hyperparameters={"max_tokens": 64})

        assert result == TokenCount(input_tokens=11, tokenized=None)
        request = route.calls.last.request
        assert request.headers["x-api-key"] == "secret"
        assert "anthropic-version" in request.headers
        assert request.headers["accept"] == "application/json"
        assert sorted(json.loads(request.content)) == ["messages", "model"]

    @respx.mock
    def test_http_error_raises_transport_error(self) -> None:
        respx.post(f"{BASE}/v1/responses/input_tokens").mock(
            return_value=httpx.Response(400, text="Unknown parameter: 'x'.")
        )
        with SyncBridge(
            vendor=responses, base_url=BASE, model="gpt", http_client=httpx.Client()
        ) as bridge:
            with pytest.raises(TransportError) as info:
                bridge.count_tokens(["안녕"])

        assert info.value.status_code == 400
        assert "Unknown parameter" in info.value.detail

    @respx.mock
    async def test_non_json_body_raises_mapping_error(self) -> None:
        respx.post(f"{BASE}/v1/messages/count_tokens").mock(
            return_value=httpx.Response(200, text="<html>proxy</html>")
        )
        async with Bridge(
            vendor=messages, base_url=BASE, model="claude", http_client=httpx.AsyncClient()
        ) as bridge:
            with pytest.raises(MappingError, match="not JSON"):
                await bridge.count_tokens(["안녕"])
