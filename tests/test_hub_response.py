"""응답 확정 시 블록 순서·주석 참조·저장용 필드 제거를 검증한다."""

import pytest

from completion_bridge import (
    AnnotationBlock,
    HubResponse,
    MappingError,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from completion_bridge.models.hub import finalize_response


class TestFinalizeResponse:
    def test_blocks_follow_seq_order_not_arrival_order(self) -> None:
        response = HubResponse(
            content=[
                TextBlock(text="c", seq=(2, 0)),
                ThinkingBlock(thinking="a", seq=(0, 0)),
                ToolUseBlock(id="b", seq=(1, 0)),
            ]
        )
        result = finalize_response(response)
        assert [block.type for block in result.content] == ["thinking", "tool_use", "text"]
        assert [block.index for block in result.content] == [0, 1, 2]
        assert [block.seq for block in result.content] == [None, None, None]

    def test_gaps_in_coordinates_become_consecutive_indices(self) -> None:
        response = HubResponse(
            content=[TextBlock(text="a", seq=(0, 0)), TextBlock(text="b", seq=(5, 0))]
        )
        assert [block.index for block in finalize_response(response).content] == [0, 1]

    def test_annotation_target_seq_becomes_target_index(self) -> None:
        response = HubResponse(
            content=[
                TextBlock(text="a", seq=(0, 0)),
                TextBlock(text="b", seq=(1, 0)),
                AnnotationBlock(id="x", seq=(1, 1), target_seq=(1, 0)),
                AnnotationBlock(id="y", seq=(1, 2), target_seq=(9, 0)),
            ]
        )
        result = finalize_response(response)
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
            for block in finalize_response(response).content
        ]
        assert all("seq" not in block and "target_seq" not in block for block in dumped)
        assert [block["index"] for block in dumped] == [0, 1, 2]
        assert dumped[1]["target_index"] == 0
        assert "target_index" not in dumped[2]

    def test_block_without_seq_is_rejected(self) -> None:
        response = HubResponse(content=[TextBlock(text="a", seq=(0, 0)), TextBlock(text="b")])
        with pytest.raises(MappingError, match="has no seq"):
            finalize_response(response)

    def test_input_is_left_unchanged(self) -> None:
        response = HubResponse(content=[TextBlock(text="a", seq=(0, 0))])
        finalize_response(response)
        assert response.content[0].seq == (0, 0)
        assert response.content[0].index is None


