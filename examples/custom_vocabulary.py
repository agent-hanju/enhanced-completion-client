"""Executable example of a non-citation XML-like custom vocabulary."""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from typing import Any, Literal

from completion_bridge import (
    ContentBlock,
    ContentSchema,
    Enter,
    Exit,
    HubMessage,
    HubResponse,
    StreamMapper,
    StreamMerger,
    SyncBridge,
    TagParser,
    TextBlock,
    TextRun,
    Vocabulary,
    compose,
)
from completion_bridge.errors import MappingError
from completion_bridge.seq import Seq, finalize
from completion_bridge.vendors import chat_completions, generate_content, messages, responses
from completion_bridge.vendors.base import VendorAdapter


class BadgeBlock(ContentBlock):
    """Application-defined JSON content block."""

    type: Literal["badge"] = "badge"
    level: str = "info"
    text: str = ""


BADGE_PATH = "/badge"


class _BadgeMapper:
    """임의의 chunk 경계로 잘린 ``<badge>`` 태그를 ``BadgeBlock``으로 바꾼다.

    wire text 블록 하나에서 나온 text 조각과 badge는 그 블록의 ``seq`` 끝에 조각 번호 0, 1,
    2, …를 추가한 seq를 받는다. badge의 조각 번호는 시작 태그에서 정하므로 최종 결과에서
    태그가 나온 위치에 놓인다. text가 아닌 블록의 seq에는 0을 추가한다.
    """

    def __init__(self, schema: ContentSchema) -> None:
        self._parser = TagParser(schema)
        self._level: str | None = None
        self._text: list[str] = []
        self._source: str | None = None
        self._badge_seq: Seq | None = None
        self._text_seq: Seq | None = None
        self._last_parent: Seq | None = None
        self._pieces: dict[Seq, int] = {}

    def map(self, delta: HubResponse) -> list[HubResponse]:
        """delta 하나의 블록을 조각 seq를 가진 블록으로 바꾼다."""
        blocks: list[ContentBlock] = []
        for block in delta.content:
            if block.seq is None:
                raise MappingError(f"content block {block.type!r} has no seq")
            if not isinstance(block, TextBlock):
                blocks.append(block.model_copy(update={"seq": (*block.seq, 0)}))
                continue
            self._last_parent = block.seq
            if block.native or block.signature:
                blocks.append(
                    TextBlock(
                        source=block.source,
                        native=block.native,
                        signature=block.signature,
                        seq=self._piece(block.seq),
                    )
                )
                self._text_seq = None
            if "text" in block.model_fields_set:
                for event in self._parser.feed(block.text):
                    blocks.extend(self._apply(event, block.source, block.seq))
        return [delta.model_copy(update={"content": blocks})]

    def flush(self) -> list[HubResponse]:
        """파서에 남은 text와 종료 태그가 없는 badge를 내보낸다."""
        blocks: list[ContentBlock] = []
        if self._last_parent is not None:
            for event in self._parser.flush():
                blocks.extend(self._apply(event, self._source, self._last_parent))
        if self._level is not None:
            blocks.append(self._close())
        return [HubResponse(content=blocks)] if blocks else []

    def _apply(
        self, event: TextRun | Enter | Exit, source: str | None, parent: Seq
    ) -> list[ContentBlock]:
        """파서 이벤트 하나를 text 조각 또는 badge로 옮긴다."""
        if isinstance(event, TextRun):
            if self._level is not None:
                self._text.append(event.content)
                return []
            if self._text_seq is None or self._text_seq[:-1] != parent:
                self._text_seq = self._piece(parent)
            return [TextBlock(text=event.content, source=source, seq=self._text_seq)]
        if isinstance(event, Enter) and event.path == BADGE_PATH:
            closed = [self._close()] if self._level is not None else []
            self._level = html.unescape(event.attributes.get("level", "info"))
            self._text = []
            self._source = source
            self._badge_seq = self._piece(parent)
            self._text_seq = None
            return closed
        if isinstance(event, Exit) and event.path == BADGE_PATH and self._level is not None:
            return [self._close()]
        return []

    def _piece(self, parent: Seq) -> Seq:
        """원래 text 블록의 다음 조각 seq를 만든다."""
        number = self._pieces.get(parent, 0)
        self._pieces[parent] = number + 1
        return (*parent, number)

    def _close(self) -> BadgeBlock:
        """모아 둔 badge 본문으로 ``BadgeBlock``을 만들고 badge 상태를 비운다."""
        block = BadgeBlock(
            level=self._level or "info",
            text=html.unescape("".join(self._text)),
            source=self._source,
            seq=self._badge_seq,
        )
        self._level = None
        self._text = []
        self._source = None
        self._badge_seq = None
        return block


class BadgeVocabulary(Vocabulary):
    """Map ``BadgeBlock`` to and from an XML-like text representation."""

    blocks = (BadgeBlock,)
    name = "badge"

    def __init__(self) -> None:
        self.schema = ContentSchema().bind(
            BADGE_PATH,
            tag="badge",
            alias=("notice",),
            attr=("level",),
        )

    def prompt_hint(self) -> str:
        return '강조할 구간은 <badge level="info|warning">내용</badge>로 출력하세요.'

    def lower(self, blocks: Sequence[ContentBlock]) -> list[ContentBlock] | None:
        if not any(isinstance(block, BadgeBlock) for block in blocks):
            return None
        lowered: list[ContentBlock] = []
        for block in blocks:
            if not isinstance(block, BadgeBlock):
                lowered.append(block)
                continue
            level = html.escape(block.level, quote=True)
            text = html.escape(block.text)
            lowered.append(TextBlock(text=f'<badge level="{level}">{text}</badge>'))
        return lowered

    def lift_mapper(self) -> StreamMapper[Any, Any]:
        return _BadgeMapper(self.schema)


TARGETS: tuple[tuple[str, VendorAdapter], ...] = (
    ("Chat Completions", chat_completions),
    ("Anthropic Messages", messages),
    ("OpenAI Responses", responses),
    ("Gemini GenerateContent", generate_content.for_model("gemini-example")),
)

RAW_CHUNKS = (
    {"choices": [{"delta": {"role": "assistant", "content": "앞 <ba"}}]},
    {"choices": [{"delta": {"content": 'dge level="warning">점검 &amp; 확인'}}]},
    {"choices": [{"delta": {"content": "</badge> 뒤"}, "finish_reason": "stop"}]},
)


def _bridge(adapter: VendorAdapter, vocabulary: BadgeVocabulary) -> SyncBridge:
    return SyncBridge(
        vendor=adapter,
        base_url="https://example.invalid",
        model="example-model",
        vocabularies=[vocabulary],
    )


def _conversation(body: dict[str, Any]) -> dict[str, Any]:
    keys = ("instructions", "system", "messages", "input", "contents")
    return {key: body[key] for key in keys if key in body}


def _lift(vocabulary: BadgeVocabulary) -> HubResponse:
    mapper = compose(chat_completions.to_hub(), vocabulary.lift_mapper())
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    for chunk in RAW_CHUNKS:
        for delta in mapper.map(chunk):
            merger.apply(delta)
    for delta in mapper.flush():
        merger.apply(delta)
    return finalize(merger.build())


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _cell(value: Any) -> str:
    return f"<pre>{html.escape(_pretty(value), quote=False)}</pre>"


def render_document() -> str:
    vocabulary = BadgeVocabulary()
    # Bridge registration makes the Pydantic content union runtime-open for stored JSON too.
    _bridge(chat_completions, vocabulary)
    restored = HubMessage.model_validate(
        {
            "role": "assistant",
            "content": [{"type": "badge", "level": "warning", "text": "저장된 블록"}],
        }
    )
    lifted = _lift(vocabulary)
    assert [type(block).__name__ for block in lifted.content] == [
        "TextBlock",
        "BadgeBlock",
        "TextBlock",
    ]
    assert isinstance(restored.content[0], BadgeBlock)
    assert restored.content[0].text == "저장된 블록"

    history = [HubMessage.of_response(lifted)]
    rows = [
        ("분할된 Chat delta", RAW_CHUNKS),
        ("올림 결과 HubResponse", lifted.model_dump(exclude_none=True)),
        ("저장 JSON 재검증", restored.model_dump(exclude_none=True)),
    ]
    for name, adapter in TARGETS:
        body = _conversation(_bridge(adapter, vocabulary).build_request(history))
        serialized = _pretty(body)
        assert "&amp; 확인</badge>" in serialized
        rows.append((f"{name} 요청 직렬화", body))

    table_rows = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{_cell(value)}</td></tr>" for label, value in rows
    )
    return "\n".join(
        [
            "# 사용자 정의 XML-like vocabulary 실행 예시",
            "",
            "이 결과는 `examples/custom_vocabulary.py`의 `BadgeBlock`/`BadgeVocabulary`를 실제로 ",
            "등록한 뒤, 태그가 세 delta로 잘린 Chat Completions 응답을 구조화된 JSON block으로 ",
            "올리고 네 요청 형식의 text content로 다시 내린 결과다.",
            "",
            "`notice`는 입력 alias이고 직렬화는 canonical `badge` 태그만 사용한다. 속성과 본문은 ",
            "escape/unescape하며, 하나의 wire text를 여러 의미 block으로 나눌 때는 파생 block이 ",
            "원래 block의 `seq` 끝에 조각 번호를 추가한 seq를 받아 최종 결과에서 순서가 정해진다.",
            "",
            "<table>",
            table_rows,
            "</table>",
            "",
        ]
    )


def main() -> None:
    print(render_document())


if __name__ == "__main__":
    main()
