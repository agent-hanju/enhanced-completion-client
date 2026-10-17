"""벤더별 요청 body 모양과 응답 chunk를 관리하는 어댑터"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from completion_bridge.http.sse import SseEvent
from completion_bridge.models.hub import HubRequest, TokenCount
from completion_bridge.streaming.mapping import StreamMapper

__all__ = ["JsonCall", "Lowerer", "NativeAccumulator", "VendorAdapter"]

NativeResponseT = TypeVar("NativeResponseT", bound=BaseModel, covariant=True)


@dataclass(frozen=True)
class JsonCall:
    """토큰 수 측정 중 보낼 JSON POST 요청 한 건."""

    path: str
    """base_url에 붙일 경로."""

    body: dict[str, Any]
    """전송할 JSON body."""


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
class NativeAccumulator(Protocol[NativeResponseT]):
    """스트림 chunk를 같은 API의 비스트리밍 응답 형식 객체 하나로 누적한다.

    로깅과 독립적으로 사용할 수 있다. 상태를 가지므로 스트림마다 새 인스턴스를 쓴다.
    받은 chunk 객체는 누적기가 소유하므로 호출자는 넘긴 뒤 그 객체를 다시 쓰지 않는다.
    """

    def apply(self, chunk: Any) -> None:
        """:meth:`VendorAdapter.decode`가 돌려준 chunk 하나를 누적한다.

        Raises:
            MappingError: chunk가 이 API의 스트림 규약으로 누적할 수 없는 모양일 때.
            pydantic.ValidationError: 응답 모델에 선언된 필드의 타입이 맞지 않을 때.
        """
        ...

    def build(self) -> NativeResponseT:
        """지금까지 누적한 응답 객체를 돌려준다. 스트림이 정상 종료된 뒤 한 번 호출한다.

        Raises:
            MappingError: 누적한 내용으로 응답 객체를 완성할 수 없을 때.
        """
        ...


@runtime_checkable
class VendorAdapter(Protocol):
    """한 벤더 API에 대한 요청 조립과 응답 해석."""

    name: str
    """진단과 블록 ``vendor`` 필드에 쓰는 이름."""

    parameter_family: str
    """공통 Hyperparameters를 투영할 표준 API 계열 이름."""

    @property
    def path(self) -> str:
        """base_url에 붙일 경로. 읽기 전용 구현도 허용한다."""
        ...

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 이 벤더 API의 body로 만든다.

        ``request.hyperparameters``의 공통 필드는 이 어댑터 계열에 맞게 투영되고, 평평한 API
        고유 필드 중 지원하는 값만 선택된다. ``request.params``는 구 버전 호출 호환을 위한
        마지막 덮어쓰기다.
        """
        ...

    def is_terminal(self, frame: SseEvent) -> bool:
        """이 프레임이 스트림의 끝인지를 확인한다."""
        ...

    def decode(self, frame: SseEvent) -> Any | None:
        """프레임 하나를 벤더 chunk 객체로. ``None``이면 무시한다."""
        ...

    def to_hub(self) -> StreamMapper[Any, Any]:
        """벤더 chunk를 허브 델타로 바꾸는 매퍼. 상태를 가지므로 매번 새 인스턴스.

        ``map`` 외에 ``flush``가 필요해서 iterator가 아니다. 태그 파서가 청크 경계에 걸린
        잔여를 배출하는 시점을 ``compose``가 순서대로 제어해야 한다.

        매퍼가 내보내는 모든 content block에는 ``seq``가 있어야 한다. 같은 블록의 조각은 같은
        seq를, 다른 블록은 다른 seq를 쓴다. seq가 없는 블록은 최종 결과를 만들 때
        :class:`~completion_bridge.errors.MappingError`가 된다. ``index``는 채우지 않는다.
        규칙은 ``docs/block_index_and_key_tuple.md``에 있다.
        """
        ...

    def to_native(self) -> NativeAccumulator[BaseModel]:
        """벤더 chunk를 이 API의 비스트리밍 응답 형식으로 누적하는 객체. 매번 새 인스턴스."""
        ...

    def token_count_calls(
        self, body: dict[str, Any]
    ) -> Generator[JsonCall, dict[str, Any], TokenCount]:
        """:meth:`build_body`가 만든 생성 body로 입력 토큰 수를 재는 요청 순서.

        보낼 요청을 :class:`JsonCall`로 ``yield``한다. 호출자는 그 요청을 보내고 응답 JSON
        객체를 ``send``로 돌려준다. 요청이 끝나면
        :class:`~completion_bridge.models.hub.TokenCount`를
        반환한다. 이 메서드는 I/O를 하지 않는다.
        """
        ...
