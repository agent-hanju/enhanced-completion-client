"""로그와 독립적인 원본 응답 모델의 누적·검증·직렬화 계약."""

from __future__ import annotations

import json
from typing import Any, assert_type

import pytest
from pydantic import BaseModel, ValidationError

from completion_bridge import (
    ChatCompletionsResponse,
    GenerateContentResponse,
    MappingError,
    MessagesResponse,
    ResponsesResponse,
    SseEvent,
)
from completion_bridge.vendors import chat_completions, generate_content, messages, responses
from completion_bridge.vendors.base import VendorAdapter


def test_chat_completion_has_typed_choices_and_tool_calls() -> None:
    """함수 호출 조각과 vLLM 확장 필드를 타입 객체에 누적한다."""
    native = chat_completions.to_native()
    native.apply(
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_", "arguments": '{"x":'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 3, "prompt_tokens_details": {"cached_tokens": 1}},
            "vllm_metrics": {"cached": True},
        }
    )
    native.apply(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "value", "arguments": "1}"},
                                "vendor_extension": {"accepted": True},
                            }
                        ]
                    },
                }
            ],
            "vllm_metrics": None,
        }
    )
    result = native.build()
    assert_type(result, ChatCompletionsResponse)
    assert result.id == "c1"
    tools = result.choices[0].message.tool_calls
    assert tools is not None
    function = tools[0].function
    assert function is not None
    assert function.name == "get_value"
    assert function.arguments == '{"x":1}'
    assert result.usage is not None
    assert result.usage.prompt_tokens == 3
    wire = result.model_dump(mode="json", exclude_unset=True)
    assert wire["vllm_metrics"] == {"cached": True}
    assert wire["choices"][0]["message"]["tool_calls"][0]["vendor_extension"] == {"accepted": True}


def test_messages_has_typed_content_and_usage() -> None:
    """본문과 usage가 타입 객체이며 미지정 값과 명시적인 null을 구분한다."""
    native = messages.to_native()
    native.apply(
        {
            "type": "message_start",
            "message": {
                "id": "m1",
                "content": [],
                "usage": {"input_tokens": 3},
                "stop_sequence": None,
            },
        }
    )
    native.apply(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "", "vendor_extension": {"v": 1}},
        }
    )
    native.apply(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"},
        }
    )
    native.apply(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2, "input_tokens": None},
        }
    )
    result = native.build()
    assert_type(result, MessagesResponse)
    assert result.content[0].text == "hello"
    assert result.usage is not None
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 2
    wire = result.model_dump(mode="json", exclude_unset=True)
    assert wire["stop_sequence"] is None
    assert "model" not in wire
    assert "thinking" not in wire["content"][0]
    assert wire["content"][0]["vendor_extension"] == {"v": 1}


def test_responses_has_typed_output_content_and_usage() -> None:
    """최종 Response의 메시지와 함수 호출 및 알 수 없는 출력 종류를 보존한다."""
    native = responses.to_native()
    native.apply(
        {
            "type": "response.completed",
            "response": {
                "id": "r1",
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
                    {"type": "function_call", "name": "get", "arguments": "{}"},
                    {"type": "future_tool", "payload": {"v": 1}},
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        }
    )
    result = native.build()
    assert_type(result, ResponsesResponse)
    content = result.output[0].content
    assert content is not None
    assert content[0].text == "hello"
    assert result.output[1].arguments == "{}"
    assert result.usage is not None
    assert result.usage.input_tokens == 3
    wire = result.model_dump(mode="json", exclude_unset=True)
    assert wire["output"][2] == {"type": "future_tool", "payload": {"v": 1}}


def test_generate_content_has_typed_parts_function_calls_and_usage() -> None:
    """서명과 확장 part를 보존하면서 REST 필드명으로 직렬화한다."""
    native = generate_content.to_native()
    native.apply(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "hello", "thoughtSignature": "UzE="},
                            {"functionCall": {"name": "get", "args": {"v": 1}}},
                            {"futurePart": {"v": 2}},
                        ],
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "futureCount": 4},
        }
    )
    native.apply({"usageMetadata": {"candidatesTokenCount": 2}})
    result = native.build()
    assert_type(result, GenerateContentResponse)
    content = result.candidates[0].content
    assert content is not None
    assert content.parts[0].thoughtSignature == "UzE="
    call = content.parts[1].functionCall
    assert call is not None
    assert call.args == {"v": 1}
    assert result.usageMetadata is not None
    assert result.usageMetadata.promptTokenCount == 3
    assert result.usageMetadata.candidatesTokenCount == 2
    wire = result.model_dump(mode="json", exclude_unset=True)
    assert wire["usageMetadata"]["futureCount"] == 4
    assert wire["candidates"][0]["content"]["parts"][2] == {"futurePart": {"v": 2}}
    assert "responseId" not in wire


@pytest.mark.parametrize(
    ("adapter", "chunk", "expected"),
    [
        (chat_completions, {"choices": []}, ChatCompletionsResponse),
        (messages, {"type": "message_start", "message": {"content": []}}, MessagesResponse),
        (responses, {"type": "response.completed", "response": {"output": []}}, ResponsesResponse),
        (generate_content, {"promptFeedback": {"blockReason": "SAFETY"}}, GenerateContentResponse),
    ],
)
def test_accumulators_work_without_bridge_or_logging(
    adapter: VendorAdapter, chunk: dict[str, Any], expected: type[BaseModel]
) -> None:
    """독립적인 SSE decode·누적 호출로 API별 응답 객체를 얻는다."""
    native = adapter.to_native()
    decoded = adapter.decode(SseEvent(event=chunk.get("type", ""), data=json.dumps(chunk)))
    assert decoded is not None
    native.apply(decoded)
    result = native.build()
    assert isinstance(result, expected)
    assert json.loads(result.model_dump_json(exclude_unset=True)) == result.model_dump(
        mode="json", exclude_unset=True
    )


@pytest.mark.parametrize(
    ("adapter", "chunk"),
    [
        (chat_completions, {"choices": [{"index": 0, "delta": {"content": 123}}]}),
        (messages, {"type": "message_start", "message": {"content": [], "usage": "wrong"}}),
        (responses, {"type": "response.completed", "response": {"output": "wrong"}}),
        (generate_content, {"usageMetadata": {"promptTokenCount": "3"}}),
    ],
)
def test_accumulators_validate_declared_field_types(
    adapter: VendorAdapter, chunk: dict[str, Any]
) -> None:
    """잘못된 타입을 문자열·숫자 변환으로 수용하지 않는다."""
    native = adapter.to_native()
    with pytest.raises(ValidationError):
        native.apply(chunk)


def test_response_attribute_assignment_validates_nested_models() -> None:
    """생성 이후 속성 대입에도 응답 타입 검증을 적용한다."""
    result = ChatCompletionsResponse()
    with pytest.raises(ValidationError):
        result.created = "wrong"  # type: ignore[assignment]


def test_chat_tool_call_entries_are_validated_before_list_insertion() -> None:
    """타입 목록에 직접 추가하는 도구 호출도 객체 형식을 검사한다."""
    native = chat_completions.to_native()
    with pytest.raises(MappingError, match="tool call must be a JSON object"):
        native.apply({"choices": [{"delta": {"tool_calls": ["wrong"]}}]})
