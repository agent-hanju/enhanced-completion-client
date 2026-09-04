"""SSE 프레임 파서.

프레임 전체를 보존하는 것이 이 모듈의 존재 이유다. Spring의 ``bodyToFlux(String)``이나 벤더
SDK는 ``data`` 필드만 넘겨주는데, 그러면 이름 붙은 이벤트를 쓰는 서버에 대응할 수 없다.
응답 어댑터는 필요에 따라 ``event``, ``data``, ``id``와 comment를 모두 볼 수 있어야 한다.

W3C SSE 규칙을 그대로 따른다. 콜론 뒤 공백 하나만 벗기므로 ``data: x``와 ``data:x``가 같은
값으로 파싱된다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = ["SseFrame", "SseParser"]


@dataclass(slots=True)
class SseFrame:
    """SSE 이벤트 하나.

    주석 전용 프레임도 방출한다. 하트비트가 왔다는 사실 자체가 소비자에게 의미 있는 신호일 수
    있고, 버리는 판단을 파서가 대신하지 않는다.
    """

    data: str = ""
    event: str = ""
    id: str = ""
    retry: int | None = None
    comments: list[str] = field(default_factory=list)

    @property
    def is_comment_only(self) -> bool:
        """하트비트처럼 주석만 있는 프레임인지."""
        return not self.data and not self.event and not self.id and bool(self.comments)


class SseParser:
    """바이트/문자 조각을 프레임으로 자른다.

    청크 경계가 라인 중간이나 ``\\r\\n`` 사이에 떨어져도 된다. 남은 조각을 버퍼에 들고 다음
    입력을 기다린다.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._data: list[str] = []
        self._event = ""
        self._id = ""
        self._retry: int | None = None
        self._comments: list[str] = []
        self._dirty = False
        self._pending_cr = False

    def feed(self, chunk: str) -> Iterator[SseFrame]:
        """조각을 넣고 완성된 프레임을 순서대로 돌려준다."""
        if not chunk:
            return

        if self._pending_cr:
            # 앞 청크가 CR로 끝났다. 이어지는 LF는 같은 줄바꿈의 일부다.
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
                # LF가 다음 청크에 올 수 있다. 판단을 미룬다.
                self._pending_cr = True
            frame = self._consume_line(line)
            if frame is not None:
                yield frame

    def flush(self) -> Iterator[SseFrame]:
        """스트림이 끝났을 때 남은 줄과 미완성 프레임을 내보낸다.

        마지막 프레임이 빈 줄로 끝나지 않는 서버가 있어서 필요하다. 규격 위반이지만 실제로
        만난다.
        """
        if self._buffer:
            line, self._buffer = self._buffer, ""
            frame = self._consume_line(line)
            if frame is not None:
                yield frame
        if self._dirty:
            yield self._emit()

    # ---- 내부 ----

    @staticmethod
    def _split_line(buffer: str) -> tuple[str, str | None, str]:
        """첫 줄바꿈에서 자른다. 반환은 (줄, 줄바꿈 종류, 남은 것)."""
        idx_n = buffer.find("\n")
        idx_r = buffer.find("\r")

        if idx_n == -1 and idx_r == -1:
            return buffer, None, buffer
        if idx_r != -1 and (idx_n == -1 or idx_r < idx_n):
            if idx_r + 1 < len(buffer) and buffer[idx_r + 1] == "\n":
                return buffer[:idx_r], "\r\n", buffer[idx_r + 2 :]
            return buffer[:idx_r], "\r", buffer[idx_r + 1 :]
        return buffer[:idx_n], "\n", buffer[idx_n + 1 :]

    def _consume_line(self, line: str) -> SseFrame | None:
        if not line:
            # 빈 줄이 프레임 경계다.
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
            # 공백 하나만 벗긴다. 'data:x'와 'data: x'가 같아지는 지점이다.
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

    def _emit(self) -> SseFrame:
        frame = SseFrame(
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
        # id는 규격상 다음 프레임까지 유지된다. 재연결 커서로 쓰기 때문이다.
        return frame
