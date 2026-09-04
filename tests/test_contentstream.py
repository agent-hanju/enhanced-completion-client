"""태그 파서.

Java ``EnhancedCompletionDeltaMapperTest`` 1,027줄의 계약을 옮긴다. 핵심은 청크 경계다. 한
글자씩, 태그 중간, 닫는 태그 끝과 여는 태그 시작이 한 청크에 들어오는 경우를 모두 본다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from enhanced_completion import ContentSchema, Enter, ParseEvent, TagParser, TextRun

CITE = "/cite"


def schema() -> ContentSchema:
    return ContentSchema().bind(CITE, tag="cite", alias=("rag",), attr=("id",))


def run(*chunks: str, sch: ContentSchema | None = None) -> list[ParseEvent]:
    parser = TagParser(sch or schema())
    out: list[ParseEvent] = []
    for chunk in chunks:
        out.extend(parser.feed(chunk))
    out.extend(parser.flush())
    return out


def text_of(events: Sequence[ParseEvent]) -> str:
    return "".join(e.content for e in events if isinstance(e, TextRun))


def shape(events: Sequence[ParseEvent]) -> list[str]:
    out: list[str] = []
    for event in events:
        if isinstance(event, TextRun):
            out.append("T")
        elif isinstance(event, Enter):
            out.append(f"E{event.path}")
        else:
            out.append(f"X{event.path}")
    return out


class TestPlainText:
    def test_text_without_tags(self) -> None:
        assert text_of(run("그냥 텍스트")) == "그냥 텍스트"

    def test_multiple_chunks(self) -> None:
        assert text_of(run("가", "나", "다")) == "가나다"

    def test_empty_chunk_is_ignored(self) -> None:
        """스트림 도중 빈 델타를 보내는 제공자가 있다."""
        assert text_of(run("a", "", "b")) == "ab"

    def test_lone_angle_bracket_is_text(self) -> None:
        assert text_of(run("2 < 3")) == "2 < 3"

    def test_unknown_tag_passes_through(self) -> None:
        assert text_of(run("<b>굵게</b>")) == "<b>굵게</b>"

    def test_tag_name_boundary_is_respected(self) -> None:
        """구 0.1.6은 ``<cite`` 접두만 봐서 ``<citation>``도 태그로 걸렸다."""
        assert text_of(run("<citation>x</citation>")) == "<citation>x</citation>"
        assert shape(run("<citation>x</citation>")) == ["T", "T", "T"]


class TestSingleCitation:
    def test_bulk(self) -> None:
        events = run('앞<cite id="d1">본문</cite>뒤')
        assert shape(events) == ["T", f"E{CITE}", "T", f"X{CITE}", "T"]
        assert text_of(events) == "앞본문뒤"

    def test_char_by_char(self) -> None:
        source = '앞<cite id="d1">본문</cite>뒤'
        events = run(*source)
        assert text_of(events) == "앞본문뒤"
        assert shape(events).count(f"E{CITE}") == 1
        assert shape(events).count(f"X{CITE}") == 1

    def test_attribute_is_captured(self) -> None:
        events = run('<cite id="d1">x</cite>')
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "d1"}

    def test_single_quoted_attribute(self) -> None:
        events = run("<cite id='d1'>x</cite>")
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "d1"}

    def test_unquoted_attribute(self) -> None:
        events = run("<cite id=d1>x</cite>")
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "d1"}

    def test_undeclared_attribute_is_dropped(self) -> None:
        events = run('<cite id="d1" score="0.9">x</cite>')
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "d1"}

    def test_tag_without_attributes(self) -> None:
        events = run("<cite>x</cite>")
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {}

    def test_empty_citation_body(self) -> None:
        assert shape(run('<cite id="d1"></cite>')) == [f"E{CITE}", f"X{CITE}"]


class TestChunkBoundaries:
    @pytest.mark.parametrize("cut", range(1, 24))
    def test_every_split_point_of_an_open_tag(self, cut: int) -> None:
        source = '앞<cite id="d1">본문</cite>뒤'
        events = run(source[:cut], source[cut:])
        assert text_of(events) == "앞본문뒤"
        assert shape(events).count(f"E{CITE}") == 1

    def test_split_inside_tag_name(self) -> None:
        events = run("<ci", 'te id="d1">x</cite>')
        assert shape(events) == [f"E{CITE}", "T", f"X{CITE}"]

    def test_split_inside_attribute_value(self) -> None:
        events = run('<cite id="d', '1">x</cite>')
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "d1"}

    def test_split_inside_closing_tag(self) -> None:
        events = run('<cite id="d1">x</ci', "te>")
        assert shape(events) == [f"E{CITE}", "T", f"X{CITE}"]

    def test_close_end_and_open_start_in_one_chunk(self) -> None:
        """닫는 태그 끝과 여는 태그 시작이 한 청크에 들어오는 경우."""
        events = run('<cite id="a">1</ci', 'te>사이<cite id="b">2</cite>')
        assert text_of(events) == "1사이2"
        assert shape(events).count(f"E{CITE}") == 2

    def test_consecutive_citations_without_gap(self) -> None:
        events = run('<cite id="a">1</cite><cite id="b">2</cite>')
        assert shape(events) == [
            f"E{CITE}",
            "T",
            f"X{CITE}",
            f"E{CITE}",
            "T",
            f"X{CITE}",
        ]

    def test_consecutive_citations_char_by_char(self) -> None:
        source = '<cite id="a">1</cite><cite id="b">2</cite>'
        events = run(*source)
        assert text_of(events) == "12"
        assert shape(events).count(f"E{CITE}") == 2

    def test_partial_non_tag_is_released_early(self) -> None:
        """알려진 태그의 접두가 될 수 없으면 기다리지 않는다."""
        assert text_of(run("<zz", "top>")) == "<zztop>"


class TestAlias:
    def test_rag_alias_maps_to_same_path(self) -> None:
        events = run('<rag id="d1">x</rag>')
        assert shape(events) == [f"E{CITE}", "T", f"X{CITE}"]

    def test_alias_char_by_char(self) -> None:
        events = run(*'<rag id="d1">x</rag>')
        assert shape(events).count(f"E{CITE}") == 1

    def test_open_with_tag_close_with_alias(self) -> None:
        """같은 경로에 묶여 있으므로 짝이 맞는다."""
        events = run('<cite id="d1">x</rag>')
        assert shape(events) == [f"E{CITE}", "T", f"X{CITE}"]


class TestMalformed:
    def test_unclosed_tag_flushes_as_text(self) -> None:
        assert text_of(run("<cite id=")) == "<cite id="

    def test_unclosed_citation_leaves_path_open(self) -> None:
        parser = TagParser(schema())
        events = list(parser.feed('<cite id="d1">본문'))
        events.extend(parser.flush())
        assert shape(events) == [f"E{CITE}", "T"]
        assert parser.path == CITE

    def test_stray_close_is_text(self) -> None:
        assert text_of(run("</cite>")) == "</cite>"

    def test_nested_same_tag_does_not_transition_twice(self) -> None:
        """``/cite`` 아래에 ``cite``가 바인딩되어 있지 않으므로 안쪽은 텍스트다."""
        events = run('<cite id="a">1<cite id="b">2</cite>')
        assert shape(events) == [f"E{CITE}", "T", "T", "T", f"X{CITE}"]
        assert text_of(events) == '1<cite id="b">2'

    def test_self_closing_tag_enters_and_exits(self) -> None:
        events = run('<cite id="d1"/>뒤')
        assert shape(events) == [f"E{CITE}", f"X{CITE}", "T"]

    def test_angle_bracket_inside_attribute_value(self) -> None:
        events = run('<cite id="a>b">x</cite>')
        enter = next(e for e in events if isinstance(e, Enter))
        assert enter.attributes == {"id": "a>b"}


class TestNestedPaths:
    def build(self) -> ContentSchema:
        return ContentSchema().bind("/doc", tag="doc", attr=("id",)).bind("/doc/title", tag="title")

    def test_nested_enter_and_exit(self) -> None:
        events = run('<doc id="1"><title>제목</title>본문</doc>', sch=self.build())
        assert shape(events) == ["E/doc", "E/doc/title", "T", "X/doc/title", "T", "X/doc"]

    def test_child_tag_outside_parent_is_text(self) -> None:
        assert text_of(run("<title>x</title>", sch=self.build())) == "<title>x</title>"

    def test_nested_char_by_char(self) -> None:
        source = '<doc id="1"><title>제목</title>본문</doc>'
        events = run(*source, sch=self.build())
        assert text_of(events) == "제목본문"
        assert shape(events).count("E/doc/title") == 1


class TestSchemaValidation:
    def test_sibling_conflict_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="sibling path"):
            ContentSchema().bind("/a", tag="x").bind("/b", tag="x")

    def test_same_tag_on_nested_paths_is_allowed(self) -> None:
        """부모가 다르면 어느 경로로 갈지 정해지므로 허용한다."""
        sch = ContentSchema().bind("/a", tag="x").bind("/a/x", tag="x")
        assert sch.paths_for("x") == frozenset({"/a", "/a/x"})

    def test_root_path_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not be root"):
            ContentSchema().bind("/", tag="x")

    def test_relative_path_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            ContentSchema().bind("cite", tag="x")

    def test_empty_tag_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ContentSchema().bind("/a", tag="")


class TestRawCapture:
    def test_raw_is_off_by_default(self) -> None:
        """긴 스트림에서 입력 길이에 비례해 메모리가 늘지 않도록 기본을 꺼둔다."""
        parser = TagParser(schema())
        parser.feed("hello")
        assert parser.raw == ""

    def test_raw_can_be_enabled(self) -> None:
        parser = TagParser(schema(), capture_raw=True)
        parser.feed("<cite ")
        parser.feed('id="d1">x</cite>')
        assert parser.raw == '<cite id="d1">x</cite>'
