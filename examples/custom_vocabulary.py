"""Executable example of a non-citation XML-like custom vocabulary."""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from typing import Any, Literal

from enhanced_completion import (
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
from enhanced_completion.vendors import chat_completions, generate_content, messages, responses
from enhanced_completion.vendors.base import VendorAdapter


class BadgeBlock(ContentBlock):
    """Application-defined JSON content block."""

    type: Literal["badge"] = "badge"
    level: str = "info"
    text: str = ""


BADGE_PATH = "/badge"


class _BadgeMapper:
    """Lift ``<badge>`` text from arbitrary stream chunk boundaries."""

    def __init__(self, schema: ContentSchema) -> None:
        self._parser = TagParser(schema)
        self._level: str | None = None
        self._text: list[str] = []
        self._source: str | None = None

    def map(self, delta: HubResponse) -> list[HubResponse]:
        blocks: list[ContentBlock] = []
        for block in delta.content:
            if not isinstance(block, TextBlock):
                blocks.append(block)
                continue
            if block.native or block.signature:
                blocks.append(
                    TextBlock(source=block.source, native=block.native, signature=block.signature)
                )
            if "text" in block.model_fields_set:
                for event in self._parser.feed(block.text):
                    blocks.extend(self._apply(event, block.source))
        return [delta.model_copy(update={"content": blocks})]

    def flush(self) -> list[HubResponse]:
        blocks: list[ContentBlock] = []
        for event in self._parser.flush():
            blocks.extend(self._apply(event, self._source))
        if self._level is not None:
            blocks.append(self._close())
        return [HubResponse(content=blocks)] if blocks else []

    def _apply(self, event: TextRun | Enter | Exit, source: str | None) -> list[ContentBlock]:
        if isinstance(event, TextRun):
            if self._level is not None:
                self._text.append(event.content)
                return []
            # One wire text part can split into text/custom/text. Unindexed blocks retain that
            # semantic order when StreamMerger appends them.
            return [TextBlock(text=event.content, source=source)]
        if isinstance(event, Enter) and event.path == BADGE_PATH:
            closed = [self._close()] if self._level is not None else []
            self._level = html.unescape(event.attributes.get("level", "info"))
            self._text = []
            self._source = source
            return closed
        if isinstance(event, Exit) and event.path == BADGE_PATH and self._level is not None:
            return [self._close()]
        return []

    def _close(self) -> BadgeBlock:
        block = BadgeBlock(
            level=self._level or "info",
            text=html.unescape("".join(self._text)),
            source=self._source,
        )
        self._level = None
        self._text = []
        self._source = None
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
    return merger.build()


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
            "escape/unescape하며, 하나의 wire text를 여러 의미 block으로 나눌 때는 파생 block을 ",
            "unindexed로 만들어 최종 병합에서 순서를 보존한다.",
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
