"""요청 방향 content part 렌더러.

허브 블록을 각 벤더의 요청 part 모양으로 바꾼다. 목록과 근거는
``docs/Block-Inventory.md``에 있다.

**content block은 응답 전용이 아니다.** 요청에 넣는 종류가 따로 있고 벤더마다 이름과 구조가
다르다. 같은 이미지 하나가 네 이름으로 불린다.

| 허브 | Anthropic | Chat Completions | Responses | Gemini |
|---|---|---|---|---|
| 이미지 | ``image.source`` | ``image_url`` | ``input_image`` | ``inlineData`` |
| 음성 | 없음 | ``input_audio`` | ``input_audio`` | ``inlineData`` |
| 문서 | ``document.source`` | ``file`` | ``input_file`` | ``fileData`` |

도구 결과는 part가 아니라 별개 항목이라 각 어댑터의 ``build_body``가 다룬다. Anthropic은
``tool_result`` 블록, Chat Completions는 role ``tool`` 메시지, Responses는
``function_call_output`` Item, Gemini는 ``functionResponse`` Part다.
"""

from __future__ import annotations

from typing import Any

from ..blocks import AudioBlock, ContentBlock, DocumentBlock, ImageBlock

__all__ = [
    "as_anthropic_part",
    "as_chat_completions_part",
    "as_gemini_part",
    "as_responses_part",
    "data_url",
]


def data_url(media_type: str | None, data: str) -> str:
    """base64를 data URL로 감싼다.

    Chat Completions는 base64 이미지를 별도 필드로 받지 않고 ``image_url.url``에 data URL로
    받는다. 다른 셋은 base64 필드를 따로 둔다.
    """
    return f"data:{media_type or 'application/octet-stream'};base64,{data}"


def as_anthropic_part(block: ContentBlock) -> dict[str, Any] | None:
    """Anthropic ``ContentBlock``. 요청과 응답이 같은 유니온을 쓴다."""
    if isinstance(block, ImageBlock):
        source = _anthropic_source(block.media_type, block.data, block.url, block.file_id)
        return {"type": "image", "source": source} if source else None
    if isinstance(block, DocumentBlock):
        source = _anthropic_source(
            block.media_type, block.data, block.uri, block.file_id, text=block.text
        )
        if source is None:
            return None
        part: dict[str, Any] = {"type": "document", "source": source}
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
    file_id: str | None,
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
    if file_id:
        return {"type": "file", "file_id": file_id}
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
        if block.file_id:
            return {"type": "file", "file": {"file_id": block.file_id}}
        return None
    if isinstance(block, AudioBlock):
        if block.data:
            return {
                "type": "input_audio",
                "input_audio": {"data": block.data, "format": block.format or "wav"},
            }
        return None
    if isinstance(block, DocumentBlock):
        if block.file_id:
            return {"type": "file", "file": {"file_id": block.file_id}}
        if block.data:
            payload: dict[str, Any] = {"file_data": data_url(block.media_type, block.data)}
            if block.title or block.id:
                payload["filename"] = block.title or block.id
            return {"type": "file", "file": payload}
        return None
    return None


def as_responses_part(block: ContentBlock) -> dict[str, Any] | None:
    """OpenAI Responses 요청 ``ContentPart``."""
    if isinstance(block, ImageBlock):
        url = block.url or (data_url(block.media_type, block.data) if block.data else None)
        if url:
            return {"type": "input_image", "image_url": url}
        if block.file_id:
            return {"type": "input_image", "file_id": block.file_id}
        return None
    if isinstance(block, AudioBlock):
        if block.data:
            return {
                "type": "input_audio",
                "data": block.data,
                "format": block.format or "wav",
            }
        return None
    if isinstance(block, DocumentBlock):
        part: dict[str, Any] = {"type": "input_file"}
        if block.file_id:
            part["file_id"] = block.file_id
        elif block.data:
            part["file_data"] = data_url(block.media_type, block.data)
        else:
            return None
        if block.title or block.id:
            part["filename"] = block.title or block.id
        return part
    return None


def as_gemini_part(block: ContentBlock) -> dict[str, Any] | None:
    """Gemini ``Part``. 판별자가 없어 채워진 필드가 종류를 말한다."""
    if isinstance(block, ImageBlock):
        if block.data:
            return {
                "inlineData": {
                    "mimeType": block.media_type or "image/png",
                    "data": block.data,
                }
            }
        if block.url:
            return {"fileData": {"mimeType": block.media_type or "image/png", "fileUri": block.url}}
        return None
    if isinstance(block, AudioBlock):
        if block.data:
            mime = block.media_type or (f"audio/{block.format}" if block.format else "audio/wav")
            return {"inlineData": {"mimeType": mime, "data": block.data}}
        return None
    if isinstance(block, DocumentBlock):
        if block.data:
            return {
                "inlineData": {
                    "mimeType": block.media_type or "application/pdf",
                    "data": block.data,
                }
            }
        if block.uri:
            return {
                "fileData": {
                    "mimeType": block.media_type or "application/pdf",
                    "fileUri": block.uri,
                }
            }
        return None
    return None
