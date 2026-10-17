"""seq의 길이 정규화와 입력 위치별 순위 계산을 검증한다."""

from __future__ import annotations

import pytest

from completion_bridge.streaming.sequence import normalize_seq, rank_seq


class TestNormalizeSeq:
    def test_padding_preserves_body_before_children(self) -> None:
        """-1 정규화 후 vocabulary가 분할해도 주석은 모든 본문 조각 다음이다."""
        body = normalize_seq((1, 2), 3)
        annotation = normalize_seq((1, 2, 0), 3)
        assert body == (1, 2, -1)
        assert annotation == (1, 2, 0)
        assert (*body, 0) < (*body, 1) < (*annotation, 0)

    def test_custom_stage_can_reserve_two_more_coordinates(self) -> None:
        """2depth 확장도 단계별로 정규화하면 다음 단계가 동일한 규칙으로 확장한다."""
        parent = normalize_seq((1,), 2)
        body = normalize_seq(parent, 4)
        first = normalize_seq((*parent, 0), 4)
        child = normalize_seq((*parent, 0, 0), 4)
        assert body < first < child
        assert (*first, 1) < (*child, 0)

    def test_received_zero_is_preserved(self) -> None:
        assert normalize_seq((0,), 1) == (0,)
        assert normalize_seq((3, 0), 2) == (3, 0)


class TestRankSeq:
    def test_ranks_correspond_to_input_positions(self) -> None:
        sequences = [(20,), (5,), (10,)]
        assert rank_seq(sequences) == [2, 0, 1]
        assert sequences == [(20,), (5,), (10,)]

    def test_duplicates_share_a_dense_rank(self) -> None:
        assert rank_seq([(20,), (5,), (20,)]) == [1, 0, 1]

    def test_empty_input_has_no_ranks(self) -> None:
        assert rank_seq([]) == []

    def test_variable_depth_and_padding_use_lexicographic_order(self) -> None:
        assert rank_seq([(1, 0), (1,), (1, -1), (0,), (1, 0, 0)]) == [3, 1, 2, 0, 4]


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5, "1", None])
def test_invalid_width_rejected(invalid) -> None:
    """최대 길이는 정수 양수여야 한다."""
    with pytest.raises(ValueError):
        normalize_seq((1,), invalid)


@pytest.mark.parametrize("invalid", [[1], (True,), (1.5,), ("1",), (), (1, 2, 3)])
def test_invalid_coordinates_rejected(invalid) -> None:
    """잘못된 성분과 최대 길이 초과를 거부한다."""
    with pytest.raises(ValueError):
        normalize_seq(invalid, 2)
