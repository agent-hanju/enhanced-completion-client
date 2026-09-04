"""Content block 유니온과 런타임 레지스트리.

허브 응답의 ``content``는 타입 태그가 붙은 블록의 리스트다. 네 벤더 API가 모두 이 모양을
쓴다. Anthropic은 ``ContentBlock``, OpenAI Responses는 ``Item``/``ContentPart``, Gemini는
``Part``다.

Pydantic의 판별 유니온은 두 방식 모두 정의 시점에 닫힌다. 이 모듈은 레지스트리와
``BeforeValidator``로 런타임 개방형 유니온을 만든다. 등록되지 않은 ``type``은 예외가 아니라
:class:`VendorBlock`으로 떨어져 원본 페이로드를 보존한다. 벤더가 새 블록을 추가해도 스트림
전체가 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

__all__ = [
    "Block",
    "CitationBlock",
    "ContentBlock",
    "DocumentBlock",
    "ImageBlock",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "VendorBlock",
    "register_block",
    "registered_blocks",
    "resolve_block",
]

# 병합 전략을 필드 메타로 선언할 때 쓰는 키.
STREAM_META_KEY = "stream"
OVERWRITE = "overwrite"


def _overwrite() -> dict[str, Any]:
    return {STREAM_META_KEY: OVERWRITE}


class ContentBlock(BaseModel):
    """모든 content block의 기반 타입.

    ``extra="allow"``인 이유는 벤더가 선언되지 않은 필드를 붙여 보내도 잃지 않기 위해서다.
    같은 벤더로 되돌릴 때의 무손실 왕복이 이 설정에 달려 있다.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str = Field(json_schema_extra=_overwrite())
    """블록 판별자. 리스트 짝짓기와 레지스트리 조회에 쓴다."""

    index: int | None = Field(default=None, json_schema_extra=_overwrite())
    """스트리밍 중 같은 블록의 조각을 짝지을 키."""

    source: str | None = Field(default=None, exclude=True, json_schema_extra=_overwrite())
    """이 블록을 만들어낸 벤더 이름.

    추론 블록처럼 발급 벤더로만 되돌릴 수 있는 블록을 다른 벤더로 내릴 때 떨어뜨리는 판단에
    쓴다. ``exclude=True``이므로 ``model_dump()``에는 실리지 않는다.
    """


class TextBlock(ContentBlock):
    """사용자에게 보이는 본문."""

    type: Literal["text"] = "text"
    text: str = ""


class ThinkingBlock(ContentBlock):
    """추론 블록.

    발급 벤더로만 되돌릴 수 있다. Anthropic은 ``signature``를 원문 그대로 요구하고 OpenAI
    Responses의 reasoning은 암호화되어 발급 응답에 묶인다. 다른 벤더로 내릴 때는 생략한다.
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = Field(default=None, json_schema_extra=_overwrite())


class ToolUseBlock(ContentBlock):
    """모델이 요청한 도구 호출.

    ``input_json``은 조각으로 도착하므로 이어붙인다. 파싱은 조립이 끝난 뒤에 한다.
    """

    type: Literal["tool_use"] = "tool_use"
    id: str = Field(default="", json_schema_extra=_overwrite())
    name: str = Field(default="", json_schema_extra=_overwrite())
    input_json: str = ""


class ToolResultBlock(ContentBlock):
    """도구 실행 결과."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = Field(default="", json_schema_extra=_overwrite())
    content: str = ""
    is_error: bool | None = Field(default=None, json_schema_extra=_overwrite())


class ImageBlock(ContentBlock):
    """이미지. ``data``는 base64이고 ``url``과 배타적으로 쓴다."""

    type: Literal["image"] = "image"
    media_type: str | None = Field(default=None, json_schema_extra=_overwrite())
    data: str | None = Field(default=None, json_schema_extra=_overwrite())
    url: str | None = Field(default=None, json_schema_extra=_overwrite())


class CitationBlock(ContentBlock):
    """본문 한 구간의 근거.

    허브 블록이다. 어휘 소속이 아니다. Anthropic Messages가 인용을 네이티브 구조 채널로
    제공하기 때문이다. 문서 블록에 ``citations``를 켜면 응답의 text 블록이 ``citations``
    배열을 들고 온다. beta 헤더도 필요 없다.

    그래서 같은 블록에 도달하는 경로가 둘이다. vLLM은 본문 태그를 올려서, Anthropic은
    네이티브 채널로 온다. 어휘가 태그를 다루고 어댑터가 네이티브를 다루지만 도착지는 같다.

    ``index``를 주지 않는다. 인용은 조각으로 도착하지 않고 한 번에 완성되므로 병합기가 짝지을
    키가 필요 없다. 키가 없는 원소는 도착 순서대로 덧붙는다.

    답변 안의 위치와 원문 안의 위치가 다른 축이다. ``start_index``/``end_index``는 답변
    문자열의 구간이고, ``source_*``는 근거 문서 안의 구간이다. 태그 경로는 앞쪽만, 네이티브
    경로는 뒤쪽만 채울 수 있다.
    """

    type: Literal["citation"] = "citation"
    id: str = Field(default="", json_schema_extra=_overwrite())
    text: str = ""
    start_index: int = Field(default=0, json_schema_extra=_overwrite())
    end_index: int = Field(default=0, json_schema_extra=_overwrite())

    document_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    document_title: str | None = Field(default=None, json_schema_extra=_overwrite())
    source_start: int | None = Field(default=None, json_schema_extra=_overwrite())
    source_end: int | None = Field(default=None, json_schema_extra=_overwrite())
    source_kind: str | None = Field(default=None, json_schema_extra=_overwrite())


class DocumentBlock(ContentBlock):
    """요청에 첨부하는 근거 문서.

    벤더마다 실리는 자리가 다르다. Anthropic Messages는 ``document`` content block으로 받고
    ``citations``를 켤 수 있다. 나머지 벤더는 그 채널이 없으므로 어휘가 본문 text에 태그로
    내린다. 어느 쪽이든 이 블록 하나로 표현한다.
    """

    type: Literal["document"] = "document"
    id: str = Field(default="", json_schema_extra=_overwrite())
    title: str | None = Field(default=None, json_schema_extra=_overwrite())
    text: str = ""
    media_type: str = Field(default="text/plain", json_schema_extra=_overwrite())
    citations_enabled: bool = Field(default=True, json_schema_extra=_overwrite())

    def to_prompt(self) -> str:
        """네이티브 문서 채널이 없는 벤더에서 본문에 실을 형태.

        Java ``IDocument.toSerializedPrompt()``와 같은 모양을 유지한다.
        """
        parts = [f'<document id="{self.id}">']
        if self.title:
            parts.append(f"<title>{self.title}</title>")
        parts.append(f"<content>{self.text}</content>")
        parts.append("</document>")
        return "\n".join(parts)


class VendorBlock(ContentBlock):
    """등록되지 않은 벤더 고유 블록.

    Responses의 ``web_search_call``, Gemini의 ``executableCode``, agent SSE의 ``activity``
    처럼 허브에 대응물이 없는 블록이 여기로 떨어진다. ``raw``에 원본을 그대로 들고 있으므로
    같은 벤더로 되돌릴 때는 손실이 없고, 다른 벤더로 내릴 때는 생략된다.
    """

    type: str = Field(json_schema_extra=_overwrite())
    raw: Mapping[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())


_REGISTRY: dict[str, type[ContentBlock]] = {}


def register_block(cls: type[ContentBlock]) -> type[ContentBlock]:
    """블록 타입을 유니온에 등록한다. 데코레이터로도 쓸 수 있다.

    ``type`` 필드의 기본값을 판별자로 쓴다. 기본값이 없으면 등록할 수 없다. 어떤 문자열에
    반응해야 할지 알 수 없기 때문이다.
    """
    field = cls.model_fields.get("type")
    tag = getattr(field, "default", None)
    if not isinstance(tag, str) or not tag:
        raise ValueError(f"{cls.__name__} must give field 'type' a non-empty string default")
    _REGISTRY[tag] = cls
    return cls


def registered_blocks() -> dict[str, type[ContentBlock]]:
    """현재 등록된 판별자와 클래스의 사본."""
    return dict(_REGISTRY)


def resolve_block(value: Any) -> Any:
    """``type``으로 구현 클래스를 골라 검증한다.

    이미 :class:`ContentBlock` 인스턴스면 그대로 통과시킨다. 매퍼가 만든 델타를 다시 검증하며
    필드를 잃지 않기 위해서다.
    """
    if isinstance(value, ContentBlock):
        return value
    if not isinstance(value, dict):
        return value

    tag = value.get("type")
    if not isinstance(tag, str):
        return value

    cls = _REGISTRY.get(tag)
    if cls is None:
        # 알 수 없는 타입은 버리지 않고 원본째로 보존한다.
        known = {"type", "index", "source", "raw"}
        raw = value.get("raw")
        if not isinstance(raw, dict):
            raw = {k: v for k, v in value.items() if k not in known}
        return VendorBlock(
            type=tag,
            index=value.get("index"),
            source=value.get("source"),
            raw=raw,
        )
    return cls.model_validate(value)


Block = Annotated[ContentBlock, BeforeValidator(resolve_block)]
"""허브 모델에서 content block 필드에 쓰는 타입."""


for _cls in (
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    CitationBlock,
    DocumentBlock,
):
    register_block(_cls)
