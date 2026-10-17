"""허브 모델. 모든 벤더가 이 타입으로 수렴한다.

Anthropic Messages 모양을 따른다. 네 API 중 content 유니온이 가장 표현력 있고, 응답의
``content``를 그대로 다음 요청의 assistant 메시지 ``content``로 넣을 수 있어 응답에서 요청으로
가는 변환이 거의 항등이 되기 때문이다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from completion_bridge.errors import MappingError
from completion_bridge.models.blocks import AnnotationBlock, Block, ContentBlock, TextBlock
from completion_bridge.models.parameters import Hyperparameters
from completion_bridge.streaming.metadata import STREAM_OVERWRITE
from completion_bridge.streaming.sequence import Seq, rank_seq

__all__ = [
    "HubMessage",
    "HubRequest",
    "HubResponse",
    "TokenCount",
    "TokenizedPrompt",
    "ToolDefinition",
    "Usage",
    "finalize_response",
]


class Usage(BaseModel):
    """토큰 사용량. 벤더별 이름 차이는 어댑터가 흡수한다."""

    model_config = ConfigDict(extra="allow")

    input_tokens: int | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    output_tokens: int | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    native: dict[str, Any] | None = None
    """벤더가 보낸 usage 객체 원본. 캐시·추론 토큰 같은 세부 항목도 그대로 둔다.

    스트림 조각마다 온 원본은 최상위 key 단위로 합친다. 같은 key는 나중 조각의 값이 대신한다.
    Anthropic Messages의 ``message_start``와 ``message_delta``, Gemini의 누적 ``usageMetadata``가
    이렇게 합쳐진다.
    """


class HubResponse(BaseModel):
    """벤더 응답의 허브 표현. 델타와 최종 결과가 같은 타입이다.

    스트리밍 중에는 조각만 채워진 델타로 전달되며,
    :class:`~completion_bridge.streaming.merge.StreamMerger`
    가 그것들을 접어 같은 타입의 최종 결과를 만든다. 소비자가 두 타입을 구분할 필요가 없다.

    단, content block의 식별 필드는 다르다. 델타의 블록은 ``seq``를 갖고 ``index``가 없으며,
    스트림의 ``result``/``partial``은 블록을 ``seq`` 순서로 늘어놓고 ``index``를 0부터 채운 뒤
    ``seq``를 비운다.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    model: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    role: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    content: list[Block] = Field(default_factory=list)
    stop_reason: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    block_reason: str | None = Field(default=None, json_schema_extra=STREAM_OVERWRITE)
    """입력이 거부되어 생성이 시작되지 않은 이유.

    ``stop_reason``과 축이 다르다. 저쪽은 생성이 왜 멈췄는지이고 이쪽은 생성이 시작조차
    못 한 이유다. 값이 있으면 ``content``가 비어 있어도 모델이 할 말이 없었던 것이 아니다.
    """
    usage: Usage | None = None

    @property
    def text(self) -> str:
        """본문 블록만 이어붙인 문자열. 흔한 소비 경로라 편의로 둔다."""
        text_values = []
        for candidate_block in self.content:
            if isinstance(candidate_block, TextBlock):
                text_values.append(candidate_block.text)
        return "".join(text_values)

    def blocks_of[B: ContentBlock](self, block_type: type[B]) -> list[B]:
        """주어진 타입의 블록만 순서대로 고른다."""
        blocks: list[B] = []
        for block in self.content:
            if isinstance(block, block_type):
                blocks.append(block)
        return blocks


class HubMessage(BaseModel):
    """요청에 실을 대화 한 턴.

    ``content``가 응답과 같은 블록 리스트다. 응답을 그대로 이력에 넣을 수 있다.
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: list[Block] = Field(default_factory=list)
    synthetic: bool = False
    """애플리케이션이 작성·편집한 assistant 이력이라는 명시적 선언."""

    @model_validator(mode="after")
    def validate_synthetic_role(self) -> HubMessage:
        """synthetic 선언을 assistant 역할에만 허용하고 잘못된 조합은 거부한다."""
        if self.synthetic and self.role != "assistant":
            raise ValueError("synthetic=True requires role='assistant'")
        return self

    @classmethod
    def user(cls, text: str) -> HubMessage:
        """본문 텍스트를 포함하는 user 메시지를 생성한다."""
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def system(cls, text: str) -> HubMessage:
        """지시문 텍스트를 포함하는 system 메시지를 생성한다."""
        return cls(role="system", content=[TextBlock(text=text)])

    @classmethod
    def assistant(
        cls, text: str | None = None, *, blocks: list[Any] | None = None, synthetic: bool = False
    ) -> HubMessage:
        """지정 블록 또는 텍스트를 포함하는 assistant 메시지를 생성한다."""
        if blocks is None:
            blocks = [TextBlock(text=text or "")]
        return cls(role="assistant", content=blocks, synthetic=synthetic)

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
        """요청 벤더가 일치할 때만 원본 도구 정의의 사본을 반환한다."""
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


class TokenizedPrompt(BaseModel):
    """vLLM 서버가 chat template을 적용해 토큰화한 프롬프트.

    ``text``는 ``/detokenize``가 돌려준 문자열이며 ``<bos>`` 같은 특수 토큰을 포함한다.
    ``token_ids``는 ``/tokenize``가 돌려준 토큰 ID 목록이다.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    token_ids: list[int]


class TokenCount(BaseModel):
    """토큰 수 측정 결과.

    ``input_tokens``는 대상 API의 토큰 수 엔드포인트가 돌려준 입력 토큰 수다.
    ``tokenized``는 ``chat_completions``(vLLM) 어댑터에서만 채워지고 다른 어댑터에서는
    ``None``이다.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    tokenized: TokenizedPrompt | None


def finalize_response(response: HubResponse) -> HubResponse:
    """병합된 응답을 복사해 index·블록 순서·주석 참조를 확정한다.

    Args:
        response: 블록마다 seq가 있는 병합된 허브 응답. 입력은 변경하지 않는다.

    Returns:
        seq 순서의 index를 지정하고 seq·target_seq를 제거한 새 응답.
        주석 대상이 없으면 기존 target_index를 유지한다. 제거한 필드는 model_fields_set에서도
        제외하므로 exclude_unset·exclude_none 직렬화에 나타나지 않는다.

    Raises:
        MappingError: seq가 없는 블록이 있을 때.
    """
    sequences: list[Seq] = []
    for block in response.content:
        if block.seq is None:
            raise MappingError(f"content block {block.type!r} has no seq")
        sequences.append(block.seq)
    ranks = rank_seq(sequences)
    positions: dict[Seq, int] = {}
    for offset in range(len(sequences)):
        positions[sequences[offset]] = ranks[offset]
    order = sorted(range(len(response.content)), key=lambda offset: ranks[offset])
    content: list[ContentBlock] = []
    for offset in order:
        block = response.content[offset]
        update: dict[str, Any] = {"index": ranks[offset], "seq": None}
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
