"""허브 모델. 모든 벤더가 이 타입으로 수렴한다.

Anthropic Messages 모양을 따른다. 네 API 중 content 유니온이 가장 표현력 있고, 응답의
``content``를 그대로 다음 요청의 assistant 메시지 ``content``로 넣을 수 있어 응답에서 요청으로
가는 변환이 거의 항등이 되기 때문이다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .blocks import Block, ContentBlock, TextBlock
from .parameters import Hyperparameters

__all__ = ["HubMessage", "HubRequest", "HubResponse", "ToolDefinition", "Usage"]


def _overwrite() -> dict[str, Any]:
    return {"stream": "overwrite"}


class Usage(BaseModel):
    """토큰 사용량. 벤더별 이름 차이는 어댑터가 흡수한다."""

    model_config = ConfigDict(extra="allow")

    input_tokens: int | None = Field(default=None, json_schema_extra=_overwrite())
    output_tokens: int | None = Field(default=None, json_schema_extra=_overwrite())


class HubResponse(BaseModel):
    """벤더 응답의 허브 표현. 델타와 최종 결과가 같은 타입이다.

    스트리밍 중에는 조각만 채워진 델타로 흐르고, :class:`~enhanced_completion.merge.StreamMerger`
    가 그것들을 접어 같은 타입의 최종 결과를 만든다. 소비자가 두 타입을 구분할 필요가 없다.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, json_schema_extra=_overwrite())
    model: str | None = Field(default=None, json_schema_extra=_overwrite())
    role: str | None = Field(default=None, json_schema_extra=_overwrite())
    content: list[Block] = Field(default_factory=list)
    stop_reason: str | None = Field(default=None, json_schema_extra=_overwrite())
    usage: Usage | None = None

    @property
    def text(self) -> str:
        """본문 블록만 이어붙인 문자열. 흔한 소비 경로라 편의로 둔다."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def blocks_of(self, block_type: type[ContentBlock]) -> list[ContentBlock]:
        """주어진 타입의 블록만 순서대로 고른다."""
        return [b for b in self.content if isinstance(b, block_type)]


class HubMessage(BaseModel):
    """요청에 실을 대화 한 턴.

    ``content``가 응답과 같은 블록 리스트다. 응답을 그대로 이력에 넣을 수 있다.
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: list[Block] = Field(default_factory=list)

    @classmethod
    def user(cls, text: str) -> HubMessage:
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def system(cls, text: str) -> HubMessage:
        return cls(role="system", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, text: str | None = None, *, blocks: list[Any] | None = None) -> HubMessage:
        if blocks is None:
            blocks = [TextBlock(text=text or "")]
        return cls(role="assistant", content=blocks)

    @classmethod
    def of_response(cls, response: HubResponse) -> HubMessage:
        """응답을 assistant 메시지로 바꾼다. 블록을 그대로 옮긴다."""
        return cls(role=response.role or "assistant", content=list(response.content))


class ToolDefinition(BaseModel):
    """모델에 노출할 도구.

    공통 함수 도구는 ``name``/``description``/``input_schema``로 표현한다. 서버 내장 도구처럼
    벤더 간 공통 구조가 없는 정의는 :meth:`native`로 원본 wire 객체를 등록한다. native 정의는
    지정한 어댑터에서만 직렬화하며 다른 벤더에서 generic function으로 바꾸지 않는다.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    vendor: str | None = None
    wire: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def native(cls, vendor: str, wire: dict[str, Any]) -> ToolDefinition:
        """특정 벤더에서만 명시적으로 활성화할 내장 도구 정의를 만든다."""
        name = wire.get("name") or wire.get("type") or next(iter(wire), "native")
        return cls(name=str(name), vendor=vendor, wire=dict(wire))

    def native_for(self, vendor: str) -> dict[str, Any] | None:
        if self.vendor == vendor and self.wire:
            return dict(self.wire)
        return None


class HubRequest(BaseModel):
    """벤더 중립 요청.

    ``params``는 벤더 확장까지 담는다. 알 수 없는 이름을 조용히 버리지 않는다. 서버가 새
    필드를 추가해도 이 라이브러리를 다시 배포하지 않기 위해서다.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[HubMessage] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    hyperparameters: Hyperparameters = Field(default_factory=Hyperparameters)
    params: dict[str, Any] = Field(default_factory=dict)

    def parameters_for(self, family: str, *, vendor_name: str | None = None) -> dict[str, Any]:
        """공통 옵션을 대상 API에 투영한 뒤 레거시 호출별 확장으로 덮는다."""
        rendered = self.hyperparameters.for_vendor(family, name=vendor_name)
        rendered.update(self.params)
        return rendered
