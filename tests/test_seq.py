"""seq 발급 규칙과 최종 index 확정. ``docs/block_index_and_key_tuple.md``의 규칙을 고정한다."""

from __future__ import annotations

import pytest

from completion_bridge import (
    AnnotationBlock,
    HubResponse,
    MappingError,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from completion_bridge.seq import SeqAllocator, finalize


class TestSeqAllocator:
    def test_coordinate_block_ends_with_zero_after(self) -> None:
        seqs = SeqAllocator(3)
        assert seqs.coord((2, 1, 4)) == (2, 1, 4, 0)

    def test_after_counts_per_prefix(self) -> None:
        seqs = SeqAllocator(1)
        assert seqs.after((2, 0)) == (2, 1)
        assert seqs.after((2, 0)) == (2, 2)
        assert seqs.after((1, 0)) == (1, 1)

    def test_after_an_after_block_keeps_counting_the_same_prefix(self) -> None:
        seqs = SeqAllocator(1)
        first = seqs.after((2, 0))
        assert seqs.after(first) == (2, 2)

    def test_after_without_anchor_follows_the_largest_seq(self) -> None:
        """좌표가 더 작은 블록이 뒤늦게 와도 기준은 가장 큰 seq다."""
        seqs = SeqAllocator(1)
        seqs.coord((0,))
        seqs.coord((3,))
        assert seqs.after() == (3, 1)
        seqs.coord((1,))
        assert seqs.after() == (3, 2)

    def test_after_before_any_block_sorts_before_every_coordinate(self) -> None:
        assert SeqAllocator(1).after() == (-1, 1)
        assert SeqAllocator(3).after() == (-1, 0, 0, 1)
        assert SeqAllocator(1).after() < (0, 0)

    def test_keyed_after_is_issued_once(self) -> None:
        seqs = SeqAllocator(1)
        seqs.coord((0,))
        grounding = seqs.after(key=("grounding",))
        seqs.coord((1,))
        assert seqs.after(key=("grounding",)) == grounding == (0, 1)

    def test_after_blocks_never_share_a_seq(self) -> None:
        """벤더 번호를 after 값으로 쓰지 않으므로 대상이 같은 두 블록이 겹치지 않는다."""
        seqs = SeqAllocator(1)
        text = seqs.coord((0,))
        audio = seqs.after()
        annotation = seqs.after(text)
        assert audio != annotation

    def test_invalid_shapes_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            SeqAllocator(0)
        seqs = SeqAllocator(2)
        with pytest.raises(ValueError):
            seqs.coord((1,))
        with pytest.raises(ValueError):
            seqs.after((1, 0))


class TestFinalize:
    def test_blocks_follow_seq_order_not_arrival_order(self) -> None:
        response = HubResponse(
            content=[
                TextBlock(text="c", seq=(2, 0)),
                ThinkingBlock(thinking="a", seq=(0, 0)),
                ToolUseBlock(id="b", seq=(1, 0)),
            ]
        )
        result = finalize(response)
        assert [block.type for block in result.content] == ["thinking", "tool_use", "text"]
        assert [block.index for block in result.content] == [0, 1, 2]
        assert [block.seq for block in result.content] == [None, None, None]

    def test_gaps_in_coordinates_become_consecutive_indices(self) -> None:
        response = HubResponse(
            content=[TextBlock(text="a", seq=(0, 0)), TextBlock(text="b", seq=(5, 0))]
        )
        assert [block.index for block in finalize(response).content] == [0, 1]

    def test_annotation_target_seq_becomes_target_index(self) -> None:
        response = HubResponse(
            content=[
                TextBlock(text="a", seq=(0, 0)),
                TextBlock(text="b", seq=(1, 0)),
                AnnotationBlock(id="x", seq=(1, 1), target_seq=(1, 0)),
                AnnotationBlock(id="y", seq=(1, 2), target_seq=(9, 0)),
            ]
        )
        result = finalize(response)
        first, second = result.content[2], result.content[3]
        assert isinstance(first, AnnotationBlock) and isinstance(second, AnnotationBlock)
        assert (first.target_index, first.target_seq) == (1, None)
        assert (second.target_index, second.target_seq) == (None, None)

    def test_cleared_keys_are_not_serialized_for_storage(self) -> None:
        """저장용 ``exclude_unset`` 직렬화에 seq 키가 남지 않는다. 대상이 없는 annotation에는
        ``target_index`` 키도 없다."""
        response = HubResponse(
            content=[
                TextBlock(text="a", seq=(0, 0)),
                AnnotationBlock(id="x", seq=(0, 1), target_seq=(0, 0)),
                AnnotationBlock(id="y", seq=(0, 2)),
            ]
        )
        # 병합 결과처럼 seq가 "값을 준 필드"인 상태에서 시작한다.
        assert all("seq" in block.model_fields_set for block in response.content)
        dumped = [
            block.model_dump(mode="json", exclude_unset=True)
            for block in finalize(response).content
        ]
        assert all("seq" not in block and "target_seq" not in block for block in dumped)
        assert [block["index"] for block in dumped] == [0, 1, 2]
        assert dumped[1]["target_index"] == 0
        assert "target_index" not in dumped[2]

    def test_block_without_seq_is_rejected(self) -> None:
        response = HubResponse(content=[TextBlock(text="a", seq=(0, 0)), TextBlock(text="b")])
        with pytest.raises(MappingError, match="has no seq"):
            finalize(response)

    def test_input_is_left_unchanged(self) -> None:
        response = HubResponse(content=[TextBlock(text="a", seq=(0, 0))])
        finalize(response)
        assert response.content[0].seq == (0, 0)
        assert response.content[0].index is None
