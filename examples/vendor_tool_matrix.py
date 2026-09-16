"""Render a multi-model conversation and its per-vendor request conversions.

한 대화의 turn 마다 다른 모델이 답하고, 각 모델은 자기 벤더에만 있는 서버 실행 도구를
쓴다. 그렇게 쌓인 이력을 네 API로 다시 내보낼 때 무엇이 보존되고 무엇이 이름을 바꾸며
무엇이 사라지는지 실제 실행 결과로 보인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conversion_matrix import (
    CHAT,
    GEMINI,
    MESSAGES,
    RESPONSES,
    TARGETS,
    bridge,
    conversation,
    merge_response,
    pretty,
    table,
)
from conversion_matrix import Scenario as _Scenario

from completion_bridge import (
    HubMessage,
    HubResponse,
    ToolDefinition,
    ToolResultBlock,
)
from completion_bridge.vendors.base import VendorAdapter


@dataclass(frozen=True)
class Turn:
    """대화의 assistant turn 하나를 만든 벤더와 그 raw 이벤트.

    ``question``은 그 turn 직전의 사용자 발화다. 이력을 합성할 때 순서를 유지한다.
    """

    label: str
    adapter: VendorAdapter
    question: str
    events: tuple[dict[str, Any], ...]


# 네 벤더가 공유하는 이식 가능한 클라이언트 함수 도구. 어느 대상에서도 살아남는다.
PORTABLE_TOOL = ToolDefinition(
    name="lookup_internal_rate",
    description="사내 고시 환율을 조회한다.",
    input_schema={
        "type": "object",
        "properties": {"date": {"type": "string"}},
        "required": ["date"],
    },
)

# 벤더 고유 서버 실행 도구. 선언한 벤더의 요청에만 실리고 다른 대상에서는 제외된다.
ANTHROPIC_WEB_SEARCH = ToolDefinition.native(
    "messages",
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
)
RESPONSES_CODE_INTERPRETER = ToolDefinition.native(
    "responses",
    {"type": "code_interpreter", "container": {"type": "auto"}},
)
GEMINI_CODE_EXECUTION = ToolDefinition.native("generate_content", {"codeExecution": {}})

ALL_TOOLS = (
    PORTABLE_TOOL,
    ANTHROPIC_WEB_SEARCH,
    RESPONSES_CODE_INTERPRETER,
    GEMINI_CODE_EXECUTION,
)

TURNS = (
    Turn(
        "Anthropic Messages",
        MESSAGES,
        "오늘 달러 환율을 웹에서 찾아줘.",
        (
            {
                "type": "message_start",
                "message": {
                    "id": "msg-1",
                    "model": "claude-model",
                    "role": "assistant",
                    "usage": {"input_tokens": 18, "output_tokens": 1},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "server_tool_use",
                    "id": "srvtoolu-1",
                    "name": "web_search",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"query":"USD KRW 환율"}',
                },
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu-1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://rates.example/usd-krw",
                            "title": "USD/KRW",
                        }
                    ],
                },
            },
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "text_delta", "text": "오늘 종가는 1,383원이다."},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 41},
            },
        ),
    ),
    Turn(
        "OpenAI Responses",
        RESPONSES,
        "지난 30일 평균을 계산해줘.",
        (
            {
                "type": "response.created",
                "response": {"id": "resp-1", "model": "responses-model"},
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [{"type": "summary_text", "text": "평균을 코드로 구한다."}],
                    "encrypted_content": "encrypted-reasoning",
                },
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "code_interpreter_call",
                    "id": "ci-1",
                    "status": "completed",
                    "arguments": '{"code":"sum(rates)/len(rates)"}',
                    "output": "1376.4",
                },
            },
            # 허브에 대응물이 없는 항목. VendorBlock으로 떨어진다.
            {
                "type": "response.output_item.done",
                "output_index": 2,
                "item": {
                    "type": "video_generation_call",
                    "id": "vg-1",
                    "status": "completed",
                    "video_id": "vid-1",
                },
            },
            {
                "type": "response.output_item.done",
                "output_index": 3,
                "item": {
                    "type": "message",
                    "id": "msg-2",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "30일 평균은 1,376.4원이다."}],
                },
            },
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 46, "output_tokens": 38},
                },
            },
        ),
    ),
    Turn(
        "Gemini GenerateContent",
        GEMINI,
        "추세를 코드로 확인해줘.",
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
                                    "executableCode": {
                                        "language": "PYTHON",
                                        "code": "print(trend(rates))",
                                    }
                                },
                                {
                                    "codeExecutionResult": {
                                        "outcome": "OUTCOME_OK",
                                        "output": "rising",
                                    }
                                },
                                {"text": "최근 추세는 상승이다."},
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 72, "candidatesTokenCount": 33},
            },
        ),
    ),
    Turn(
        "Chat Completions",
        CHAT,
        "사내 고시 환율도 확인해줘.",
        (
            {
                "id": "chatcmpl-1",
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup_internal_rate",
                                        "arguments": '{"date":"2026-09-16"}',
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
)


def history() -> list[HubMessage]:
    """네 벤더의 turn을 한 대화 이력으로 합성한다.

    마지막 turn이 클라이언트 함수 도구를 호출하므로 그 결과 메시지까지 덧붙인다.
    """
    messages: list[HubMessage] = []
    for turn in TURNS:
        messages.append(HubMessage.user(turn.question))
        messages.append(HubMessage.of_response(responses_of(turn)))
    messages.append(
        HubMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="call-1",
                    name="lookup_internal_rate",
                    content='{"rate":1380.0}',
                    structured_content={"rate": 1380.0},
                )
            ],
        )
    )
    return messages


def responses_of(turn: Turn) -> HubResponse:
    """turn의 raw 이벤트를 매퍼와 병합기에 통과시켜 HubResponse를 만든다."""
    return merge_response(_Scenario(turn.label, turn.adapter, turn.events))


def _tool_declaration_rows() -> list[tuple[str, Any]]:
    """대상별로 ``tools``에 실제로 실리는 선언을 모은다."""
    prompt = [HubMessage.user("환율을 알려줘.")]
    rows: list[tuple[str, Any]] = []
    for target_name, adapter in TARGETS:
        body = bridge(adapter).build_request(prompt, tools=list(ALL_TOOLS))
        rows.append((target_name, body.get("tools", "필드 없음")))
    return rows


def _conversation_rows() -> list[tuple[str, Any]]:
    """합성한 이력을 네 API의 요청 대화로 변환한다."""
    messages = history()
    rows: list[tuple[str, Any]] = []
    for target_name, adapter in TARGETS:
        body = conversation(
            bridge(adapter).build_request(messages, tools=list(ALL_TOOLS)),
            include_tools=False,
        )
        rows.append((target_name, body))
    return rows


def _vendor_block_note() -> str:
    """미등록 항목이 어느 대상에서 살아남는지 한 줄로 요약한다."""
    block = next(
        b for b in responses_of(TURNS[1]).content if b.type == "responses_video_generation_call"
    )
    survivors = []
    for target_name, adapter in TARGETS:
        body = bridge(adapter).build_request([HubMessage.of_response(responses_of(TURNS[1]))])
        if "video_generation_call" in pretty(body):
            survivors.append(target_name)
    return f"`{block.type}` 블록이 남는 대상: {', '.join(survivors) or '없음'}"


def render_document() -> str:
    lines = [
        "# 멀티 모델 대화의 벤더 도구 변환 예시",
        "",
        "이 문서는 `uv run python examples/vendor_tool_matrix.py`가 현재 어댑터를 직접 "
        "실행해 출력한다. 네트워크 호출은 없고 벤더 응답 fixture를 실제 `to_hub()` 매퍼와 "
        "request builder에 통과시킨다. JSON은 손으로 재작성하지 않으며 "
        "`tests/test_conversion_examples.py`가 실행 결과와 이 문서의 일치를 검사한다.",
        "",
        "한 대화의 turn 마다 다른 모델이 답한다. 각 모델은 자기 벤더에만 있는 서버 실행 "
        "도구를 쓰고, 마지막 turn만 네 벤더가 공유하는 클라이언트 함수 도구를 호출한다.",
        "",
        "| turn | 모델 | 사용한 도구 | 도구 실행 주체 |",
        "|---|---|---|---|",
        "| 1 | Anthropic Messages | `web_search` | 벤더 서버 |",
        "| 2 | OpenAI Responses | `code_interpreter` | 벤더 서버 |",
        "| 3 | Gemini GenerateContent | `codeExecution` | 벤더 서버 |",
        "| 4 | Chat Completions | `lookup_internal_rate` | 클라이언트 |",
        "",
        "## 1. 도구 선언은 대상이 다르면 제외된다",
        "",
        "네 도구를 모두 넘긴 같은 요청을 네 API로 만든다. 공용 함수 도구는 어디에나 실리고, "
        "`ToolDefinition.native(...)`로 선언한 서버 도구는 선언한 벤더의 요청에만 실린다. "
        "다른 대상에서 일반 함수로 바꾸지 않고 제외한다. Chat Completions는 서버 실행 도구 "
        "개념이 없어 공용 함수 도구만 남는다.",
        "",
        table(_tool_declaration_rows()),
        "",
        "## 2. 각 모델이 낸 raw 응답",
        "",
        "turn 마다 벤더가 보낸 원본 이벤트다. 이것을 정규화한 결과는 3절의 이력에 그대로 "
        "들어 있어 여기서는 반복하지 않는다.",
        "",
    ]
    for turn in TURNS:
        lines.extend(
            [
                f"### {turn.label}",
                "",
                table(
                    (
                        ("사용자 발화", turn.question),
                        ("수신 raw event", turn.events),
                    )
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 3. 합성된 대화 이력",
            "",
            "네 turn을 한 이력으로 이은 `HubMessage` 목록이다. 이것이 다음 요청의 입력이다.",
            "",
            table((("HubMessage 이력", [m.model_dump(exclude_none=True) for m in history()]),)),
            "",
            "## 4. 네 API로 보낼 때의 변환 결과",
            "",
            "같은 이력을 네 API의 요청 대화로 직렬화한다. 도구 스키마는 1절과 같아 생략한다.",
            "",
            "이미 실행이 끝난 서버 도구는 사라지지 않는다. 원래 벤더로 되보내면 "
            "`server_tool_use`나 `executableCode` 원형이 복원되고, 다른 벤더로 보내면 "
            "벤더 접두사가 붙은 일반 함수 호출과 그 결과 한 쌍으로 풀린다. 실행 권한을 주는 "
            "선언만 차단되고 실행된 사실은 보존된다는 뜻이다.",
            "",
            f"허브에 대응물이 없는 미등록 블록은 원 벤더로만 돌아간다. {_vendor_block_note()}.",
            "",
            table(_conversation_rows()),
        ]
    )
    return "\n".join(lines)


def main() -> None:
    print(render_document())


if __name__ == "__main__":
    main()
