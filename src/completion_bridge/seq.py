"""블록 순서 키 ``seq``의 발급과 최종 ``index`` 확정.

delta의 content block은 ``seq`` 튜플로 병합되고, 병합이 끝난 최종 결과에서만 ``index``가
정해진다. 규칙은 ``docs/block_index_and_key_tuple.md``에 있다.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from .blocks import AnnotationBlock, ContentBlock
from .errors import MappingError
from .hub import HubResponse

__all__ = ["Seq", "SeqAllocator", "finalize"]

Seq = tuple[int, ...]
"""content block의 순서 키. 사전식 비교로 순서가 정해진다."""


class SeqAllocator:
    """어댑터 하나가 스트림 하나 동안 쓰는 seq 발급 상태.

    발급하는 seq는 ``(좌표..., after)`` 모양이고 좌표 개수는 생성할 때 정한 ``width``로
    고정된다. 좌표가 있는 블록은 :meth:`coord`, 좌표가 없거나 다른 블록 뒤에 와야 하는 블록은
    :meth:`after`로 받는다.
    """

    def __init__(self, width: int) -> None:
        """좌표 개수를 정한다.

        Args:
            width: seq의 좌표 성분 개수. after 성분은 포함하지 않는다.

        Raises:
            ValueError: ``width``가 1보다 작을 때.
        """
        if width < 1:
            raise ValueError(f"seq width must be at least 1, got {width}")
        self._width = width
        self._last: Seq | None = None
        self._after: dict[Seq, int] = {}
        self._keyed: dict[Hashable, Seq] = {}

    def coord(self, coordinates: Seq) -> Seq:
        """좌표가 있는 블록의 seq를 돌려준다. after 성분은 0이다.

        같은 좌표로 다시 부르면 같은 seq를 돌려준다. 돌려준 seq는 :meth:`after`가 기준 블록을
        정할 때 쓰는 최댓값에 반영된다.

        Args:
            coordinates: ``width``개의 정수 좌표.

        Raises:
            ValueError: 좌표 개수가 ``width``와 다를 때.
        """
        if len(coordinates) != self._width:
            raise ValueError(
                f"expected {self._width} seq coordinates, got {len(coordinates)}: {coordinates}"
            )
        seq = (*coordinates, 0)
        self._observe(seq)
        return seq

    def after(self, anchor: Seq | None = None, *, key: Hashable | None = None) -> Seq:
        """기준 블록 바로 뒤에 올 블록의 seq를 돌려준다.

        기준 블록의 seq에서 마지막 성분을 뺀 앞 성분을 그대로 쓰고, 그 앞 성분으로 지금까지
        발급한 after 최댓값에 1을 더해 마지막 성분으로 쓴다.

        Args:
            anchor: 기준 블록의 seq. ``None``이면 지금까지 발급한 seq 중 가장 큰 것을 쓰고,
                발급한 seq가 없으면 앞 성분을 ``(-1, 0, …)``으로 둔다.
            key: 여러 delta로 이어지는 블록의 식별 키. 같은 키로 다시 부르면 처음 발급한
                seq를 그대로 돌려준다.

        Raises:
            ValueError: ``anchor``의 길이가 ``width + 1``과 다를 때.
        """
        if anchor is not None and len(anchor) != self._width + 1:
            raise ValueError(f"expected an anchor seq of length {self._width + 1}, got {anchor}")
        if key is not None:
            known = self._keyed.get(key)
            if known is not None:
                return known

        base = anchor if anchor is not None else self._last
        prefix = base[:-1] if base is not None else (-1, *(0,) * (self._width - 1))
        number = self._after.get(prefix, 0) + 1
        self._after[prefix] = number
        seq = (*prefix, number)
        self._observe(seq)
        if key is not None:
            self._keyed[key] = seq
        return seq

    def _observe(self, seq: Seq) -> None:
        """발급한 seq가 지금까지의 최댓값보다 크면 최댓값을 갱신한다."""
        if self._last is None or seq > self._last:
            self._last = seq


def finalize(response: HubResponse) -> HubResponse:
    """병합된 응답의 블록을 seq 순서로 늘어놓고 ``index``를 확정한다.

    동작:
        블록을 ``seq`` 사전식 순서로 늘어놓고 ``index``에 0부터 1씩 증가하는 위치를 넣는다.
        :class:`AnnotationBlock`은 ``target_seq``와 같은 seq를 가진 블록이 있으면 그 블록의
        ``index``를 ``target_index``에 넣고, 없으면 ``target_index``를 바꾸지 않는다. 모든
        블록의 ``seq``와 ``target_seq``는 ``None``으로 비우고 ``model_fields_set``에서도
        제거한다. 따라서 ``exclude_unset=True``나 ``exclude_none=True``로 직렬화하면 두 키가
        남지 않는다.

    Args:
        response: :class:`~completion_bridge.merge.StreamMerger`가 만든 병합 결과.

    Returns:
        블록 순서와 ``index``가 확정된 새 응답. ``response``는 바뀌지 않는다.

    Raises:
        MappingError: ``seq``가 없는 블록이 있을 때.
    """
    keyed: list[tuple[Seq, ContentBlock]] = []
    for block in response.content:
        if block.seq is None:
            raise MappingError(f"content block {block.type!r} has no seq")
        keyed.append((block.seq, block))
    keyed.sort(key=lambda pair: pair[0])
    positions = {seq: position for position, (seq, _) in enumerate(keyed)}

    content: list[ContentBlock] = []
    for position, (_, block) in enumerate(keyed):
        update: dict[str, Any] = {"index": position, "seq": None}
        cleared = ["seq"]
        if isinstance(block, AnnotationBlock):
            target = block.target_seq
            if target is not None and target in positions:
                update["target_index"] = positions[target]
            update["target_seq"] = None
            cleared.append("target_seq")
        final = block.model_copy(update=update)
        final.model_fields_set.difference_update(cleared)
        content.append(final)
    return response.model_copy(update={"content": content})
