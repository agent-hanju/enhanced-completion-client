"""Print deterministic, executable conversion examples as Markdown tables."""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from enhanced_completion import (
    HubMessage,
    HubResponse,
    StreamMerger,
    SyncBridge,
    ToolDefinition,
    ToolResultBlock,
)
from enhanced_completion.vendors import (
    ChatCompletionsAdapter,
    GenerateContentAdapter,
    MessagesAdapter,
    ResponsesAdapter,
)
from enhanced_completion.vendors.base import VendorAdapter


@dataclass(frozen=True)
class Scenario:
    name: str
    adapter: VendorAdapter
    events: tuple[dict[str, Any], ...]


TOOL = ToolDefinition(
    name="get_weather",
    description="도시의 현재 기온을 조회한다.",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)

CHAT = ChatCompletionsAdapter(reasoning_input_field="reasoning_content")
MESSAGES = MessagesAdapter()
RESPONSES = ResponsesAdapter()
GEMINI = GenerateContentAdapter(model="gemini-example")

TARGETS: tuple[tuple[str, VendorAdapter], ...] = (
    ("Chat Completions", CHAT),
    ("Anthropic Messages", MESSAGES),
    ("OpenAI Responses", RESPONSES),
    ("Gemini GenerateContent", GEMINI),
)

SCENARIOS = (
    Scenario(
        "Chat Completions",
        CHAT,
        (
            {
                "id": "chatcmpl-1",
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "reasoning_content": "날씨 도구를 호출한다.",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city":"서울"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        ),
    ),
    Scenario(
        "Anthropic Messages",
        MESSAGES,
        (
            {
                "type": "message_start",
                "message": {
                    "id": "msg-1",
                    "model": "claude-model",
                    "role": "assistant",
                    "usage": {"input_tokens": 12, "output_tokens": 1},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "날씨 도구를 호출한다."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "anthropic-signature"},
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "get_weather",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"city":"서울"}',
                },
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 24},
            },
        ),
    ),
    Scenario(
        "OpenAI Responses",
        RESPONSES,
        (
            {
                "type": "response.created",
                "response": {"id": "resp-1", "model": "responses-model"},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "reasoning", "id": "rs-1", "summary": []},
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "summary_index": 0,
                "delta": "날씨 도구를 호출한다.",
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [{"type": "summary_text", "text": "날씨 도구를 호출한다."}],
                    "encrypted_content": "encrypted-reasoning",
                },
            },
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "get_weather",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": '{"city":"서울"}',
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "get_weather",
                    "arguments": '{"city":"서울"}',
                    "status": "completed",
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 12, "output_tokens": 24},
                },
            },
        ),
    ),
    Scenario(
        "Gemini GenerateContent",
        GEMINI,
        (
            {
                "responseId": "gemini-1",
                "modelVersion": "gemini-model",
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": "날씨 도구를 호출한다.",
                                    "thought": True,
                                    "thoughtSignature": "gemini-thought-signature",
                                },
                                {
                                    "functionCall": {
                                        "id": "call-1",
                                        "name": "get_weather",
                                        "args": {"city": "서울"},
                                    },
                                    "thoughtSignature": "gemini-call-signature",
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 24},
            },
        ),
    ),
)


def merge_response(scenario: Scenario) -> HubResponse:
    mapper = scenario.adapter.to_hub()
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    for event in scenario.events:
        for delta in mapper.map(event):
            merger.apply(delta)
    for delta in mapper.flush():
        merger.apply(delta)
    return merger.build()


def bridge(adapter: VendorAdapter) -> SyncBridge:
    return SyncBridge(
        vendor=adapter,
        base_url="https://example.invalid",
        model="example-model",
    )


def conversation(body: dict[str, Any], *, include_tools: bool = True) -> dict[str, Any]:
    keys = ["instructions", "system", "messages", "input", "contents"]
    if include_tools:
        keys.append("tools")
    return {key: body[key] for key in keys if key in body}


def follow_up(response: HubResponse) -> list[HubMessage]:
    return [
        HubMessage.user("서울 날씨를 조회해줘."),
        HubMessage.of_response(response),
        HubMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="call-1",
                    name="get_weather",
                    content='{"temp_c":25}',
                    structured_content={"temp_c": 25},
                )
            ],
        ),
    ]


def pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def cell(value: Any) -> str:
    return f"<pre>{html.escape(pretty(value), quote=False)}</pre>"


def table(rows: Iterable[tuple[str, Any]]) -> str:
    body = "\n".join(f"<tr><th>{label}</th><td>{cell(value)}</td></tr>" for label, value in rows)
    return f"<table>\n{body}\n</table>"


def render_document() -> str:
    lines = ["# 변환 규칙 및 실행 예시", ""]
    lines.append(
        "이 문서는 `uv run python examples/conversion_matrix.py`가 현재 어댑터를 직접 실행해 "
        "출력한다. raw payload는 짧고 결정적인 fixture이며 JSON은 손으로 재작성하지 않는다. "
        "`tests/test_conversion_examples.py`가 실행 결과와 이 문서의 완전 일치를 검사한다."
    )
    lines.extend(
        [
            "",
            "모든 시나리오는 `서울 날씨를 조회해줘.` → `get_weather({city: 서울})` → "
            "`{temp_c: 25}` 순서다. 후속 요청 표는 반복되는 tool schema를 생략하고 "
            "대화 payload만 표시한다.",
            "",
            "| 의미 | Chat Completions | Anthropic Messages | OpenAI Responses | "
            "Gemini GenerateContent |",
            "|---|---|---|---|---|",
            "| 도구 호출 | `assistant.tool_calls[]` | assistant의 `tool_use` | "
            "`function_call` item | model의 `functionCall` part |",
            "| 도구 결과 | `role: tool` message | user의 `tool_result` | "
            "`function_call_output` item | user의 `functionResponse` part |",
            "| 원 벤더 추론 복원 | `reasoning_content` | 서명된 `thinking` | "
            "암호문 포함 `reasoning` item | `thoughtSignature` 포함 thought part |",
            "| 다른 벤더로 이동한 추론 | 생략 | 생략 | 생략 | 생략 |",
            "",
        ]
    )
    for scenario in SCENARIOS:
        result = merge_response(scenario)
        initial = conversation(
            bridge(scenario.adapter).build_request(
                [HubMessage.user("서울 날씨를 조회해줘.")], tools=[TOOL]
            )
        )
        lines.extend([f"## {scenario.name}에서 시작", ""])
        lines.append(
            table(
                (
                    ("최초 요청", initial),
                    ("수신 raw event", scenario.events),
                    ("정규화된 HubResponse", result.model_dump(exclude_none=True)),
                )
            )
        )
        lines.extend(
            [
                "",
                "### 후속 요청 변환",
                "",
                "<table>",
                "<tr><th>대상</th><th>실제 request conversation</th></tr>",
            ]
        )
        for target_name, adapter in TARGETS:
            body = conversation(
                bridge(adapter).build_request(follow_up(result), tools=[TOOL]),
                include_tools=False,
            )
            suffix = " (원 벤더 복원)" if adapter is scenario.adapter else ""
            lines.append(f"<tr><th>{target_name}{suffix}</th><td>{cell(body)}</td></tr>")
        lines.extend(["</table>", ""])
    return "\n".join(lines)


def main() -> None:
    print(render_document())


if __name__ == "__main__":
    main()
