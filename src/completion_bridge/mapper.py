"""스트림 변환 단계와 그 합성.

파이프라인의 모든 단계가 :class:`StreamMapper`다. 벤더 chunk를 허브 델타로 바꾸는 어댑터도,
본문 text에서 custom block을 들어올리는 어휘도 같은 모양이다. 합성이 닫혀 있으므로 벤더 축과
어휘 축이 직교한다. 벤더 N개와 어휘 M개를 조합할 때 N×M개의 타입이 아니라 N+M개의 매퍼만
있으면 된다.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

__all__ = ["StreamMapper", "compose", "identity_mapper"]

# 입력은 반공변이다. 더 넓은 델타 타입을 받는 매퍼를 좁은 자리에 끼울 수 있어야 한다.
T = TypeVar("T", contravariant=True)
R = TypeVar("R")


@runtime_checkable
class StreamMapper(Protocol[T, R]):
    """입력 델타 하나를 0개 이상의 출력 델타로 바꾼다.

    반환이 리스트인 이유는 세 경우를 모두 담기 위해서다. 0개는 버퍼링, 1개는 단순 변환,
    N개는 분할이다. ``<cite>`` 태그 하나가 본문 조각과 인용 블록 둘로 갈라지는 경우가 N개다.

    상태를 가져도 된다. 그런 매퍼는 파이프라인마다 새 인스턴스여야 한다.
    """

    def map(self, delta: T) -> list[R]:
        """델타 하나를 변환한다."""
        ...

    def flush(self) -> list[R]:
        """스트림이 끝났을 때 남은 버퍼를 내보낸다."""
        ...


class _Identity:
    """변환 없이 통과시킨다."""

    def map(self, delta: Any) -> list[Any]:
        return [delta]

    def flush(self) -> list[Any]:
        return []


def identity_mapper() -> StreamMapper[Any, Any]:
    return _Identity()


class _Composed:
    """두 매퍼를 하나로 잇는다.

    ``flush``의 순서가 계약이다. ``f``의 잔여 버퍼가 ``g``를 거친 다음에 ``g`` 자신의
    잔여물이 와야 한다. 순진하게 ``f.flush() + g.flush()``로 쓰면 ``f``의 마지막 출력이
    ``g``를 건너뛴다. 스트림 끝에서 닫히지 않은 태그가 정확히 이 경로를 탄다.
    """

    def __init__(self, first: StreamMapper[Any, Any], second: StreamMapper[Any, Any]) -> None:
        self._first = first
        self._second = second

    def map(self, delta: Any) -> list[Any]:
        out: list[Any] = []
        for mid in self._first.map(delta):
            out.extend(self._second.map(mid))
        return out

    def flush(self) -> list[Any]:
        out: list[Any] = []
        for mid in self._first.flush():
            out.extend(self._second.map(mid))
        out.extend(self._second.flush())
        return out


def compose(*mappers: StreamMapper[Any, Any]) -> StreamMapper[Any, Any]:
    """매퍼들을 왼쪽에서 오른쪽 순서로 잇는다.

    ``compose(vendor, cite)``는 벤더 chunk를 허브 델타로 바꾸고 그 델타에서 인용을
    들어올린다. 인자가 없으면 통과 매퍼를 돌려준다.
    """
    if not mappers:
        return _Identity()
    result = mappers[0]
    for nxt in mappers[1:]:
        result = _Composed(result, nxt)
    return result
