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

import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SerializeAsAny, model_validator

__all__ = [
    "AnnotationBlock",
    "AudioBlock",
    "Block",
    "Citation",
    "CitationBlock",
    "ContentBlock",
    "DocumentBlock",
    "GroundingBlock",
    "GroundingSource",
    "GroundingSupport",
    "ImageBlock",
    "ServerToolBlock",
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


def _index() -> dict[str, Any]:
    return {STREAM_META_KEY: "index"}


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

    source: str | None = Field(default=None, json_schema_extra=_overwrite())
    """이 블록을 만들어낸 벤더 이름.

    추론 블록처럼 발급 벤더로만 되돌릴 수 있는 블록을 다른 벤더로 내릴 때 떨어뜨리는 판단에
    쓴다. 응답을 JSON/DB에 저장한 뒤에도 같은 벤더 재생이 가능해야 하므로 직렬화에 포함한다.
    """

    native: dict[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())
    """발급 벤더의 원본 블록·Part·Item에서 공통 필드 밖의 정보를 보존한다.

    같은 벤더로 되돌릴 때만 사용한다. 다른 벤더 어댑터는 이 값을 해석하지 않는다.
    """


class Citation(BaseModel):
    """한 text block에 붙는 Anthropic형 인용 정보.

    ``cited_text``는 답변에서 인용 표시가 붙은 문구가 아니라 근거 원문이다. XML-like
    ``<cite>``는 근거 원문을 알 수 없으므로 이 필드를 채우지 않고, 태그가 감싼 답변 문구를
    부모 :class:`TextBlock`의 ``text``로 둔다.

    위치 종류는 벤더마다 다르다. char/page/content-block 위치를 공통 ``source_start``와
    ``source_end``로 정규화하고 원본은 ``native``에 보존한다.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(default="citation", json_schema_extra=_overwrite())
    source: str | None = Field(default=None, json_schema_extra=_overwrite())
    """인용 규약의 소유자. ``messages``는 Anthropic native, ``cite``는 XML 어휘다."""
    id: str = Field(default="", json_schema_extra=_overwrite())
    cited_text: str | None = Field(default=None, json_schema_extra=_overwrite())
    document_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    document_title: str | None = Field(default=None, json_schema_extra=_overwrite())
    source_start: int | None = Field(default=None, json_schema_extra=_overwrite())
    source_end: int | None = Field(default=None, json_schema_extra=_overwrite())
    uri: str | None = Field(default=None, json_schema_extra=_overwrite())
    file_id: str | None = Field(default=None, json_schema_extra=_overwrite())
    encrypted_index: str | None = Field(default=None, json_schema_extra=_overwrite())
    native: dict[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())


class TextBlock(ContentBlock):
    """사용자에게 보이는 본문과 그 본문 블록에 붙는 인용."""

    type: Literal["text"] = "text"
    text: str = ""
    signature: str | None = Field(default=None, json_schema_extra=_overwrite())
    citations: list[Citation] = Field(default_factory=list)


class ThinkingBlock(ContentBlock):
    """추론 블록.

    발급 벤더로만 되돌릴 수 있다. Anthropic은 ``signature``를 원문 그대로 요구하고 OpenAI
    Responses의 reasoning은 암호화되어 발급 응답에 묶인다. 다른 벤더로 내릴 때는 생략한다.
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = Field(default=None, json_schema_extra=_overwrite())
    encrypted_content: str | None = Field(default=None, json_schema_extra=_overwrite())


class ToolUseBlock(ContentBlock):
    """모델이 요청한 도구 호출.

    ``input_json``은 조각으로 도착하므로 이어붙인다. 파싱은 조립이 끝난 뒤에 한다.
    """

    type: Literal["tool_use"] = "tool_use"
    id: str = Field(default="", json_schema_extra=_overwrite())
    name: str = Field(default="", json_schema_extra=_overwrite())
    kind: str = Field(default="function", json_schema_extra=_overwrite())
    input_json: str = ""
    input: Any | None = Field(default=None, json_schema_extra=_overwrite())
    signature: str | None = Field(default=None, json_schema_extra=_overwrite())

    @model_validator(mode="after")
    def parse_complete_input(self) -> ToolUseBlock:
        """완성된 JSON 인수는 원문과 파싱 결과를 함께 제공한다."""
        if self.input is not None or not self.input_json:
            return self
        try:
            parsed = json.loads(self.input_json)
        except json.JSONDecodeError:
            return self
        self.input = parsed
        return self


class ToolResultBlock(ContentBlock):
    """도구 실행 결과. 요청 방향 블록이다.

    ``blocks``가 있는 이유는 Anthropic ``ToolResultBlock.content``가 ``List<ContentBlock>``
    이기 때문이다. 이미지를 돌려주는 도구가 그 경로를 쓴다. 문자열 하나로는 표현할 수 없다.

    ``content``는 평문 결과를 담는 짧은 길이고 ``blocks``와 함께 쓸 수 있다. 벤더가 블록
    리스트를 받지 않으면 어댑터가 ``content``만 싣는다.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = Field(default="", json_schema_extra=_overwrite())
    name: str | None = Field(default=None, json_schema_extra=_overwrite())
    kind: str = Field(default="function", json_schema_extra=_overwrite())
    content: str = ""
    structured_content: Any | None = Field(default=None, json_schema_extra=_overwrite())
    blocks: list[Any] = Field(default_factory=list)
    is_error: bool | None = Field(default=None, json_schema_extra=_overwrite())


class ImageBlock(ContentBlock):
    """이미지. 출처가 셋이고 벤더마다 받는 모양이 다르다.

    - ``data`` + ``media_type``: base64. Anthropic ``source.base64``,
      Gemini ``inlineData``, Chat Completions는 data URL로 감싼다
    - ``url``: Anthropic ``source.url``, Chat Completions ``image_url.url``,
      Responses ``input_image.image_url``
    - ``file_id``: 응답에서 관찰한 원격 참조를 진단용으로 보존한다. 공통 요청에는 사용할 수 없다
    """

    type: Literal["image"] = "image"
    media_type: str | None = Field(default=None, json_schema_extra=_overwrite())
    data: str | None = Field(default=None, json_schema_extra=_overwrite())
    url: str | None = Field(default=None, json_schema_extra=_overwrite())
    file_id: str | None = Field(default=None, json_schema_extra=_overwrite())
    """응답 보존 전용. 요청은 URL 또는 inline ``data``만 지원한다."""
    detail: str | None = Field(default=None, json_schema_extra=_overwrite())
    """Chat Completions ``image_url.detail``. ``auto``/``low``/``high``."""


class AudioBlock(ContentBlock):
    """음성 입력 또는 출력 stream.

    입력은 Chat Completions ``input_audio{data, format}``와 Gemini ``inlineData``를 지원한다.
    Responses에서는 전역 audio/transcript stream을 이 블록으로 수집하지만 현재 요청 content
    유니온에는 오디오가 없어 재생하지 않는다.

    ``format``이 ``media_type``과 따로 있는 이유는 두 API가 ``wav``/``mp3`` 같은 짧은 이름을
    요구하고 Gemini는 ``audio/wav`` 형태의 MIME을 요구하기 때문이다.
    """

    type: Literal["audio"] = "audio"
    data: str | None = None
    uri: str | None = Field(default=None, json_schema_extra=_overwrite())
    format: str | None = Field(default=None, json_schema_extra=_overwrite())
    media_type: str | None = Field(default=None, json_schema_extra=_overwrite())
    file_id: str | None = Field(default=None, json_schema_extra=_overwrite())
    """응답 보존 전용. 요청은 URL 또는 inline ``data``만 지원한다."""
    transcript: str | None = None
    expires_at: int | None = Field(default=None, json_schema_extra=_overwrite())


class CitationBlock(ContentBlock):
    """구 버전 저장 데이터와 API 호환을 위한 독립 인용 블록.

    새 응답 매퍼는 이 타입을 만들지 않는다. XML/Anthropic 인용은 ``TextBlock.citations``로,
    범위 annotation은 :class:`AnnotationBlock`으로 저장한다. 기존 JSON과 호출자 코드를 한
    릴리스에서 깨뜨리지 않기 위해 읽기와 요청 내림만 유지한다.
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


class AnnotationBlock(ContentBlock):
    """출력 text의 문자 범위를 가리키는 annotation.

    OpenAI Chat/Responses의 ``annotations``와 Gemini ``citationMetadata``가 이 계열이다.
    text보다 늦게 도착할 수 있으므로 text block을 소급 분할하지 않고 독립 블록으로 둔다.
    """

    type: Literal["annotation"] = "annotation"
    annotation_index: int | None = Field(default=None, json_schema_extra=_index())
    target_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    kind: str = Field(default="annotation", json_schema_extra=_overwrite())
    id: str = Field(default="", json_schema_extra=_overwrite())
    text: str | None = Field(default=None, json_schema_extra=_overwrite())
    start_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    end_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    title: str | None = Field(default=None, json_schema_extra=_overwrite())
    uri: str | None = Field(default=None, json_schema_extra=_overwrite())
    file_id: str | None = Field(default=None, json_schema_extra=_overwrite())


class GroundingSource(BaseModel):
    """Gemini grounding chunk의 정규화된 출처."""

    model_config = ConfigDict(extra="allow")

    index: int = Field(json_schema_extra=_index())
    kind: str = Field(default="unknown", json_schema_extra=_overwrite())
    uri: str | None = Field(default=None, json_schema_extra=_overwrite())
    title: str | None = Field(default=None, json_schema_extra=_overwrite())
    native: dict[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())


class GroundingSupport(BaseModel):
    """생성 답변 구간과 하나 이상의 grounding source를 잇는 관계."""

    model_config = ConfigDict(extra="allow")

    index: int = Field(json_schema_extra=_index())
    text: str | None = Field(default=None, json_schema_extra=_overwrite())
    start_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    end_index: int | None = Field(default=None, json_schema_extra=_overwrite())
    source_indices: list[int] = Field(default_factory=list, json_schema_extra=_overwrite())
    confidence_scores: list[float] = Field(default_factory=list, json_schema_extra=_overwrite())
    native: dict[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())


class GroundingBlock(ContentBlock):
    """Gemini candidate의 grounding graph.

    ``groundingSupports``는 한 답변 구간을 여러 ``groundingChunks``에 연결하므로 단일 인용
    목록으로 평탄화하지 않는다. 검색 UI와 질의도 같은 응답 메타데이터로 보존한다.
    """

    type: Literal["grounding"] = "grounding"
    candidate_index: int = Field(default=0, json_schema_extra=_index())
    sources: list[GroundingSource] = Field(default_factory=list)
    supports: list[GroundingSupport] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list, json_schema_extra=_overwrite())
    search_entry_point: dict[str, Any] | None = Field(
        default=None, json_schema_extra=_overwrite()
    )
    retrieval_metadata: dict[str, Any] | None = Field(
        default=None, json_schema_extra=_overwrite()
    )


class DocumentBlock(ContentBlock):
    """요청에 첨부하는 근거 문서 또는 파일.

    출처를 세 가지로 표현한다. 벤더가 지원하는 모양이 다르기 때문이다.

    - ``text``: 평문. Anthropic ``source.type=text``, 나머지는 본문 태그
    - ``data`` + ``media_type``: base64. Anthropic ``base64``, Gemini ``inlineData``,
      Responses ``file_data``
    - ``uri``: 대상 API가 받는 URL
    - ``file_id``: 응답에서 관찰한 원격 참조를 진단용으로 보존하며 요청에는 쓰지 않는다

    네이티브 문서 채널은 네 주요 API에 있다. 최신 Chat Completions도 ``file`` part를 받는다.
    다만 평문 문서 source를 직접 받지 않는 대상에서는 본문 태그로 내린다.
    """

    type: Literal["document"] = "document"
    id: str = Field(default="", json_schema_extra=_overwrite())
    title: str | None = Field(default=None, json_schema_extra=_overwrite())
    text: str = ""
    data: str | None = Field(default=None, json_schema_extra=_overwrite())
    uri: str | None = Field(default=None, json_schema_extra=_overwrite())
    file_id: str | None = Field(default=None, json_schema_extra=_overwrite())
    """응답 보존 전용. 요청은 URL 또는 inline ``data``만 지원한다."""
    media_type: str = Field(default="text/plain", json_schema_extra=_overwrite())
    citations_enabled: bool = Field(default=True, json_schema_extra=_overwrite())

    @property
    def is_inline_text(self) -> bool:
        """평문 본문만 있는지. 참이면 본문 태그로 내려도 무손실이다."""
        return not self.data and not self.uri and not self.file_id

    def to_prompt(self) -> str:
        """네이티브 문서 채널이 없는 벤더에서 본문에 실을 형태.

        Java ``IDocument.toSerializedPrompt()``와 같은 모양이다. 평문이 아닌 문서는 여기로
        내릴 수 없으므로 ``media_type``과 참조만 남긴다.
        """
        parts = [f'<document id="{self.id}">']
        if self.title:
            parts.append(f"<title>{self.title}</title>")
        if self.is_inline_text:
            parts.append(f"<content>{self.text}</content>")
        else:
            reference = self.uri or self.file_id or ""
            parts.append(f'<content media-type="{self.media_type}">{reference}</content>')
        parts.append("</document>")
        return "\n".join(parts)


class ServerToolBlock(ContentBlock):
    """벤더 서버가 실행한 도구의 호출과 결과.

    ``tool_use``/``tool_result``와 별개 개념이 아니다. Anthropic이 그 결론을 타입 계층으로
    적어두었다. ``ServerToolUseBlock extends ToolUseBlock``이고
    ``McpToolResultBlock extends ToolResultBlock``이다. 다른 것은 실행 주체뿐이다.

    일반적으로 클라이언트가 결과를 되보낼 필요가 없다. 클라이언트 실행이나 승인이 필요한
    호출은 ``ToolUseBlock``으로 분류하고 이 블록과 구분한다.

    Responses, Anthropic, Gemini와 agent SSE가 이 개념을 갖는다.

    - Anthropic: ``server_tool_use``, ``web_search_tool_result``, ``web_fetch_tool_result``,
      ``mcp_tool_use``, ``mcp_tool_result``, ``code_execution_tool_result``,
      ``bash_code_execution_tool_result``, ``text_editor_code_execution_tool_result``,
      ``tool_search_tool_result``
    - Responses: ``web_search_call``, ``code_interpreter_call``, ``image_generation_call``,
      ``mcp_call``, ``mcp_list_tools``. ``mcp_approval_request``는 클라이언트 응답이 필요하므로
      ``ToolUseBlock``이다
    - Gemini: ``executableCode``, ``codeExecutionResult``. ``groundingMetadata``는 관계형
      :class:`GroundingBlock`으로 분리한다
    - agent-studio: ``bash``, ``edit``, ``read``, ``write``, ``web_search``, ``skill_run`` 등
    - chat completions: 없음
    """

    type: Literal["server_tool"] = "server_tool"
    id: str = Field(default="", json_schema_extra=_overwrite())
    name: str = Field(default="", json_schema_extra=_overwrite())
    status: str | None = Field(default=None, json_schema_extra=_overwrite())
    input_json: str = ""
    output: str = ""
    is_error: bool | None = Field(default=None, json_schema_extra=_overwrite())
    raw: Mapping[str, Any] = Field(default_factory=dict, json_schema_extra=_overwrite())


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
        known = {"type", "index", "source", "native", "raw"}
        raw = value.get("raw")
        if not isinstance(raw, dict):
            raw = {k: v for k, v in value.items() if k not in known}
        native = value.get("native")
        if not isinstance(native, dict):
            native = {}
        return VendorBlock(
            type=tag,
            index=value.get("index"),
            source=value.get("source"),
            native=native,
            raw=raw,
        )
    return cls.model_validate(value)


Block = Annotated[SerializeAsAny[ContentBlock], BeforeValidator(resolve_block)]
"""허브 모델에서 content block 필드에 쓰는 타입."""


for _cls in (
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    AudioBlock,
    CitationBlock,
    AnnotationBlock,
    GroundingBlock,
    DocumentBlock,
    ServerToolBlock,
):
    register_block(_cls)
