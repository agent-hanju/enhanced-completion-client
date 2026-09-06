"""SSE 파서. 청크 경계와 표준에서 허용하는 형태 편차를 검증한다."""

from __future__ import annotations

from completion_bridge import SseParser


def parse_all(*chunks: str) -> list[tuple[str, str, str]]:
    parser = SseParser()
    out: list[tuple[str, str, str]] = []
    for chunk in chunks:
        for frame in parser.feed(chunk):
            out.append((frame.event, frame.data, frame.id))
    for frame in parser.flush():
        out.append((frame.event, frame.data, frame.id))
    return out


class TestDataPrefix:
    def test_space_after_colon_is_stripped(self) -> None:
        assert parse_all("data: hello\n\n") == [("", "hello", "")]

    def test_no_space_after_colon(self) -> None:
        """SSE 표준은 콜론 뒤 공백을 선택 사항으로 둔다."""
        assert parse_all("data:hello\n\n") == [("", "hello", "")]

    def test_only_one_space_is_stripped(self) -> None:
        assert parse_all("data:  hello\n\n") == [("", " hello", "")]

    def test_multiline_data_joined_with_newline(self) -> None:
        assert parse_all("data: a\ndata: b\n\n") == [("", "a\nb", "")]


class TestChunkBoundaries:
    def test_frame_split_mid_line(self) -> None:
        assert parse_all("data: hel", "lo\n\n") == [("", "hello", "")]

    def test_frame_split_at_newline(self) -> None:
        assert parse_all("data: hello\n", "\n") == [("", "hello", "")]

    def test_one_char_at_a_time(self) -> None:
        payload = "event: tick\ndata: 1\n\n"
        assert parse_all(*payload) == [("tick", "1", "")]

    def test_crlf_split_across_chunks(self) -> None:
        """CR로 끝난 청크 뒤의 LF는 같은 줄바꿈이다."""
        assert parse_all("data: hello\r", "\n\r\n") == [("", "hello", "")]

    def test_bare_cr_terminates_line(self) -> None:
        assert parse_all("data: hello\r\r") == [("", "hello", "")]


class TestFrameFields:
    def test_event_name_is_kept(self) -> None:
        assert parse_all("event: message_delta\ndata: x\n\n") == [("message_delta", "x", "")]

    def test_id_persists_across_frames(self) -> None:
        """규격상 id는 다음 프레임까지 유지된다. 재연결 커서로 쓰기 때문이다."""
        frames = parse_all("id: 7\ndata: a\n\ndata: b\n\n")
        assert frames == [("", "a", "7"), ("", "b", "7")]

    def test_field_without_colon_is_empty_value(self) -> None:
        assert parse_all("data\n\n") == [("", "", "")]

    def test_unknown_field_is_ignored(self) -> None:
        assert parse_all("weird: 1\ndata: x\n\n") == [("", "x", "")]

    def test_non_numeric_retry_is_ignored(self) -> None:
        parser = SseParser()
        frames = list(parser.feed("retry: soon\ndata: x\n\n"))
        assert frames[0].retry is None


class TestComments:
    def test_comment_only_frame_is_flagged(self) -> None:
        parser = SseParser()
        frames = list(parser.feed(": heartbeat\n\n"))
        assert len(frames) == 1
        assert frames[0].is_comment_only
        assert frames[0].comments == ["heartbeat"]

    def test_comment_alongside_data_is_not_comment_only(self) -> None:
        parser = SseParser()
        frames = list(parser.feed(": ping\ndata: x\n\n"))
        assert not frames[0].is_comment_only
        assert frames[0].data == "x"


class TestFlush:
    def test_unterminated_frame_is_emitted_on_flush(self) -> None:
        """마지막 프레임을 빈 줄로 끝내지 않는 서버가 있다. 규격 위반이지만 실제로 만난다."""
        assert parse_all("data: last") == [("", "last", "")]

    def test_flush_on_clean_end_emits_nothing_extra(self) -> None:
        assert parse_all("data: a\n\n") == [("", "a", "")]
