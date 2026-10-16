"""Pydantic 필드의 타입과 메타데이터에 따라 스트림 델타를 누적한다.

문자열은 연결하고 수치는 합산한다. 모델과 목록은 재귀 병합하며 Literal 필드와
명시적인 overwrite 필드는 값을 교체한다. 리스트 모델 원소는 지정한 식별 필드로
같은 원소를 찾는다. 누적 상태는 매핑으로 관리하고 build에서 모델로 검증한다.
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from completion_bridge.streaming.metadata import INDEX, MERGE_META_KEY, OVERWRITE

__all__ = ["StreamMerger"]

# 리스트 원소를 짝지을 때 어노테이션 없이도 인정하는 필드 이름.
DEFAULT_INDEX_FIELD = "index"


def _meta(field: FieldInfo) -> str | None:
    """Pydantic 필드에 선언된 스트림 병합 전략 문자열을 조회한다."""
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        value = extra.get(MERGE_META_KEY)
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
    args = []
    for a in get_args(annotation):
        if a is not type(None):
            args.append(a)
    if len(args) == 1:
        return args[0]
    return annotation


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
    return get_origin(_unwrap_optional(field.annotation)) is Literal


def _index_field_name(value: Any) -> str | None:
    """모델 리스트 원소를 식별할 필드를 선택한다.

    INDEX 병합 정책을 선언한 필드를 우선하고, 없으면 index라는 필드 이름을 사용한다.
    """
    if not isinstance(value, BaseModel):
        return None
    fields = type(value).model_fields
    for name in fields:
        field = fields[name]
        if _meta(field) == INDEX:
            return name
    if DEFAULT_INDEX_FIELD in fields:
        return DEFAULT_INDEX_FIELD
    return None


class StreamMerger[M: BaseModel]:
    """같은 타입의 델타들을 누적해 하나로 만든다.

    같은 인스턴스의 apply·build 호출은 순차적으로 수행해야 한다.
    누적은 모델 인스턴스가 아니라 딕셔너리에 한다. 조각만 채워진 델타는 필수 필드를 만족하지
    못해 중간 상태를 모델로 들 수 없기 때문이다. :meth:`build`에서 한 번에 검증한다.

    입력은 선언한 모델 타입의 델타여야 한다. apply는 해당 필드만 순차적으로 갱신하며
    트랜잭션이나 스레드 간 동시 접근을 제공하지 않는다. build 검증 실패는 누적 상태를
    변경하지 않는다. 식별 목록의 원소 탐색은 선형이므로 n개 원소를 각각 적용하면
    최악 O(n²) 탐색이 필요하다. 문자열 연결은 누적 문자열 길이에 비례하며,
    저장 공간은 누적 응답 크기에 비례한다.
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
        """모델의 필드와 확장 필드를 누적 상태에 반영한다.

        model_fields_set에 있는 필드는 선언된 전략으로 병합한다. 생략된 기본값은
        최초 등장 시에만 보존한다. None은 변경 없음으로 처리한다.
        """
        provided = delta.model_fields_set
        for name in type(delta).model_fields:
            field = type(delta).model_fields[name]
            value = getattr(delta, name, None)
            if value is None:
                continue
            if name in provided:
                self._merge_field(acc, name, field, value)
            elif name not in acc:
                acc[name] = self._seed(field, value)

        # extra="allow"로 들어온 선언 밖 필드도 잃지 않는다.
        mapped_fields = delta.__pydantic_extra__ or {}
        for name in mapped_fields:
            value = mapped_fields[name]
            if value is None:
                continue
            acc[name] = self._replace(acc.get(name), value)

    def _merge_field(self, acc: dict[str, Any], name: str, field: FieldInfo, value: Any) -> None:
        """새 필드·값 교체·누적 전략에 따라 필드 하나를 누적 상태에 반영한다."""
        if name not in acc:
            acc[name] = self._seed(field, value)
            return
        if _is_overwrite(name, field):
            acc[name] = self._seed(field, value)
            return
        acc[name] = self._combine(acc[name], value, field)

    def _seed(self, field: FieldInfo, value: Any) -> Any:
        """처음 등장한 값을 누적 형태로 바꾼다.

        dict는 얕게 복사한다. 뒤 조각을 합칠 때 누적 상태를 고치므로, 복사하지 않으면 이미
        소비자에게 넘어간 델타의 값이 바뀐다.
        """
        if isinstance(value, BaseModel):
            nested: dict[str, Any] = {}
            self._merge_model(nested, value)
            return nested
        if isinstance(value, list):
            seeded: list[Any] = []
            self._extend_list(seeded, value)
            return seeded
        if isinstance(value, dict):
            return dict(value)
        return value

    def _replace(self, _current: Any, value: Any) -> Any:
        """모델 값은 누적용 매핑으로 변환하고 그 밖의 값은 그대로 반환한다."""
        if isinstance(value, BaseModel):
            nested: dict[str, Any] = {}
            self._merge_model(nested, value)
            return nested
        return value

    def _combine(self, current: Any, value: Any, field: FieldInfo) -> Any:
        """문자열·수치·모델·매핑·목록의 타입에 맞는 병합 연산을 수행한다."""
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
        """원시 원소는 추가하고 모델 원소는 식별 필드가 같으면 같은 누적 원소에 반영한다."""
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
        """식별값이 같은 누적 원소를 찾거나 새 매핑을 추가한다.

        기존 원소의 도착 순서를 유지하며 원소 수에 비례해 순회한다.
        """
        for slot in acc:
            if isinstance(slot, dict) and key_name and slot.get(key_name) == key:
                return slot
        fresh: dict[str, Any] = {}
        acc.append(fresh)
        return fresh
