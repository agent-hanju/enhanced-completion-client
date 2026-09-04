"""Gemini GenerateContent 어댑터.

**이 벤더는 content part에 판별자가 없다.** ``Part``에 ``text``, ``functionCall``,
``executableCode``가 나란히 놓이고 채워진 필드로 종류를 알아낸다. 다른 세 벤더가 모두
``type`` 태그를 쓰는데 여기만 다르다. 그래서 판정 순서가 계약이 된다.

추론도 별도 종류가 아니라 ``thought`` 불리언이다. ``text``에 ``thought: true``가 붙으면
추론이고 아니면 본문이다. 같은 필드가 두 채널을 나른다.

이름 붙은 SSE 이벤트를 쓰지 않는다. 종료 표지도 없다. ``finishReason``이 실린 프레임이
마지막이다.

경로에 모델명과 API key가 들어가므로 :attr:`path`가 요청마다 달라진다. 브리지가 고정 경로를
쓰므로 어댑터를 모델별로 만든다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks import (
    AnnotationBlock,
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    GroundingBlock,
    GroundingSource,
    GroundingSupport,
    ImageBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from ..errors import MappingError
from ..hub import HubRequest, HubResponse, ToolDefinition, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer
from .normalize import normalize_role, stop_reason_from_gemini
from .parts import as_gemini_part, has_opaque_media_reference
from .tool_policy import can_replay_client_tool

__all__ = ["GenerateContentAdapter", "generate_content"]

SOURCE = "generate_content"

# 허브에 대응물이 있는 Part 필드. 판정 순서가 계약이다.
_TEXT = "text"
_FUNCTION_CALL = "functionCall"
_FUNCTION_RESPONSE = "functionResponse"

# 서버가 실행한 코드. 클라이언트가 결과를 되보내지 않는다.
_SERVER_TOOL_FIELDS = (
    "executableCode",
    "codeExecutionResult",
    "toolCall",
    "toolResponse",
)

# 허브에 대응물이 없어 원본을 보존하는 Part 필드.
_PART_FIELDS = frozenset(
    {
        "text",
        "inlineData",
        "functionCall",
        "functionResponse",
        "fileData",
        "executableCode",
        "codeExecutionResult",
        "toolCall",
        "toolResponse",
        "audioTranscription",
    }
)


def _parse_args(raw: str) -> dict[str, Any]:
    """도구 인수를 객체로. 이 API는 문자열이 아니라 객체를 요구한다."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class _ToHub:
    """GenerateContent 응답을 허브 델타로 바꾼다.

    본문과 추론에 고정 인덱스를 주고 도구 호출은 도착 순서대로 자리를 잡는다. 이 API는
    part 인덱스를 주지 않으므로 어댑터가 만들어야 한다.
    """

    def __init__(self) -> None:
        self._next_index = 0
        self._last_stream_key: str | None = None
        self._last_stream_index: int | None = None

    def map(self, chunk: dict[str, Any]) -> list[HubResponse]:
        candidates = chunk.get("candidates") or []
        head: dict[str, Any] = candidates[0] if candidates else {}
        content = head.get("content") or {}

        blocks: list[ContentBlock] = []
        for position, part in enumerate(content.get("parts") or []):
            if isinstance(part, dict):
                block = self._part(part, self._part_index(part, position=position))
                if block is not None:
                    blocks.append(block)

        text_indices = [
            block.index
            for block in blocks
            if isinstance(block, TextBlock) and isinstance(block.index, int)
        ]
        target_index = text_indices[0] if len(set(text_indices)) == 1 else None
        blocks.extend(self._citations(head.get("citationMetadata"), target_index))
        grounding = self._grounding(head.get("groundingMetadata"))
        if grounding is not None:
            blocks.append(grounding)
        blocks.extend(self._candidate_meta(head))

        fields: dict[str, Any] = {}
        if isinstance(chunk.get("modelVersion"), str):
            fields["model"] = chunk["modelVersion"]
        if isinstance(chunk.get("responseId"), str):
            fields["id"] = chunk["responseId"]
        role = content.get("role")
        if isinstance(role, str) and role:
            fields["role"] = normalize_role(role)
        reason = head.get("finishReason")
        if isinstance(reason, str) and reason:
            fields["stop_reason"] = stop_reason_from_gemini(reason)
        usage = self._usage(chunk.get("usageMetadata"))
        if usage is not None:
            fields["usage"] = usage

        if not blocks and not fields:
            return []
        return [HubResponse(content=blocks, **fields)]

    def flush(self) -> list[HubResponse]:
        return []

    def _citations(self, metadata: Any, target_index: int | None) -> list[ContentBlock]:
        """``citationMetadata``의 출력 범위를 annotation으로 보존한다.

        이 벤더의 ``citationSources``는 ``startIndex``/``endIndex``가 **답변 문자열 안의
        위치**다. Anthropic이 원문 좌표를 주는 것과 축이 반대이므로 이쪽은 답변 좌표를 채운다.

        인용이 part가 아니라 candidate 메타데이터에 실린다. part만 훑으면 통째로 놓친다.
        """
        if not isinstance(metadata, dict):
            return []
        out: list[ContentBlock] = []
        for annotation_index, source in enumerate(metadata.get("citationSources") or []):
            if not isinstance(source, dict):
                continue
            fields: dict[str, Any] = {
                "source": SOURCE,
                "annotation_index": annotation_index,
                "target_index": target_index,
                "kind": "citation_source",
                "native": dict(source),
            }
            uri = source.get("uri")
            if isinstance(uri, str) and uri:
                fields["id"] = uri
                fields["uri"] = uri
            title = source.get("title")
            if isinstance(title, str) and title:
                fields["title"] = title
            for key, dst in (("startIndex", "start_index"), ("endIndex", "end_index")):
                value = source.get(key)
                if isinstance(value, int):
                    fields[dst] = value
            out.append(AnnotationBlock(**fields))
        return out

    @staticmethod
    def _grounding(metadata: Any) -> GroundingBlock | None:
        """검색 source와 답변 support의 관계를 평탄화하지 않고 보존한다."""
        if not isinstance(metadata, dict):
            return None

        sources: list[GroundingSource] = []
        for index, chunk in enumerate(metadata.get("groundingChunks") or []):
            if not isinstance(chunk, dict):
                continue
            kind = next(
                (name for name, value in chunk.items() if isinstance(value, dict)),
                "unknown",
            )
            detail = chunk.get(kind)
            detail = detail if isinstance(detail, dict) else {}
            sources.append(
                GroundingSource(
                    index=index,
                    kind=kind,
                    uri=detail.get("uri") if isinstance(detail.get("uri"), str) else None,
                    title=detail.get("title") if isinstance(detail.get("title"), str) else None,
                    native=dict(chunk),
                )
            )

        supports: list[GroundingSupport] = []
        for index, support in enumerate(metadata.get("groundingSupports") or []):
            if not isinstance(support, dict):
                continue
            segment = support.get("segment")
            segment = segment if isinstance(segment, dict) else {}
            source_indices = [
                value
                for value in support.get("groundingChunkIndices") or []
                if isinstance(value, int)
            ]
            confidence_scores = [
                float(value)
                for value in support.get("confidenceScores") or []
                if isinstance(value, int | float) and not isinstance(value, bool)
            ]
            supports.append(
                GroundingSupport(
                    index=index,
                    text=segment.get("text") if isinstance(segment.get("text"), str) else None,
                    start_index=(
                        segment.get("startIndex")
                        if isinstance(segment.get("startIndex"), int)
                        else None
                    ),
                    end_index=(
                        segment.get("endIndex")
                        if isinstance(segment.get("endIndex"), int)
                        else None
                    ),
                    source_indices=source_indices,
                    confidence_scores=confidence_scores,
                    native=dict(support),
                )
            )

        queries = [
            query for query in metadata.get("webSearchQueries") or [] if isinstance(query, str)
        ]
        search_entry = metadata.get("searchEntryPoint")
        retrieval = metadata.get("retrievalMetadata")
        return GroundingBlock(
            source=SOURCE,
            candidate_index=0,
            sources=sources,
            supports=supports,
            search_queries=queries,
            search_entry_point=dict(search_entry) if isinstance(search_entry, dict) else None,
            retrieval_metadata=dict(retrieval) if isinstance(retrieval, dict) else None,
            native=dict(metadata),
        )

    def _candidate_meta(self, candidate: dict[str, Any]) -> list[ContentBlock]:
        """허브에 대응물이 없는 candidate 메타데이터를 보존한다.

        grounding은 별도 공통 블록으로 처리한다. 여기에는 URL 조회 결과와 안전 등급처럼
        정규화하지 않는 candidate 메타데이터만 남긴다.
        """
        out: list[ContentBlock] = []
        for key in ("urlContextMetadata",):
            value = candidate.get(key)
            if isinstance(value, dict):
                # 서버가 검색을 돌린 결과다. 다른 벤더의 서버 도구와 같은 자리다.
                out.append(ServerToolBlock(name=key, raw=value, source=SOURCE))
        ratings = candidate.get("safetyRatings")
        if isinstance(ratings, list) and ratings:
            out.append(VendorBlock(type="safetyRatings", raw={"ratings": ratings}, source=SOURCE))
        return out

    def _part_index(self, part: dict[str, Any], *, position: int) -> int:
        """명시적 part index가 없는 스트림에서 연속 텍스트 조각만 같은 슬롯에 모은다."""
        if isinstance(part.get("text"), str):
            key = "thinking" if part.get("thought") else "text"
            if (
                position == 0
                and key == self._last_stream_key
                and self._last_stream_index is not None
            ):
                return self._last_stream_index
        elif isinstance(part.get("audioTranscription"), dict):
            key = "audioTranscription"
            if (
                position == 0
                and key == self._last_stream_key
                and self._last_stream_index is not None
            ):
                return self._last_stream_index
        else:
            key = next((field for field in _PART_FIELDS if field in part), "unknown")

        index = self._next_index
        self._next_index += 1
        self._last_stream_key = key
        self._last_stream_index = index
        return index

    def _part(self, part: dict[str, Any], index: int) -> ContentBlock | None:
        """채워진 필드로 종류를 알아낸다. 판별자가 없어 순서가 계약이다."""
        text = part.get(_TEXT)
        # 빈 문자열도 블록을 만든다. 확립된 규칙이 ``text != null``이다.
        if isinstance(text, str):
            # 같은 필드가 두 채널을 나른다. thought 불리언이 갈림길이다.
            if part.get("thought"):
                fields: dict[str, Any] = {
                    "thinking": text,
                    "index": index,
                    "native": dict(part),
                }
                signature = part.get("thoughtSignature")
                if isinstance(signature, str) and signature:
                    fields["signature"] = signature
                return ThinkingBlock(source=SOURCE, **fields)
            fields = {"text": text, "index": index, "native": dict(part)}
            signature = part.get("thoughtSignature")
            if isinstance(signature, str) and signature:
                fields["signature"] = signature
            return TextBlock(source=SOURCE, **fields)

        call = part.get(_FUNCTION_CALL)
        if isinstance(call, dict):
            return self._call(call, part, index)

        response = part.get(_FUNCTION_RESPONSE)
        if isinstance(response, dict):
            # 요청 방향 어휘이지만 응답에도 온다. 허브 도구 결과로 올린다.
            payload = response.get("response")
            result = payload.get("result") if isinstance(payload, dict) else None
            nested = self._function_response_blocks(response.get("parts"))
            return ToolResultBlock(
                tool_use_id=str(response.get("id") or response.get("name") or ""),
                name=str(response.get("name") or "") or None,
                content=(
                    ""
                    if result is None
                    else result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False)
                ),
                structured_content=payload,
                blocks=nested,
                native=dict(part),
                index=index,
                source=SOURCE,
            )

        transcription = part.get("audioTranscription")
        if isinstance(transcription, dict):
            text = transcription.get("text")
            return AudioBlock(
                transcript=text if isinstance(text, str) else None,
                native=dict(part),
                index=index,
                source=SOURCE,
            )

        # inlineData는 이미지 또는 음성이다. MIME으로 갈린다.
        inline = part.get("inlineData")
        if isinstance(inline, dict):
            mime = str(inline.get("mimeType") or "")
            data = inline.get("data")
            if mime.startswith("audio/"):
                return AudioBlock(
                    media_type=mime,
                    data=data,
                    native=dict(part),
                    index=index,
                    source=SOURCE,
                )
            if mime.startswith("image/"):
                return ImageBlock(
                    media_type=mime,
                    data=data,
                    native=dict(part),
                    index=index,
                    source=SOURCE,
                )
            return DocumentBlock(
                media_type=mime or "application/octet-stream",
                data=data,
                native=dict(part),
                index=index,
                source=SOURCE,
            )

        file_data = part.get("fileData")
        if isinstance(file_data, dict):
            mime = str(file_data.get("mimeType") or "")
            uri = file_data.get("fileUri")
            if mime.startswith("image/"):
                return ImageBlock(
                    media_type=mime,
                    url=uri,
                    native=dict(part),
                    index=index,
                    source=SOURCE,
                )
            if mime.startswith("audio/"):
                return AudioBlock(
                    media_type=mime,
                    uri=uri,
                    native=dict(part),
                    index=index,
                    source=SOURCE,
                )
            return DocumentBlock(
                media_type=mime or "application/octet-stream",
                uri=uri,
                native=dict(part),
                index=index,
                source=SOURCE,
            )

        for field in _SERVER_TOOL_FIELDS:
            value = part.get(field)
            if isinstance(value, dict):
                return ServerToolBlock(
                    name=field,
                    input_json=json.dumps(value, ensure_ascii=False)
                    if field == "executableCode"
                    else "",
                    output=str(value.get("output", "")) if field == "codeExecutionResult" else "",
                    raw=value,
                    native=dict(part),
                    index=index,
                    source=SOURCE,
                )
        return VendorBlock(
            type="gemini_part",
            raw=dict(part),
            native=dict(part),
            index=index,
            source=SOURCE,
        )

    def _call(
        self,
        call: dict[str, Any],
        part: dict[str, Any],
        index: int,
    ) -> ToolUseBlock:
        fields: dict[str, Any] = {
            "index": index,
            "source": SOURCE,
            "native": dict(part),
        }
        if call.get("id"):
            fields["id"] = call["id"]
        if call.get("name"):
            fields["name"] = call["name"]
        args = call.get("args")
        if isinstance(args, (dict, list)):
            # 이 API는 인수를 조각으로 쪼개지 않고 완성된 객체로 준다.
            fields["input_json"] = json.dumps(args, ensure_ascii=False)
            fields["input"] = args
        signature = part.get("thoughtSignature")
        if isinstance(signature, str) and signature:
            fields["signature"] = signature
        return ToolUseBlock(**fields)

    @staticmethod
    def _function_response_blocks(raw: Any) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for part in raw or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
            data = inline.get("data")
            if mime.startswith("image/"):
                blocks.append(ImageBlock(media_type=mime, data=data))
            elif mime.startswith("audio/"):
                blocks.append(AudioBlock(media_type=mime, data=data))
            else:
                blocks.append(DocumentBlock(media_type=mime, data=data))
        return blocks

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("promptTokenCount"), int):
            fields["input_tokens"] = raw["promptTokenCount"]
        if isinstance(raw.get("candidatesTokenCount"), int):
            fields["output_tokens"] = raw["candidatesTokenCount"]
        return Usage(**fields) if fields else None


class GenerateContentAdapter:
    """``POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse``.

    경로에 모델명이 들어가므로 모델별로 어댑터를 만든다. ``for_model``이 그 일을 한다.

    API key는 ``x-goog-api-key`` 헤더로 보낸다. ``Bridge(api_key=...)``는
    ``Authorization: Bearer``를 쓰므로 이 어댑터는 자기 헤더를 따로 요구한다.
    """

    name = SOURCE
    parameter_family = "generate_content"

    def __init__(self, *, model: str = "", api_key: str = "", version: str = "v1beta") -> None:
        self.model = model
        self.api_key = api_key
        self.version = version

    @property
    def path(self) -> str:
        model = self.model or "{model}"
        return f"/{self.version}/models/{model}:streamGenerateContent?alt=sse"

    def for_model(self, model: str, *, api_key: str = "") -> GenerateContentAdapter:
        return GenerateContentAdapter(
            model=model,
            api_key=api_key or self.api_key,
            version=self.version,
        )

    def request_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 GenerateContent body로.

        이 API는 ``messages``가 아니라 ``contents``를 받고 role ``assistant``를 ``model``이라
        부른다. system은 ``systemInstruction``으로 따로 나간다.
        """
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        for message in request.messages:
            if message.role == "system":
                text = lowerer.lower_text(message.content)
                if text:
                    system.append(text)
                continue
            contents.extend(self._contents(message.role, message.content, lowerer, call_names))

        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        tools = self._tools(request.tools)
        if tools:
            body["tools"] = tools

        # 생성 파라미터가 generationConfig 안에 들어간다. 다른 셋은 최상위다.
        config = dict(params.pop("generationConfig", {}) or {})
        for key in ("temperature", "topP", "topK", "maxOutputTokens", "stopSequences", "seed"):
            if key in params:
                config[key] = params.pop(key)
        # 허브 어휘를 이 API 이름으로 옮긴다.
        if "max_tokens" in params:
            config["maxOutputTokens"] = params.pop("max_tokens")
        if "top_p" in params:
            config["topP"] = params.pop("top_p")
        if config:
            body["generationConfig"] = config

        for key, value in params.items():
            if key not in ("contents", "tools", "systemInstruction"):
                body[key] = value
        return body

    def _contents(
        self,
        role: str,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
        call_names: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Part가 요구하는 role을 지키며 한 허브 턴을 0..N Content로 펼친다."""
        contents: list[dict[str, Any]] = []
        pending: list[ContentBlock] = []
        pending_role: str | None = None

        def flush() -> None:
            nonlocal pending
            if not pending or pending_role is None:
                return
            parts = self._parts(pending, lowerer, call_names)
            pending = []
            if parts:
                contents.append({"role": pending_role, "parts": parts})

        default_role = "model" if role == "assistant" else "user"
        for block in blocks:
            if isinstance(block, ToolUseBlock | ToolResultBlock) and not can_replay_client_tool(
                block, self.name
            ):
                continue
            if isinstance(block, ToolUseBlock):
                block_role = "model"
                if block.id and block.name:
                    call_names[block.id] = block.name
            elif isinstance(block, ToolResultBlock):
                block_role = "user"
            else:
                block_role = default_role
            if pending_role is not None and block_role != pending_role:
                flush()
            pending_role = block_role
            pending.append(block)
        flush()
        return contents

    def _parts(
        self,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
        call_names: dict[str, str],
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []

        def flush_plain() -> None:
            text_parts = lowerer.lower_text_parts(plain)
            plain.clear()
            for text in text_parts:
                parts.append({"text": text})

        for block in blocks:
            if has_opaque_media_reference(block):
                continue
            if isinstance(block, ToolResultBlock):
                flush_plain()
                parts.append(self._function_response(block, call_names))
                continue
            part = as_gemini_part(block)
            if part is None:
                plain.append(block)
                continue
            if (
                isinstance(block, ServerToolBlock | VendorBlock)
                and not any(field in part for field in _PART_FIELDS)
                and not block.native
            ):
                # candidate 메타데이터는 Part가 아니므로 이력에 넣지 않는다.
                continue
            flush_plain()
            parts.append(part)
        flush_plain()
        return parts

    @staticmethod
    def _function_response(
        block: ToolResultBlock,
        call_names: dict[str, str],
    ) -> dict[str, Any]:
        if block.source == SOURCE and block.native:
            return dict(block.native)

        name = block.name or call_names.get(block.tool_use_id) or block.tool_use_id
        response: Any = block.structured_content
        if response is None:
            response = {"result": block.content}
        elif not isinstance(response, dict):
            response = {"result": response}

        function_response: dict[str, Any] = {"name": name, "response": response}
        if block.tool_use_id:
            function_response["id"] = block.tool_use_id
        nested = [
            as_gemini_part(part)
            for part in block.blocks
            if isinstance(part, ContentBlock) and not has_opaque_media_reference(part)
        ]
        rendered = [part for part in nested if part is not None]
        if rendered:
            function_response["parts"] = rendered
        return {"functionResponse": function_response}

    def _tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        native: list[dict[str, Any]] = []
        declarations: list[dict[str, Any]] = []
        for tool in tools:
            wire = tool.native_for(self.name)
            if wire is not None:
                native.append(wire)
                continue
            if tool.vendor is not None:
                continue
            declarations.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
            )
        if declarations:
            native.insert(0, {"functionDeclarations": declarations})
        return native

    def is_terminal(self, frame: SseFrame) -> bool:
        """종료 표지가 없다. 스트림이 끊기면 끝이다."""
        _ = frame
        return False

    def decode(self, frame: SseFrame) -> dict[str, Any] | None:
        payload = frame.data.strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MappingError("generate_content chunk is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MappingError("generate_content chunk must be a JSON object")
        if "error" in parsed:
            detail = json.dumps(parsed["error"], ensure_ascii=False)[:200]
            raise MappingError(f"generate_content stream reported an error: {detail}")
        return parsed

    def to_hub(self) -> StreamMapper[Any, Any]:
        return _ToHub()


generate_content = GenerateContentAdapter()
"""기본 인스턴스. ``for_model(name, api_key=...)``로 대상을 지정한다."""
