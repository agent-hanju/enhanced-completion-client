"""벤더 어댑터 경계.

전송 계층은 하나로 둔다. SSE 규약이 같기 때문이다. 갈라지는 것은 요청 body 모양과 응답 chunk
해석이다. 이 둘만 어댑터로 분리한다.

``decode``와 ``is_terminal``이 ``data`` 문자열이 아니라 프레임 전체를 받는 것이 설계의 핵심이다.
API마다 이벤트 이름과 종료 표지를 서로 다른 SSE 필드에 실을 수 있다. ``data``만 넘기면
어댑터가 그 차이를 판별할 수 없다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..hub import HubRequest
from ..mapper import StreamMapper
from ..transport.sse import SseFrame

__all__ = ["Lowerer", "VendorAdapter"]


@runtime_checkable
class Lowerer(Protocol):
    """블록을 요청 text로 되쓰는 경계. :class:`Bridge`가 어휘들을 모아 구현한다."""

    def lower_text(self, blocks: Any) -> str:
        """블록 리스트를 하나의 text로 만든다."""
        ...

    def lower_text_parts(self, blocks: Any) -> list[str]:
        """블록 경계를 유지한 요청 text part 목록을 만든다."""
        ...


@runtime_checkable
class VendorAdapter(Protocol):
    """한 벤더 API에 대한 요청 조립과 응답 해석."""

    name: str
    """진단과 블록 ``source`` 태그에 쓰는 이름."""

    parameter_family: str
    """공통 Hyperparameters를 투영할 표준 API 계열 이름."""

    path: str
    """base_url에 붙일 경로."""

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 이 벤더의 wire body로 만든다.

        ``request.hyperparameters``의 공통 필드는 이 어댑터 계열에 맞게 투영되고, 벤더별
        섹션만 선택된다. ``request.params``는 구 버전 호출 호환을 위한 마지막 덮어쓰기다.
        """
        ...

    def is_terminal(self, frame: SseFrame) -> bool:
        """이 프레임이 스트림의 끝을 알리는지."""
        ...

    def decode(self, frame: SseFrame) -> Any | None:
        """프레임 하나를 벤더 chunk 객체로. ``None``이면 무시한다."""
        ...

    def to_hub(self) -> StreamMapper[Any, Any]:
        """벤더 chunk를 허브 델타로 바꾸는 매퍼. 상태를 가지므로 매번 새 인스턴스."""
        ...
