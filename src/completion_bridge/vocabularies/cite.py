"""인용 어휘. ``<cite id="d1">본문</cite>``을 인용이 붙은 text block으로 올린다."""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from typing import Any

from ..blocks import Citation, CitationBlock, ContentBlock, TextBlock
from ..contentstream import ContentSchema, Enter, Exit, ParseEvent, TagParser, TextRun
from ..hub import HubResponse
from ..mapper import StreamMapper
from ..vocabulary import Vocabulary

__all__ = ["CITE_PATH", "Citation", "CitationBlock", "CiteVocabulary", "cite_schema"]

CITE_PATH = "/cite"


def cite_schema(*, tag: str = "cite", alias: Iterable[str] = ("rag",)) -> ContentSchema:
    """인용 태그 스키마. 태그 이름을 바꿔도 경로는 ``/cite``로 고정된다."""
    return ContentSchema().bind(CITE_PATH, tag=tag, alias=alias, attr=("id",))


class _CiteMapper:
    """허브 델타의 본문에서 인용을 들어올린다.

    본문이 아닌 블록은 손대지 않고 통과시킨다. 추론과 도구 호출이 그렇다.

    인용 내부 text는 고유한 block index로 흘리고 닫는 태그에서 같은 index에 citation delta를
    보낸다. 따라서 이미 전달한 text를 취소하거나 최종 응답까지 버퍼링하지 않아도 병합 결과는
    Anthropic형 ``TextBlock.citations``가 된다.
    """

    def __init__(self, schema: ContentSchema) -> None:
        self._parser = TagParser(schema)
        self._next_segment = -(1 << 60)
        self._active_key: tuple[str | None, int | None] | None = None
        self._active_segment: int | None = None
        self._segments: dict[tuple[str | None, int | None], list[int]] = {}
        self._pending: dict[tuple[str | None, int | None], dict[str, Any]] = {}
        self._last_key: tuple[str | None, int | None] = (None, None)
        self._last_source: str | None = None
        self._last_index: int | None = None
        self._open_id: str | None = None
        self._open_key: tuple[str | None, int | None] | None = None
        self._open_source: str | None = None
        self._open_index: int | None = None

    def map(self, delta: HubResponse) -> list[HubResponse]:
        blocks: list[ContentBlock] = []
        for block in delta.content:
            if isinstance(block, TextBlock):
                key = (block.source, block.index)
                self._last_key = key
                self._last_source = block.source
                self._last_index = block.index
                blocks.extend(self._metadata(block, key))
                if "text" in block.model_fields_set:
                    blocks.extend(self._lift(block.text, key, block.index, block.source))
            else:
                blocks.append(block)
        return [delta.model_copy(update={"content": blocks})]

    def flush(self) -> list[HubResponse]:
        """스트림이 끝났을 때 남은 것을 내보낸다.

        파서 버퍼의 미완성 태그는 텍스트가 되고, 닫히지 않은 인용은 지금까지의 구간으로
        확정된다. Java 구현도 같은 자리에서 마지막 citation을 만들었다.
        """
        blocks: list[ContentBlock] = []
        for event in self._parser.flush():
            blocks.extend(
                self._apply(
                    event,
                    self._last_key,
                    self._last_index,
                    self._last_source,
                )
            )
        if self._open_id is not None:
            blocks.append(self._close())
        for key, metadata in list(self._pending.items()):
            index, fields = self._new_segment(key, key[1], key[0])
            fields.update(metadata)
            blocks.append(TextBlock(index=index, source=key[0], **fields))
            self._pending.pop(key, None)
        if not blocks:
            return []
        return [HubResponse(content=blocks)]

    # ---- 내부 ----

    def _lift(
        self,
        text: str,
        key: tuple[str | None, int | None],
        index: int | None,
        source: str | None,
    ) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for event in self._parser.feed(text):
            blocks.extend(self._apply(event, key, index, source))
        return blocks

    def _apply(
        self,
        event: ParseEvent,
        key: tuple[str | None, int | None],
        index: int | None,
        source: str | None = None,
    ) -> list[ContentBlock]:
        """이벤트 하나를 블록으로 옮긴다.

        ``match``의 패턴 위치에 상수 이름을 쓰지 않는다. 거기서 ``CITE_PATH``는 값 비교가
        아니라 이름 바인딩이 되어 모든 경로에 걸린다.
        """
        if isinstance(event, TextRun):
            if self._active_key != key or self._active_segment is None:
                self._active_segment, fields = self._new_segment(key, index, source)
                self._active_key = key
            else:
                fields = {}
            return [
                TextBlock(
                    text=event.content,
                    index=self._active_segment,
                    source=source,
                    **fields,
                )
            ]

        if isinstance(event, Enter) and event.path == CITE_PATH:
            closed: list[ContentBlock] = []
            if self._open_id is not None:
                closed.append(self._close())
            self._active_segment = None
            self._active_key = key
            self._open(event.attributes, key, index, source)
            return closed

        if isinstance(event, Exit) and event.path == CITE_PATH:
            if self._open_id is None:
                return []
            closed_block = self._close()
            self._active_segment = None
            return [closed_block]

        return []

    def _open(
        self,
        attrs: dict[str, str],
        key: tuple[str | None, int | None],
        index: int | None,
        source: str | None,
    ) -> None:
        self._open_id = attrs.get("id", "")
        self._open_key = key
        self._open_index = index
        self._open_source = source

    def _close(self) -> TextBlock:
        key = self._open_key or self._last_key
        if self._active_segment is None:
            self._active_segment, fields = self._new_segment(
                key,
                self._open_index,
                self._open_source,
            )
        else:
            fields = {}
        block = TextBlock(
            index=self._active_segment,
            source=self._open_source,
            citations=[Citation(type="document", source="cite", id=self._open_id or "")],
            **fields,
        )
        self._open_id = None
        self._open_key = None
        self._open_index = None
        self._open_source = None
        return block

    def _metadata(
        self,
        block: TextBlock,
        key: tuple[str | None, int | None],
    ) -> list[ContentBlock]:
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
                out.append(TextBlock(index=segment, source=block.source, **metadata))
        if block.citations:
            out.append(
                TextBlock(index=segments[-1], source=block.source, citations=block.citations)
            )
        return out

    def _new_segment(
        self,
        key: tuple[str | None, int | None],
        original_index: int | None,
        source: str | None,
    ) -> tuple[int, dict[str, Any]]:
        existing = self._segments.setdefault(key, [])
        if not existing and original_index is not None:
            segment = original_index
        else:
            segment = self._next_segment
            self._next_segment -= 1
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
