"""Gemini GenerateContent 어댑터.

**이 벤더는 content part에 판별자가 없다.** ``Part``에 ``text``, ``functionCall``,
``executableCode``가 나란히 놓이고 채워진 필드로 종류를 알아낸다. 다른 세 벤더가 모두
``type`` 태그를 쓰는데 여기만 다르다. 그래서 판정 순서가 계약이 된다.

추론도 별도 종류가 아니라 ``thought`` 불리언이다. ``text``에 ``thought: true``가 붙으면
추론이고 아니면 본문이다. 같은 필드가 두 채널을 나른다.

이름 붙은 SSE 이벤트를 쓰지 않는다. 종료 표지도 없다. ``finishReason``이 실린 프레임이
마지막이다.

경로의 모델명은 Bridge가 요청별로 바인딩하고 API key는 헤더로 전달한다.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from pydantic import BaseModel

from completion_bridge.errors import MappingError, VendorStreamError
from completion_bridge.http.sse import SseEvent
from completion_bridge.models.blocks import (
    AnnotationBlock,
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    GroundingBlock,
    GroundingSource,
    GroundingSupport,
    ImageBlock,
    SearchResultBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from completion_bridge.models.hub import HubRequest, HubResponse, TokenCount, ToolDefinition, Usage
from completion_bridge.models.native import (
    GenerateContentCandidate,
    GenerateContentContent,
    GenerateContentPart,
    GenerateContentResponse,
)
from completion_bridge.streaming.mapping import StreamMapper
from completion_bridge.streaming.sequence import Seq, normalize_seq
from completion_bridge.vendors.base import JsonCall, Lowerer, NativeAccumulator
from completion_bridge.vendors.normalize import (
    INSTRUCTION_ROLES,
    normalize_role,
    stop_reason_from_gemini,
)
from completion_bridge.vendors.parts import as_gemini_part, has_opaque_media_reference
from completion_bridge.vendors.tool_adaptation import ToolAdaptation

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


def _gemini_media_block(part: dict[str, Any], seq: Seq) -> ContentBlock | None:
    """inlineData·fileData를 이미지·음성·문서로 변환하고 원본 Part를 보존한다."""
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
                seq=seq,
                vendor=SOURCE,
            )
        if mime.startswith("image/"):
            return ImageBlock(
                media_type=mime,
                data=data,
                native=dict(part),
                seq=seq,
                vendor=SOURCE,
            )
        return DocumentBlock(
            media_type=mime or "application/octet-stream",
            data=data,
            native=dict(part),
            seq=seq,
            vendor=SOURCE,
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
                seq=seq,
                vendor=SOURCE,
            )
        if mime.startswith("audio/"):
            return AudioBlock(
                media_type=mime,
                uri=uri,
                native=dict(part),
                seq=seq,
                vendor=SOURCE,
            )
        return DocumentBlock(
            media_type=mime or "application/octet-stream",
            uri=uri,
            native=dict(part),
            seq=seq,
            vendor=SOURCE,
        )

    return None


_SEQ_WIDTH = 2


class _ToHub(StreamMapper[dict[str, Any], HubResponse]):
    """GenerateContent 응답을 허브 델타로 바꾼다.

    이 API는 part 인덱스를 주지 않으므로 어댑터가 0부터 part 번호 n을 만들고 ``seq=(1, n)``을 쓴다.
    입력 차단·콘텐츠·인용·grounding·URL 조회·안전 평가는 각각 고정 그룹 0~5를 사용한다.
    """

    def __init__(self) -> None:
        """스트림별 블록 순서와 마지막 채널 좌표를 초기화한다."""
        self._next_index = 0
        self._last_stream_key: str | None = None
        self._last_stream_index: int | None = None

    def map(self, delta: dict[str, Any]) -> list[HubResponse]:
        """벤더 스트림 이벤트 하나를 허브 델타 목록으로 변환한다."""
        chunk = delta
        candidates = chunk.get("candidates") or []
        head: dict[str, Any] = candidates[0] if candidates else {}
        content = head.get("content") or {}

        blocks: list[ContentBlock] = []
        feedback = chunk.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            # 입력이 차단되면 candidates가 아예 오지 않는다. 여기서 잡지 않으면 빈 응답과
            # 구분되지 않는다.
            blocks.append(
                VendorBlock(
                    type="promptFeedback",
                    raw=dict(feedback),
                    vendor=SOURCE,
                    seq=normalize_seq((0,), _SEQ_WIDTH),
                )
            )
        indexed_parts = content.get("parts") or []
        for position in range(len(indexed_parts)):
            part = indexed_parts[position]
            if isinstance(part, dict):
                seq = normalize_seq((1, self._part_index(part, position=position)), _SEQ_WIDTH)
                block = self._part(part, seq)
                if block is not None:
                    blocks.append(block)

        text_seqs = []
        for block in blocks:
            if isinstance(block, TextBlock) and block.seq is not None:
                text_seqs.append(block.seq)
        target_seq = text_seqs[0] if len(set(text_seqs)) == 1 else None
        blocks.extend(self._citations(head.get("citationMetadata"), target_seq))
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
        if isinstance(feedback, dict) and isinstance(feedback.get("blockReason"), str):
            # 허브 어휘가 따로 없으므로 원문을 유지한다.
            fields["block_reason"] = feedback["blockReason"]
        usage = self._usage(chunk.get("usageMetadata"))
        if usage is not None:
            fields["usage"] = usage

        if not blocks and not fields:
            return []
        return [HubResponse(content=blocks, **fields)]

    def flush(self) -> list[HubResponse]:
        """이 변환기는 종료 시 보관하는 잔여 델타가 없으므로 빈 목록을 반환한다."""
        return []

    def _citations(self, metadata: Any, target_seq: Seq | None) -> list[ContentBlock]:
        """``citationMetadata``의 출력 범위를 annotation으로 보존한다.

        이 벤더의 ``citationSources``는 ``startIndex``/``endIndex``가 **답변 문자열 안의
        위치**다. Anthropic이 원문 좌표를 주는 것과 축이 반대이므로 이쪽은 답변 좌표를 채운다.

        인용이 part가 아니라 candidate 메타데이터에 실린다. part만 훑으면 통째로 놓친다.

        ``citationSources``의 배열 위치가 같은 annotation은 chunk가 달라도 같은 seq를 받아 한
        블록으로 병합된다. 첫 좌표는 인용 그룹 2이고 두 번째 좌표는 원본 배열 위치다.
        """
        if not isinstance(metadata, dict):
            return []
        out: list[ContentBlock] = []
        indexed_sources = metadata.get("citationSources") or []
        for position in range(len(indexed_sources)):
            source = indexed_sources[position]
            if not isinstance(source, dict):
                continue
            fields: dict[str, Any] = {
                "vendor": SOURCE,
                "seq": normalize_seq((2, position), _SEQ_WIDTH),
                "target_seq": target_seq,
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
            field_names = {"startIndex": "start_index", "endIndex": "end_index"}
            for key in field_names:
                dst = field_names[key]
                value = source.get(key)
                if isinstance(value, int):
                    fields[dst] = value
            out.append(AnnotationBlock(**fields))
        return out

    def _grounding(self, metadata: Any) -> GroundingBlock | None:
        """검색 source와 답변 support의 관계를 평탄화하지 않고 보존한다.

        스트림의 모든 grounding 조각은 같은 seq를 받아 블록 하나로 병합된다.
        seq는 수신 시점과 무관하게 grounding 그룹 ``(3, -1)``이다.
        """
        if not isinstance(metadata, dict):
            return None

        sources: list[GroundingSource] = []
        indexed_chunks = metadata.get("groundingChunks") or []
        for index in range(len(indexed_chunks)):
            chunk = indexed_chunks[index]
            if not isinstance(chunk, dict):
                continue
            kind = "unknown"
            for name in chunk:
                value = chunk[name]
                if isinstance(value, dict):
                    kind = name
                    break
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
        indexed_supports = metadata.get("groundingSupports") or []
        for index in range(len(indexed_supports)):
            support = indexed_supports[index]
            if not isinstance(support, dict):
                continue
            segment = support.get("segment")
            segment = segment if isinstance(segment, dict) else {}
            source_indices = []
            for value in support.get("groundingChunkIndices") or []:
                if isinstance(value, int):
                    source_indices.append(value)
            confidence_scores = []
            for value in support.get("confidenceScores") or []:
                if isinstance(value, int | float) and (not isinstance(value, bool)):
                    confidence_scores.append(float(value))
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

        queries = []
        for query in metadata.get("webSearchQueries") or []:
            if isinstance(query, str):
                queries.append(query)
        search_entry = metadata.get("searchEntryPoint")
        retrieval = metadata.get("retrievalMetadata")
        return GroundingBlock(
            vendor=SOURCE,
            seq=normalize_seq((3,), _SEQ_WIDTH),
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
        URL 조회와 안전 평가는 각각 그룹 4와 5의 동일 블록을 갱신한다.
        """
        out: list[ContentBlock] = []
        for key in ("urlContextMetadata",):
            value = candidate.get(key)
            if isinstance(value, dict):
                # 서버가 검색을 돌린 결과다. 다른 벤더의 서버 도구와 같은 자리다.
                out.append(
                    ServerToolBlock(
                        name=key, raw=value, vendor=SOURCE, seq=normalize_seq((4,), _SEQ_WIDTH)
                    )
                )
        ratings = candidate.get("safetyRatings")
        if isinstance(ratings, list) and ratings:
            out.append(
                VendorBlock(
                    type="safetyRatings",
                    raw={"ratings": ratings},
                    vendor=SOURCE,
                    seq=normalize_seq((5,), _SEQ_WIDTH),
                )
            )
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
            key = "unknown"
            for field in _PART_FIELDS:
                if field in part:
                    key = field
                    break

        index = self._next_index
        self._next_index += 1
        self._last_stream_key = key
        self._last_stream_index = index
        return index

    def _part(self, part: dict[str, Any], seq: Seq) -> ContentBlock | None:
        """채워진 필드로 종류를 알아낸다. 판별자가 없어 순서가 계약이다."""
        text = part.get(_TEXT)
        # 빈 문자열도 블록을 만든다. 확립된 규칙이 ``text != null``이다.
        if isinstance(text, str):
            # 같은 필드가 두 채널을 나른다. thought 불리언이 갈림길이다.
            if part.get("thought"):
                fields: dict[str, Any] = {
                    "thinking": text,
                    "seq": seq,
                    "native": dict(part),
                }
                signature = part.get("thoughtSignature")
                if isinstance(signature, str) and signature:
                    fields["signature"] = signature
                return ThinkingBlock(vendor=SOURCE, **fields)
            fields = {"text": text, "seq": seq, "native": dict(part)}
            signature = part.get("thoughtSignature")
            if isinstance(signature, str) and signature:
                fields["signature"] = signature
            return TextBlock(vendor=SOURCE, **fields)

        call = part.get(_FUNCTION_CALL)
        if isinstance(call, dict):
            return self._call(call, part, seq)

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
                seq=seq,
                vendor=SOURCE,
            )

        transcription = part.get("audioTranscription")
        if isinstance(transcription, dict):
            text = transcription.get("text")
            return AudioBlock(
                transcript=text if isinstance(text, str) else None,
                native=dict(part),
                seq=seq,
                vendor=SOURCE,
            )

        media = _gemini_media_block(part, seq)
        if media is not None:
            return media

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
                    seq=seq,
                    vendor=SOURCE,
                )
        return VendorBlock(
            type="gemini_part",
            raw=dict(part),
            native=dict(part),
            seq=seq,
            vendor=SOURCE,
        )

    def _call(
        self,
        call: dict[str, Any],
        part: dict[str, Any],
        seq: Seq,
    ) -> ToolUseBlock:
        """함수 이름·인자·ID를 허브 도구 호출로 변환하고 호출 좌표를 유지한다."""
        fields: dict[str, Any] = {
            "seq": seq,
            "vendor": SOURCE,
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
        """함수 응답의 inlineData Part를 MIME에 따라 이미지·음성·문서 블록으로 변환한다."""
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
        """벤더 사용량을 허브 토큰 사용량과 원본 사용량으로 변환한다."""
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("promptTokenCount"), int):
            fields["input_tokens"] = raw["promptTokenCount"]
        if isinstance(raw.get("candidatesTokenCount"), int):
            fields["output_tokens"] = raw["candidatesTokenCount"]
        return Usage(**fields, native=dict(raw))


class _ToNative:
    """GenerateContent chunk를 ``GenerateContentResponse`` 형식 객체 하나로 누적한다.

    Google Gen AI SDK의 채팅 이력처럼 chunk의 part를 받은 순서대로 보존한다.

    - candidate는 ``index``별로 모은다. ``index``가 없으면 proto3 JSON의 생략 규칙대로 0이다.
    - ``content.parts``는 text 조각도 연결하지 않고 받은 순서대로 추가한다.
      ``thoughtSignature``를 포함한 part의 필드와 구성을 유지한다.
    - 그 밖의 필드는 :meth:`_merge_field` 규칙으로 반영한다.
    """

    def __init__(self) -> None:
        self._response = GenerateContentResponse()
        self._candidates: dict[int, GenerateContentCandidate] = {}

    def apply(self, chunk: dict[str, Any]) -> None:
        """chunk 하나를 누적한다.

        Raises:
            MappingError: ``candidates``가 리스트가 아니거나, candidate가 객체가 아니거나,
                candidate ``index``가 정수가 아닐 때. ``content`` 규약 위반은
                :meth:`_merge_content`가 알린다.
        """
        # 최상위 필드: usageMetadata, modelVersion 등을 반영한다.
        for key in chunk:
            if key != "candidates":
                self._merge_field(self._response, key, chunk[key])

        # candidates: index별 candidate에 content와 메타데이터를 반영한다.
        candidates = chunk.get("candidates")
        if candidates is None:
            return
        if not isinstance(candidates, list):
            raise MappingError("generate_content chunk candidates must be a list")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise MappingError("generate_content candidate must be a JSON object")
            index = candidate.get("index", 0)
            if not isinstance(index, int):
                raise MappingError("generate_content candidate index must be an integer")
            merged = self._candidates.get(index)
            if merged is None:
                merged = GenerateContentCandidate()
                self._candidates[index] = merged
            for key in candidate:
                if key == "content":
                    self._merge_content(merged, candidate[key])
                else:
                    self._merge_field(merged, key, candidate[key])

    def build(self) -> GenerateContentResponse:
        """누적한 응답 객체를 돌려준다.

        ``candidates``는 candidate를 받은 적이 있을 때만 ``index`` 순서로 맨 앞에 둔다.
        """
        if self._candidates:
            candidates: list[GenerateContentCandidate] = []
            for index in sorted(self._candidates):
                candidates.append(self._candidates[index])
            self._response.candidates = candidates
        return self._response

    def _merge_content(self, candidate: GenerateContentCandidate, content: Any) -> None:
        """chunk candidate의 ``content``를 누적 중인 candidate에 반영한다.

        ``parts``는 클래스 설명의 part 규칙으로, 그 밖의 필드는 :meth:`_merge_field`로 반영한다.

        Raises:
            MappingError: ``content``가 객체가 아니거나, ``parts``가 리스트가 아니거나, part가
                객체가 아닐 때.
        """
        if content is None:
            return
        if not isinstance(content, dict):
            raise MappingError("generate_content candidate content must be a JSON object")
        if candidate.content is None:
            candidate.content = GenerateContentContent()
        target = candidate.content
        for key in content:
            value = content[key]
            if key != "parts":
                self._merge_field(target, key, value)
                continue
            if not isinstance(value, list):
                raise MappingError("generate_content content parts must be a list")
            if "parts" not in target.model_fields_set:
                target.parts = []
            for part in value:
                if not isinstance(part, dict):
                    raise MappingError("generate_content part must be a JSON object")
                target.parts.append(GenerateContentPart.model_validate(part))

    @staticmethod
    def _merge_field(target: BaseModel, key: str, value: Any) -> None:
        """part 외 필드 하나를 반영한다.

        처음 오는 key는 값을 그대로 둔다. 그 뒤로 ``None``은 기존 값을 유지한다. 기존 값과 새
        값이 모두 객체이면 최상위 key 단위로 나중 값을 쓰고, 그 밖에는 나중 값으로 바꾼다.
        """
        present = key in target.model_fields_set or key in (target.model_extra or {})
        if not present:
            setattr(target, key, value)
            return
        if value is None:
            return
        current = getattr(target, key)
        if isinstance(current, BaseModel):
            current = current.model_dump(exclude_unset=True)
        if isinstance(current, dict) and isinstance(value, dict):
            for field in value:
                current[field] = value[field]
            value = current
        setattr(target, key, value)


class GenerateContentAdapter:
    """``POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse``.

    ``Bridge(vendor=generate_content, model=..., api_key=...)``로 사용한다.
    Bridge가 ``for_model``로 모델을 바인딩하고 키를 ``x-goog-api-key`` 헤더로 보낸다.
    """

    name = SOURCE
    parameter_family = "generate_content"
    api_key_header = "x-goog-api-key"

    def __init__(self, *, model: str = "", api_key: str = "", version: str = "v1beta") -> None:
        """모델 이름·API 키·API 버전을 보관한다."""
        self.model = model
        self.api_key = api_key
        self.version = version

    @property
    def path(self) -> str:
        """선택한 모델을 포함하는 streamGenerateContent 요청 경로를 반환한다."""
        model = self.model or "{model}"
        return f"/{self.version}/models/{model}:streamGenerateContent?alt=sse"

    def for_model(self, model: str, *, api_key: str = "") -> GenerateContentAdapter:
        """원본을 변경하지 않고 모델을 바인딩한다. Bridge가 자동으로 호출한다."""
        return GenerateContentAdapter(
            model=model,
            api_key=api_key or self.api_key,
            version=self.version,
        )

    def request_headers(self) -> dict[str, str]:
        """설정한 인증 정보로 벤더가 요구하는 추가 요청 헤더를 만든다."""
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 GenerateContent body로.

        이 API는 ``messages``가 아니라 ``contents``를 받고 role ``assistant``를 ``model``이라
        부른다. system은 ``systemInstruction``으로 따로 나간다.

        함수 도구와 ``generate_content`` native 도구가 함께 있으면
        ``toolConfig.includeServerSideToolInvocations``를 ``True``로 채운다. 호출자가
        ``Hyperparameters.tool_config``나 raw ``toolConfig``로 이 키를 명시했으면 그 값을 그대로
        보낸다.
        """
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        for message in request.messages:
            if message.role in INSTRUCTION_ROLES:
                text = lowerer.lower_text(message.content)
                if text:
                    system.append(text)
                continue
            contents.extend(
                self._contents(
                    message.role,
                    message.content,
                    lowerer,
                    call_names,
                    synthetic=message.synthetic,
                )
            )

        params = request.parameters_for(self.parameter_family, vendor_name=self.name)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        tools = self._tools(request.tools)
        if tools:
            body["tools"] = tools

        # ``for_vendor``가 generationConfig 중첩까지 만들어 준다. 여기서 옮길 것이 없다.
        for key in params:
            value = params[key]
            if key not in ("contents", "tools", "systemInstruction"):
                body[key] = value

        # 함수 선언과 내장 도구가 함께 있으면 서버 측 도구 실행 플래그의 기본값을 켠다.
        # ``_tools``는 함수 선언을 0번에 두므로 길이 2 이상이면 두 종류가 모두 있다.
        # 호출자가 이 키를 명시했으면 값을 바꾸지 않고 서버의 판단에 맡긴다.
        if len(tools) > 1 and "functionDeclarations" in tools[0]:
            tool_config = dict(body.get("toolConfig") or {})
            tool_config.setdefault("includeServerSideToolInvocations", True)
            body["toolConfig"] = tool_config
        return body

    def _contents(
        self,
        role: str,
        blocks: list[ContentBlock],
        lowerer: Lowerer,
        call_names: dict[str, str],
        *,
        synthetic: bool = False,
    ) -> list[dict[str, Any]]:
        """Part가 요구하는 role을 지키며 한 허브 턴을 0..N Content로 펼친다."""
        contents: list[dict[str, Any]] = []
        pending: list[ContentBlock] = []
        pending_role: str | None = None

        def flush() -> None:
            """보관한 같은 역할의 메시지들을 Content 하나로 변환하고 임시 상태를 초기화한다."""
            nonlocal pending
            if not pending or pending_role is None:
                return
            parts = self._parts(pending, lowerer, call_names, synthetic=synthetic)
            pending = []
            if parts:
                contents.append({"role": pending_role, "parts": parts})

        default_role = "model" if role == "assistant" else "user"
        for block in blocks:
            if isinstance(
                block, ToolUseBlock | ToolResultBlock
            ) and not ToolAdaptation.can_replay_client_tool(block, self.name):
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
        *,
        synthetic: bool = False,
    ) -> list[dict[str, Any]]:
        """허브 블록을 Gemini Part 목록으로 변환하며 일반 텍스트의 순서를 보존한다."""
        parts: list[dict[str, Any]] = []
        plain: list[ContentBlock] = []

        def flush_plain() -> None:
            """보관한 일반 블록을 텍스트 Part로 변환하고 임시 목록을 초기화한다."""
            text_parts = lowerer.lower_text_parts(plain)
            plain.clear()
            for text in text_parts:
                parts.append({"text": text})

        for block in blocks:
            if has_opaque_media_reference(block):
                continue
            if isinstance(block, ToolResultBlock):
                flush_plain()
                parts.append(self._function_response(block, call_names, lowerer))
                continue
            part = as_gemini_part(block)
            if part is None:
                plain.append(block)
                continue
            if synthetic and isinstance(block, ToolUseBlock) and not part.get("thoughtSignature"):
                # 직접 작성한 호출의 원본 블록을 변경하지 않고 요청용 사본을 사용한다.
                part["thoughtSignature"] = "skip_thought_signature_validator"
            has_native_field = False
            for field in _PART_FIELDS:
                if field in part:
                    has_native_field = True
                    break
            if (
                isinstance(block, ServerToolBlock | VendorBlock)
                and not has_native_field
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
        lowerer: Lowerer,
    ) -> dict[str, Any]:
        """도구 결과를 ``functionResponse`` part로 바꾼다.

        ``blocks``가 있으면 ``response``의 ``result``에 ``content``와 search result 텍스트를
        넣고 ``structured_content``는 보내지 않는다. search result가 아닌 블록은 ``parts``로
        보낸다. ``blocks``가 없으면 ``structured_content``를, 그것도 없으면 ``content``를
        ``response``로 쓴다.
        """
        if block.vendor == SOURCE and block.native:
            return dict(block.native)

        name = block.name or call_names.get(block.tool_use_id) or block.tool_use_id
        searches = []
        for candidate_block in block.blocks:
            if isinstance(candidate_block, SearchResultBlock):
                searches.append(candidate_block)
        response: Any
        if block.blocks:
            texts = [block.content, lowerer.lower_text(searches) if searches else ""]
            non_empty_texts = []
            for text in texts:
                if text:
                    non_empty_texts.append(text)
            response = {"result": "\n".join(non_empty_texts)}
        elif block.structured_content is None:
            response = {"result": block.content}
        elif isinstance(block.structured_content, dict):
            response = block.structured_content
        else:
            response = {"result": block.structured_content}

        function_response: dict[str, Any] = {"name": name, "response": response}
        if block.tool_use_id:
            function_response["id"] = block.tool_use_id
        rendered: list[dict[str, Any]] = []
        for nested_block in block.blocks:
            if isinstance(nested_block, SearchResultBlock) or has_opaque_media_reference(
                nested_block
            ):
                continue
            rendered_part = as_gemini_part(nested_block)
            if rendered_part is not None:
                rendered.append(rendered_part)
        if rendered:
            function_response["parts"] = rendered
        return {"functionResponse": function_response}

    def _tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """공통 함수 도구와 Gemini 원본 도구를 요청의 tools 목록으로 구성한다."""
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
                    # ``parameters``와 배타적이다. 둘을 함께 실으면 안 된다.
                    # 이 필드는 일반 JSON Schema를 그대로 받는다.
                    "parametersJsonSchema": tool.input_schema,
                }
            )
        if declarations:
            native.insert(0, {"functionDeclarations": declarations})
        return native

    def is_terminal(self, frame: SseEvent) -> bool:
        """종료 표지가 없다. 스트림이 끊기면 끝이다."""
        _ = frame
        return False

    def decode(self, frame: SseEvent) -> dict[str, Any] | None:
        """빈 프레임과 종료 표식을 제외하고 SSE data의 JSON을 해석한다."""
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
            raise VendorStreamError(SOURCE, parsed["error"])
        return parsed

    def to_hub(self) -> StreamMapper[Any, Any]:
        """스트림마다 독립적인 벤더 응답→허브 델타 변환기를 생성한다."""
        return _ToHub()

    def to_native(self) -> NativeAccumulator[GenerateContentResponse]:
        """스트림마다 독립적인 원본 Response 누적기를 생성한다."""
        return _ToNative()

    def token_count_calls(
        self, body: dict[str, Any]
    ) -> Generator[JsonCall, dict[str, Any], TokenCount]:
        """``POST /{version}/models/{model}:countTokens``로 입력 토큰 수를 얻는다.

        생성 body 전체를 ``generateContentRequest``로 감싸고 그 안에 ``models/{model}``을
        넣는다. countTokens는 감싸지 않은 ``systemInstruction``, ``generationConfig``와
        ``model``이 없는 ``generateContentRequest``를 거부한다.

        Raises:
            ValueError: 모델이 없을 때. Bridge의 ``model`` 또는 :meth:`for_model`로 지정한다.
            MappingError: 응답에 정수 ``totalTokens``가 없을 때.
        """
        if not self.model:
            raise ValueError(
                "generate_content needs a model; set Bridge(model=...) or for_model(model)"
            )
        payload = yield JsonCall(
            f"/{self.version}/models/{self.model}:countTokens",
            {"generateContentRequest": {"model": f"models/{self.model}", **body}},
        )
        total_tokens = payload.get("totalTokens")
        if not isinstance(total_tokens, int):
            raise MappingError("gemini countTokens response must have an integer totalTokens")
        return TokenCount(input_tokens=total_tokens, tokenized=None)


generate_content = GenerateContentAdapter()
"""기본 인스턴스. Bridge의 ``model``과 ``api_key``로 대상을 지정한다."""
