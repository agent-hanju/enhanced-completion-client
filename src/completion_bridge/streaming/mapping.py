"""벤더 응답과 사용자 정의 어휘의 스트림 변환 및 순차 합성.

각 변환기는 입력 하나를 출력 목록으로 변환하고 flush로 잔여 출력을 제공한다.
상태를 가진 변환기는 스트림마다 별도 인스턴스를 사용한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

from completion_bridge.models.blocks import ContentBlock, register_block

__all__ = ["StreamMapper", "compose", "identity_mapper", "Vocabulary"]

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
        """입력 델타 하나를 변경 없이 목록으로 반환한다."""
        return [delta]

    def flush(self) -> list[Any]:
        """보관하는 상태가 없으므로 빈 출력 목록을 반환한다."""
        return []


def identity_mapper() -> StreamMapper[Any, Any]:
    """델타를 변경 없이 통과시키는 변환기를 생성한다."""
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
        """첫 변환기의 각 출력을 두 번째 변환기에 순서대로 적용한다."""
        out: list[Any] = []
        for mid in self._first.map(delta):
            out.extend(self._second.map(mid))
        return out

    def flush(self) -> list[Any]:
        """첫 변환기의 잔여 출력을 두 번째에 적용한 뒤 두 번째의 잔여 출력을 추가한다."""
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


class Vocabulary:
    """한 종류의 custom content type을 다루는 규칙 묶음.

    하위 클래스는 :attr:`blocks`를 선언하고 :meth:`lower`를 구현한다. :meth:`lift_mapper`는
    응답 쪽 올림이 필요할 때만 재정의한다.
    """

    blocks: Sequence[type[ContentBlock]] = ()
    """이 어휘가 도입하는 블록 타입. :class:`Bridge`에 등록되면 자동으로 유니온에 들어간다."""

    name: str = ""
    """진단용 이름. 비어 있으면 클래스 이름을 쓴다."""

    def register(self) -> None:
        """블록 타입을 전역 유니온에 등록한다. :class:`Bridge`가 부른다."""
        for cls in self.blocks:
            register_block(cls)

    def lower(self, blocks: Sequence[ContentBlock]) -> list[ContentBlock] | None:
        """이 어휘의 블록을 text로 접어 넣는다.

        블록 하나가 아니라 리스트 전체를 받는 것이 중요하다. 내림은 올림의 역이고, 올림이
        스트림 전체를 보고 ``text``를 ``text + custom block``으로 갈랐으므로 내림도 전체를 보고
        되합쳐야 한다. 인용이 그 예다. 인용 구간의 텍스트는 본문에도 실려 있으므로 블록별로
        내리면 같은 문장이 두 번 나간다.

        돌려준 리스트에서 :class:`~completion_bridge.models.blocks.TextBlock`만 wire에 실린다.
        아무 어휘도 가져가지 않은 블록은 생략된다. 다른 벤더로 옮길 수 없는 추론 블록이 그
        경로로 조용히 빠진다.

        ``None``은 "이 리스트에 내 블록이 없다"는 뜻이고 리스트가 그대로 다음 어휘로 넘어간다.
        """
        _ = blocks
        return None

    def lift_mapper(self) -> StreamMapper[Any, Any] | None:
        """응답 델타에서 블록을 들어올리는 매퍼를 만든다.

        상태를 가지므로 매번 새 인스턴스를 돌려줘야 한다. 태그 파서가 상태 기계라 재사용하면
        앞 스트림의 상태가 남는다.

        블록을 나누지 않는 매퍼는 블록의 ``seq``를 그대로 둔다. 블록 하나를 여러 블록으로 나누는
        매퍼는 지나가는 모든 블록의 ``seq`` 끝에 성분 하나를 추가한다. 나눈 조각은 원래 블록마다
        0, 1, 2, …를, 나누지 않은 블록은 0을 쓴다. 규칙은 ``docs/block_index_and_key_tuple.md``에
        있다.

        ``None``이면 올림 단계를 파이프라인에 넣지 않는다.
        """
        return None

    def __repr__(self) -> str:
        return f"<{self.name or type(self).__name__}>"
