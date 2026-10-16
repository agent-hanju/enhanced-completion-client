"""검색 결과 블록. Anthropic에는 네이티브 search_result로, 다른 벤더에는 문서 텍스트로 보낸다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from completion_bridge import (
    DocumentBlock,
    HubMessage,
    HubResponse,
    Hyperparameters,
    ImageBlock,
    MappingError,
    ResponseFormat,
    SearchResultBlock,
    StreamMerger,
    SyncBridge,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from completion_bridge.streaming.sequence import finalize
from completion_bridge.vendors import (
    GenerateContentAdapter,
    chat_completions,
    messages,
    responses,
)
from completion_bridge.vendors.base import VendorAdapter

BASE = "https://search.invalid"
GEMINI = GenerateContentAdapter(model="gemini-test")

DOCUMENTS = (
    "<documents>\n"
    '<document id="kb://doc-1">\n'
    "<title>점검 지침</title>\n"
    "<content>청크 1</content>\n"
    "<content>청크 2</content>\n"
    "</document>\n"
    "</documents>"
)


def search(enabled: bool = True, source: str = "kb://doc-1") -> SearchResultBlock:
    return SearchResultBlock(
        source=source, title="점검 지침", content=["청크 1", "청크 2"], citations_enabled=enabled
    )


def tool_turns(result: ToolResultBlock) -> list[HubMessage]:
    """도구 호출 뒤에 결과가 오는 대화. 결과의 tool_use_id는 ``t1``이다."""
    return [
        HubMessage(role="user", content=[TextBlock(text="점검 주기?")]),
        HubMessage(role="assistant", content=[ToolUseBlock(id="t1", name="rag", input_json="{}")]),
        HubMessage(role="user", content=[result]),
    ]


def build(adapter: VendorAdapter, history: list[HubMessage], **options: Any) -> dict[str, Any]:
    return SyncBridge(vendor=adapter, base_url=BASE, model="m").build_request(history, **options)


def anthropic_tool_result(body: dict[str, Any]) -> dict[str, Any]:
    content = body["messages"][-1]["content"]
    assert isinstance(content, list)
    part = content[0]
    assert part["type"] == "tool_result"
    return part


class TestBlock:
    def test_nested_blocks_are_restored_from_stored_json(self) -> None:
        """저장 JSON에서 읽은 tool_result 안 블록이 dict로 남지 않는다."""
        stored = HubMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1", blocks=[search(), ImageBlock(url="https://img.test/a")]
                )
            ],
        ).model_dump(mode="json")
        restored = HubMessage.model_validate(stored).content[0]
        assert isinstance(restored, ToolResultBlock)
        assert [type(block) for block in restored.blocks] == [SearchResultBlock, ImageBlock]
        searches_only = ToolResultBlock(tool_use_id="t1", blocks=restored.blocks[:1])
        body = build(messages, tool_turns(searches_only))
        assert anthropic_tool_result(body)["content"][0]["type"] == "search_result"

    @pytest.mark.parametrize(
        "fields",
        [
            {"source": "", "title": "t", "content": ["a"]},
            {"source": "s", "title": "", "content": ["a"]},
            {"source": "s", "title": "t", "content": []},
            {"source": "s", "title": "t", "content": [""]},
        ],
    )
    def test_required_fields_must_be_non_empty(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            SearchResultBlock(**fields)


class TestMessages:
    def test_tool_result_sends_search_results_only(self) -> None:
        result = ToolResultBlock(
            tool_use_id="t1",
            content="",
            structured_content={"type": "search_result"},
            blocks=[search()],
        )
        part = anthropic_tool_result(build(messages, tool_turns(result)))
        assert part == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [
                {
                    "type": "search_result",
                    "source": "kb://doc-1",
                    "title": "점검 지침",
                    "content": [
                        {"type": "text", "text": "청크 1"},
                        {"type": "text", "text": "청크 2"},
                    ],
                    "citations": {"enabled": True},
                }
            ],
        }

    @pytest.mark.parametrize(
        "result",
        [
            ToolResultBlock(tool_use_id="t1", content="요약", blocks=[search()]),
            ToolResultBlock(tool_use_id="t1", blocks=[search(), TextBlock(text="덧붙임")]),
        ],
    )
    def test_tool_result_mixing_search_results_is_rejected(self, result: ToolResultBlock) -> None:
        with pytest.raises(MappingError, match="mixes search results"):
            build(messages, tool_turns(result))

    def test_user_message_keeps_search_results_beside_other_blocks(self) -> None:
        history = [HubMessage(role="user", content=[search(), TextBlock(text="점검 주기?")])]
        content = build(messages, history)["messages"][0]["content"]
        assert [part["type"] for part in content] == ["search_result", "text"]

    def test_one_enabled_search_result_enables_all_in_the_request(self) -> None:
        """설정이 다른 search result가 섞이면 거절되므로 요청 전체를 같은 값으로 맞춘다."""
        history = tool_turns(ToolResultBlock(tool_use_id="t1", blocks=[search(enabled=False)]))
        history[0] = HubMessage(role="user", content=[search(enabled=True), TextBlock(text="?")])
        body = build(messages, history)
        top = body["messages"][0]["content"][0]
        nested = anthropic_tool_result(body)["content"][0]
        assert top["citations"] == nested["citations"] == {"enabled": True}

    def test_search_result_and_document_settings_are_independent(self) -> None:
        """두 종류 사이의 설정 일치는 요구되지 않는다(라이브 확인)."""
        history = [
            HubMessage(
                role="user",
                content=[
                    DocumentBlock(id="d1", text="본문", citations_enabled=True),
                    search(enabled=False),
                    TextBlock(text="?"),
                ],
            )
        ]
        content = build(messages, history)["messages"][0]["content"]
        assert [part.get("citations") for part in content[:2]] == [
            {"enabled": True},
            {"enabled": False},
        ]

    def test_search_result_citations_conflict_with_structured_output(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        options = {
            "hyperparameters": Hyperparameters(
                response_format=ResponseFormat(type="json_schema", name="a", json_schema=schema)
            )
        }
        enabled = [HubMessage(role="user", content=[search(enabled=True), TextBlock(text="?")])]
        disabled = [HubMessage(role="user", content=[search(enabled=False), TextBlock(text="?")])]
        with pytest.raises(MappingError, match="search_result citations"):
            build(messages, enabled, **options)
        assert "format" in build(messages, disabled, **options)["output_config"]

    def test_search_result_location_maps_to_citation_fields(self) -> None:
        location = {
            "type": "search_result_location",
            "cited_text": "청크 2",
            "source": "kb://doc-1",
            "title": "점검 지침",
            "search_result_index": 0,
            "start_block_index": 1,
            "end_block_index": 2,
        }
        text_delta = {"type": "text_delta", "text": "500시간"}
        citation_delta = {"type": "citations_delta", "citation": location}
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": text_delta},
            {"type": "content_block_delta", "index": 0, "delta": citation_delta},
        ]
        mapper = messages.to_hub()
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        for event in events:
            for delta in mapper.map(event):
                merger.apply(delta)
        text = finalize(merger.build()).content[0]
        assert isinstance(text, TextBlock)
        citation = text.citations[0]
        assert (citation.type, citation.source, citation.id, citation.uri) == (
            "search_result_location",
            "messages",
            "kb://doc-1",
            "kb://doc-1",
        )
        assert (citation.document_title, citation.document_index) == ("점검 지침", 0)
        assert (citation.source_start, citation.source_end) == (1, 2)

        replay = build(chat_completions, [HubMessage(role="assistant", content=[text])])
        assert replay["messages"][0]["content"] == '<cite id="kb://doc-1">500시간</cite>'


class TestOtherVendors:
    """search result는 문서 텍스트로, tool_result에서는 structured_content 대신 보낸다."""

    def result(self) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id="t1",
            content="",
            structured_content={"type": "search_result", "results": []},
            blocks=[search()],
        )

    def test_chat_completions_tool_message_is_the_document_text(self) -> None:
        body = build(chat_completions, tool_turns(self.result()))
        assert body["messages"][-1] == {"role": "tool", "tool_call_id": "t1", "content": DOCUMENTS}

    def test_responses_function_call_output_is_the_document_text(self) -> None:
        body = build(responses, tool_turns(self.result()))
        output = next(item for item in body["input"] if item["type"] == "function_call_output")
        assert output["output"] == [{"type": "input_text", "text": DOCUMENTS}]

    def test_gemini_function_response_sends_the_text_once(self) -> None:
        body = build(GEMINI, tool_turns(self.result()))
        part = body["contents"][-1]["parts"][0]["functionResponse"]
        assert part["response"] == {"result": DOCUMENTS}
        assert "parts" not in part

    def test_content_comes_before_the_document_text(self) -> None:
        result = ToolResultBlock(tool_use_id="t1", content="2건 찾음", blocks=[search()])
        body = build(chat_completions, tool_turns(result))
        assert body["messages"][-1]["content"] == f"2건 찾음\n{DOCUMENTS}"

    def test_structured_content_is_still_sent_without_blocks(self) -> None:
        result = ToolResultBlock(tool_use_id="t1", structured_content={"n": 1})
        body = build(chat_completions, tool_turns(result))
        assert body["messages"][-1]["content"] == '{"n": 1}'

    @pytest.mark.parametrize("adapter", [chat_completions, responses, GEMINI])
    def test_user_message_search_results_precede_the_question(self, adapter: VendorAdapter) -> None:
        history = [HubMessage(role="user", content=[search(), TextBlock(text="점검 주기?")])]
        serialized = str(build(adapter, history))
        assert serialized.index('<document id="kb://doc-1">') < serialized.index("점검 주기?")
