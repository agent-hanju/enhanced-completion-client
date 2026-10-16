"""스트리밍 텍스트의 XML 형태 태그를 경로별 이벤트로 해석한다.

청크가 태그 중간에서 끝나면 미완성 입력을 보관한다. 스키마는 의미 경로마다 태그 이름,
별칭과 허용 속성을 지정하며, 등록되지 않은 태그는 원문 텍스트로 반환한다.
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
    """경로의 부모를 반환한다. /cite/id는 /cite로, /cite는 /로 변환한다."""
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

    동시 등록·조회는 지원하지 않는다. bind는 입력을 모두 검증한 뒤 등록하므로 검증 실패 시
    기존 상태를 유지한다. 이름별 경로 수를 k라 하면 paths_for·open_target은 O(k),
    closes는 집합 조회의 평균 O(1)이다.
    속성 수를 a라 하면 allowed_attrs는 O(a), tag_names는 전체 이름 수에 비례한다.
    bind는 입력 문자 수와 같은 이름의 기존 경로 수에 비례하며 등록 공간은 전체 등록량에
    비례한다. could_be_tag_name은 등록 이름 전체를 검사한다.
    """

    def __init__(self) -> None:
        """태그 경로와 허용 속성의 빈 등록 상태를 생성한다."""
        self._tag_to_paths: dict[str, set[str]] = {}
        self._path_attrs: dict[str, set[str]] = {}

    def bind(
        self,
        path: str,
        *,
        tag: str,
        alias: Iterable[str] = (),
        attr: Iterable[str] = (),
    ) -> ContentSchema:
        """경로 하나에 태그 이름과 별칭, 허용 속성을 등록한다.

        입력 검증이나 별칭·속성 순회가 실패하면 기존 등록 상태를 유지한다.
        같은 인스턴스의 등록과 조회를 동시에 수행하는 사용은 지원하지 않는다.
        """
        if not isinstance(path, str) or not path.startswith("/") or path == ROOT:
            raise ValueError(f"path must start with '/' and not be root: {path!r}")

        # 별칭·속성 순회를 모두 마친 뒤 등록해 실패 시 기존 상태를 유지한다.
        names = (tag, *alias)
        attributes = []
        for attribute in attr:
            if not isinstance(attribute, str) or not attribute:
                raise ValueError("attribute names must be non-empty strings")
            for character in attribute:
                if character.isspace() or character in "<>/='\"\x00":
                    raise ValueError("attribute names must not contain markup delimiters")
            attributes.append(attribute)
        parent = parent_path(path)
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError("tag name must not be empty and must be a string")
            for character in name:
                if character.isspace() or character in "<>/='\"\x00":
                    raise ValueError("tag names must not contain markup delimiters or whitespace")
            for existing in self._tag_to_paths.get(name, ()):
                if existing != path and parent_path(existing) == parent:
                    raise ValueError(
                        f"tag {name!r} is already bound to sibling path "
                        f"{existing!r} under {parent!r}"
                    )

        for name in names:
            self._tag_to_paths.setdefault(name, set()).add(path)
        self._path_attrs.setdefault(path, set()).update(attributes)
        return self

    # ---- 조회 ----

    @property
    def tag_names(self) -> frozenset[str]:
        """등록된 태그와 별칭 이름의 변경 불가능한 집합을 반환한다."""
        return frozenset(self._tag_to_paths)

    def paths_for(self, tag: str) -> frozenset[str]:
        """태그 이름에 연결된 경로의 변경 불가능한 집합을 반환한다."""
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
        """지정 경로에서 허용하는 속성 이름의 변경 불가능한 집합을 반환한다."""
        return frozenset(self._path_attrs.get(path, ()))

    def could_be_tag_name(self, partial: str) -> bool:
        """지금까지 모은 이름이 아직 어떤 태그의 접두일 수 있는지.

        아니라면 더 기다릴 이유가 없으므로 바로 텍스트로 흘려보낸다. ``<citation>``이 ``cite``의
        접두를 지나 결국 태그가 아니라고 판정되는 경로가 이것이다.
        """
        for name in self._tag_to_paths:
            if name.startswith(partial):
                return True
        return False


@dataclass(slots=True)
class _Tag:
    """입력 문자열에서 해석한 태그 이름·속성·원문과 시작·종료 구분."""

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

    feed·flush는 순차 호출하며 실행 중 스키마를 변경하지 않는다. 일반 텍스트는 확정 즉시
    출력하고 미완성 태그만 보관한다. capture_raw=True이면 전체 원문도 보관한다.
    미완성 태그 길이에 상한은 없으며 한 글자씩 계속 입력하면 버퍼 재검사·복사로 최악
    O(n²) 시간이 필요하다. 공간은 미완성 태그 크기와 선택한 원문 보관량에 비례한다.
    """

    def __init__(self, schema: ContentSchema, *, capture_raw: bool = False) -> None:
        """스키마와 원문 보관 옵션을 적용하고 입력 버퍼·현재 경로를 초기화한다."""
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
        """capture_raw가 활성화된 경우 지금까지 받은 원문을 반환한다.

        원문 보관에 필요한 메모리가 입력 길이에 비례하므로 기본값은 비활성화다.
        """
        return "".join(self._raw)

    def feed(self, token: str) -> list[ParseEvent]:
        """조각 하나를 넣고 확정된 이벤트를 돌려준다.

        빈 문자열은 빈 리스트다. 스트림 도중 빈 델타를 보내는 제공자가 있다.
        """
        if not isinstance(token, str):
            raise TypeError("TagParser.feed requires a string")
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
        """버퍼의 확정된 본문과 태그 이벤트를 순서대로 반환하고 미완성 입력은 유지한다."""
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
        attrs = {}
        for field_name in tag.attributes:
            field_value = tag.attributes[field_name]
            if field_name in allowed:
                attrs[field_name] = field_value
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
