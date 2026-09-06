"""평면 Hyperparameters의 벤더별 투영과 미지원 필드 제거."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from completion_bridge import (
    Hyperparameters,
    OutputFormat,
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


class TestCommonProjection:
    def test_same_semantics_use_each_wire_name_and_nesting(self) -> None:
        parameters = Hyperparameters(
            max_output_tokens=64,
            temperature=0.2,
            top_p=0.9,
            top_k=20,
            seed=7,
            stop_sequences=["끝"],
        )

        chat = body(ChatCompletionsAdapter(), parameters)
        assert chat["max_completion_tokens"] == 64
        assert chat["stop"] == ["끝"]
        assert chat["temperature"] == 0.2
        assert "top_k" not in chat

        response = body(responses, parameters)
        assert response["max_output_tokens"] == 64
        assert response["temperature"] == 0.2
        assert "seed" not in response and "stop" not in response

        anthropic = body(messages, parameters)
        assert anthropic["max_tokens"] == 64
        assert anthropic["stop_sequences"] == ["끝"]
        assert anthropic["temperature"] == 0.2
        assert anthropic["top_p"] == 0.9
        assert anthropic["top_k"] == 20

        gemini = body(generate_content.for_model("m"), parameters)
        assert gemini["generationConfig"] == {
            "temperature": 0.2,
            "topP": 0.9,
            "topK": 20,
            "seed": 7,
            "maxOutputTokens": 64,
            "stopSequences": ["끝"],
        }

    def test_tool_choice_shapes_are_not_assumed_identical(self) -> None:
        parameters = Hyperparameters(
            tool_choice=ToolChoice(mode="named", name="lookup"),
            parallel_tool_calls=False,
        )
        assert body(ChatCompletionsAdapter(), parameters)["tool_choice"] == {
            "type": "function",
            "function": {"name": "lookup"},
        }
        assert body(responses, parameters)["tool_choice"] == {
            "type": "function",
            "name": "lookup",
        }
        assert body(messages, parameters)["tool_choice"] == {
            "type": "tool",
            "name": "lookup",
            "disable_parallel_tool_use": True,
        }
        assert body(generate_content.for_model("m"), parameters)["toolConfig"] == {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["lookup"],
            }
        }

    def test_json_schema_is_rendered_per_api(self) -> None:
        parameters = Hyperparameters(
            output_format=OutputFormat(
                type="json_schema",
                name="answer",
                json_schema={"type": "object"},
                strict=True,
            )
        )
        assert body(ChatCompletionsAdapter(), parameters)["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {"type": "object"},
                "strict": True,
            },
        }
        assert body(responses, parameters)["text"] == {
            "format": {
                "type": "json_schema",
                "name": "answer",
                "schema": {"type": "object"},
                "strict": True,
            }
        }
        assert body(messages, parameters)["output_config"] == {
            "format": {"type": "json_schema", "schema": {"type": "object"}}
        }
        assert body(generate_content.for_model("m"), parameters)["generationConfig"] == {
            "responseMimeType": "application/json",
            "responseJsonSchema": {"type": "object"},
        }


class TestVendorFiltering:
    def test_flat_known_fields_are_only_sent_to_supporting_vendor(self) -> None:
        parameters = Hyperparameters(
            verbosity="low",
            include=["reasoning.encrypted_content"],
            inference_geo="us",
            safety_settings=[{"category": "HARM_CATEGORY_HATE_SPEECH"}],
            service_tier="auto",
            anthropic_service_tier="standard_only",
            gemini_service_tier="PRIORITY",
        )
        assert body(ChatCompletionsAdapter(), parameters)["verbosity"] == "low"
        assert body(ChatCompletionsAdapter(), parameters)["service_tier"] == "auto"
        assert "include" not in body(ChatCompletionsAdapter(), parameters)
        assert body(responses, parameters)["include"] == ["reasoning.encrypted_content"]
        assert "verbosity" not in body(responses, parameters)
        assert body(messages, parameters)["inference_geo"] == "us"
        assert body(messages, parameters)["service_tier"] == "standard_only"
        assert "include" not in body(messages, parameters)
        gemini = body(generate_content.for_model("m"), parameters)
        assert gemini["serviceTier"] == "PRIORITY"
        assert gemini["safetySettings"] == [{"category": "HARM_CATEGORY_HATE_SPEECH"}]
        assert "inference_geo" not in gemini

    def test_explicit_extensions_apply_only_to_the_current_request(self) -> None:
        adapter = ChatCompletionsAdapter(name="vllm")
        parameters = Hyperparameters(
            max_output_tokens=8,
            extensions={"chat_template_kwargs": {"enable_thinking": False}},
        )
        request = body(adapter, parameters)
        assert request["max_completion_tokens"] == 8
        assert request["chat_template_kwargs"] == {"enable_thinking": False}

    def test_wire_specific_limits_override_common_limit_only_on_their_api(self) -> None:
        parameters = Hyperparameters(
            max_output_tokens=64,
            max_completion_tokens=32,
            max_tokens=16,
        )
        assert body(ChatCompletionsAdapter(), parameters)["max_completion_tokens"] == 32
        assert body(messages, parameters)["max_tokens"] == 16
        assert body(responses, parameters)["max_output_tokens"] == 64
        assert body(generate_content.for_model("m"), parameters)["generationConfig"][
            "maxOutputTokens"
        ] == 64

    def test_native_nested_config_deep_merges_over_common_projection(self) -> None:
        parameters = Hyperparameters(
            max_output_tokens=64,
            generation_config={"thinkingConfig": {"thinkingBudget": 0}},
            reasoning_effort="low",
            reasoning={"summary": "auto"},
        )
        gemini = body(generate_content.for_model("m"), parameters)
        assert gemini["generationConfig"] == {
            "maxOutputTokens": 64,
            "thinkingConfig": {"thinkingBudget": 0},
        }
        response = body(responses, parameters)
        assert response["reasoning"] == {"effort": "low", "summary": "auto"}

    def test_constructor_defaults_merge_with_call_override(self) -> None:
        bridge = SyncBridge(
            vendor=ChatCompletionsAdapter(),
            base_url=BASE,
            model="m",
            hyperparameters=Hyperparameters(temperature=0.3, max_output_tokens=32),
        )
        request = bridge.build_request(
            ["x"], hyperparameters=Hyperparameters(max_output_tokens=16)
        )
        assert request["temperature"] == 0.3
        assert request["max_completion_tokens"] == 16

    def test_legacy_keyword_is_the_final_override(self) -> None:
        request = SyncBridge(
            vendor=ChatCompletionsAdapter(),
            base_url=BASE,
            model="m",
        ).build_request(
            ["x"],
            hyperparameters=Hyperparameters(temperature=0.3),
            temperature=0.7,
        )
        assert request["temperature"] == 0.7

    def test_unknown_common_field_is_rejected_instead_of_leaking(self) -> None:
        with pytest.raises(ValidationError):
            Hyperparameters.model_validate({"temperatur": 0.2})
