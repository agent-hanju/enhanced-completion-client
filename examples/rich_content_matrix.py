"""Render rich-content conversion examples through the real adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conversion_matrix import TARGETS, bridge, cell, conversation, table

from completion_bridge import (
    AudioBlock,
    DocumentBlock,
    HubMessage,
    HubResponse,
    Hyperparameters,
    ImageBlock,
    OutputFormat,
    StreamMerger,
    TextBlock,
    ToolChoice,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from completion_bridge.errors import MappingError
from completion_bridge.vendors.base import VendorAdapter


@dataclass(frozen=True)
class Scenario:
    name: str
    adapter: VendorAdapter
    events: tuple[dict[str, Any], ...]
    highlights: str


PNG = "iVBORw0KGgo="
WAV = "UklGRg=="
PDF = "JVBERi0xLjQ="

MULTIMODAL_MESSAGE = HubMessage(
    role="user",
    content=[
        TextBlock(text="다음 자료를 함께 분석해."),
        ImageBlock(media_type="image/png", data=PNG, detail="low"),
        AudioBlock(media_type="audio/wav", data=WAV, format="wav"),
        DocumentBlock(
            id="report.pdf",
            title="보고서.pdf",
            media_type="application/pdf",
            data=PDF,
        ),
        DocumentBlock(id="facts", title="사실표", text="서울=25도"),
    ],
)

REPEATED_TEXT_MESSAGE = HubMessage(
    role="assistant",
    content=[TextBlock(text="첫 번째 text block"), TextBlock(text="두 번째 text block")],
)

NESTED_TOOL_HISTORY = [
    HubMessage(
        role="assistant",
        content=[ToolUseBlock(id="call-media", name="capture", input_json='{"city":"서울"}')],
    ),
    HubMessage(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id="call-media",
                name="capture",
                content="관측 결과",
                structured_content={"temp_c": 25},
                blocks=[
                    ImageBlock(media_type="image/png", data=PNG),
                    DocumentBlock(
                        id="result.pdf",
                        title="결과.pdf",
                        media_type="application/pdf",
                        data=PDF,
                    ),
                ],
            )
        ],
    ),
]

REACT_HISTORY = [
    HubMessage(
        role="assistant",
        content=[ToolUseBlock(id="call-1", name="lookup", input_json='{"step":1}')],
    ),
    HubMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id="call-1", name="lookup", content="first")],
    ),
    HubMessage(
        role="assistant",
        content=[ToolUseBlock(id="call-2", name="lookup", input_json='{"step":2}')],
    ),
    HubMessage(
        role="user",
        content=[ToolResultBlock(tool_use_id="call-2", name="lookup", content="second")],
    ),
    HubMessage(role="assistant", content=[TextBlock(text="완료")]),
]

STATELESS_WEB_TOOLS = (
    (
        "Anthropic Messages",
        TARGETS[1][1],
        ToolDefinition.native(
            "messages",
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 1},
        ),
    ),
    (
        "OpenAI Responses",
        TARGETS[2][1],
        ToolDefinition.native("responses", {"type": "web_search_preview"}),
    ),
    (
        "Gemini GenerateContent",
        TARGETS[3][1],
        ToolDefinition.native("generate_content", {"googleSearch": {}}),
    ),
)

PARAMETERS = Hyperparameters(
    max_output_tokens=64,
    temperature=0.2,
    top_p=0.9,
    top_k=20,
    seed=7,
    stop_sequences=["<END>"],
    presence_penalty=0.1,
    frequency_penalty=0.2,
    reasoning_effort="low",
    tool_choice=ToolChoice(mode="named", name="lookup"),
    parallel_tool_calls=False,
    output_format=OutputFormat(
        type="json_schema",
        name="answer",
        json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        strict=True,
    ),
    verbosity="low",
    include=["reasoning.encrypted_content"],
    inference_geo="us",
    service_tier="auto",
    anthropic_service_tier="standard_only",
    gemini_service_tier="PRIORITY",
)

PARAMETER_TOOL = ToolDefinition(
    name="lookup",
    description="짧은 조회",
    input_schema={"type": "object", "properties": {}},
)


def _chat_events() -> tuple[dict[str, Any], ...]:
    return (
        {
            "id": "chatcmpl-rich-1",
            "model": "chat-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": "이미지와 근거를 결합한다.",
                        "content": "서울은 25도다.",
                        "audio": {
                            "id": "audio-1",
                            "data": WAV,
                            "transcript": "서울은 25도다.",
                            "expires_at": 1893456000,
                        },
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "start_index": 0,
                                    "end_index": 2,
                                    "url": "https://weather.example/seoul",
                                    "title": "서울 관측",
                                },
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 18, "completion_tokens": 12},
        },
    )


def _messages_events() -> tuple[dict[str, Any], ...]:
    citation = {
        "type": "char_location",
        "cited_text": "서울은 25도",
        "document_index": 0,
        "document_title": "기상 문서",
        "start_char_index": 0,
        "end_char_index": 7,
    }
    starts = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": ""},
        {
            "type": "server_tool_use",
            "id": "srv-search-1",
            "name": "web_search",
            "input": {"query": "서울 현재 기온"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srv-search-1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://weather.example/seoul",
                    "title": "서울 관측",
                    "encrypted_content": "anthropic-search-result",
                    "page_age": "today",
                }
            ],
        },
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
        {"type": "container_upload", "file_id": "file_1"},
    ]
    return (
        {
            "type": "message_start",
            "message": {
                "id": "msg-rich-1",
                "model": "claude-model",
                "role": "assistant",
                "usage": {"input_tokens": 18, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": starts[0],
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "웹 근거를 확인한다."},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "anthropic-rich-signature"},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": starts[1],
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "서울은 25도다."},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "citations_delta", "citation": citation},
        },
        {"type": "content_block_start", "index": 2, "content_block": starts[2]},
        {"type": "content_block_start", "index": 3, "content_block": starts[3]},
        {"type": "content_block_start", "index": 4, "content_block": starts[4]},
        {"type": "content_block_start", "index": 5, "content_block": starts[5]},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 32},
        },
    )


def _responses_events() -> tuple[dict[str, Any], ...]:
    annotation = {
        "type": "url_citation",
        "start_index": 0,
        "end_index": 2,
        "url": "https://weather.example/seoul",
        "title": "서울 관측",
    }
    return (
        {
            "type": "response.created",
            "response": {"id": "resp-rich-1", "model": "responses-model"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "id": "rs-rich-1",
                "summary": [{"type": "summary_text", "text": "웹 결과를 비교했다."}],
                "encrypted_content": "responses-encrypted-reasoning",
            },
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {
                "type": "message",
                "id": "msg-responses-1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "서울은 25도다.",
                        "annotations": [annotation],
                    }
                ],
            },
        },
        {"type": "response.audio.delta", "delta": WAV, "sequence_number": 0},
        {
            "type": "response.audio.transcript.delta",
            "delta": "서울은 25도다.",
            "sequence_number": 1,
        },
        {
            "type": "response.output_text.annotation.added",
            "output_index": 1,
            "content_index": 0,
            "annotation": annotation,
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "type": "web_search_call",
                "id": "ws-rich-1",
                "status": "completed",
                "action": {"type": "search", "query": "서울 현재 기온"},
            },
        },
        {
            "type": "response.output_item.done",
            "output_index": 3,
            "item": {
                "type": "code_interpreter_call",
                "id": "ci-rich-1",
                "status": "completed",
                "code": "print(25)",
                "outputs": [{"type": "logs", "logs": "25\n"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"input_tokens": 18, "output_tokens": 32},
            },
        },
    )


def _gemini_events() -> tuple[dict[str, Any], ...]:
    return (
        {
            "responseId": "gemini-rich-1",
            "modelVersion": "gemini-model",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "text": "자료를 함께 검토한다.",
                                "thought": True,
                                "thoughtSignature": "gemini-rich-thought",
                            },
                            {
                                "text": "서울은 25도다.",
                                "thoughtSignature": "gemini-rich-text",
                            },
                            {"inlineData": {"mimeType": "image/png", "data": PNG}},
                            {
                                "fileData": {
                                    "mimeType": "application/pdf",
                                    "fileUri": "gs://example/result.pdf",
                                }
                            },
                            {
                                "executableCode": {
                                    "language": "PYTHON",
                                    "code": "print(25)",
                                }
                            },
                            {
                                "codeExecutionResult": {
                                    "outcome": "OUTCOME_OK",
                                    "output": "25\n",
                                }
                            },
                        ],
                    },
                    "finishReason": "STOP",
                    "citationMetadata": {
                        "citationSources": [
                            {
                                "startIndex": 0,
                                "endIndex": 2,
                                "uri": "https://weather.example/seoul",
                                "license": "example",
                            }
                        ]
                    },
                    "groundingMetadata": {
                        "webSearchQueries": ["서울 현재 기온"],
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://weather.example/seoul",
                                    "title": "서울 관측",
                                }
                            }
                        ],
                        "groundingSupports": [
                            {
                                "segment": {
                                    "text": "서울은 25도다.",
                                    "startIndex": 0,
                                    "endIndex": 9,
                                },
                                "groundingChunkIndices": [0],
                                "confidenceScores": [0.98],
                            }
                        ],
                    },
                    "urlContextMetadata": {
                        "urlMetadata": [
                            {
                                "retrievedUrl": "https://weather.example/seoul",
                                "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS",
                            }
                        ]
                    },
                }
            ],
            "usageMetadata": {"promptTokenCount": 18, "candidatesTokenCount": 32},
        },
    )


SCENARIOS = (
    Scenario(
        "Chat Completions",
        TARGETS[0][1],
        _chat_events(),
        "reasoning_content, text, output audio, URL citation",
    ),
    Scenario(
        "Anthropic Messages",
        TARGETS[1][1],
        _messages_events(),
        ("signed thinking, citation, server tool/result, code result, container-uploaded file ID"),
    ),
    Scenario(
        "OpenAI Responses",
        TARGETS[2][1],
        _responses_events(),
        "encrypted reasoning, text, global audio stream, annotation, web/code server tools",
    ),
    Scenario(
        "Gemini GenerateContent",
        TARGETS[3][1],
        _gemini_events(),
        "thought signatures, image/PDF, code execution, citation/grounding metadata",
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


def _request_rows(history: list[HubMessage]) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for target_name, adapter in TARGETS:
        rows.append((target_name, _request_result(adapter, history)))
    return rows


def _request_result(
    adapter: VendorAdapter,
    history: list[HubMessage],
) -> dict[str, Any]:
    try:
        body = bridge(adapter).build_request(history)
    except MappingError as exc:
        return {"rejected": str(exc)}
    return conversation(body, include_tools=False)


def _target_table(response: HubResponse, source: VendorAdapter) -> str:
    history = [
        MULTIMODAL_MESSAGE,
        HubMessage.of_response(response),
        HubMessage.user("한 줄로 다시 정리해줘."),
    ]
    lines = ["<table>", "<tr><th>대상</th><th>실제 request conversation</th></tr>"]
    for target_name, adapter in TARGETS:
        suffix = " (원 벤더 복원)" if adapter is source else ""
        payload = _request_result(adapter, history)
        lines.append(f"<tr><th>{target_name}{suffix}</th><td>{cell(payload)}</td></tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _tool_activation_rows() -> list[tuple[str, dict[str, Any]]]:
    prompt = [HubMessage.user("오늘 자료를 웹에서 찾아줘.")]
    rows: list[tuple[str, dict[str, Any]]] = []
    for target_name, adapter in TARGETS:
        body = conversation(bridge(adapter).build_request(prompt))
        rows.append((f"{target_name}: tools 생략", body))
    for target_name, adapter, tool in STATELESS_WEB_TOOLS:
        rows.append(
            (
                f"{target_name}: web search 명시",
                conversation(bridge(adapter).build_request(prompt, tools=[tool])),
            )
        )
    return rows


def _parameter_rows() -> list[tuple[str, dict[str, Any]]]:
    ignored = {
        "model",
        "stream",
        "messages",
        "input",
        "contents",
        "instructions",
        "system",
        "systemInstruction",
        "tools",
    }
    rows: list[tuple[str, dict[str, Any]]] = []
    for target_name, adapter in TARGETS:
        body = bridge(adapter).build_request(
            [HubMessage.user("조회해줘.")],
            tools=[PARAMETER_TOOL],
            hyperparameters=PARAMETERS,
        )
        rendered = {key: value for key, value in body.items() if key not in ignored}
        rows.append((target_name, rendered))
    return rows


def render_document() -> str:
    lines = [
        "# 다양한 content 및 ReAct 변환 예시",
        "",
        "이 문서는 `uv run python examples/rich_content_matrix.py`가 현재 구현을 직접 "
        "실행한 결과다. 네트워크 호출 없이 벤더 응답 fixture를 실제 `to_hub()` 매퍼와 "
        "request builder에 통과시킨다. JSON은 손으로 재작성하지 않으며 "
        "`tests/test_conversion_examples.py`가 실행 결과와 기록 문서의 완전 일치를 검사한다.",
        "",
        "`원 벤더 복원`은 응답을 `HubMessage.of_response()`로 옮겨 같은 벤더의 다음 요청 "
        "이력으로 직렬화한 결과다.",
        "",
        "## 변환 정책 요약",
        "",
        "| 블록 | 같은 벤더 | 다른 벤더 |",
        "|---|---|---|",
        "| text | native 메타데이터와 함께 복원 | text로 보존 |",
        "| thinking/reasoning | signature/encrypted payload까지 복원 | 생략 |",
        "| image/document 입력 | URL/inline data를 native part로 변환 | "
        "미지원 채널은 생략 또는 text fallback |",
        "| audio 입력 | Chat/Gemini native part | Anthropic/Responses에서는 생략 |",
        "| output audio | data/transcript 수집; 원격 audio ID는 재생 거부 | 다른 벤더에서는 생략 |",
        "| generic function call/result | 원 형식 복원 | 대상의 tool call/result로 변환 |",
        "| 환경 의존 call/result | 수신 block을 파싱하되 자동 활성화하지 않음 | 생략 |",
        "| server tool block | 수신 결과와 순서 파싱; 활성화는 별도 `tools` 입력 | 생략 |",
        "| XML/Anthropic citation | `TextBlock.citations`에 원형 구분, 가능한 입력만 복원 | "
        "명시적 XML 어휘가 없으면 생략 |",
        "| OpenAI/Gemini annotation | Responses native 이력만 복원 | 다른 요청에서는 생략 |",
        "| Gemini grounding graph | Hub에 source/support 관계 보존 | 모든 요청 이력에서 생략 |",
        "",
        "## 1. 멀티모달 사용자 입력",
        "",
        "text, base64 image, base64 audio, base64 PDF, 평문 문서를 한 HubMessage에 넣었다.",
        "",
        table(
            [("HubMessage", MULTIMODAL_MESSAGE.model_dump(exclude_none=True))]
            + _request_rows([MULTIMODAL_MESSAGE])
        ),
        "",
        "## 2. 같은 타입의 독립 block 반복",
        "",
        "같은 메시지 안의 두 text block을 합치지 않고 네 요청 형식으로 내렸다. 단, 실제 "
        "스트림에서 이 구분을 복원할 수 있는지는 원 프로토콜이 part index를 주는지에 달려 있다.",
        "",
        table(
            [("HubMessage", REPEATED_TEXT_MESSAGE.model_dump(exclude_none=True))]
            + _request_rows([REPEATED_TEXT_MESSAGE])
        ),
        "",
        "## 3. 여러 턴의 ReAct tool loop",
        "",
        "두 번의 function call/result와 최종 text까지 role, call ID와 턴 순서를 보존한다.",
        "",
        table(
            [("Hub history", [m.model_dump(exclude_none=True) for m in REACT_HISTORY])]
            + _request_rows(REACT_HISTORY)
        ),
        "",
        "## 4. 이미지와 문서가 포함된 tool result",
        "",
        "`content`, `structured_content`, image block, PDF block을 한 tool result에 넣었다.",
        "",
        table(
            [
                (
                    "HubMessage history",
                    [message.model_dump(exclude_none=True) for message in NESTED_TOOL_HISTORY],
                )
            ]
            + _request_rows(NESTED_TOOL_HISTORY)
        ),
        "",
        "## 5. 벤더별 rich response 왕복",
        "",
    ]
    for section, scenario in enumerate(SCENARIOS, start=1):
        response = merge_response(scenario)
        lines.extend(
            [
                f"### 5.{section}. {scenario.name}",
                "",
                f"포함 요소: {scenario.highlights}.",
                "",
                table(
                    [
                        ("수신 raw event", scenario.events),
                        ("정규화된 HubResponse", response.model_dump(exclude_none=True)),
                    ]
                ),
                "",
                "#### 네 벤더의 다음 요청 이력으로 변환",
                "",
                _target_table(response, scenario.adapter),
                "",
            ]
        )
    lines.extend(
        [
            "## 6. 내장 도구의 명시적 활성화",
            "",
            "Bridge는 내장 도구를 자동 등록하지 않는다. `tools`를 생략한 네 요청에는 해당 필드가 "
            "없고, 세션 상태가 필요 없는 web search도 호출자가 matching vendor의 "
            "`ToolDefinition.native(...)`를 전달한 요청에서만 활성화된다.",
            "",
            table(_tool_activation_rows()),
            "",
            "## 7. 공통 Hyperparameters의 API별 투영",
            "",
            "같은 공통 옵션 객체를 네 request builder에 넣고, 대화·도구 정의를 제외한 실제 "
            "wire 파라미터만 표시했다. 지원하지 않는 필드는 빠지고 API 고유 필드는 해당 "
            "대상에만 남는다.",
            "",
            table(_parameter_rows()),
            "",
            "## 8. 견본 범위",
            "",
            "이 문서는 core Hub content 계열 전부(text, thinking, tool use/result, image, "
            "audio, document, nested citation, annotation, grounding, server tool, "
            "vendor fallback)와 "
            "공통/전용 요청 Hyperparameters의 "
            "변환 정책을 실제 adapter 또는 해당 adapter의 회귀 테스트에 통과시킨다. "
            "각 서버 도구의 모든 버전 문자열을 반복하지는 않고 실행 주체와 변환 정책이 같은 "
            "계열별 대표 wire payload를 사용한다. 전체 subtype 목록은 "
            "[지원표](Support-Matrix.md)에 있다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    print(render_document())


if __name__ == "__main__":
    main()
