"""인용 어휘. ``<cite id="d1">본문</cite>``을 구조화된 블록으로 들어올린다.

속성형을 쓴다. 실측에서 확인한 것이 근거다. 모델은 인용 문법을 스스로 정하지 않는다.
프롬프트가 지시하지 않으면 입력 문서의 태그를 흉내낸다. 즉 이 문법은 우리가 고르는 것이고,
Java 초기 구현의 중첩형(``<cite><id>d1</id>본문</cite>``)보다 파서 상태가 하나 적다. 중첩형은
``id`` 태그 안의 텍스트를 본문 인덱스에서 빼는 별도 처리가 필요하다.

``common-hitl-chat``의 ``CitationAwareLlmProvider``도 속성형을 쓰므로 두 구현이 같은 어휘를
공유한다.

인용 구간의 텍스트는 **본문에도 그대로 실린다.** 인용은 그 텍스트가 어디서 왔는지를 가리키는
곁정보이므로 본문에서 빼면 답변이 끊긴다. ``start_index``와 ``end_index``가 본문 문자열 안의
위치를 가리킨다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from ..blocks import ContentBlock, TextBlock
from ..contentstream import ContentSchema, Enter, Exit, ParseEvent, TagParser, TextRun
from ..hub import HubResponse
from ..mapper import StreamMapper
from ..vocabulary import Vocabulary

__all__ = ["CITE_PATH", "CitationBlock", "CiteVocabulary", "cite_schema"]

CITE_PATH = "/cite"


class CitationBlock(ContentBlock):
    """본문 한 구간의 근거.

    ``index``를 주지 않는다. 인용은 조각으로 도착하지 않고 닫는 태그에서 한 번에 완성되므로
    병합기가 짝지을 키가 필요 없다. 키가 없는 원소는 도착 순서대로 덧붙는다.
    """

    type: Literal["citation"] = "citation"
    id: str = ""
    text: str = ""
    start_index: int = 0
    end_index: int = 0


def cite_schema(*, tag: str = "cite", alias: Iterable[str] = ("rag",)) -> ContentSchema:
    """인용 태그 스키마. 태그 이름을 바꿔도 경로는 ``/cite``로 고정된다."""
    return ContentSchema().bind(CITE_PATH, tag=tag, alias=alias, attr=("id",))


class _CiteMapper:
    """허브 델타의 본문에서 인용을 들어올린다.

    본문이 아닌 블록은 손대지 않고 통과시킨다. 추론과 도구 호출이 그렇다.

    상태 넷을 든다. 본문 커서, 열린 인용의 식별자, 그 인용의 시작 위치, 모인 인용 텍스트다.
    Java ``EnhancedCompletionDeltaMapper``와 같은 구성이되 ``id``를 태그 속성에서 받으므로
    식별자 버퍼가 없다.
    """

    def __init__(self, schema: ContentSchema) -> None:
        self._parser = TagParser(schema)
        self._cursor = 0
        self._open_id: str | None = None
        self._open_start = 0
        self._open_text: list[str] = []

    def map(self, delta: HubResponse) -> list[HubResponse]:
        blocks: list[ContentBlock] = []
        for block in delta.content:
            if isinstance(block, TextBlock):
                blocks.extend(self._lift(block.text, block.index))
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
            blocks.extend(self._apply(event, TextBlock.model_fields["index"].default))
        if self._open_id is not None:
            blocks.append(self._close())
        if not blocks:
            return []
        return [HubResponse(content=blocks)]

    # ---- 내부 ----

    def _lift(self, text: str, index: int | None) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for event in self._parser.feed(text):
            blocks.extend(self._apply(event, index))
        return blocks

    def _apply(self, event: ParseEvent, index: int | None) -> list[ContentBlock]:
        """이벤트 하나를 블록으로 옮긴다.

        ``match``의 패턴 위치에 상수 이름을 쓰지 않는다. 거기서 ``CITE_PATH``는 값 비교가
        아니라 이름 바인딩이 되어 모든 경로에 걸린다.
        """
        if isinstance(event, TextRun):
            # 인용 안이든 밖이든 본문에는 그대로 실린다. 빼면 답변이 끊긴다.
            self._cursor += len(event.content)
            if self._open_id is not None:
                self._open_text.append(event.content)
            return [TextBlock(text=event.content, index=index)]

        if isinstance(event, Enter) and event.path == CITE_PATH:
            if self._open_id is not None:
                # 중첩 인용은 규격에 없다. 앞선 것을 여기서 닫아 상태를 잃지 않는다.
                closed = self._close()
                self._open(event.attributes)
                return [closed]
            self._open(event.attributes)
            return []

        if isinstance(event, Exit) and event.path == CITE_PATH:
            if self._open_id is None:
                return []
            return [self._close()]

        return []

    def _open(self, attrs: dict[str, str]) -> None:
        self._open_id = attrs.get("id", "")
        self._open_start = self._cursor
        self._open_text = []

    def _close(self) -> CitationBlock:
        block = CitationBlock(
            id=self._open_id or "",
            text="".join(self._open_text),
            start_index=self._open_start,
            end_index=self._cursor,
        )
        self._open_id = None
        self._open_text = []
        return block


class CiteVocabulary(Vocabulary):
    """인용을 올리고 내리는 규칙 묶음.

        bridge = Bridge(..., vocabularies=[CiteVocabulary()])

    :meth:`prompt_hint`가 있는 이유가 실측에서 나왔다. 모델은 프롬프트가 지시하지 않으면 이
    태그를 쓰지 않는다. 어휘가 파서 스키마만 들고 프롬프트 지시를 들지 않으면, 소비 앱이 그
    문장을 따로 관리하다 파서와 어긋난다. Java 구현이 어긋날 수 있었던 자리다.
    """

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
        cites = [b for b in blocks if isinstance(b, CitationBlock)]
        if not cites:
            return None

        full = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        merged = TextBlock(text=self._splice(full, cites))

        out: list[ContentBlock] = []
        placed = False
        for block in blocks:
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
        return f'<{self._tag} id="{cite_id}">{body}</{self._tag}>'

    def lift_mapper(self) -> StreamMapper[Any, Any]:
        return _CiteMapper(self._schema)
