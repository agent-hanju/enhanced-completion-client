"""W3C SSE 규칙에 기반한 SSE 파서. ``event``, ``data``, ``id``와 comment를 모두 지원한다.
W3C 파싱 규칙에 따라 콜론 뒤 공백 하나만 벗기므로 ``data: x``와 ``data:x``가 같은 값으로 파싱된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = ["SseEvent", "SseParser"]


@dataclass(slots=True)
class SseEvent:
    """SSE 전송에서의 이벤트 하나"""

    data: str = ""
    event: str = ""
    id: str = ""
    retry: int | None = None
    comments: list[str] = field(default_factory=list)

    @property
    def is_comment_only(self) -> bool:
        """하트비트처럼 주석만 있는 프레임인지 확인한다"""
        return not self.data and not self.event and not self.id and bool(self.comments)


class SseParser:
    """W3C SSE 규칙으로 문자열 조각을 이벤트로 변환하고 남은 조각을 버퍼에 보존한다.
    청크 경계가 라인 중간이나 ``\\r\\n`` 사이에 있어도 보정한다.

    feed·flush는 한 소비자가 순차 호출해야 한다. 미완성 라인과 현재 이벤트를 보관하며
    크기 상한은 두지 않는다. 한 조각의 처리는 버퍼 길이에 비례하고, 줄바꿈 없이 작은
    조각을 반복하면 문자열 복사로 전체 입력 길이 n에 대해 최악 O(n²)이 될 수 있다.
    추가 공간은 아직 완성되지 않은 이벤트 크기에 비례한다.
    파싱 규격: https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream
    """

    def __init__(self) -> None:
        """SSE 입력 버퍼·이벤트 필드·줄바꿈 해석 상태를 초기화한다."""
        self._buffer = ""
        self._data: list[str] = []
        self._event = ""
        self._id = ""
        self._retry: int | None = None
        self._comments: list[str] = []
        self._dirty = False
        self._pending_cr = False

    def feed(self, chunk: str) -> Iterator[SseEvent]:
        """조각을 넣고 완성된 이벤트를 순서대로 돌려준다."""
        if not isinstance(chunk, str):
            raise TypeError("SseParser.feed requires a string")
        if not chunk:
            return

        if self._pending_cr:
            # 앞 청크가 CR로 끝났다면 이어지는 LF는 같은 줄바꿈의 일부이므로 무시한다.
            self._pending_cr = False
            if chunk.startswith("\n"):
                chunk = chunk[1:]
                if not chunk:
                    return

        self._buffer += chunk

        while True:
            line, sep, rest = self._split_line(self._buffer)
            if sep is None:
                break
            self._buffer = rest
            if sep == "\r" and not rest:
                # LF가 다음 청크에 올 수 있으므로 플래그를 활성화한다.
                self._pending_cr = True
            event = self._consume_line(line)
            if event is not None:
                yield event

    def flush(self) -> Iterator[SseEvent]:
        """스트림이 끝났을 때 남은 줄과 미완성 이벤트 내보낸다.
        마지막 이벤트가 빈 줄로 끝나지 않는 경우의 보정 역할을 겸한다.
        """
        if self._buffer:
            line, self._buffer = self._buffer, ""
            frame = self._consume_line(line)
            if frame is not None:
                yield frame
        if self._dirty:
            yield self._emit()

    @staticmethod
    def _split_line(buffer: str) -> tuple[str, str | None, str]:
        """첫 줄바꿈에서 자른다. 줄바꿈 종류는 \n, \r, \n\r을 인식한다.
        반환 튜플은 (줄, 줄바꿈 종류, 남은 문자열)
        """
        idx_n = buffer.find("\n")
        idx_r = buffer.find("\r")

        if idx_n == -1 and idx_r == -1:
            return buffer, None, buffer  # 줄바꿈 없음
        if idx_r != -1 and (idx_n == -1 or idx_r < idx_n):
            if idx_r + 1 < len(buffer) and buffer[idx_r + 1] == "\n":  # 줄바꿈 \r\n의 경우
                return buffer[:idx_r], "\r\n", buffer[idx_r + 2 :]
            return buffer[:idx_r], "\r", buffer[idx_r + 1 :]  # 줄바꿈 \r의 경우
        return buffer[:idx_n], "\n", buffer[idx_n + 1 :]  # 줄바꿈 \n의 경우

    def _consume_line(self, line: str) -> SseEvent | None:
        """SSE 한 줄을 이벤트 상태에 반영하고 빈 줄이면 준비된 프레임을 반환한다."""
        if not line:
            return self._emit() if self._dirty else None

        if line.startswith(":"):
            self._comments.append(line[1:].lstrip())
            self._dirty = True
            return None

        name, sep, value = line.partition(":")
        if not sep:
            # 콜론 없는 줄은 값이 빈 필드다.
            name, value = line, ""
        elif value.startswith(" "):
            # 공백 하나를 제거한다
            value = value[1:]

        if name == "data":
            self._data.append(value)
            self._dirty = True
        elif name == "event":
            self._event = value
            self._dirty = True
        elif name == "id":
            # NUL이 들어오면 규격상 무시한다.
            if "\x00" not in value:
                self._id = value
                self._dirty = True
        elif name == "retry":
            if value.isdigit():
                self._retry = int(value)
                self._dirty = True
        # 그 밖의 필드 이름은 규격대로 무시한다.
        return None

    def _emit(self) -> SseEvent:
        """누적 상태를 프레임으로 반환하고 재연결 ID를 제외한 이벤트 상태를 초기화한다."""
        event = SseEvent(
            data="\n".join(self._data),
            event=self._event,
            id=self._id,
            retry=self._retry,
            comments=self._comments,
        )
        self._data = []
        self._event = ""
        self._retry = None
        self._comments = []
        self._dirty = False
        # id는 재연결 커서로 쓰기 때문에 다음 이벤트까지 유지한다.
        return event
