"""벤더별 요청 body 모양과 응답 chunk를 관리하는 어댑터"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..hub import HubRequest
from ..mapper import StreamMapper
from ..transport.sse import SseEvent

__all__ = ["Lowerer", "VendorAdapter"]


@runtime_checkable
class Lowerer(Protocol):
    """블록을 요청 text로 되쓰는 경계. :class:`Bridge`가 어휘들을 모아 구현한다."""

    def lower_text(self, blocks: Any) -> str:
        """블록 리스트를 하나의 text로 만든다.

        part 목록을 받지 못하는 자리가 쓴다. system 지시문, Responses의
        ``EasyInputMessageParam``과 ``function_call_output.output``이 그렇다.
        """
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
        """허브 요청을 이 벤더 API의 body로 만든다.

        ``request.hyperparameters``의 공통 필드는 이 어댑터 계열에 맞게 투영되고, 평평한 API
        고유 필드 중 지원하는 값만 선택된다. ``request.params``는 구 버전 호출 호환을 위한
        마지막 덮어쓰기다.
        """
        ...

    def is_terminal(self, event: SseEvent) -> bool:
        """이 이벤트가 스트림의 끝인지를 확인한다."""
        ...

    def decode(self, event: SseEvent) -> Any | None:
        """이벤트 하나를 벤더 chunk 객체로. ``None``이면 무시한다."""
        ...

    def to_hub(self) -> StreamMapper[Any, Any]:
        """벤더 chunk를 허브 델타로 바꾸는 매퍼. 상태를 가지므로 매번 새 인스턴스.

        ``map`` 외에 ``flush``가 필요해서 iterator가 아니다. 태그 파서가 청크 경계에 걸린
        잔여를 배출하는 시점을 ``compose``가 순서대로 제어해야 한다.
        """
        ...
