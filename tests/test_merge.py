"""병합 규칙. 기본이 이어붙이기이고 예외만 표시라는 계약을 고정한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from completion_bridge import (
    HubResponse,
    StreamMerger,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)


class Item(BaseModel):
    index: int | None = None
    name: str = Field(default="", json_schema_extra={"stream": "overwrite"})
    args: str = ""


class Envelope(BaseModel):
    ident: str | None = Field(default=None, json_schema_extra={"stream": "overwrite"})
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
        assert merger.build().text == "Hello world"

    def test_numbers_add(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(count=3))
        merger.apply(Envelope(count=4))
        assert merger.build().count == 7

    def test_overwrite_marker_replaces(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(ident="a"))
        merger.apply(Envelope(ident="b"))
        assert merger.build().ident == "b"

    def test_literal_field_overwrites_without_marker(self) -> None:
        """Literal은 합법적인 값이 하나뿐이라 이어붙이기가 맞을 수 없다."""
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(kind="a"))
        merger.apply(Envelope(kind="a"))
        assert merger.build().kind == "a"

    def test_none_delta_leaves_value_alone(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(text="kept"))
        merger.apply(Envelope(count=1))
        assert merger.build().text == "kept"

    def test_apply_none_is_a_noop(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(None)
        assert merger.build().text is None


class TestListRules:
    def test_primitive_list_extends(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(tags=["a"]))
        merger.apply(Envelope(tags=["b"]))
        assert merger.build().tags == ["a", "b"]

    def test_model_list_merges_by_index(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=0, name="get", args='{"a')]))
        merger.apply(Envelope(items=[Item(index=0, args='":1}')]))
        built = merger.build()
        assert len(built.items) == 1
        assert built.items[0].name == "get"
        assert built.items[0].args == '{"a":1}'

    def test_distinct_indexes_stay_separate(self) -> None:
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=0, args="x")]))
        merger.apply(Envelope(items=[Item(index=1, args="y")]))
        merger.apply(Envelope(items=[Item(index=0, args="z")]))
        built = merger.build()
        assert [i.args for i in built.items] == ["xz", "y"]

    def test_arrival_order_is_preserved(self) -> None:
        """벤더가 인덱스를 촘촘히 주지 않을 수 있다. 순서가 곧 의미인 리스트라 도착 순서를 쓴다."""
        merger: StreamMerger[Envelope] = StreamMerger(Envelope)
        merger.apply(Envelope(items=[Item(index=5, args="first")]))
        merger.apply(Envelope(items=[Item(index=2, args="second")]))
        assert [i.args for i in merger.build().items] == ["first", "second"]


class TestNested:
    def test_nested_model_merges_recursively(self) -> None:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(usage=Usage(input_tokens=10)))
        merger.apply(HubResponse(usage=Usage(output_tokens=4)))
        usage = merger.build().usage
        assert usage is not None
        assert (usage.input_tokens, usage.output_tokens) == (10, 4)


class TestHubResponse:
    def test_text_blocks_accumulate_into_one_block(self) -> None:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(role="assistant", content=[TextBlock(text="Hel", index=0)]))
        merger.apply(HubResponse(content=[TextBlock(text="lo", index=0)]))
        built = merger.build()
        assert built.role == "assistant"
        assert built.text == "Hello"
        assert len(built.content) == 1

    def test_block_type_is_not_concatenated(self) -> None:
        """type이 'texttext'가 되면 유니온 조회가 깨진다."""
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[TextBlock(text="a", index=0)]))
        merger.apply(HubResponse(content=[TextBlock(text="b", index=0)]))
        assert merger.build().content[0].type == "text"

    def test_thinking_and_text_stay_separate(self) -> None:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[ThinkingBlock(thinking="why", index=-1)]))
        merger.apply(HubResponse(content=[TextBlock(text="because", index=0)]))
        built = merger.build()
        assert [b.type for b in built.content] == ["thinking", "text"]
        assert built.text == "because"

    def test_tool_arguments_accumulate(self) -> None:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(
            HubResponse(content=[ToolUseBlock(id="c1", name="get", input_json='{"q', index=1)])
        )
        merger.apply(HubResponse(content=[ToolUseBlock(input_json='":2}', index=1)]))
        block = merger.build().content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.id == "c1"
        assert block.name == "get"
        assert block.input_json == '{"q":2}'

    def test_build_is_repeatable(self) -> None:
        merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        merger.apply(HubResponse(content=[TextBlock(text="x", index=0)]))
        assert merger.build().text == merger.build().text == "x"
