"""Hyperparameters의 소유 기반 선택. 필드 사이 전이는 없다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from completion_bridge import (
    Hyperparameters,
    ResponseFormat,
    SyncBridge,
    ToolChoice,
    ToolChoiceAny,
    ToolChoiceAuto,
    ToolChoiceNone,
    ToolChoiceTool,
    ToolDefinition,
)
from completion_bridge.vendors import (
    ChatCompletionsAdapter,
    generate_content,
    messages,
    responses,
)

BASE = "https://params.invalid"


def body(
    adapter: object,
    parameters: Hyperparameters,
    tools: list[ToolDefinition] | None = None,
) -> dict[str, Any]:
    return SyncBridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        model="m",
    ).build_request(["질문"], tools=tools or [], hyperparameters=parameters)


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

    def test_thinking_token_budget_reaches_only_vllm(self) -> None:
        params = Hyperparameters(thinking_token_budget=512)
        assert body(VLLM, params)["thinking_token_budget"] == 512
        assert "thinking_token_budget" not in body(responses, params)
        assert "thinking_token_budget" not in body(messages, params)
        assert "thinking_token_budget" not in body(GEMINI, params)

    def test_vllm_generation_controls_reach_only_vllm(self) -> None:
        """``include_reasoning=False``는 None이 아니므로 빠지지 않는다."""
        detection = {"max_pattern_size": 20, "min_count": 3}
        params = Hyperparameters(
            include_reasoning=False,
            repetition_detection=detection,
            stream_interval=4,
        )
        chat = body(VLLM, params)
        assert chat["include_reasoning"] is False
        assert chat["repetition_detection"] == detection
        assert chat["stream_interval"] == 4
        controls = {"include_reasoning", "repetition_detection", "stream_interval"}
        for other in (body(responses, params), body(messages, params), body(GEMINI, params)):
            assert not controls & other.keys()

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


class TestToolChoice:
    """``tool_choice``는 Messages 객체 형식만 받고 네 API의 표현으로 변환된다."""

    @pytest.mark.parametrize(
        "value",
        [
            {"type": "auto"},
            {"type": "none"},
            {"type": "any"},
            {"type": "tool", "name": "deep-research"},
            {"type": "auto", "disable_parallel_tool_use": True},
            {"type": "any", "disable_parallel_tool_use": False},
            {"type": "tool", "name": "deep-research", "disable_parallel_tool_use": True},
        ],
    )
    def test_messages_shaped_objects_are_accepted(self, value: dict[str, Any]) -> None:
        params = Hyperparameters.model_validate({"tool_choice": value})
        assert params.tool_choice is not None
        assert params.tool_choice.model_dump(exclude_none=True) == value

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({"type": "required"}, id="unknown-type"),
            pytest.param({"name": "lookup"}, id="missing-type"),
            pytest.param({"type": "tool"}, id="tool-without-name"),
            pytest.param({"type": "tool", "name": ""}, id="empty-name"),
            pytest.param({"type": "auto", "name": "lookup"}, id="name-on-auto"),
            pytest.param({"type": "any", "name": "lookup"}, id="name-on-any"),
            pytest.param({"type": "none", "name": "lookup"}, id="name-on-none"),
            pytest.param(
                {"type": "none", "disable_parallel_tool_use": True}, id="parallel-on-none"
            ),
            pytest.param(
                {"type": "auto", "disable_parallel_tool_use": "true"}, id="parallel-not-bool"
            ),
            pytest.param({"type": "auto", "extra": 1}, id="undeclared-field"),
            pytest.param("auto", id="string-shorthand"),
            pytest.param({"mode": "auto"}, id="legacy-mode"),
            pytest.param({"mode": "named", "name": "lookup"}, id="legacy-named"),
        ],
    )
    def test_invalid_objects_are_rejected(self, value: object) -> None:
        with pytest.raises(ValidationError):
            Hyperparameters.model_validate({"tool_choice": value})

    def test_json_schema_states_fields_per_type(self) -> None:
        """공개 JSON Schema가 type별 필수·허용 필드를 런타임 검증과 같게 표현한다."""
        schema = TypeAdapter(ToolChoice).json_schema()
        assert schema["discriminator"]["propertyName"] == "type"
        definitions = schema["$defs"]

        auto = definitions["ToolChoiceAuto"]
        assert auto["required"] == ["type"]
        assert sorted(auto["properties"]) == ["disable_parallel_tool_use", "type"]
        assert auto["additionalProperties"] is False

        any_choice = definitions["ToolChoiceAny"]
        assert any_choice["required"] == ["type"]
        assert sorted(any_choice["properties"]) == ["disable_parallel_tool_use", "type"]
        assert any_choice["additionalProperties"] is False

        tool = definitions["ToolChoiceTool"]
        assert tool["required"] == ["type", "name"]
        assert sorted(tool["properties"]) == ["disable_parallel_tool_use", "name", "type"]
        assert tool["properties"]["name"]["minLength"] == 1
        assert tool["additionalProperties"] is False

        none = definitions["ToolChoiceNone"]
        assert none["required"] == ["type"]
        assert sorted(none["properties"]) == ["type"]
        assert none["additionalProperties"] is False

        parallel = auto["properties"]["disable_parallel_tool_use"]
        assert parallel["anyOf"] == [{"type": "boolean"}, {"type": "null"}]

        field = Hyperparameters.model_json_schema()["properties"]["tool_choice"]
        assert field["anyOf"][0]["discriminator"]["propertyName"] == "type"
        assert field["anyOf"][1] == {"type": "null"}

    def test_omitted_choice_and_parallel_option_are_not_sent(self) -> None:
        omitted = Hyperparameters()
        cleared = Hyperparameters(tool_choice=None)
        for params in (omitted, cleared):
            assert "tool_choice" not in body(VLLM, params)
            assert "tool_choice" not in body(responses, params)
            assert "tool_choice" not in body(messages, params)
            assert "toolConfig" not in body(GEMINI, params)

        auto = Hyperparameters(tool_choice=ToolChoiceAuto(type="auto"))
        assert body(messages, auto)["tool_choice"] == {"type": "auto"}
        assert "parallel_tool_calls" not in body(VLLM, auto)
        assert "parallel_tool_calls" not in body(responses, auto)

    def test_auto_is_converted_per_api(self) -> None:
        params = Hyperparameters(tool_choice=ToolChoiceAuto(type="auto"))
        assert body(VLLM, params)["tool_choice"] == "auto"
        assert body(responses, params)["tool_choice"] == "auto"
        assert body(messages, params)["tool_choice"] == {"type": "auto"}
        assert body(GEMINI, params)["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}

    def test_none_is_converted_per_api(self) -> None:
        params = Hyperparameters(tool_choice=ToolChoiceNone(type="none"))
        assert body(VLLM, params)["tool_choice"] == "none"
        assert body(responses, params)["tool_choice"] == "none"
        assert body(messages, params)["tool_choice"] == {"type": "none"}
        assert body(GEMINI, params)["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}

    def test_any_is_converted_per_api(self) -> None:
        params = Hyperparameters(tool_choice=ToolChoiceAny(type="any"))
        assert body(VLLM, params)["tool_choice"] == "required"
        assert body(responses, params)["tool_choice"] == "required"
        assert body(messages, params)["tool_choice"] == {"type": "any"}
        assert body(GEMINI, params)["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

    def test_tool_is_converted_per_api(self) -> None:
        params = Hyperparameters(tool_choice=ToolChoiceTool(type="tool", name="lookup"))
        assert body(VLLM, params)["tool_choice"] == {
            "type": "function",
            "function": {"name": "lookup"},
        }
        assert body(responses, params)["tool_choice"] == {"type": "function", "name": "lookup"}
        assert body(messages, params)["tool_choice"] == {"type": "tool", "name": "lookup"}
        assert body(GEMINI, params)["toolConfig"] == {
            "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["lookup"]}
        }

    def test_named_tool_matches_the_sent_tool_definition(self) -> None:
        """지정한 이름이 실제로 전송되는 도구 정의의 식별자와 같다."""
        tool = ToolDefinition(
            name="deep-research", description="d", input_schema={"type": "object"}
        )
        params = Hyperparameters(tool_choice=ToolChoiceTool(type="tool", name="deep-research"))

        chat = body(VLLM, params, [tool])
        assert chat["tool_choice"]["function"]["name"] == chat["tools"][0]["function"]["name"]
        responses_body = body(responses, params, [tool])
        assert responses_body["tool_choice"]["name"] == responses_body["tools"][0]["name"]
        messages_body = body(messages, params, [tool])
        assert messages_body["tool_choice"]["name"] == messages_body["tools"][0]["name"]
        gemini = body(GEMINI, params, [tool])
        declaration = gemini["tools"][0]["functionDeclarations"][0]
        allowed = gemini["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"]
        assert allowed == [declaration["name"]]

    @pytest.mark.parametrize("disable", [True, False])
    def test_parallel_option_is_sent_per_api(self, disable: bool) -> None:
        params = Hyperparameters(
            tool_choice=ToolChoiceAny(type="any", disable_parallel_tool_use=disable)
        )
        assert body(messages, params)["tool_choice"] == {
            "type": "any",
            "disable_parallel_tool_use": disable,
        }
        chat = body(VLLM, params)
        assert chat["tool_choice"] == "required"
        assert chat["parallel_tool_calls"] is (not disable)
        responses_body = body(responses, params)
        assert responses_body["tool_choice"] == "required"
        assert responses_body["parallel_tool_calls"] is (not disable)

    @pytest.mark.parametrize("disable", [True, False])
    def test_top_level_option_with_same_meaning_is_accepted(self, disable: bool) -> None:
        params = Hyperparameters(
            tool_choice=ToolChoiceAuto(type="auto", disable_parallel_tool_use=disable),
            parallel_tool_calls=not disable,
        )
        assert body(VLLM, params)["parallel_tool_calls"] is (not disable)
        assert body(responses, params)["parallel_tool_calls"] is (not disable)
        assert body(messages, params)["tool_choice"] == {
            "type": "auto",
            "disable_parallel_tool_use": disable,
        }

    @pytest.mark.parametrize("disable", [True, False])
    def test_conflicting_top_level_option_is_rejected(self, disable: bool) -> None:
        with pytest.raises(ValidationError, match="conflicts with parallel_tool_calls"):
            Hyperparameters(
                tool_choice=ToolChoiceTool(
                    type="tool", name="lookup", disable_parallel_tool_use=disable
                ),
                parallel_tool_calls=disable,
            )

    def test_conflict_between_default_and_call_override_is_rejected(self) -> None:
        bridge = SyncBridge(
            vendor=VLLM,
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(parallel_tool_calls=False),
        )
        override = Hyperparameters(
            tool_choice=ToolChoiceAuto(type="auto", disable_parallel_tool_use=False)
        )
        with pytest.raises(ValidationError, match="conflicts with parallel_tool_calls"):
            bridge.build_request(["질문"], hyperparameters=override)

    @pytest.mark.parametrize("disable", [True, False])
    def test_generate_content_rejects_parallel_option(self, disable: bool) -> None:
        params = Hyperparameters(
            tool_choice=ToolChoiceAuto(type="auto", disable_parallel_tool_use=disable)
        )
        with pytest.raises(ValueError, match="not supported by GenerateContent"):
            body(GEMINI, params)

    def test_top_level_parallel_option_reaches_messages_choice(self) -> None:
        """``parallel_tool_calls``는 반전되어 Messages tool_choice 안에 들어가고 none에는 없다."""
        auto = Hyperparameters(tool_choice=ToolChoiceAuto(type="auto"), parallel_tool_calls=False)
        assert body(messages, auto)["tool_choice"] == {
            "type": "auto",
            "disable_parallel_tool_use": True,
        }
        none = Hyperparameters(tool_choice=ToolChoiceNone(type="none"), parallel_tool_calls=False)
        assert body(messages, none)["tool_choice"] == {"type": "none"}

    def test_call_override_replaces_the_whole_choice(self) -> None:
        """기본값의 name이나 병렬 옵션이 호출별 선택에 남지 않는다."""
        bridge = SyncBridge(
            vendor=messages,  # type: ignore[arg-type]
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(
                tool_choice=ToolChoiceTool(
                    type="tool", name="lookup", disable_parallel_tool_use=True
                )
            ),
        )
        none = bridge.build_request(
            ["질문"], hyperparameters=Hyperparameters(tool_choice=ToolChoiceNone(type="none"))
        )
        assert none["tool_choice"] == {"type": "none"}
        auto = bridge.build_request(["질문"], hyperparameters={"tool_choice": {"type": "auto"}})
        assert auto["tool_choice"] == {"type": "auto"}
        untouched = bridge.build_request(["질문"], hyperparameters=Hyperparameters(top_k=5))
        assert untouched["tool_choice"] == {
            "type": "tool",
            "name": "lookup",
            "disable_parallel_tool_use": True,
        }


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
        cleared = bridge.build_request(["질문"], hyperparameters=Hyperparameters(temperature=None))
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

    def _config(self, **kwargs: Any) -> dict[str, Any]:
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


def test_deep_mapping_merge_without_recursive_calls() -> None:
    from completion_bridge.models.parameters import _merge

    left = {"value": "left"}
    right = {"value": "right"}
    for _ in range(1500):
        left = {"child": left}
        right = {"child": right}
    result = _merge(left, right)
    for _ in range(1500):
        assert result is not left
        result = result["child"]
        left = left["child"]
    assert result["value"] == "right"
    assert left["value"] == "left"
