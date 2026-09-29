"""인용 어휘. ``<cite id="d1">본문</cite>``을 인용이 붙은 text block으로 올린다."""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from typing import Any

from ..blocks import AnnotationBlock, Citation, CitationBlock, ContentBlock, TextBlock
from ..contentstream import ContentSchema, Enter, Exit, ParseEvent, TagParser, TextRun
from ..errors import MappingError
from ..hub import HubResponse
from ..mapper import StreamMapper
from ..seq import Seq
from ..vocabulary import Vocabulary

__all__ = ["CITE_PATH", "Citation", "CitationBlock", "CiteVocabulary", "cite_schema"]

CITE_PATH = "/cite"


def cite_schema(*, tag: str = "cite", alias: Iterable[str] = ("rag",)) -> ContentSchema:
    """인용 태그 스키마. 태그 이름을 바꿔도 경로는 ``/cite``로 고정된다."""
    return ContentSchema().bind(CITE_PATH, tag=tag, alias=alias, attr=("id",))


_Key = tuple[str | None, Seq]
"""원래 text 블록의 식별자. ``(source, seq)``다."""


class _CiteMapper:
    """허브 델타의 본문에서 인용을 인용 text block으로 바꾼다.

    본문이 아닌 블록은 내용을 바꾸지 않는다. 추론과 도구 호출이 그렇다.

    원래 text 블록 하나를 인용 전후로 여러 조각으로 나누고, 각 조각의 seq는 원래 블록의 seq
    끝에 조각 번호 0, 1, 2, …를 추가한 값이다. 인용 조각의 text를 먼저 보내고 종료 태그
    ``</cite>``에서 같은 seq로 citation delta를 보낸다. 따라서 이미 전달한 text를 취소하거나
    최종 응답까지 버퍼링하지 않아도 병합 결과는 Anthropic형 ``TextBlock.citations``가 된다.

    text가 아닌 블록의 seq에는 0을 추가한다. :class:`AnnotationBlock`의 ``target_seq``에도 0을
    추가해 원래 text 블록의 첫 조각을 가리키게 한다.
    """

    def __init__(self, schema: ContentSchema) -> None:
        self._parser = TagParser(schema)
        self._active_key: _Key | None = None
        self._active_segment: Seq | None = None
        self._segments: dict[_Key, list[Seq]] = {}
        self._pending: dict[_Key, dict[str, Any]] = {}
        self._last_key: _Key | None = None
        self._open_id: str | None = None
        self._open_key: _Key | None = None

    def map(self, delta: HubResponse) -> list[HubResponse]:
        """delta 하나의 블록을 조각 seq를 가진 블록으로 바꾼다.

        Raises:
            MappingError: ``seq``가 없는 블록이 있을 때. 어댑터는 모든 content block에 seq를
                채워야 한다.
        """
        blocks: list[ContentBlock] = []
        for block in delta.content:
            if block.seq is None:
                raise MappingError(f"content block {block.type!r} has no seq")
            if isinstance(block, TextBlock):
                key = (block.source, block.seq)
                self._last_key = key
                blocks.extend(self._metadata(block, key))
                if "text" in block.model_fields_set:
                    blocks.extend(self._lift(block.text, key))
                continue
            update: dict[str, Any] = {"seq": (*block.seq, 0)}
            if isinstance(block, AnnotationBlock) and block.target_seq is not None:
                update["target_seq"] = (*block.target_seq, 0)
            blocks.append(block.model_copy(update=update))
        return [delta.model_copy(update={"content": blocks})]

    def flush(self) -> list[HubResponse]:
        """스트림이 끝났을 때 남은 것을 내보낸다.

        파서 버퍼의 미완성 태그는 마지막으로 받은 text 블록의 text가 되고, 종료 태그가 없는
        인용은 지금까지의 구간으로 확정된다. 본문 없이 메타데이터만 받은 text 블록은 첫 조각
        seq로 보낸다.
        """
        blocks: list[ContentBlock] = []
        if self._last_key is not None:
            for event in self._parser.flush():
                blocks.extend(self._apply(event, self._last_key))
        if self._open_id is not None:
            blocks.append(self._close())
        for key in list(self._pending):
            segment, fields = self._new_segment(key)
            blocks.append(TextBlock(seq=segment, source=key[0], **fields))
        if not blocks:
            return []
        return [HubResponse(content=blocks)]

    # ---- 내부 ----

    def _lift(self, text: str, key: _Key) -> list[ContentBlock]:
        """text 조각을 파서에 넣고 나온 이벤트를 블록으로 바꾼다."""
        blocks: list[ContentBlock] = []
        for event in self._parser.feed(text):
            blocks.extend(self._apply(event, key))
        return blocks

    def _apply(self, event: ParseEvent, key: _Key) -> list[ContentBlock]:
        """이벤트 하나를 블록으로 옮긴다.

        text는 지금 쓰는 조각에 이어 쓰고, 조각이 없거나 다른 원래 블록의 조각이면 새 조각을
        만든다. 인용 시작 태그는 열린 인용을 확정하고 새 인용을 연다. 종료 태그는 열린 인용을
        확정한다.

        ``match``의 패턴 위치에 상수 이름을 쓰지 않는다. 거기서 ``CITE_PATH``는 값 비교가
        아니라 이름 바인딩이 되어 모든 경로에 걸린다.
        """
        if isinstance(event, TextRun):
            if self._active_key != key or self._active_segment is None:
                self._active_segment, fields = self._new_segment(key)
                self._active_key = key
            else:
                fields = {}
            return [
                TextBlock(
                    text=event.content,
                    seq=self._active_segment,
                    source=key[0],
                    **fields,
                )
            ]

        if isinstance(event, Enter) and event.path == CITE_PATH:
            closed: list[ContentBlock] = []
            if self._open_id is not None:
                closed.append(self._close())
            self._active_segment = None
            self._active_key = key
            self._open(event.attributes, key)
            return closed

        if isinstance(event, Exit) and event.path == CITE_PATH:
            if self._open_id is None:
                return []
            closed_block = self._close()
            self._active_segment = None
            return [closed_block]

        return []

    def _open(self, attrs: dict[str, str], key: _Key) -> None:
        """인용 시작 태그의 ``id``와 그 태그가 나온 원래 블록을 기록한다."""
        self._open_id = attrs.get("id", "")
        self._open_key = key

    def _close(self) -> TextBlock:
        """열린 인용을 지금 쓰는 조각의 citation delta로 확정한다.

        인용 안에 text가 없었으면 원래 블록의 새 조각을 만들어 citation을 둔다.
        """
        key = self._open_key or self._last_key
        assert key is not None, "an open citation always records its block key"
        if self._active_segment is None:
            self._active_segment, fields = self._new_segment(key)
        else:
            fields = {}
        block = TextBlock(
            seq=self._active_segment,
            source=key[0],
            citations=[Citation(type="document", source="cite", id=self._open_id or "")],
            **fields,
        )
        self._open_id = None
        self._open_key = None
        return block

    def _metadata(self, block: TextBlock, key: _Key) -> list[ContentBlock]:
        """text 블록의 메타데이터(``native``, ``signature``, ``citations``)를 조각으로 옮긴다.

        조각이 아직 없으면 보류했다가 첫 조각을 만들 때 넣는다. 조각이 있으면 ``native``와
        ``signature``는 모든 조각에, ``citations``는 마지막 조각에 보낸다.
        """
        fields: dict[str, Any] = {}
        if block.native:
            fields["native"] = block.native
        if block.signature:
            fields["signature"] = block.signature
        if "citations" in block.model_fields_set and block.citations:
            fields["citations"] = block.citations
        if not fields:
            return []

        segments = self._segments.get(key) or []
        if not segments:
            pending = self._pending.setdefault(key, {})
            for name, value in fields.items():
                if name == "citations":
                    pending.setdefault(name, []).extend(value)
                else:
                    pending[name] = value
            return []

        out: list[ContentBlock] = []
        metadata = {name: value for name, value in fields.items() if name != "citations"}
        for segment in segments:
            if metadata:
                out.append(TextBlock(seq=segment, source=block.source, **metadata))
        if block.citations:
            out.append(TextBlock(seq=segments[-1], source=block.source, citations=block.citations))
        return out

    def _new_segment(self, key: _Key) -> tuple[Seq, dict[str, Any]]:
        """원래 블록의 다음 조각 seq를 만들고, 보류 중인 메타데이터를 꺼내 함께 돌려준다."""
        existing = self._segments.setdefault(key, [])
        segment = (*key[1], len(existing))
        existing.append(segment)
        return segment, self._pending.pop(key, {})


class CiteVocabulary(Vocabulary):
    """인용을 올리고 내리는 규칙 묶음.

        bridge = Bridge(..., vocabularies=[CiteVocabulary()])

    :meth:`prompt_hint`가 있는 이유가 실측에서 나왔다. 모델은 프롬프트가 지시하지 않으면 이
    태그를 쓰지 않는다. 어휘가 파서 스키마만 들고 프롬프트 지시를 들지 않으면, 소비 앱이 그
    문장을 따로 관리하다 파서와 어긋난다. Java 구현이 어긋날 수 있었던 자리다.
    """

    # CitationBlock은 구 버전 저장 JSON을 읽고 내리기 위해서만 등록한다. 새 citation은
    # TextBlock의 중첩 모델이라 content-block 레지스트리에 넣지 않는다.
    blocks = (CitationBlock,)
    name = "cite"

    def __init__(self, *, tag: str = "cite", alias: Iterable[str] = ("rag",)) -> None:
        self._tag = tag
        self._alias = tuple(alias)
        self._schema = cite_schema(tag=tag, alias=self._alias)

    @property
    def schema(self) -> ContentSchema:
        return self._schema

    def prompt_hint(self) -> str:
        """모델에 이 어휘를 쓰라고 알리는 문장. 시스템 프롬프트에 넣는다."""
        return (
            f'근거가 있는 구간은 <{self._tag} id="문서ID">본문</{self._tag}>로 감싸세요. '
            f"감싼 본문은 답변에 그대로 남아야 하며 태그만 덧붙입니다."
        )

    def lower(self, blocks: Sequence[ContentBlock]) -> list[ContentBlock] | None:
        """인용을 본문에 태그로 되끼운다.

        인용 구간의 텍스트는 본문에도 실려 있으므로 인용 블록을 따로 이어붙이면 같은 문장이
        두 번 나간다. 본문을 인덱스로 잘라 그 자리에만 태그를 감싼다.

        대화를 이어가면 이전 답변이 요청에 되실리므로 왕복이 실제로 일어난다. 여기가 내놓는
        태그와 :attr:`schema`가 읽는 태그가 같아야 하고, 둘이 한 객체에 있으므로 어긋날 수 없다.
        """
        legacy = [b for b in blocks if isinstance(b, CitationBlock)]
        nested = any(isinstance(b, TextBlock) and b.citations for b in blocks)
        if not legacy and not nested:
            return None

        lowered: list[ContentBlock] = []
        for block in blocks:
            if not isinstance(block, TextBlock) or not block.citations:
                lowered.append(block)
                continue
            portable = [
                citation
                for citation in block.citations
                if citation.id and citation.source in (None, "cite") and not citation.native
            ]
            text = self._wrap(portable[0].id, block.text) if portable else block.text
            lowered.append(block.model_copy(update={"text": text, "citations": []}))

        if not legacy:
            return lowered

        full = "".join(b.text for b in lowered if isinstance(b, TextBlock))
        merged = TextBlock(text=self._splice(full, legacy))

        out: list[ContentBlock] = []
        placed = False
        for block in lowered:
            if isinstance(block, TextBlock):
                if not placed:
                    out.append(merged)
                    placed = True
            elif not isinstance(block, CitationBlock):
                out.append(block)
        if not placed:
            out.insert(0, merged)
        return out

    def _splice(self, full: str, cites: Sequence[CitationBlock]) -> str:
        """본문에 인용 태그를 끼운다.

        인용 텍스트는 블록의 ``text`` 필드가 아니라 인덱스로 본문에서 잘라 쓴다. 인덱스가
        권위 있는 정보이기 때문이다. 소비 앱이 본문을 편집했다면 ``text``는 낡은 값이고
        인덱스는 새 본문을 가리킨다. 인덱스가 범위를 벗어난 경우에만 ``text``로 물러선다.

        겹치는 구간은 앞선 것만 살린다. 겹친 태그를 만들면 파서가 되읽을 수 없다.
        """
        parts: list[str] = []
        cursor = 0
        for cite in sorted(cites, key=lambda c: (c.start_index, c.end_index)):
            start, end = cite.start_index, cite.end_index
            if not (0 <= start <= end <= len(full)):
                # 인덱스를 믿을 수 없다. 태그만 뒤에 덧붙인다.
                parts.append(self._wrap(cite.id, cite.text))
                continue
            if start < cursor:
                continue
            parts.append(full[cursor:start])
            parts.append(self._wrap(cite.id, full[start:end]))
            cursor = end
        parts.append(full[cursor:])
        return "".join(parts)

    def _wrap(self, cite_id: str, body: str) -> str:
        safe_id = html.escape(cite_id, quote=True)
        return f'<{self._tag} id="{safe_id}">{body}</{self._tag}>'

    def lift_mapper(self) -> StreamMapper[Any, Any]:
        return _CiteMapper(self._schema)
