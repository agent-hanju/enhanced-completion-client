"""정수 좌표 ``seq``의 길이 정규화와 순위 계산"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["Seq", "normalize_seq", "rank_seq"]

Seq = tuple[int, ...]
"""content block의 식별·순서 좌표. 공통 앞 성분이 같으면 짧은 좌표가 먼저다."""


def normalize_seq(seq: Seq, width: int) -> Seq:
    """seq의 length가 witdh가 되도록 -1 right padding한다.

    Args:
        seq: 가변 길이 정수 좌표 튜플
        width: 길이 정규화할 크기

    Returns:
        원래 좌표 뒤를 -1로 채운 길이 width의 튜플

    Raises:
        ValueError: width가 1 미만이거나, seq의 length가 width를 초과할 때
    """
    if type(width) is not int or width < 1:
        raise ValueError(f"seq width must be a positive integer, got {width!r}")
    if not isinstance(seq, tuple) or not seq or len(seq) > width:
        raise ValueError(f"expected 1 to {width} seq coordinates, got {seq!r}")
    for coordinate in seq:
        if type(coordinate) is not int:
            raise ValueError(f"seq coordinates must be integers, got {seq!r}")
    return seq + (-1,) * (width - len(seq))


def rank_seq(sequences: Sequence[Seq]) -> list[int]:
    """입력 순서의 각 seq에 대응하는 zero-base 순위로 변환한 리스트를 반환한다.

    Args:
        sequences: 사전식으로 비교할 정수 좌표 목록. 입력 순서를 변경하지 않는다.

    Returns:
        입력과 같은 길이의 순위 목록. 같은 seq에는 같은 순위를 부여하며 순위에 공백이 없다.
        빈 입력에는 빈 목록을 반환한다.
    """
    positions: dict[Seq, int] = {}
    for seq in sorted(set(sequences)):
        positions[seq] = len(positions)
    ranks: list[int] = []
    for seq in sequences:
        ranks.append(positions[seq])
    return ranks
