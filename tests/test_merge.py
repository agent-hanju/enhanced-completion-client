"""병합 규칙. 기본이 이어붙이기이고 예외만 표시라는 계약을 고정한다."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from completion_bridge import (
    AnnotationBlock,
    AudioBlock,
    ContentBlock,
    GroundingBlock,
    GroundingSource,
    GroundingSupport,
    HubResponse,
    StreamMergeError,
    StreamMerger,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    register_block,
)
from completion_bridge.streaming.metadata import STREAM_INDEX, STREAM_OVERWRITE


class TestNested:
    def test_usage_fields_merge_independently(self) -> None:
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(HubResponse(usage=Usage(input_tokens=10)))
        merger.apply(HubResponse(usage=Usage(output_tokens=4)))
        usage = merger.result.usage
        assert usage is not None
        assert (usage.input_tokens, usage.output_tokens) == (10, 4)

    def test_native_usage_merges_by_key_and_keeps_the_first_delta(self) -> None:
        first = HubResponse(usage=Usage(native={"input_tokens": 10, "output_tokens": 1}))
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(first)
        merger.apply(HubResponse(usage=Usage(native={"output_tokens": 4, "cache": {"read": 2}})))
        usage = merger.result.usage
        assert usage is not None
        assert usage.native == {"input_tokens": 10, "output_tokens": 4, "cache": {"read": 2}}
        assert first.usage is not None
        assert first.usage.native == {"input_tokens": 10, "output_tokens": 1}


class TestHubResponse:
    def test_text_blocks_accumulate_into_one_block(self) -> None:
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(HubResponse(role="assistant", content=[TextBlock(text="Hel", seq=(0, 0))]))
        merger.apply(HubResponse(content=[TextBlock(text="lo", seq=(0, 0))]))
        built = merger.result
        assert built.role == "assistant"
        assert built.text == "Hello"
        assert len(built.content) == 1

    def test_block_type_is_not_concatenated(self) -> None:
        """type이 'texttext'가 되면 유니온 조회가 깨진다."""
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[TextBlock(text="a", seq=(0, 0))]))
        merger.apply(HubResponse(content=[TextBlock(text="b", seq=(0, 0))]))
        assert merger.result.content[0].type == "text"

    def test_thinking_and_text_stay_separate(self) -> None:
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[ThinkingBlock(thinking="why", seq=(-1, 0))]))
        merger.apply(HubResponse(content=[TextBlock(text="because", seq=(0, 0))]))
        built = merger.result
        assert [b.type for b in built.content] == ["thinking", "text"]
        assert built.text == "because"

    def test_tool_arguments_accumulate(self) -> None:
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(
            HubResponse(content=[ToolUseBlock(id="c1", name="get", input_json='{"q', seq=(1, 0))])
        )
        merger.apply(HubResponse(content=[ToolUseBlock(input_json='":2}', seq=(1, 0))]))
        block = merger.result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "c1"
        assert block.name == "get"
        assert block.input_json == '{"q":2}'
        assert block.input == {"q": 2}

    def test_self_valid_fragment_does_not_win_input(self) -> None:
        """그 자체로 유효한 JSON인 조각이 ``input``을 덮지 않는다.

        ``'":"'``는 JSON 문자열 ``:``로 파싱되는 조각이다.
        """
        merger: StreamMerger = StreamMerger(HubResponse)
        for fragment in ('{"term', '":"', '하이비"}'):
            merger.apply(HubResponse(content=[ToolUseBlock(input_json=fragment, seq=(0, 0))]))
        block = merger.result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input_json == '{"term":"하이비"}'
        assert block.input == {"term": "하이비"}

    def test_truncated_arguments_leave_input_empty(self) -> None:
        """스트림이 인수 중간에 끊기면 ``input``은 ``None``으로 남는다."""
        merger: StreamMerger = StreamMerger(HubResponse)
        for fragment in ('{"term', '":"'):
            merger.apply(HubResponse(content=[ToolUseBlock(input_json=fragment, seq=(0, 0))]))
        block = merger.result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input_json == '{"term":"'
        assert block.input is None

    def test_build_is_repeatable(self) -> None:
        merger: StreamMerger = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[TextBlock(text="x", seq=(0, 0))]))
        assert merger.result.text == merger.result.text == "x"


class TestLookupIndex:
    def test_many_seq_values_keep_arrival_order_and_merge(self) -> None:
        merger = StreamMerger(HubResponse)
        for index in reversed(range(1000)):
            merger.apply(HubResponse(content=[TextBlock(seq=(index,), text="a")]))
        for index in range(1000):
            merger.apply(HubResponse(content=[TextBlock(seq=(index,), text="b")]))
        built = merger.result
        assert [block.seq for block in built.content] == [(i,) for i in reversed(range(1000))]
        assert all(isinstance(block, TextBlock) and block.text == "ab" for block in built.content)


class TestStringFragments:
    def test_build_between_fragments_and_result_mutation(self) -> None:
        merger = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[TextBlock(seq=(0,), text="a")]))
        merger.apply(HubResponse(content=[TextBlock(seq=(0,), text="b")]))
        first = merger.result
        assert first.text == "ab"
        assert isinstance(first.content[0], TextBlock)
        first.content[0].text = "changed"
        merger.apply(HubResponse(content=[TextBlock(seq=(0,), text="c")]))
        assert merger.result.text == "abc"
        assert merger.result.text == "abc"

    def test_arguments_parse_only_the_combined_value(self) -> None:
        merger = StreamMerger(HubResponse)
        for fragment in ('{"query":', '"검색어"', "}"):
            merger.apply(HubResponse(content=[ToolUseBlock(seq=(0,), input_json=fragment)]))
        block = merger.result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.input_json == '{"query":"검색어"}'
        assert block.input == {"query": "검색어"}


class TestResponseRules:
    def test_metadata_replaces_and_none_preserves(self) -> None:
        merger = StreamMerger(HubResponse)
        merger.apply(HubResponse(id="a", model="first", role="assistant"))
        merger.apply(HubResponse(id="b", model="second", stop_reason="done"))
        merger.apply(None)
        built = merger.result
        assert (built.id, built.model, built.role, built.stop_reason) == (
            "b",
            "second",
            "assistant",
            "done",
        )

    def test_usage_snapshots_replace_instead_of_adding(self) -> None:
        merger = StreamMerger(HubResponse)
        merger.apply(HubResponse(usage=Usage(input_tokens=10, output_tokens=2)))
        merger.apply(HubResponse(usage=Usage(output_tokens=5)))
        assert merger.result.usage == Usage(input_tokens=10, output_tokens=5)

    def test_grounding_indexes_are_independent_per_block_and_field(self) -> None:
        merger = StreamMerger(HubResponse)
        for title in ("a", "b"):
            merger.apply(
                HubResponse(
                    content=[
                        GroundingBlock(
                            seq=(0,),
                            sources=[GroundingSource(index=0, title=title)],
                            supports=[GroundingSupport(index=0, text=title)],
                        ),
                        GroundingBlock(seq=(1,), sources=[GroundingSource(index=0, title="other")]),
                    ]
                )
            )
        first, second = merger.result.content
        assert isinstance(first, GroundingBlock) and isinstance(second, GroundingBlock)
        assert len(first.sources) == len(first.supports) == 1
        assert first.sources[0].title == first.supports[0].text == "b"
        assert second.sources[0].title == "other"

    def test_audio_fragments_and_native_snapshot(self) -> None:
        merger = StreamMerger(HubResponse)
        merger.apply(
            HubResponse(content=[AudioBlock(seq=(0,), data="a", transcript="x", native={"old": 1})])
        )
        merger.apply(
            HubResponse(content=[AudioBlock(seq=(0,), data="b", transcript="y", native={"new": 2})])
        )
        block = merger.result.content[0]
        assert isinstance(block, AudioBlock)
        assert (block.data, block.transcript, block.native) == ("ab", "xy", {"new": 2})

    def test_custom_block_uses_declared_field_policies(self) -> None:
        class Custom(ContentBlock):
            type: Literal["merge_test_custom"] = "merge_test_custom"
            text: str = ""
            count: int = 0
            label: str = Field(default="", json_schema_extra=STREAM_OVERWRITE)

        register_block(Custom)
        merger = StreamMerger(HubResponse)
        for value in ("a", "b"):
            merger.apply(HubResponse(content=[Custom(seq=(0,), text=value, count=1, label=value)]))
        block = merger.result.content[0]
        assert isinstance(block, Custom)
        assert (block.text, block.count, block.label) == ("ab", 2, "b")

    def test_deltas_remain_unchanged_after_accumulation_and_build(self) -> None:
        first = HubResponse(content=[TextBlock(seq=(0,), text="a")])
        second = HubResponse(content=[TextBlock(seq=(0,), text="b")])
        before_first = first.model_dump()
        before_second = second.model_dump()
        merger = StreamMerger(HubResponse)
        merger.apply(first)
        merger.apply(second)
        assert merger.result.text == "ab"
        assert first.model_dump() == before_first
        assert second.model_dump() == before_second

    def test_nested_tool_results_merge_with_dfs(self) -> None:
        merger = StreamMerger(HubResponse)
        for text in ("a", "b"):
            child = TextBlock(seq=(0,), text=text)
            for depth in range(20):
                child = ToolResultBlock(seq=(0,), tool_use_id=str(depth), blocks=[child])
            merger.apply(HubResponse(content=[child]))
        block = merger.result.content[0]
        for _ in range(20):
            assert isinstance(block, ToolResultBlock)
            assert len(block.blocks) == 1
            block = block.blocks[0]
        assert isinstance(block, TextBlock)
        assert block.text == "ab"

    def test_annotation_text_is_a_snapshot(self) -> None:
        merger = StreamMerger(HubResponse)
        for text in ("first", "second"):
            merger.apply(HubResponse(content=[AnnotationBlock(seq=(0,), text=text)]))
        block = merger.result.content[0]
        assert isinstance(block, AnnotationBlock)
        assert block.text == "second"


class Item(BaseModel):
    index: int | None = None
    name: str = Field(default="", json_schema_extra=STREAM_OVERWRITE)
    args: str = ""


class Envelope(BaseModel):
    ident: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    text: str | None = None
    count: int | None = None
    kind: Literal["a", "b"] | None = None
    items: list[Item] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TestScalarRules:
    def test_strings_concatenate_by_default(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(text="Hello"))
        merger.apply(Envelope(text=" world"))
        assert merger.result.text == "Hello world"

    def test_numbers_add(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(count=3))
        merger.apply(Envelope(count=4))
        assert merger.result.count == 7

    def test_overwrite_marker_replaces(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(ident="a"))
        merger.apply(Envelope(ident="b"))
        assert merger.result.ident == "b"

    def test_literal_field_overwrites_without_marker(self) -> None:
        """Literal은 합법적인 값이 하나뿐이라 이어붙이기가 맞을 수 없다."""
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(kind="a"))
        merger.apply(Envelope(kind="a"))
        assert merger.result.kind == "a"

    def test_none_delta_leaves_value_alone(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(text="kept"))
        merger.apply(Envelope(count=1))
        assert merger.result.text == "kept"

    def test_apply_none_is_a_noop(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(None)
        assert merger.result is None


class TestListRules:
    def test_primitive_list_extends(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(tags=["a"]))
        merger.apply(Envelope(tags=["b"]))
        assert merger.result.tags == ["a", "b"]

    def test_model_list_merges_by_index(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=0, name="get", args='{"a')]))
        merger.apply(Envelope(items=[Item(index=0, args='":1}')]))
        built = merger.result
        assert len(built.items) == 1
        assert built.items[0].name == "get"
        assert built.items[0].args == '{"a":1}'

    def test_distinct_indexes_stay_separate(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=0, args="x")]))
        merger.apply(Envelope(items=[Item(index=1, args="y")]))
        merger.apply(Envelope(items=[Item(index=0, args="z")]))
        built = merger.result
        assert [i.args for i in built.items] == ["xz", "y"]

    def test_arrival_order_is_preserved(self) -> None:
        """벤더가 인덱스를 촘촘히 주지 않을 수 있다. 순서가 곧 의미인 리스트라 도착 순서를 쓴다."""
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=5, args="first")]))
        merger.apply(Envelope(items=[Item(index=2, args="second")]))
        assert [i.args for i in merger.result.items] == ["first", "second"]


class TestDiscussion:
    def test_nested_discussion_merges_turns_and_conclusion(self) -> None:
        class Turn(BaseModel):
            index: int = Field(json_schema_extra=STREAM_INDEX)
            name: str = Field(default="", json_schema_extra=STREAM_OVERWRITE)
            text: str = ""

        class Discussion(BaseModel):
            conversation: list[Turn] = Field(default_factory=list)
            conclusion: str = ""

        merger = StreamMerger(Discussion)
        merger.apply(Discussion(conversation=[Turn(index=0, name="a", text="첫 ")]))
        merger.apply(Discussion(conversation=[Turn(index=0, text="발언")]))
        merger.apply(Discussion(conversation=[Turn(index=1, name="b", text="반론")]))
        merger.apply(Discussion(conversation=[Turn(index=2, name="a", text="재답변")]))
        merger.apply(Discussion(conclusion="최종 "))
        merger.apply(Discussion(conclusion="결론"))
        result = merger.result
        assert result.conversation == [
            Turn(index=0, name="a", text="첫 발언"),
            Turn(index=1, name="b", text="반론"),
            Turn(index=2, name="a", text="재답변"),
        ]
        assert result.conclusion == "최종 결론"

    def test_deep_models_use_iterative_dfs(self) -> None:
        class Node(BaseModel):
            child: Any = None
            text: str = ""

        first = Node(text="a")
        second = Node(text="b")
        for _ in range(1500):
            first = Node.model_construct(child=first)
            second = Node.model_construct(child=second)
        merger = StreamMerger(Node)
        merger.apply(first)
        merger.apply(second)
        current = merger.result.child
        for _ in range(1499):
            current = current.child
        assert current.text == "ab"


class TestExplicitDfsTasks:
    def test_duplicate_items_finish_nested_merge_in_arrival_order(self) -> None:
        class Child(BaseModel):
            text: str = "default"

        class Entry(BaseModel):
            index: int
            child: Child

        class Root(BaseModel):
            items: list[Entry]

        merger = StreamMerger(Root)
        merger.apply(Root(items=[
            Entry(index=0, child=Child(text="first")),
            Entry(index=0, child=Child()),
            Entry(index=0, child=Child(text="last")),
        ]))
        assert merger.result.items[0].child.text == "firstlast"

    def test_extra_model_replaces_previous_value_after_child_completion(self) -> None:
        class Extra(BaseModel):
            text: str
            items: list[Item]

        class Root(BaseModel):
            model_config = {"extra": "allow"}

        merger = StreamMerger(Root)
        for text in ("old", "new"):
            merger.apply(Root(detail=Extra(text=text, items=[
                Item(index=0, args="a"), Item(index=0, args="b"),
            ])))
        assert merger.result.model_dump() == {
            "detail": {"text": "new", "items": [{"index": 0, "name": "", "args": "ab"}]}
        }


class TestBestEffortMerge:
    def test_merging_does_not_revalidate_field_constraints(self) -> None:
        class Limited(BaseModel):
            text: str = Field(default="", max_length=3)
            count: int = 0

        merger = StreamMerger(Limited)
        merger.apply(Limited(text="a", count=1))
        merger.apply(Limited(text="xyz", count=9))
        assert merger.result.text == "axyz"
        assert merger.result.count == 10
        assert merger.result.model_dump(exclude_unset=True) == {"text": "axyz", "count": 10}

    def test_identity_failure_keeps_prior_field_changes_and_reports_delta(self) -> None:
        class ByIndex(BaseModel):
            index: int
            text: str = ""

        class ById(BaseModel):
            id: int = Field(json_schema_extra=STREAM_INDEX)
            text: str = ""

        class Root(BaseModel):
            text: str = ""
            items: list[ByIndex | ById] = Field(default_factory=list)

        merger = StreamMerger(Root)
        first = Root(text="first", items=[ByIndex(index=0, text="a")])
        merger.apply(first)
        delta = Root(text="bad", items=[ById(id=1)])
        with pytest.raises(StreamMergeError) as caught:
            merger.apply(delta)
        assert isinstance(caught.value.__cause__, ValueError)
        assert caught.value.delta is delta
        assert merger.result.text == "firstbad"
        assert merger.result.items == first.items
        assert caught.value.result == merger.result
        merger.apply(Root(text="later"))
        assert caught.value.result.text == "firstbad"

    def test_result_and_inputs_do_not_share_mutable_data(self) -> None:
        first = HubResponse(content=[TextBlock(seq=(0,), text="a", native={"nested": [1]})])
        merger = StreamMerger(HubResponse)
        merger.apply(first)
        first.content[0].native["nested"].append(2)
        result = merger.result
        assert result is not None
        result.content[0].native["nested"].append(3)
        assert merger.result.content[0].native == {"nested": [1]}


def test_dictionary_identity_can_be_completed_by_a_later_model() -> None:
    class RequiredItem(BaseModel):
        index: int
        text: str

    class Root(BaseModel):
        items: list[Any]

    merger = StreamMerger(Root)
    merger.apply(Root(items=[{"index": 0}]))
    merger.apply(Root(items=[RequiredItem(index=0, text="completed")]))
    assert merger.result.items == [RequiredItem(index=0, text="completed")]


def test_index_lookup_is_reused_across_deltas(monkeypatch) -> None:
    merger = StreamMerger(Envelope)
    original = merger._index_field
    calls = 0

    def counted(model):
        nonlocal calls
        calls += 1
        return original(model)

    monkeypatch.setattr(merger, "_index_field", counted)
    for i in range(1000):
        merger.apply(Envelope(items=[Item(index=i, args="x")]))
    assert len(merger.result.items) == 1000
    assert calls == 1000


def test_replaced_lists_release_old_lookup_and_do_not_merge_old_items() -> None:
    class Root(BaseModel):
        items: list[Item] = Field(default_factory=list, json_schema_extra=STREAM_OVERWRITE)

    merger = StreamMerger(Root)
    for text in ("a", "b", "c"):
        merger.apply(Root(items=[Item(index=0, args=text)]))
    assert merger.result.items[0].args == "c"
    assert len(merger._indexes) == 1


def test_first_explicit_required_none_is_preserved_without_initialization_policy() -> None:
    class Root(BaseModel):
        value: str | None

    merger = StreamMerger(Root)
    merger.apply(Root(value=None))
    assert merger.result.value is None
    assert merger.result.model_dump() == {"value": None}


def test_new_models_do_not_rerun_default_factories() -> None:
    calls = 0

    def factory(data):
        nonlocal calls
        calls += 1
        return data["name"] + "-created"

    class Root(BaseModel):
        name: str
        label: str = Field(default_factory=factory)

    delta = Root(name="test")
    merger = StreamMerger(Root)
    merger.apply(delta)
    assert calls == 1
    assert merger.result == delta
