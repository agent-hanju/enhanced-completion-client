"""허브 블록을 각 벤더의 요청 part 모양으로 바꾸기 위한 렌더러
도구 결과의 경우 block과 part가 대응되지 않을 수 있기에 각 어댑터의 ``build_body``가 다룬다.
벤더가 발급한 ``file_id``/container 참조는 사용할 수 없다. URL 또는 inline base64만 직렬화한다.

| 허브 | Anthropic | Chat Completions | Responses | Gemini |
|---|---|---|---|---|
| 이미지 | ``image.source`` | ``image_url`` | ``input_image`` | ``inlineData`` |
| 음성 | 없음 | ``input_audio`` | 없음 | ``inlineData`` |
| 문서 | ``document`` | ``file`` | ``input_file`` | ``inlineData``/``fileData`` |
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..blocks import (
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    VendorBlock,
)

__all__ = [
    "as_anthropic_part",
    "as_chat_completions_part",
    "as_gemini_part",
    "as_responses_part",
    "data_url",
    "has_opaque_media_reference",
]


def data_url(media_type: str | None, data: str) -> str:
    """base64를 data URL로 감싼다.

    Chat Completions는 base64 이미지를 별도 필드로 받지 않고 ``image_url.url``에 data URL로
    받는다. 다른 셋은 base64 필드를 따로 둔다.
    """
    return f"data:{media_type or 'application/octet-stream'};base64,{data}"


def has_opaque_media_reference(block: ContentBlock) -> bool:
    """멀티모달 content block이 서버 발급 ID 혹은 vendor URI로 지시되는지 검사한다.(지원 불가능)"""
    if isinstance(block, ImageBlock | AudioBlock | DocumentBlock) and block.file_id:
        return True
    reference = None
    if isinstance(block, ImageBlock):
        reference = block.url
    elif isinstance(block, AudioBlock | DocumentBlock):
        reference = block.uri
    return bool(reference and urlsplit(reference).scheme.lower() not in {"http", "https", "data"})


def _native(block: ContentBlock, source: str) -> dict[str, Any]:
    if block.source != source:
        return {}
    return dict(block.native)


def _raw(block: ServerToolBlock | VendorBlock, source: str) -> dict[str, Any] | None:
    if block.source != source or not block.raw:
        return None
    return dict(block.raw)


def as_anthropic_part(block: ContentBlock) -> dict[str, Any] | None:
    """Anthropic ``ContentBlock``. 요청과 응답이 같은 유니온을 쓴다."""
    if getattr(block, "serialize", False):
        # 호출자가 직렬화를 선택했다. 네이티브 채널이 있어도 쓰지 않는다.
        return None
    if isinstance(block, TextBlock) and block.source == "messages":
        part = _native(block, "messages")
        part.update({"type": "text", "text": block.text})
        citations = [dict(citation.native) for citation in block.citations if citation.native]
        if citations:
            part["citations"] = citations
        return part
    if isinstance(block, ThinkingBlock):
        if block.source != "messages" or not block.signature:
            return None
        part = _native(block, "messages")
        part.update({"type": "thinking", "thinking": block.thinking, "signature": block.signature})
        return part
    if isinstance(block, ToolUseBlock):
        part = _native(block, "messages")
        part.update(
            {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input if block.input is not None else {},
            }
        )
        return part
    if isinstance(block, ServerToolBlock | VendorBlock):
        return _raw(block, "messages")
    if isinstance(block, ImageBlock):
        source = _anthropic_source(block.media_type, block.data, block.url)
        if source is None:
            return None
        part = _native(block, "messages")
        part.update({"type": "image", "source": source})
        return part
    if isinstance(block, DocumentBlock):
        source = _anthropic_source(block.media_type, block.data, block.uri, text=block.text)
        if source is None:
            return None
        part = _native(block, "messages")
        part.update({"type": "document", "source": source})
        title = block.title or block.id
        if title:
            part["title"] = title
        return part
    # 음성 content block이 없다. 이 API는 오디오 입력을 받지 않는다.
    return None


def _anthropic_source(
    media_type: str | None,
    data: str | None,
    url: str | None,
    *,
    text: str | None = None,
) -> dict[str, Any] | None:
    if data:
        return {
            "type": "base64",
            "media_type": media_type or "application/octet-stream",
            "data": data,
        }
    if url:
        return {"type": "url", "url": url}
    if text:
        return {"type": "text", "media_type": media_type or "text/plain", "data": text}
    return None


def as_chat_completions_part(block: ContentBlock) -> dict[str, Any] | None:
    """vLLM OpenAI compatible Chat Completions 요청 part."""
    if getattr(block, "serialize", False):
        # 호출자가 직렬화를 선택했다. 네이티브 채널이 있어도 쓰지 않는다.
        return None
    if isinstance(block, ImageBlock):
        url = block.url or (data_url(block.media_type, block.data) if block.data else None)
        if url:
            image: dict[str, Any] = {"url": url}
            if block.detail:
                image["detail"] = block.detail
            return {"type": "image_url", "image_url": image}
        return None
    if isinstance(block, AudioBlock):
        if block.data:
            return {
                "type": "input_audio",
                "input_audio": {"data": block.data, "format": block.format or "wav"},
            }
        return None
    if isinstance(block, DocumentBlock):
        if block.data:
            payload: dict[str, Any] = {"file_data": data_url(block.media_type, block.data)}
            if block.title or block.id:
                payload["filename"] = block.title or block.id
            return {"type": "file", "file": payload}
        return None
    return None


def as_responses_part(block: ContentBlock) -> dict[str, Any] | None:
    """OpenAI Responses 요청 ``ContentPart``.

    ``ResponseInputMessageContentListParam`` 유니온은 text/image/file만 허용한다.
    """
    if getattr(block, "serialize", False):
        # 호출자가 직렬화를 선택했다. 네이티브 채널이 있어도 쓰지 않는다.
        return None
    if isinstance(block, ImageBlock):
        url = block.url or (data_url(block.media_type, block.data) if block.data else None)
        if url:
            return {"type": "input_image", "image_url": url}
        return None
    if isinstance(block, DocumentBlock):
        part: dict[str, Any] = {"type": "input_file"}
        if block.data:
            part["file_data"] = data_url(block.media_type, block.data)
        elif block.uri:
            part["file_url"] = block.uri
        else:
            return None
        if block.title or block.id:
            part["filename"] = block.title or block.id
        return part
    return None


def as_gemini_part(block: ContentBlock) -> dict[str, Any] | None:
    """Gemini ``Part``는 type 없이 채워진 필드가 종류를 말한다."""
    if getattr(block, "serialize", False):
        # 호출자가 직렬화를 선택했다. 네이티브 채널이 있어도 쓰지 않는다.
        return None
    if (
        isinstance(block, TextBlock)
        and block.source == "generate_content"
        and (block.signature or block.native)
    ):
        part = _native(block, "generate_content")
        part["text"] = block.text
        if block.signature:
            part["thoughtSignature"] = block.signature
        return part
    if isinstance(block, ThinkingBlock):
        if block.source != "generate_content":
            return None
        part = _native(block, "generate_content")
        part.update({"text": block.thinking, "thought": True})
        if block.signature:
            part["thoughtSignature"] = block.signature
        return part
    if isinstance(block, ToolUseBlock):
        call: dict[str, Any] = {"name": block.name, "args": block.input or {}}
        if block.id:
            call["id"] = block.id
        part = _native(block, "generate_content")
        part["functionCall"] = call
        if block.source == "generate_content" and block.signature:
            part["thoughtSignature"] = block.signature
        return part
    if isinstance(block, ServerToolBlock):
        if block.source != "generate_content":
            return None
        return dict(block.native or block.raw) or None
    if isinstance(block, VendorBlock):
        return _raw(block, "generate_content")
    if isinstance(block, ImageBlock):
        part = _native(block, "generate_content")
        if block.data:
            part["inlineData"] = {
                "mimeType": block.media_type or "image/png",
                "data": block.data,
            }
            return part
        if block.url:
            part["fileData"] = {
                "mimeType": block.media_type or "image/png",
                "fileUri": block.url,
            }
            return part
        return None
    if isinstance(block, AudioBlock):
        if block.source == "generate_content" and block.native and block.transcript is not None:
            part = _native(block, "generate_content")
            transcription = part.get("audioTranscription")
            if not isinstance(transcription, dict):
                transcription = {}
            transcription["text"] = block.transcript
            part["audioTranscription"] = transcription
            return part
        if block.data:
            mime = block.media_type or (f"audio/{block.format}" if block.format else "audio/wav")
            part = _native(block, "generate_content")
            part["inlineData"] = {"mimeType": mime, "data": block.data}
            return part
        if block.uri:
            part = _native(block, "generate_content")
            part["fileData"] = {
                "mimeType": block.media_type or "audio/mpeg",
                "fileUri": block.uri,
            }
            return part
        return None
    if isinstance(block, DocumentBlock):
        part = _native(block, "generate_content")
        if block.data:
            part["inlineData"] = {
                "mimeType": block.media_type or "application/pdf",
                "data": block.data,
            }
            return part
        if block.uri:
            part["fileData"] = {
                "mimeType": block.media_type or "application/pdf",
                "fileUri": block.uri,
            }
            return part
        return None
    return None
