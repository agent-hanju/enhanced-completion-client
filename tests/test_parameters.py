"""Hyperparameters의 소유 기반 선택. 필드 사이 전이는 없다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from completion_bridge import (
    Hyperparameters,
    ResponseFormat,
    SyncBridge,
    ToolChoice,
)
from completion_bridge.vendors import (
    ChatCompletionsAdapter,
    generate_content,
    messages,
    responses,
)

BASE = "https://params.invalid"


def body(adapter: object, parameters: Hyperparameters) -> dict[str, object]:
    return SyncBridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        model="m",
    ).build_request(["질문"], hyperparameters=parameters)


VLLM = ChatCompletionsAdapter()
GEMINI = generate_content.for_model("m")


class TestNoPropagation:
    """한 필드에 넣은 값은 다른 필드로 전이되지 않는다."""

    def test_output_budget_does_not_transfer_between_apis(self) -> None:
        """vLLM 예산을 설정해도 다른 API의 예산 필드가 생기지 않는다."""
        params = Hyperparameters(max_completion_tokens=256)

        assert body(VLLM, params)["max_completion_tokens"] == 256
        assert "max_output_tokens" not in body(responses, params)
        assert "maxOutputTokens" not in body(GEMINI, params).get("generationConfig", {})
        # Messages의 max_tokens는 필수라 어댑터 기본값이 채운다. 전이가 아니다.
        assert body(messages, params)["max_tokens"] == 4096

    def test_each_api_needs_its_own_field(self) -> None:
        params = Hyperparameters(
            max_completion_tokens=1,
            max_tokens=2,
            max_output_tokens=3,
        )
        assert body(VLLM, params)["max_completion_tokens"] == 1
        assert body(messages, params)["max_tokens"] == 2
        assert body(responses, params)["max_output_tokens"] == 3
        assert body(GEMINI, params)["generationConfig"]["maxOutputTokens"] == 3

    def test_stop_shapes_stay_separate(self) -> None:
        """``stop``은 vLLM, ``stop_sequences``는 Messages/Gemini다."""
        params = Hyperparameters(stop=["A"], stop_sequences=["B"])
        assert body(VLLM, params)["stop"] == ["A"]
        assert "stop" not in body(messages, params)
        assert body(messages, params)["stop_sequences"] == ["B"]
        assert body(GEMINI, params)["generationConfig"]["stopSequences"] == ["B"]


class TestOwnership:
    """한 필드을 여러 API가 소유할 수 있고, 소유하지 않으면 빠진다."""

    def test_shared_field_reaches_every_owner(self) -> None:
        params = Hyperparameters(temperature=0.2)
        assert body(VLLM, params)["temperature"] == 0.2
        assert body(responses, params)["temperature"] == 0.2
        assert body(messages, params)["temperature"] == 0.2
        assert body(GEMINI, params)["generationConfig"]["temperature"] == 0.2

    def test_top_k_skips_responses_only(self) -> None:
        params = Hyperparameters(top_k=40)
        assert body(VLLM, params)["top_k"] == 40
        assert body(messages, params)["top_k"] == 40
        assert body(GEMINI, params)["generationConfig"]["topK"] == 40
        assert "top_k" not in body(responses, params)

    def test_vendor_only_fields_do_not_leak(self) -> None:
        params = Hyperparameters(
            chat_template_kwargs={"enable_thinking": False},
            previous_response_id="resp_1",
            inference_geo="us",
            safety_settings=[{"category": "HARM_CATEGORY_HATE_SPEECH"}],
        )
        chat = body(VLLM, params)
        assert chat["chat_template_kwargs"] == {"enable_thinking": False}
        assert "previous_response_id" not in chat and "inference_geo" not in chat

        resp = body(responses, params)
        assert resp["previous_response_id"] == "resp_1"
        assert "chat_template_kwargs" not in resp and "safety_settings" not in resp

        msg = body(messages, params)
        assert msg["inference_geo"] == "us"
        assert "previous_response_id" not in msg

        gem = body(GEMINI, params)
        assert gem["safetySettings"] == [{"category": "HARM_CATEGORY_HATE_SPEECH"}]
        assert "inference_geo" not in gem

    def test_same_wire_name_different_contract_is_split(self) -> None:
        """``service_tier``와 ``logprobs``는 값 계약이 달라 접두어로 나뉜다."""
        params = Hyperparameters(
            service_tier="auto",
            anthropic_service_tier="standard_only",
            gemini_service_tier="PRIORITY",
            logprobs=True,
            gemini_logprobs=5,
        )
        assert body(responses, params)["service_tier"] == "auto"
        assert body(messages, params)["service_tier"] == "standard_only"
        assert body(GEMINI, params)["serviceTier"] == "PRIORITY"

        assert body(VLLM, params)["logprobs"] is True
        assert body(GEMINI, params)["generationConfig"]["logprobs"] == 5
        assert "logprobs" not in body(messages, params)


class TestTypedObjects:
    """``tool_choice``와 ``response_format``만 대상 모양으로 투영된다."""

    def test_tool_choice_shapes_are_not_assumed_identical(self) -> None:
        named = Hyperparameters(tool_choice=ToolChoice(mode="named", name="lookup"))
        assert body(VLLM, named)["tool_choice"] == {
            "type": "function",
            "function": {"name": "lookup"},
        }
        assert body(responses, named)["tool_choice"] == {"type": "function", "name": "lookup"}
        assert body(messages, named)["tool_choice"] == {"type": "tool", "name": "lookup"}
        assert body(GEMINI, named)["toolConfig"] == {
            "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["lookup"]}
        }

    def test_json_schema_is_rendered_per_api(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        params = Hyperparameters(
            response_format=ResponseFormat(type="json_schema", name="answer", json_schema=schema)
        )
        assert body(VLLM, params)["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema},
        }
        assert body(responses, params)["text"]["format"] == {
            "type": "json_schema",
            "name": "answer",
            "schema": schema,
        }
        assert body(messages, params)["output_config"]["format"] == {
            "type": "json_schema",
            "schema": schema,
        }
        config = body(GEMINI, params)["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"] == schema

    def test_text_mode_has_no_counterpart_on_messages_and_gemini(self) -> None:
        params = Hyperparameters(response_format=ResponseFormat(type="json_object"))
        assert body(VLLM, params)["response_format"] == {"type": "json_object"}
        assert "output_config" not in body(messages, params)
        assert "responseMimeType" not in body(GEMINI, params).get("generationConfig", {})


class TestMergeAndValidation:
    def test_vendor_raw_field_wins_over_projection(self) -> None:
        """명시한 raw wire 필드가 타입 객체 투영 위에 덮인다."""
        params = Hyperparameters(
            response_format=ResponseFormat(type="json_schema", json_schema={"type": "object"}),
            text={"format": {"type": "text"}},
        )
        assert body(responses, params)["text"]["format"] == {"type": "text"}

    def test_constructor_defaults_merge_with_call_override(self) -> None:
        bridge = SyncBridge(
            vendor=VLLM,
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(temperature=0.1, max_completion_tokens=100),
        )
        request = bridge.build_request(
            ["질문"], hyperparameters=Hyperparameters(max_completion_tokens=16)
        )
        assert request["max_completion_tokens"] == 16
        assert request["temperature"] == 0.1

    def test_explicit_none_clears_a_constructor_default(self) -> None:
        """명시한 ``None``은 그 요청에서 필드를 지운다. 안 쓴 것과 구분된다."""
        bridge = SyncBridge(
            vendor=VLLM,
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(temperature=0.2, max_completion_tokens=256),
        )
        cleared = bridge.build_request(
            ["질문"], hyperparameters=Hyperparameters(temperature=None)
        )
        assert "temperature" not in cleared
        assert cleared["max_completion_tokens"] == 256

        untouched = bridge.build_request(["질문"], hyperparameters=Hyperparameters(top_p=0.9))
        assert untouched["temperature"] == 0.2
        assert untouched["top_p"] == 0.9

    def test_explicit_none_clears_a_typed_object(self) -> None:
        bridge = SyncBridge(
            vendor=VLLM,
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(response_format=ResponseFormat(type="json_object")),
        )
        assert "response_format" not in bridge.build_request(
            ["질문"], hyperparameters=Hyperparameters(response_format=None)
        )

    def test_legacy_keyword_is_the_final_override(self) -> None:
        """``**params``는 대상 wire body에 그대로 실린다. 키를 옮기지 않는다."""
        bridge = SyncBridge(
            vendor=VLLM,
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(temperature=0.1),
        )
        assert bridge.build_request(["질문"], temperature=0.9)["temperature"] == 0.9

    def test_mutually_exclusive_pair_is_rejected_before_the_request(self) -> None:
        with pytest.raises(ValidationError):
            Hyperparameters(previous_response_id="resp_1", conversation="conv_1")

    def test_unknown_field_is_rejected_instead_of_leaking(self) -> None:
        with pytest.raises(ValidationError):
            Hyperparameters(temperatur=0.2)  # type: ignore[call-arg]


class TestMessagesCombinationGuards:
    """Anthropic이 함께 받지 않는 조합을 요청 생성 시점에 막는다."""

    def test_citations_and_structured_output_conflict(self) -> None:
        from completion_bridge import DocumentBlock, HubMessage, MappingError

        bridge = SyncBridge(vendor=messages, base_url=BASE, model="m")
        message = HubMessage(
            role="user",
            content=[DocumentBlock(id="d1", text="근거", citations_enabled=True)],
        )
        params = Hyperparameters(
            response_format=ResponseFormat(type="json_schema", json_schema={"type": "object"})
        )
        with pytest.raises(MappingError, match="citations"):
            bridge.build_request([message], hyperparameters=params)

    def test_citations_alone_is_fine(self) -> None:
        from completion_bridge import DocumentBlock, HubMessage

        bridge = SyncBridge(vendor=messages, base_url=BASE, model="m")
        message = HubMessage(
            role="user",
            content=[DocumentBlock(id="d1", text="근거", citations_enabled=True)],
        )
        assert bridge.build_request([message])["messages"]

    def test_mcp_servers_without_toolset_is_rejected(self) -> None:
        from completion_bridge import MappingError

        bridge = SyncBridge(vendor=messages, base_url=BASE, model="m")
        params = Hyperparameters(mcp_servers=[{"type": "url", "url": "u", "name": "srv"}])
        with pytest.raises(MappingError, match="mcp_toolset"):
            bridge.build_request(["질문"], hyperparameters=params)

    def test_mcp_servers_with_matching_toolset_passes(self) -> None:
        from completion_bridge import ToolDefinition

        bridge = SyncBridge(vendor=messages, base_url=BASE, model="m")
        params = Hyperparameters(mcp_servers=[{"type": "url", "url": "u", "name": "srv"}])
        toolset = ToolDefinition.native(
            "messages", {"type": "mcp_toolset", "mcp_server_name": "srv"}
        )
        request = bridge.build_request(["질문"], tools=[toolset], hyperparameters=params)
        assert request["mcp_servers"][0]["name"] == "srv"


class TestGeminiResponseFormat:
    """``responseFormat``은 흩어진 출력 형식 필드를 modality별로 모은 새 구조다."""

    SCHEMA = {"type": "object"}

    def _config(self, **kwargs: object) -> dict[str, object]:
        params = Hyperparameters(**kwargs)  # type: ignore[arg-type]
        return body(GEMINI, params).get("generationConfig", {})

    def test_projection_targets_the_established_fields(self) -> None:
        config = self._config(
            response_format=ResponseFormat(type="json_schema", json_schema=self.SCHEMA)
        )
        assert config["responseMimeType"] == "application/json"
        assert config["responseJsonSchema"] == self.SCHEMA

    def test_raw_response_format_suppresses_the_projection(self) -> None:
        """둘을 함께 보내면 mimeType이 서로 모순된다. raw 쪽이 이긴다."""
        config = self._config(
            response_format=ResponseFormat(type="json_schema", json_schema=self.SCHEMA),
            gemini_response_format={"text": {"mimeType": "text/plain"}},
        )
        assert config == {"responseFormat": {"text": {"mimeType": "text/plain"}}}
        assert "responseMimeType" not in config
