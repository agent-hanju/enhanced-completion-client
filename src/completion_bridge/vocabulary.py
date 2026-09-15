"""어휘. custom content type의 내림과 올림을 한 객체로 묶는다.

custom field는 결국 custom content type이고, wire에는 실을 수 없다. 벤더가 모르는 ``type``을
보내면 요청이 거절된다. 그래서 text 채널에 태그로 숨겨서 나른다. 네 벤더 API가 모두 text
채널을 가지므로 이 메커니즘은 벤더 독립적이다.

- 내림(lowering): 블록을 요청 text로 되쓴다. 지금 필요하다. 대화를 이어가면 이전 답변의
  블록이 다음 요청에 되실린다.
- 올림(lifting): 응답 text에서 블록을 들어올린다. 태그 파서가 붙는 자리다.

두 방향을 한 객체에 두는 이유는 어휘가 어긋나지 않게 하는 것이다. Java 구현은 이 둘이 다른
패키지에 흩어져 하드코딩으로만 일치했다. 한쪽만 고치면 조용히 왕복이 깨졌다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .blocks import ContentBlock, register_block

if TYPE_CHECKING:
    from .mapper import StreamMapper

__all__ = ["Vocabulary"]


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

        돌려준 리스트에서 :class:`~completion_bridge.blocks.TextBlock`만 wire에 실린다.
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

        ``None``이면 올림 단계를 파이프라인에 넣지 않는다.
        """
        return None

    def __repr__(self) -> str:
        return f"<{self.name or type(self).__name__}>"
