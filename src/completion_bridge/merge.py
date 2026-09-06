"""델타 병합. 조각으로 도착한 스트림을 하나의 최종 객체로 접는다.

병합 규칙의 기본값이 **이어붙이기**인 것이 핵심이다. OpenAI 델타 스트림에서 실제로 누적되는
필드는 본문, 추론, 도구 인수 셋뿐이고 나머지는 전부 덮어쓰기다. 기본을 반대로 잡으면 표시가
서른 개 붙는다. 이쪽으로 잡으면 아홉 개면 된다.

Java ``streambind``의 규칙을 그대로 따르되 ``TypeVariableResolver``에 해당하는 층은 없다.
제네릭 소거가 없어 ``model_fields``가 실제 타입을 들고 있기 때문이다.

| 필드 종류 | 동작 |
|---|---|
| ``str`` | 이어붙이기 |
| ``int`` / ``float`` | 더하기 |
| 모델 | 재귀 병합 |
| 원시값 리스트 | 뒤에 추가 |
| 모델 리스트 | 인덱스 키로 짝지어 병합 |
| ``Literal`` 필드 | 덮어쓰기 (자동) |
| ``{"stream": "overwrite"}`` | 덮어쓰기 |
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

__all__ = ["StreamMerger"]

STREAM_META_KEY = "stream"
OVERWRITE = "overwrite"
INDEX = "index"

# 리스트 원소를 짝지을 때 어노테이션 없이도 인정하는 필드 이름.
DEFAULT_INDEX_FIELD = "index"


def _meta(field: FieldInfo) -> str | None:
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        value = extra.get(STREAM_META_KEY)
        if isinstance(value, str):
            return value
    return None


def _unwrap_optional(annotation: Any) -> Any:
    """``X | None``에서 ``X``를 꺼낸다. 유니온이 아니면 그대로 돌려준다.

    ``Literal["text"]``에도 ``get_args``가 값을 돌려주므로 유니온인지 먼저 확인해야 한다.
    확인 없이 벗기면 ``Literal["text"]``이 문자열 ``"text"``로 줄어들어 판정이 깨진다.
    """
    if get_origin(annotation) not in (Union, UnionType):
        return annotation
    args = [a for a in get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def _is_literal(annotation: Any) -> bool:
    return get_origin(_unwrap_optional(annotation)) is Literal


def _is_overwrite(name: str, field: FieldInfo) -> bool:
    """덮어쓰기 판정.

    세 경로가 있다. 명시 표시, 리스트 짝짓기 키, ``Literal`` 필드다.

    짝짓기 키를 누적하면 인덱스 1이 두 번 오면 2가 되어 슬롯을 잃는다. ``Literal``은 합법적인
    값이 하나뿐이라 이어붙이기가 맞을 수 없다. 하위 클래스가 ``type``을 재선언하면 부모의 필드
    메타가 교체되어 표시가 사라지는데, 마지막 규칙이 그 구멍을 막는다.
    """
    if _meta(field) in (OVERWRITE, INDEX):
        return True
    if name == DEFAULT_INDEX_FIELD:
        return True
    return _is_literal(field.annotation)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    inner = _unwrap_optional(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


def _list_element(annotation: Any) -> Any | None:
    """``list[X]``에서 ``X``를 꺼낸다. 리스트가 아니면 ``None``."""
    inner = _unwrap_optional(annotation)
    if get_origin(inner) is list:
        args = get_args(inner)
        return args[0] if args else Any
    return None


def _index_field_name(value: Any) -> str | None:
    """리스트 원소를 짝지을 필드 이름을 정한다.

    ``{"stream": "index"}`` 표시가 있으면 그 필드를, 없으면 ``index``라는 이름의 정수 필드를
    쓴다. Java에서 ``ToolCall.index``와 ``Choice.index``가 어노테이션 없이 이 규칙에 걸렸다.
    """
    if not isinstance(value, BaseModel):
        return None
    fields = type(value).model_fields
    for name, field in fields.items():
        if _meta(field) == INDEX:
            return name
    if DEFAULT_INDEX_FIELD in fields:
        return DEFAULT_INDEX_FIELD
    return None


class StreamMerger[M: BaseModel]:
    """같은 타입의 델타들을 누적해 하나로 만든다.

    누적은 모델 인스턴스가 아니라 딕셔너리에 한다. 조각만 채워진 델타는 필수 필드를 만족하지
    못해 중간 상태를 모델로 들 수 없기 때문이다. :meth:`build`에서 한 번에 검증한다.
    """

    def __init__(self, model: type[M]) -> None:
        self._model = model
        self._acc: dict[str, Any] = {}

    def apply(self, delta: M | None) -> None:
        """델타 하나를 누적 상태에 적용한다. ``None``은 무시한다."""
        if delta is None:
            return
        self._merge_model(self._acc, delta)

    def build(self) -> M:
        """누적 상태를 최종 객체로 만든다. 여러 번 불러도 된다."""
        return self._model.model_validate(self._acc)

    # ---- 내부 ----

    def _merge_model(self, acc: dict[str, Any], delta: BaseModel) -> None:
        """델타의 필드를 누적 상태에 적용한다.

        "이 필드가 델타에 실렸는가"를 ``model_fields_set``으로 판단한다. Java는 모든 필드를
        nullable로 만들어 ``null``을 "변경 없음"으로 썼는데, Python에서 같은 짓을 하면 소비자가
        매번 None 검사를 해야 한다. Pydantic이 명시적으로 주어진 필드를 알려주므로 편한 기본값을
        유지하면서 같은 구분을 얻는다.

        기본값은 처음 등장할 때만 자리를 채운다. ``type`` 같은 판별자가 그 경로로 들어온다.
        어댑터가 ``TextBlock(text=...)``만 써도 ``type``이 누적 상태에 남아야 유니온 조회가
        된다. 반면 두 번째 델타의 기본값이 누적된 값을 덮어쓰면 안 된다.
        """
        provided = delta.model_fields_set
        for name, field in type(delta).model_fields.items():
            value = getattr(delta, name, None)
            if value is None:
                continue
            if name in provided:
                self._merge_field(acc, name, field, value)
            elif name not in acc:
                acc[name] = self._seed(field, value)

        # extra="allow"로 들어온 선언 밖 필드도 잃지 않는다.
        for name, value in (delta.__pydantic_extra__ or {}).items():
            if value is None:
                continue
            acc[name] = self._replace(acc.get(name), value)

    def _merge_field(self, acc: dict[str, Any], name: str, field: FieldInfo, value: Any) -> None:
        if name not in acc:
            acc[name] = self._seed(field, value)
            return
        if _is_overwrite(name, field):
            acc[name] = self._seed(field, value)
            return
        acc[name] = self._combine(acc[name], value, field)

    def _seed(self, field: FieldInfo, value: Any) -> Any:
        """처음 등장한 값을 누적 형태로 바꾼다."""
        if isinstance(value, BaseModel):
            nested: dict[str, Any] = {}
            self._merge_model(nested, value)
            return nested
        if isinstance(value, list):
            seeded: list[Any] = []
            self._extend_list(seeded, value)
            return seeded
        return value

    def _replace(self, _current: Any, value: Any) -> Any:
        if isinstance(value, BaseModel):
            nested: dict[str, Any] = {}
            self._merge_model(nested, value)
            return nested
        return value

    def _combine(self, current: Any, value: Any, field: FieldInfo) -> Any:
        if isinstance(current, str) and isinstance(value, str):
            return current + value
        if isinstance(current, bool) or isinstance(value, bool):
            return value
        if isinstance(current, (int, float)) and isinstance(value, (int, float)):
            return current + value
        if isinstance(current, dict) and isinstance(value, BaseModel):
            self._merge_model(current, value)
            return current
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
            return current
        if isinstance(current, list) and isinstance(value, list):
            self._extend_list(current, value)
            return current
        _ = field
        return value

    def _extend_list(self, acc: list[Any], deltas: list[Any]) -> None:
        for item in deltas:
            if not isinstance(item, BaseModel):
                acc.append(item)
                continue

            key_name = _index_field_name(item)
            key = getattr(item, key_name, None) if key_name else None
            if key is None:
                seeded: dict[str, Any] = {}
                self._merge_model(seeded, item)
                acc.append(seeded)
                continue

            slot = self._slot_for(acc, key_name, key)
            self._merge_model(slot, item)

    def _slot_for(self, acc: list[Any], key_name: str | None, key: Any) -> dict[str, Any]:
        """같은 인덱스 키를 가진 누적 슬롯을 찾거나 새로 만든다.

        Java 구현은 리스트를 인덱스 값으로 직접 주소 지정해 빈 자리를 ``null``로 채웠다.
        여기서는 도착 순서를 보존한다. 벤더가 인덱스를 0부터 촘촘히 주지 않는 경우가 있고,
        순서가 곧 의미인 블록 리스트에서는 도착 순서가 더 안전하다.
        """
        for slot in acc:
            if isinstance(slot, dict) and key_name and slot.get(key_name) == key:
                return slot
        fresh: dict[str, Any] = {}
        acc.append(fresh)
        return fresh
