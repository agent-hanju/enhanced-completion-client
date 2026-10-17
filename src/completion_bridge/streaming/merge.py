"""모델 후보에 델타를 병합하고 검증 성공 시에만 결과를 교체한다."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from functools import partial
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from completion_bridge.streaming.metadata import INDEX, MERGE_META_KEY, OVERWRITE

__all__ = ["StreamMerger"]


class StreamMerger[M: BaseModel]:
    """델타마다 후보를 검증·확정하는 요청별 모델 병합기.

    문자열은 연결하고 수치는 합산한다. overwrite·index·Literal 필드는 교체한다.
    사전은 얕게 갱신하고 목록의 모델은 고정 식별 필드로 병합한다.
    델타는 부분 데이터를 표현할 수 있어야 한다. None은 변경하지 않는다.
    후보 복사와 검증 비용은 누적 크기에 비례한다. 동시 접근은 지원하지 않는다.
    """

    def __init__(self, model: type[M]) -> None:
        self._type = model
        self._model: M | None = None

    @property
    def result(self) -> M | None:
        """마지막 확정 모델의 독립된 복사본. 첫 성공 전에는 None이다."""
        return cast(M | None, self._transform(self._model, validate=False))

    def apply(self, delta: M | None) -> None:
        """후보 병합·검증에 성공하면 확정한다. 실패하면 이전 결과를 유지한다."""
        if delta is None:
            return
        candidate = (
            self._transform(self._model, validate=False)
            if self._model is not None else self._empty_model(delta)
        )
        indexes: dict[int, tuple[str, dict[Any, Any]]] = {}
        # 작업은 이름 있는 메서드와 인수만 보관한다. 중단한 실행 위치는 없다.
        stack: list[Callable[[], None]] = []
        stack.append(partial(self._merge_model, candidate, delta, stack, indexes))
        while stack:
            stack.pop()()
        validated = self._transform(candidate, validate=True)
        if not isinstance(validated, self._type):
            raise TypeError("델타 모델 타입이 병합기의 대상 타입과 다릅니다")
        self._model = validated

    @staticmethod
    def _empty_model(source: BaseModel) -> BaseModel:
        """입력과 같은 타입의 비어 있는 작업용 모델을 만든다. 외부에 노출하지 않는다."""
        model = source.model_copy()
        model.__dict__.clear()
        if model.__pydantic_extra__ is not None:
            model.__pydantic_extra__.clear()
        model.__pydantic_fields_set__.clear()
        return model

    def _merge_model(
        self, target: BaseModel, source: BaseModel, stack: list[Callable[[], None]],
        indexes: dict[int, tuple[str, dict[Any, Any]]],
    ) -> None:
        """모델을 만났을 때 필드 작업을 역순 등록해 수신 순서대로 실행한다."""
        for name in reversed(source.model_extra or {}):
            stack.append(partial(self._merge_field, target, source, name, None, stack, indexes))
        for name, definition in reversed(type(source).model_fields.items()):
            stack.append(partial(
                self._merge_field, target, source, name, definition, stack, indexes
            ))

    def _merge_field(
        self, target: BaseModel, source: BaseModel, name: str, definition: FieldInfo | None,
        stack: list[Callable[[], None]], indexes: dict[int, tuple[str, dict[Any, Any]]],
    ) -> None:
        """필드 하나를 후보에 누적한다. 중첩 모델·목록은 하위 작업으로 등록한다."""
        extra = definition is None
        value = (source.model_extra or {}).get(name) if extra else getattr(source, name, None)
        if value is None:
            return
        values = target.__pydantic_extra__ if extra else target.__dict__
        assert values is not None
        replace = extra or name not in values
        if not replace:
            if name not in source.model_fields_set:
                return
            assert definition is not None
            annotation = definition.annotation
            if get_origin(annotation) in (Union, UnionType):
                args = [arg for arg in get_args(annotation) if arg is not type(None)]
                if len(args) == 1:
                    annotation = args[0]
            metadata = definition.json_schema_extra
            policy = metadata.get(MERGE_META_KEY) if isinstance(metadata, dict) else None
            replace = policy in (INDEX, OVERWRITE) or name == "index" or (
                get_origin(annotation) is Literal
            )
        if replace:
            if isinstance(value, BaseModel):
                values[name] = self._empty_model(value)
            elif isinstance(value, list):
                values[name] = []
            else:
                values[name] = self._transform(value, validate=False)
        else:
            current = values[name]
            if isinstance(current, str) and isinstance(value, str):
                values[name] = current + value
            elif isinstance(current, bool) or isinstance(value, bool):
                values[name] = value
            elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
                values[name] = current + value
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(self._transform(value, validate=False))
            elif not (
                isinstance(current, BaseModel) and isinstance(value, BaseModel)
                or isinstance(current, list) and isinstance(value, list)
            ):
                values[name] = self._transform(value, validate=False)
                return
        current = values[name]
        if isinstance(current, BaseModel) and isinstance(value, BaseModel):
            stack.append(partial(self._merge_model, current, value, stack, indexes))
        elif isinstance(current, list) and isinstance(value, list):
            for item in reversed(value):
                stack.append(partial(self._merge_item, current, item, stack, indexes))

    def _merge_item(
        self, target: list[Any], item: Any, stack: list[Callable[[], None]],
        indexes: dict[int, tuple[str, dict[Any, Any]]],
    ) -> None:
        """목록 원소를 추가하거나 고정 식별값의 기존 모델에 하위 병합을 등록한다."""
        if not isinstance(item, BaseModel):
            copied = self._transform(item, validate=False)
            target.append(copied)
            state = indexes.get(id(target))
            if state is not None and isinstance(copied, dict):
                previous_field, existing_lookup = state
                key = copied.get(previous_field)
                if key is not None:
                    existing_lookup.setdefault(key, copied)
            return
        name = self._index_field(item)
        key = getattr(item, name, None) if name else None
        slot = None
        if name is not None and key is not None:
            state = indexes.get(id(target))
            if state is None:
                lookup: dict[Any, Any] = {}
                for existing in target:
                    if isinstance(existing, BaseModel):
                        previous_identity = self._index_field(existing)
                        if previous_identity is not None and (
                            getattr(existing, previous_identity, None) is not None
                            and previous_identity != name
                        ):
                            raise ValueError("같은 목록의 식별 필드는 변경할 수 없습니다")
                    existing_key = existing.get(name) if isinstance(existing, dict) else (
                        getattr(existing, name, None)
                    )
                    if existing_key is not None:
                        lookup.setdefault(existing_key, existing)
                indexes[id(target)] = (name, lookup)
            else:
                previous_name, lookup = state
                if previous_name != name:
                    raise ValueError("같은 목록의 식별 필드는 변경할 수 없습니다")
            slot = lookup.get(key)
        if slot is None:
            slot = self._empty_model(item)
            target.append(slot)
            if name is not None and key is not None:
                lookup[key] = slot
        if isinstance(slot, dict):
            # 먼저 도착한 일반 사전을 해당 모델의 필드 병합 대상으로 변환한다.
            position = next(i for i, existing in enumerate(target) if existing is slot)
            previous_values = slot
            slot = self._empty_model(item)
            slot.__dict__.update(previous_values)
            target[position] = slot
            if name is not None and key is not None:
                lookup[key] = slot
        stack.append(partial(self._merge_model, slot, item, stack, indexes))

    @staticmethod
    def _index_field(model: BaseModel) -> str | None:
        """명시한 index 정책의 첫 필드 또는 관례적인 index 필드 이름을 반환한다."""
        definitions = type(model).model_fields
        for name, definition in definitions.items():
            metadata = definition.json_schema_extra
            if isinstance(metadata, dict) and metadata.get(MERGE_META_KEY) == INDEX:
                return name
        return "index" if "index" in definitions else None

    @staticmethod
    def _transform(value: Any, *, validate: bool) -> Any:
        """반복형 DFS로 컨테이너를 복사하고 지정 시 모델을 자식부터 검증한다.

        부모 검증에는 검증된 자식 모델을 전달한다. 입력 모델과 공유하는 가변
        컨테이너를 남기지 않으며 Python 재귀 호출 스택을 사용하지 않는다.
        """
        holder: dict[Any, Any] = {}
        stack: list[tuple[Any, Any, Any, bool]] = [(value, holder, "result", False)]
        while stack:
            source, parent, key, finish = stack.pop()
            if finish:
                parent[key] = type(source).model_validate(parent[key])
                continue
            if isinstance(source, BaseModel):
                if validate:
                    data: dict[str, Any] = {}
                    parent[key] = data
                    stack.append((source, parent, key, True))
                    fields = {**source.__dict__, **(source.model_extra or {})}
                    stack.extend((v, data, k, False) for k, v in reversed(fields.items()))
                else:
                    copied = source.model_copy()
                    copied.__dict__.clear()
                    parent[key] = copied
                    stack.extend((v, copied.__dict__, k, False)
                                 for k, v in reversed(source.__dict__.items()))
                    if source.model_extra:
                        assert copied.__pydantic_extra__ is not None
                        copied.__pydantic_extra__.clear()
                        stack.extend((v, copied.__pydantic_extra__, k, False)
                                     for k, v in reversed(source.model_extra.items()))
            elif isinstance(source, dict):
                data = {}
                parent[key] = data
                stack.extend((v, data, k, False) for k, v in reversed(source.items()))
            elif isinstance(source, list):
                items = [None] * len(source)
                parent[key] = items
                stack.extend((v, items, i, False) for i, v in reversed(list(enumerate(source))))
            else:
                parent[key] = deepcopy(source)
        return holder["result"]
