"""요청 방향 content part 렌더러.

허브 블록을 각 벤더의 요청 part 모양으로 바꾼다. 목록과 근거는
``docs/Support-Matrix.md``에 있다.

**content block은 응답 전용이 아니다.** 요청에 넣는 종류가 따로 있고 벤더마다 이름과 구조가
다르다. 같은 이미지 하나가 네 이름으로 불린다.

| 허브 | Anthropic | Chat Completions | Responses | Gemini |
|---|---|---|---|---|
| 이미지 | ``image.source`` | ``image_url`` | ``input_image`` | ``inlineData`` |
| 음성 | 없음 | ``input_audio`` | 없음 | ``inlineData`` |
| 문서 | ``document`` | ``file`` | ``input_file`` | ``inlineData``/``fileData`` |

벤더가 발급한 ``file_id``/container 참조는 이 렌더러의 공통 입력 범위가 아니다. URL 또는 inline
base64만 직렬화한다.

도구 결과는 part가 아니라 별개 항목이라 각 어댑터의 ``build_body``가 다룬다. Anthropic은
``tool_result`` 블록, Chat Completions는 role ``tool`` 메시지, Responses는
``function_call_output`` Item, Gemini는 ``functionResponse`` Part다.
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
    """서버 발급 ID 또는 public URL이 아닌 vendor URI인지 검사한다."""
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
    """OpenAI Chat Completions 요청 part.

    ``streambind-base``의 ``RequestContentPart``는 ``text``와 ``image_url`` 둘만 permit하지만
    실제 API는 ``input_audio``와 ``file``도 받는다. 그 둘까지 낸다.
    """
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

    현재 ``ResponseInputMessageContentListParam`` 유니온은 text/image/file만 허용한다.
    SDK에는 독립된 ``ResponseInputAudioParam`` 타입이 남아 있지만 요청 ``input`` 유니온에는
    연결되어 있지 않으므로 오디오를 임의로 넣지 않는다.
    """
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
    """Gemini ``Part``. 판별자가 없어 채워진 필드가 종류를 말한다."""
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
