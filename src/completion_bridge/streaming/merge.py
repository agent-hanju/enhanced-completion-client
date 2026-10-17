"""모델에 델타를 직접 병합하고 실패 시 부분 반영 상태를 보존한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from types import UnionType
from typing import Any, Generic, Literal, NamedTuple, TypeVar, Union, cast, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from completion_bridge.errors import StreamMergeError
from completion_bridge.streaming.metadata import INDEX, MERGE_META_KEY, OVERWRITE

__all__ = ["StreamMerger"]


class _ListIndex(NamedTuple):
    """목록의 식별 필드와 원소 위치를 보관하고 목록의 수명을 유지한다."""

    items: list[Any]
    field_name: str
    positions: dict[Any, int]


@dataclass(slots=True)
class _CopyTask(ABC):
    """DFS 스택에서 인자 없이 실행하는 복사 작업의 공통 인터페이스."""

    stack: list[_CopyTask]
    source: Any
    parent: Any
    key: Any

    @abstractmethod
    def execute(self) -> None:
        """보관한 입력의 복사 작업을 실행한다."""


@dataclass(slots=True)
class _CopyValueTask(_CopyTask):
    """값을 복사하고 하위 모델·사전·목록의 복사가 필요할 때 등록한다."""

    def execute(self) -> None:
        """결과를 기록하고 하위 복사 작업을 역순 등록한다."""
        source = self.source
        scalar = (str, int, float, bool)
        if source is None or isinstance(source, scalar):
            self.parent[self.key] = source
        elif isinstance(source, BaseModel):
            data = dict(source)
            self.parent[self.key] = data
            # 하위 복사가 모두 끝난 뒤 모델 인스턴스를 구성한다.
            self.stack.append(_FinishCopyModelTask(
                stack=self.stack, source=source, parent=self.parent, key=self.key
            ))
            for name in reversed(data):
                child = data[name]
                if child is not None and not isinstance(child, scalar):
                    self.stack.append(_CopyValueTask(
                        stack=self.stack, source=child, parent=data, key=name
                    ))
        elif isinstance(source, dict):
            data = dict(source)
            self.parent[self.key] = data
            for name in reversed(data):
                child = data[name]
                if child is not None and not isinstance(child, scalar):
                    self.stack.append(_CopyValueTask(
                        stack=self.stack, source=child, parent=data, key=name
                    ))
        elif isinstance(source, list):
            items = list(source)
            self.parent[self.key] = items
            for offset in range(len(source) - 1, -1, -1):
                child = source[offset]
                if child is not None and not isinstance(child, scalar):
                    self.stack.append(_CopyValueTask(
                        stack=self.stack, source=child, parent=items, key=offset
                    ))
        else:
            self.parent[self.key] = deepcopy(source)


@dataclass(slots=True)
class _FinishCopyModelTask(_CopyTask):
    """모델의 하위 값 복사 후 인스턴스와 명시 입력 필드 정보를 복원할 때 등록한다."""

    def execute(self) -> None:
        """복사를 완료한 필드들로 검증 없이 모델을 구성한다."""
        model = self.source.model_copy(update=self.parent[self.key])
        model.model_fields_set.clear()
        model.model_fields_set.update(self.source.model_fields_set)
        self.parent[self.key] = model


@dataclass(slots=True)
class _MergeTask(ABC):
    """DFS 스택에서 인자 없이 실행하는 병합 작업의 공통 인터페이스."""

    merger: StreamMerger[Any]
    stack: list[_MergeTask]

    @abstractmethod
    def execute(self) -> None:
        """보관한 입력으로 작업을 실행하고 필요한 하위 작업을 스택에 등록한다."""


@dataclass(slots=True)
class _MergeModelTask(_MergeTask):
    """루트 또는 하위 모델의 필드 순회를 예약할 때 등록한다."""

    target: BaseModel
    source: BaseModel
    initial: bool

    def execute(self) -> None:
        """모델의 각 필드에 대한 병합 작업을 등록한다."""
        self.merger._merge_model(self.target, self.source, self.initial, self.stack)


@dataclass(slots=True)
class _MergeFieldTask(_MergeTask):
    """모델의 필드 하나를 병합하고 하위 모델·목록을 처리할 때 등록한다."""

    target: BaseModel
    source: BaseModel
    name: str
    definition: FieldInfo | None
    initial: bool

    def execute(self) -> None:
        """필드를 즉시 반영하고 필요한 하위 작업을 등록한다."""
        self.merger._merge_field(
            self.target, self.source, self.name, self.definition, self.initial, self.stack
        )


@dataclass(slots=True)
class _MergeItemTask(_MergeTask):
    """목록 원소를 추가하거나 같은 식별값의 모델에 병합할 때 등록한다."""

    target: list[Any]
    item: Any

    def execute(self) -> None:
        """목록 원소를 반영하고 모델 필드 순회를 등록한다."""
        self.merger._merge_item(self.target, self.item, self.stack)


M = TypeVar("M", bound=BaseModel)


class StreamMerger(Generic[M]):  # noqa: UP046 - 상호 참조 클래스의 variance 자동 추론을 피한다.
    """요청별 best effort 모델 병합기. 별도 검증과 롤백은 수행하지 않는다.

    문자열 연결·수치 합산·사전의 얕은 갱신·식별 목록 병합을 수행한다.
    기존 값에 대한 None은 무시하고 extra·overwrite·index·Literal 필드는 교체한다.
    실패하면 해당 지점까지 반영한 상태와 실패 델타를 StreamMergeError로 전달한다.
    모델의 기존 속성 대입 정책을 따르며 같은 인스턴스의 동시 접근은 지원하지 않는다.
    """

    def __init__(self, model: type[M]) -> None:
        self._type = model
        self._model: M | None = None
        # 목록 자체도 보관해 id 재사용을 방지한다. 값은 원소의 목록 위치이다.
        self._indexes: dict[int, _ListIndex] = {}

    @property
    def result(self) -> M | None:
        """현재 상태의 독립된 복사본. 초기에는 None이며 오류 후에는 부분 반영 모델이다."""
        return cast(M | None, self._copy(self._model))

    def apply(self, delta: M | None) -> None:
        """직접 병합한다. 실패 시 이전 변경을 유지하고 델타와 결과를 예외로 전달한다."""
        if delta is None:
            return
        try:
            if not isinstance(delta, self._type):
                raise TypeError("델타 모델 타입이 병합기의 대상 타입과 다릅니다")
            initial = self._model is None
            if initial:
                self._model = cast(M, self._new_model(delta))
            assert self._model is not None
            stack: list[_MergeTask] = []
            stack.append(_MergeModelTask(
                merger=self, stack=stack, target=self._model, source=delta, initial=initial
            ))
            while stack:
                task = stack.pop()
                task.execute()
        except Exception as error:
            raise StreamMergeError(delta, self.result) from error

    def _merge_model(
        self, target: BaseModel, source: BaseModel, initial: bool,
        stack: list[_MergeTask],
    ) -> None:
        """모델의 필드 작업을 역순 등록한다. 새 모델은 최초 값으로 초기화한다."""
        for name in reversed(source.model_extra or {}):
            value = getattr(source, name, None)
            if value is not None and (not initial or isinstance(value, (BaseModel, list))):
                stack.append(_MergeFieldTask(
                    merger=self, stack=stack, target=target, source=source,
                    name=name, definition=None, initial=initial,
                ))
        for name in reversed(type(source).model_fields):
            definition = type(source).model_fields[name]
            value = getattr(source, name, None)
            if initial:
                # 새 모델의 일반 값은 이미 복사했다. 하위 모델·목록만 순서대로 처리한다.
                if not isinstance(value, (BaseModel, list)):
                    continue
            elif name not in source.model_fields_set or value is None:
                continue
            stack.append(_MergeFieldTask(
                merger=self, stack=stack, target=target, source=source,
                name=name, definition=definition, initial=initial,
            ))

    def _merge_field(
        self, target: BaseModel, source: BaseModel, name: str, definition: FieldInfo | None,
        initial: bool, stack: list[_MergeTask],
    ) -> None:
        """필드 하나를 즉시 반영하고 필요한 하위 모델·목록 작업을 등록한다."""
        value = getattr(source, name, None)
        if value is None:
            return
        replace = initial or definition is None or not hasattr(target, name)
        if not replace:
            if name not in source.model_fields_set:
                return
            assert definition is not None
            annotation = definition.annotation
            if get_origin(annotation) in (Union, UnionType):
                args = []
                for argument in get_args(annotation):
                    if argument is not type(None):
                        args.append(argument)
                if len(args) == 1:
                    annotation = args[0]
            metadata = definition.json_schema_extra
            policy = metadata.get(MERGE_META_KEY) if isinstance(metadata, dict) else None
            replace = policy in (INDEX, OVERWRITE) or name == "index" or (
                get_origin(annotation) is Literal
            )
        current = getattr(target, name, None)
        provided = name in source.model_fields_set
        if replace or current is None:
            self._forget(current)
            if isinstance(value, BaseModel):
                current = self._new_model(value)
                self._assign(target, name, current, provided)
                stack.append(_MergeModelTask(
                    merger=self, stack=stack, target=current, source=value, initial=True
                ))
                return
            if isinstance(value, list):
                current = []
                self._assign(target, name, current, provided)
            else:
                self._assign(target, name, self._copy(value), provided)
                return
        elif isinstance(current, str) and isinstance(value, str):
            self._assign(target, name, current + value, provided)
            return
        elif isinstance(current, bool) or isinstance(value, bool):
            self._forget(current)
            self._assign(target, name, self._copy(value), provided)
            return
        elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
            self._assign(target, name, current + value, provided)
            return
        elif isinstance(current, dict) and isinstance(value, dict):
            current.update(self._copy(value))
            return
        elif isinstance(current, BaseModel) and isinstance(value, BaseModel):
            stack.append(_MergeModelTask(
                merger=self, stack=stack, target=current, source=value, initial=False
            ))
            return
        elif not (isinstance(current, list) and isinstance(value, list)):
            self._forget(current)
            self._assign(target, name, self._copy(value), provided)
            return
        if not value:
            return
        # 식별 검색이 없는 일반 목록은 한 번 복사·추가하고 원소별 호출도 생략한다.
        contains_model = False
        for item in value:
            if isinstance(item, BaseModel):
                contains_model = True
                break
        if not contains_model:
            if id(current) not in self._indexes:
                current.extend(self._copy(value))
            else:
                for item in value:
                    self._merge_item(current, item, stack)
        else:
            for item in reversed(value):
                stack.append(_MergeItemTask(
                    merger=self, stack=stack, target=current, item=item
                ))

    def _new_model(self, source: BaseModel) -> BaseModel:
        """새 입력의 일반 값을 복사하고 하위 모델·목록은 DFS로 채울 자리를 만든다.

        이미 생성된 입력을 복사하므로 기본값 팩터리와 초기화 훅을 재실행하지 않는다.
        기존 누적 모델을 복사하지 않으며 새 모델의 초기 값도 즉시 현재 상태에 포함된다.
        """
        values: dict[str, Any] = {}
        for name in dict(source):
            value = getattr(source, name)
            if isinstance(value, BaseModel):
                values[name] = None
            elif isinstance(value, list):
                values[name] = []
            else:
                values[name] = self._copy(value)
        model = source.model_copy(update=values)
        model.model_fields_set.clear()
        model.model_fields_set.update(source.model_fields_set)
        return model

    @staticmethod
    def _assign(target: BaseModel, name: str, value: Any, provided: bool) -> None:
        """일반 속성 대입을 사용하고 최초 기본값을 명시 입력으로 바꾸지 않는다."""
        setattr(target, name, value)
        if not provided:
            target.model_fields_set.discard(name)

    def _merge_item(
        self, target: list[Any], item: Any, stack: list[_MergeTask],
    ) -> None:
        """목록 원소를 추가하거나 유지 중인 검색 사전으로 기존 모델을 찾는다."""
        if not isinstance(item, BaseModel):
            copied = self._copy(item)
            target.append(copied)
            state = self._indexes.get(id(target))
            if state is not None and isinstance(copied, dict):
                key = copied.get(state.field_name)
                if key is not None:
                    state.positions.setdefault(key, len(target) - 1)
            return
        name = self._index_field(item)
        key = getattr(item, name, None) if name else None
        position = None
        lookup: dict[Any, int] = {}
        if name is not None and key is not None:
            state = self._indexes.get(id(target))
            if state is None:
                for offset in range(len(target)):
                    existing = target[offset]
                    if isinstance(existing, BaseModel):
                        previous = self._index_field(existing)
                        if (previous and getattr(existing, previous, None) is not None
                                and previous != name):
                            raise ValueError("같은 목록의 식별 필드는 변경할 수 없습니다")
                    existing_key = existing.get(name) if isinstance(existing, dict) else (
                        getattr(existing, name, None)
                    )
                    if existing_key is not None:
                        lookup.setdefault(existing_key, offset)
                self._indexes[id(target)] = _ListIndex(
                    items=target, field_name=name, positions=lookup
                )
            else:
                lookup = state.positions
                if state.field_name != name:
                    raise ValueError("같은 목록의 식별 필드는 변경할 수 없습니다")
            position = lookup.get(key)
        initial = position is None
        if position is None:
            slot = self._new_model(item)
            target.append(slot)
            if name is not None and key is not None:
                lookup[key] = len(target) - 1
        else:
            slot = target[position]
            if isinstance(slot, dict):
                slot = type(item).model_construct(**slot)
                target[position] = slot
        stack.append(_MergeModelTask(
            merger=self, stack=stack, target=slot, source=item, initial=initial
        ))

    def _forget(self, value: Any) -> None:
        """교체로 버리는 하위 모델·목록의 검색 정보를 제거해 객체를 계속 보관하지 않는다."""
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, BaseModel):
                stack.extend(dict(current).values())
            elif isinstance(current, list):
                self._indexes.pop(id(current), None)
                stack.extend(current)
            elif isinstance(current, dict):
                stack.extend(current.values())

    @staticmethod
    def _index_field(model: BaseModel) -> str | None:
        """index 정책의 첫 필드 또는 관례적인 index 필드 이름을 반환한다."""
        definitions = type(model).model_fields
        for name in definitions:
            definition = definitions[name]
            metadata = definition.json_schema_extra
            if isinstance(metadata, dict) and metadata.get(MERGE_META_KEY) == INDEX:
                return name
        return "index" if "index" in definitions else None

    @staticmethod
    def _copy(value: Any) -> Any:
        """검증 없이 독립된 스냅샷을 만든다. 모델과 컨테이너는 반복형 DFS로 복사한다."""
        scalar = (str, int, float, bool)
        if value is None or isinstance(value, scalar):
            return value
        holder: dict[Any, Any] = {}
        stack: list[_CopyTask] = []
        stack.append(_CopyValueTask(stack=stack, source=value, parent=holder, key="result"))
        while stack:
            task = stack.pop()
            task.execute()
        return holder["result"]
