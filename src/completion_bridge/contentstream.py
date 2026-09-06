"""스트리밍 XML-like 태그 파서.

custom content type을 wire에 직접 실을 수 없으므로 text 채널에 태그로 숨겨 나른다. 이 모듈은
그 태그를 스트림에서 다시 들어올린다.

정규식으로 풀 수 없다. ``<cite``가 ``"<ci"``와 ``"te>본문"``으로 쪼개져 도착하기 때문이다.
태그일 수도 있는 접미사를 버퍼에 붙들고 다음 청크를 기다려야 한다.

Java ``content-stream-adapter`` 0.3.0의 설계를 따른다. **의미 경로와 표면 문법을 분리**하는
것이 핵심이다. 한 경로에 태그 이름 여러 개를 별칭으로 묶을 수 있고, 모델이 뱉는 태그가 바뀌어도
소비 코드가 보는 경로 문자열은 그대로다.

0.1.6 대비 고친 것 하나를 옮겼다. 구 버전은 패턴을 ``<cite`` 접두로만 봐서 ``<citation>``도
걸렸다. 여기서는 태그 이름 뒤에 오는 문자가 ``>``, ``/``, 공백류 중 하나여야 태그로 본다.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

__all__ = [
    "ContentSchema",
    "Enter",
    "Exit",
    "ParseEvent",
    "TagParser",
    "TextRun",
]

ROOT = "/"
_NAME_END = frozenset(">/ \t\n\r\f")
_WS = " \t\n\r\f"


@dataclass(slots=True)
class TextRun:
    """태그가 아닌 텍스트 조각. ``path``는 이 텍스트가 놓인 경로다."""

    path: str
    content: str


@dataclass(slots=True)
class Enter:
    """바인딩된 경로로 진입했다."""

    path: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Exit:
    """경로에서 이탈했다."""

    path: str


ParseEvent = TextRun | Enter | Exit


def parent_path(path: str) -> str:
    """``/cite/id`` -> ``/cite``, ``/cite`` -> ``/``."""
    if path == ROOT:
        return ROOT
    head, _, _ = path.rpartition("/")
    return head or ROOT


class ContentSchema:
    """경로에 태그 문법을 붙인다.

        schema = (ContentSchema()
                  .bind("/cite", tag="cite", alias=("rag",), attr=("id",))
                  .bind("/cite/id", tag="id"))

    같은 태그 이름을 같은 부모 아래 두 형제 경로에 붙이면 거부한다. 어느 경로로 전이해야 할지
    구분할 수 없기 때문이다. 이 보장 덕분에 전이 판정이 (부모 경로 -> 자식 경로) 조회 하나로
    끝난다.
    """

    def __init__(self) -> None:
        self._tag_to_paths: dict[str, set[str]] = {}
        self._path_attrs: dict[str, set[str]] = {}
        self._paths: set[str] = set()

    def bind(
        self,
        path: str,
        *,
        tag: str,
        alias: Iterable[str] = (),
        attr: Iterable[str] = (),
    ) -> ContentSchema:
        """경로 하나에 태그 이름과 별칭, 허용 속성을 붙인다."""
        if not path.startswith("/") or path == ROOT:
            raise ValueError(f"path must start with '/' and not be root: {path!r}")
        if not tag:
            raise ValueError("tag must not be empty")

        self._paths.add(path)
        for name in (tag, *alias):
            if not name:
                raise ValueError("tag name must not be empty")
            self._register(name, path)

        allowed = self._path_attrs.setdefault(path, set())
        allowed.update(a for a in attr if a)
        return self

    def _register(self, name: str, path: str) -> None:
        bound = self._tag_to_paths.setdefault(name, set())
        parent = parent_path(path)
        for existing in bound:
            if existing != path and parent_path(existing) == parent:
                raise ValueError(
                    f"tag {name!r} is already bound to sibling path {existing!r} under {parent!r}"
                )
        bound.add(path)

    # ---- 조회 ----

    @property
    def tag_names(self) -> frozenset[str]:
        return frozenset(self._tag_to_paths)

    def paths_for(self, tag: str) -> frozenset[str]:
        return frozenset(self._tag_to_paths.get(tag, ()))

    def open_target(self, current: str, tag: str) -> str | None:
        """``current``에서 ``tag``를 열었을 때 갈 경로. 없으면 ``None``."""
        for path in self._tag_to_paths.get(tag, ()):
            if parent_path(path) == current:
                return path
        return None

    def closes(self, current: str, tag: str) -> bool:
        """``tag``가 ``current``를 닫는지."""
        return current in self._tag_to_paths.get(tag, ())

    def allowed_attrs(self, path: str) -> frozenset[str]:
        return frozenset(self._path_attrs.get(path, ()))

    def could_be_tag_name(self, partial: str) -> bool:
        """지금까지 모은 이름이 아직 어떤 태그의 접두일 수 있는지.

        아니라면 더 기다릴 이유가 없으므로 바로 텍스트로 흘려보낸다. ``<citation>``이 ``cite``의
        접두를 지나 결국 태그가 아니라고 판정되는 경로가 이것이다.
        """
        return any(name.startswith(partial) for name in self._tag_to_paths)


@dataclass(slots=True)
class _Tag:
    name: str
    closing: bool
    self_closing: bool
    attributes: dict[str, str]
    raw: str


class TagParser:
    """토큰 조각을 :data:`ParseEvent`로 자른다.

    스키마에 없는 태그는 이벤트가 아니라 텍스트로 흘려보낸다. 모델이 뱉는 임의의 꺾쇠를
    삼키지 않기 위해서다.

    상태를 가지므로 스트림마다 새 인스턴스여야 한다.
    """

    def __init__(self, schema: ContentSchema, *, capture_raw: bool = False) -> None:
        self._schema = schema
        self._capture_raw = capture_raw
        self._buffer = ""
        self._path = ROOT
        self._in_tag = False
        self._raw: list[str] = []

    @property
    def path(self) -> str:
        """현재 경로."""
        return self._path

    @property
    def raw(self) -> str:
        """지금까지 들어온 원문. ``capture_raw``가 꺼져 있으면 빈 문자열.

        긴 스트림에서 입력 길이에 비례해 메모리가 늘지 않도록 기본을 꺼둔다. Java 0.1.6은
        무조건 쌓았다.
        """
        return "".join(self._raw)

    def feed(self, token: str) -> list[ParseEvent]:
        """조각 하나를 넣고 확정된 이벤트를 돌려준다.

        빈 문자열은 빈 리스트다. 스트림 도중 빈 델타를 보내는 제공자가 있다.
        """
        if not token:
            return []
        if self._capture_raw:
            self._raw.append(token)
        self._buffer += token
        return list(self._drain())

    def flush(self) -> list[ParseEvent]:
        """남은 버퍼를 텍스트로 내보낸다.

        미완성 태그는 태그가 아니었던 것으로 본다. 닫히지 않은 경로에 대해 :class:`Exit`를
        만들지는 않는다. 그 판단은 어휘가 자기 상태를 보고 한다.
        """
        out: list[ParseEvent] = []
        if self._buffer:
            out.append(TextRun(self._path, self._buffer))
            self._buffer = ""
            self._in_tag = False
        return out

    # ---- 내부 ----

    def _drain(self) -> Iterator[ParseEvent]:
        while True:
            if self._in_tag:
                events, consumed = self._consume_tag()
                yield from events
                if not consumed:
                    return
                continue

            idx = self._buffer.find("<")
            if idx == -1:
                if self._buffer:
                    yield TextRun(self._path, self._buffer)
                    self._buffer = ""
                return
            if idx > 0:
                yield TextRun(self._path, self._buffer[:idx])
                self._buffer = self._buffer[idx:]
            self._in_tag = True

    def _consume_tag(self) -> tuple[list[ParseEvent], bool]:
        """버퍼가 ``<``로 시작한다. 태그를 확정하거나 더 기다린다.

        두 번째 반환값이 소비 여부다. ``False``면 입력이 더 필요하다.
        """
        tag = self._parse_tag(self._buffer)

        if tag is None:
            if self._still_possible(self._buffer):
                return [], False
            # 태그가 아니다. 꺾쇠 하나만 텍스트로 내보내고 그 뒤부터 다시 훑는다.
            event = TextRun(self._path, self._buffer[0])
            self._buffer = self._buffer[1:]
            self._in_tag = False
            return [event], True

        self._buffer = self._buffer[len(tag.raw) :]
        self._in_tag = False

        if tag.closing:
            if self._schema.closes(self._path, tag.name):
                closed = self._path
                self._path = parent_path(closed)
                return [Exit(closed)], True
            return [TextRun(self._path, tag.raw)], True

        target = self._schema.open_target(self._path, tag.name)
        if target is None:
            return [TextRun(self._path, tag.raw)], True

        allowed = self._schema.allowed_attrs(target)
        attrs = {k: v for k, v in tag.attributes.items() if k in allowed}
        if tag.self_closing:
            # 진입과 이탈을 한 번에. 경로는 제자리로 돌아온다.
            return [Enter(target, attrs), Exit(target)], True

        self._path = target
        return [Enter(target, attrs)], True

    def _still_possible(self, buffer: str) -> bool:
        """이 버퍼가 아직 알려진 태그로 완성될 수 있는지."""
        body = buffer[1:]
        closing = body.startswith("/")
        if closing:
            body = body[1:]

        name = ""
        for ch in body:
            if ch in _NAME_END:
                break
            name += ch
        else:
            # 이름이 끝나지 않았다. 접두일 가능성이 남아 있으면 기다린다.
            return self._schema.could_be_tag_name(name)

        # 이름은 끝났고 닫는 꺾쇠만 아직 안 왔다. 알려진 이름일 때만 기다린다.
        return name in self._schema.tag_names

    @staticmethod
    def _parse_tag(buffer: str) -> _Tag | None:
        """``<``로 시작하는 완성된 태그를 읽는다. 미완성이면 ``None``.

        속성값 안의 꺾쇠를 태그의 끝으로 오해하지 않도록 인용 상태를 따라간다.
        """
        if not buffer.startswith("<"):
            return None

        pos = 1
        closing = buffer[pos : pos + 1] == "/"
        if closing:
            pos += 1

        start = pos
        while pos < len(buffer) and buffer[pos] not in _NAME_END:
            pos += 1
        name = buffer[start:pos]
        if not name:
            return None

        quote = ""
        while pos < len(buffer):
            ch = buffer[pos]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == ">":
                raw = buffer[: pos + 1]
                body = raw[2:-1] if closing else raw[1:-1]
                self_closing = body.endswith("/")
                if self_closing:
                    body = body[:-1]
                attrs = TagParser._parse_attrs(body[len(name) :])
                return _Tag(name, closing, self_closing, attrs, raw)
            pos += 1
        return None

    @staticmethod
    def _parse_attrs(text: str) -> dict[str, str]:
        """``id="d1" n=2`` 형태를 딕셔너리로. 값 없는 속성은 빈 문자열."""
        attrs: dict[str, str] = {}
        pos = 0
        size = len(text)
        while pos < size:
            while pos < size and text[pos] in _WS:
                pos += 1
            start = pos
            while pos < size and text[pos] not in _WS and text[pos] != "=":
                pos += 1
            key = text[start:pos]
            if not key:
                break
            if pos < size and text[pos] == "=":
                pos += 1
                if pos < size and text[pos] in "\"'":
                    quote = text[pos]
                    pos += 1
                    start = pos
                    while pos < size and text[pos] != quote:
                        pos += 1
                    attrs[key] = text[start:pos]
                    pos += 1
                else:
                    start = pos
                    while pos < size and text[pos] not in _WS:
                        pos += 1
                    attrs[key] = text[start:pos]
            else:
                attrs[key] = ""
        return attrs
