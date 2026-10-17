"""필드 메타데이터에 따라 모델 델타를 누적하고 최종 모델로 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from completion_bridge.streaming.metadata import INDEX, MERGE_META_KEY, OVERWRITE

__all__ = ["StreamMerger"]

DEFAULT_INDEX_FIELD = "index"


def _meta(definition: FieldInfo) -> str | None:
    """선언된 필드 병합 전략 문자열을 반환한다."""
    extra = definition.json_schema_extra
    if isinstance(extra, dict):
        value = extra.get(MERGE_META_KEY)
        if isinstance(value, str):
            return value
    return None


@dataclass(slots=True)
class _IndexedList:
    """고정 식별 필드의 검색 사전과 수신 순서 목록을 관리한다."""

    items: list[Any] = field(default_factory=list)
    _key_name: str | None = None
    _lookup: dict[Any, dict[str, Any]] = field(default_factory=dict)

    def append(self, value: Any) -> None:
        """원소를 추가하고 고정 식별값이 있는 사전은 평균 O(1)로 등록한다."""
        self.items.append(value)
        if isinstance(value, dict) and self._key_name is not None:
            key = value.get(self._key_name)
            if key is not None:
                self._lookup.setdefault(key, value)

    def get_or_create(self, key_name: str | None, key: Any) -> dict[str, Any]:
        """식별값으로 찾거나 새 사전을 추가한다. 식별 필드 변경은 허용하지 않는다.
        필드나 값이 None이면 검색 없이 추가한다.
        """
        slot = None
        if key_name is not None and key is not None:
            if self._key_name is None:
                self._key_name = key_name
                for candidate in self.items:
                    if isinstance(candidate, dict):
                        candidate_key = candidate.get(key_name)
                        if candidate_key is not None:
                            self._lookup.setdefault(candidate_key, candidate)
            elif self._key_name != key_name:
                raise ValueError("같은 목록의 식별 필드는 변경할 수 없습니다")
            slot = self._lookup.get(key)
        if slot is None:
            slot = {}
            self.items.append(slot)
        if key_name is not None and key is not None:
            self._lookup[key] = slot
        return slot


@dataclass(slots=True)
class _StringParts:
    """일반 목록과 구분하여 문자열 조각을 보관한다."""

    parts: list[str] = field(default_factory=list)

    def append(self, value: str) -> None:
        """기존 문자열을 복사하지 않고 새 조각을 추가한다. 평균 O(1)이다."""
        self.parts.append(value)

    def build(self) -> str:
        """원본을 변경하지 않고 전체 문자 수에 비례하는 비용으로 결합한다."""
        return "".join(self.parts)


class StreamMerger[M: BaseModel]:
    """요청별 모델 델타를 딕셔너리에 누적하고 build에서 최종 검증한다.

    - 문자열은 조각으로 보관하고 수치는 합산한다.
    - overwrite·index·Literal 필드는 교체한다.
    - 모델과 목록의 하위 작업은 명시적인 DFS 스택으로 처리한다.
    """

    def __init__(self, model: type[M]) -> None:
        self._model = model
        self._acc: dict[str, Any] = {}

    def apply(self, delta: M | None) -> None:
        """수신 델타를 변경하지 않고 누적한다. None은 무시한다."""
        if delta is None:
            return
        # 부모 반복자를 보존하고 하위 작업을 먼저 완료하는 DFS이다.
        stack = [self._merge_model(self._acc, delta)]
        while stack:
            try:
                # --- 현재 반복자를 시작하거나, 이전 yield 다음 줄부터 재개한다. ---
                target, source = next(stack[-1])
            except StopIteration:
                # --- 하위 반복자가 끝나면 제거하고 다음 반복에서 부모를 재개한다. ---
                stack.pop()
                continue
            # --- 부모는 정지한 채 보존하고, 전달받은 하위 작업부터 처리한다. ---
            if isinstance(source, BaseModel):
                stack.append(self._merge_model(target, source))
            else:
                stack.append(self._extend_list(target, source))

    def build(self) -> M:
        """조각을 결합한 별도 결과를 검증한다. 이후에도 델타를 추가할 수 있다."""
        return self._model.model_validate(self._materialize(self._acc))

    def _merge_model(self, acc: dict[str, Any], delta: BaseModel) -> Iterator[tuple[Any, Any]]:
        """필드 정책으로 누적하고 모델·목록은 DFS 하위 작업으로 반환한다.

        생략된 기본값은 최초에만 보존하고 None은 변경하지 않는다. 명시된
        교체 필드 이외에는 문자열 연결·수치 합산·사전 갱신·목록 병합을 적용한다.
        부모 반복자는 하위 작업 완료 후 재개하므로 중복 원소의 수신 순서가 유지된다.
        """
        provided = delta.model_fields_set
        for name in type(delta).model_fields:
            definition = type(delta).model_fields[name]
            value = getattr(delta, name, None)
            if value is None:
                continue
            if name not in acc:
                # --- 사전 작업: 최초 값을 저장할 누적 객체를 준비한다. ---
                acc[name] = self._seed(value)
                if isinstance(value, (BaseModel, list)):
                    # --- 하위 작업: 모델의 필드 또는 목록의 원소를 처리한다. ---
                    yield acc[name], value
                # --- 재개 지점: 하위 작업이 끝났으므로 다음 필드로 진행한다. ---
                continue
            if name not in provided:
                continue

            # Optional의 단일 실제 타입만 확인한다. Literal의 인수는 분해하지 않는다.
            annotation = definition.annotation
            if get_origin(annotation) in (Union, UnionType):
                args = []
                for arg in get_args(annotation):
                    if arg is not type(None):
                        args.append(arg)
                if len(args) == 1:
                    annotation = args[0]
            if (
                _meta(definition) in (OVERWRITE, INDEX)
                or name == DEFAULT_INDEX_FIELD
                or get_origin(annotation) is Literal
            ):
                # --- 사전 작업: 교체할 새 누적 객체를 준비한다. ---
                acc[name] = self._seed(value)
                if isinstance(value, (BaseModel, list)):
                    # --- 하위 작업: 교체한 모델 또는 목록의 내용을 처리한다. ---
                    yield acc[name], value
                # --- 재개 지점: 하위 작업이 끝났으므로 다음 필드로 진행한다. ---
                continue

            # bool은 int의 하위 타입이므로 수치 합산보다 먼저 교체한다.
            current = acc[name]
            if isinstance(current, _StringParts) and isinstance(value, str):
                current.append(value)
            elif isinstance(current, str) and isinstance(value, str):
                acc[name] = _StringParts(parts=[current, value])
            elif isinstance(current, bool) or isinstance(value, bool):
                acc[name] = value
            elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
                acc[name] = current + value
            elif isinstance(current, dict) and isinstance(value, BaseModel):
                # --- 하위 작업: 기존 사전에 모델 필드를 병합한다. ---
                yield current, value
                # --- 재개 지점: 이 분기의 추가 처리 없이 다음 필드로 진행한다. ---
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            elif isinstance(current, (_IndexedList, list)) and isinstance(value, list):
                # --- 사전 작업: 기존 목록을 식별값으로 병합할 형태로 준비한다. ---
                if isinstance(current, list):
                    current = _IndexedList(items=current)
                    acc[name] = current
                # --- 하위 작업: 목록 원소를 수신 순서대로 처리한다. ---
                yield current, value
                # --- 재개 지점: 이 분기의 추가 처리 없이 다음 필드로 진행한다. ---
            else:
                acc[name] = value

        # extra 필드는 새 값으로 교체한다. BaseModel 값은 필드 처리 후 사전으로 저장한다.
        for name, value in (delta.model_extra or {}).items():
            if value is None:
                continue
            if isinstance(value, BaseModel):
                # --- 사전 작업: 기존 extra 값과 별개인 새 사전을 준비한다. ---
                nested: dict[str, Any] = {}
                # --- 하위 작업: 새 사전에 모델 필드를 처리한다. ---
                yield nested, value
                # --- 후작업: 하위 작업이 완료된 사전으로 기존 extra 값을 교체한다. ---
                acc[name] = nested
            else:
                acc[name] = value

    def _materialize(self, value: Any) -> Any:
        """반복형 DFS로 컨테이너를 복사하고 문자열 조각을 결합한다.

        스택 프레임 수는 중첩 깊이에 비례한다. 일반 문자열 목록은 결합하지
        않으며 튜플 식별값은 그대로 반환한다. 호출 스택을 사용하지 않는다.
        """
        holder: dict[Any, Any] = {}
        stack: list[tuple[Iterator[Any], Any]] = [(iter((("result", value),)), holder)]
        while stack:
            iterator, target = stack[-1]
            try:
                key, item = next(iterator)
            except StopIteration:
                stack.pop()
                continue
            if isinstance(item, _StringParts):
                converted: Any = item.build()
            elif isinstance(item, _IndexedList):
                converted = []
                stack.append((enumerate(item.items), converted))
            elif isinstance(item, dict):
                converted = {}
                stack.append((iter(item.items()), converted))
            elif isinstance(item, list):
                converted = []
                stack.append((enumerate(item), converted))
            else:
                converted = item
            if isinstance(target, list):
                target.append(converted)
            else:
                target[key] = converted
        return holder["result"]

    def _seed(self, value: Any) -> Any:
        """모델·목록은 빈 누적 객체를, 사전은 얕은 사본을 생성한다.

        빈 컨테이너의 내용은 호출 측에서 DFS 작업으로 등록하여 처리한다.
        나머지 값은 그대로 반환한다.
        """
        if isinstance(value, BaseModel):
            return {}
        if isinstance(value, list):
            return _IndexedList()
        if isinstance(value, dict):
            return dict(value)
        return value

    def _extend_list(self, acc: _IndexedList, deltas: list[Any]) -> Iterator[tuple[Any, Any]]:
        """식별 필드로 모델 원소를 찾고 하위 모델 병합을 DFS 작업으로 반환한다.

        index 정책을 선언한 첫 필드를 우선하고 없으면 이름이 index인 필드를
        사용한다. 일반 원소와 식별값이 없는 모델은 수신 순서대로 추가한다.
        """
        for item in deltas:
            if not isinstance(item, BaseModel):
                acc.append(item)
                continue
            definitions = type(item).model_fields
            key_name = None
            for name in definitions:
                if _meta(definitions[name]) == INDEX:
                    key_name = name
                    break
            if key_name is None and DEFAULT_INDEX_FIELD in definitions:
                key_name = DEFAULT_INDEX_FIELD
            key = getattr(item, key_name, None) if key_name else None
            # --- 사전 작업: 현재 원소를 병합할 사전을 찾거나 만든다. ---
            slot = acc.get_or_create(key_name, key)
            # --- 하위 작업: 현재 원소의 모델 필드를 병합한다. ---
            yield slot, item
            # --- 재개 지점: 현재 원소의 병합이 끝난 뒤 다음 원소로 진행한다. ---
