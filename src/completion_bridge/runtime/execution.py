"""HubRequest와 외부 도구 콜백을 사용한 모델 반복 호출."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from completion_bridge.errors import (
    CompletionBridgeError,
    ExecutionStateError,
    MappingError,
    StreamNotFinished,
)
from completion_bridge.logging import OperationLog, current_operation_id
from completion_bridge.models.blocks import ToolResultBlock, ToolUseBlock
from completion_bridge.models.hub import HubMessage, HubRequest, HubResponse
from completion_bridge.models.tools import Execution, HitlDraft, ToolCall, ToolCallback, ToolOutput


class _ResponseStream(Protocol):
    """반복 실행에서 사용하는 모델 스트림의 결과와 수명 관리 계약."""

    @property
    def result(self) -> HubResponse | None: ...

    def __aiter__(self) -> AsyncIterator[HubResponse]: ...

    async def aclose(self) -> None: ...


RunStatus = Literal[
    "pending", "running", "completed", "hitl_required", "limit_reached", "error", "cancelled"
]


@dataclass
class Run:
    """입력 요청과 생성 메시지·응답·HITL 기록 및 콜백이 제공한 작업을 보관한다."""

    request: HubRequest
    generated_messages: list[HubMessage] = field(default_factory=list)
    responses: list[HubResponse] = field(default_factory=list)
    tasks: list[HitlDraft] = field(default_factory=list)
    status: RunStatus = "pending"
    _active_response: HubResponse | None = field(default=None, repr=False, init=False)
    _read_response: Callable[[], HubResponse | None] | None = field(
        default=None, repr=False, init=False
    )
    final_result: ToolResultBlock | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def active_response(self) -> HubResponse | None:
        """실행 중에는 조회 시점에만 스냅샷을 만들고, 종료 후에는 보관한 응답을 반환한다."""
        return self._read_response() if self._read_response is not None else self._active_response

    @active_response.setter
    def active_response(self, value: HubResponse | None) -> None:
        """종료 시 스냅샷을 보관하고 진행 중 스트림에 대한 참조를 해제한다."""
        self._read_response = None
        self._active_response = value

    @property
    def messages(self) -> list[HubMessage]:
        """입력 이력과 이번 실행에서 생성한 메시지를 순서대로 제공한다."""
        return self.request.messages + self.generated_messages

    async def aclose(self) -> None:
        """호출자가 명시적으로 요청하면 전달받은 작업들을 취소하고 완료를 기다린다."""
        pending = []
        for draft in self.tasks:
            if draft.task is not None:
                draft.task.cancel()
                pending.append(draft.task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class RuntimeErrorWithPartial(CompletionBridgeError):
    """실행을 중단한 예외와 확보한 부분 결과를 제공한다."""

    def __init__(self, partial: Run) -> None:
        super().__init__("Completion runtime failed; inspect partial and __cause__")
        self.partial = partial


class RuntimeCancelled(asyncio.CancelledError):
    """취소 의미를 유지하면서 부분 실행 결과를 제공한다."""

    def __init__(self, partial: Run) -> None:
        super().__init__("Completion runtime cancelled")
        self.partial = partial


class RuntimeExecution:
    """델타 순회 또는 await로 소비하고 결과와 부분 실행 기록을 제공한다.

    조기 반복 종료에는 async with 또는 aclose()를 사용한다.
    콜백이 제공한 작업은 자동으로 취소하지 않으며 Run.aclose()로 별도 정리한다.
    """

    def __init__(
        self,
        stream: Callable[..., _ResponseStream],
        request: HubRequest,
        execute_tool: ToolCallback,
        max_tool_calls: int,
    ) -> None:
        """호출 설정을 검증하고 요청 전체의 독립 사본과 반복자를 준비한다."""
        if not isinstance(request, HubRequest):
            raise TypeError("request must be HubRequest")
        if not callable(execute_tool):
            raise TypeError("execute_tool must be callable")
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a nonnegative integer")
        reserved = {"messages", "tools", "model", "hyperparameters", "request_id"}
        if reserved.intersection(request.params):
            raise ValueError("request.params contains a reserved request field")
        self.partial = Run(request=request.model_copy(deep=True))
        self.request_index = -1
        self._stream = stream
        self._execute_tool = execute_tool
        self._max_tool_calls = max_tool_calls
        self._iterator = self._iterate()
        self._finished = False
        self._closed = False
        self._advancing = False
        self._operation = OperationLog("runtime", request_id=self.partial.request_id)

    @property
    def result(self) -> Run:
        """정상 종료 후 결과를 제공한다. 오류와 취소는 partial로 조회한다."""
        if not self._finished:
            raise StreamNotFinished("Runtime has not finished normally")
        return self.partial

    def __aiter__(self) -> RuntimeExecution:
        return self

    async def __anext__(self) -> HubResponse:
        if self._closed:
            raise StopAsyncIteration
        if self._advancing:
            raise ExecutionStateError("Runtime execution is already being consumed")
        self._advancing = True
        try:
            return await self._iterator.__anext__()
        finally:
            self._advancing = False

    def __await__(self) -> Generator[Any, None, Run]:
        return self.collect().__await__()

    async def collect(self) -> Run:
        """델타를 끝까지 소비하고 Run을 반환한다. 이미 완료했으면 같은 결과를 반환한다."""
        if self._finished:
            return self.result
        async with self:
            async for _ in self:
                pass
        return self.result

    async def __aenter__(self) -> RuntimeExecution:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """모델 연결을 정리한다. 전달받은 외부 작업은 partial에 유지한다."""
        if self._advancing:
            raise ExecutionStateError("Cancel the consuming task before closing runtime execution")
        await self._iterator.aclose()
        if self.partial.status in ("pending", "running"):
            self.partial.status = "cancelled"
        self._closed = True

    async def _iterate(self) -> AsyncGenerator[HubResponse, None]:
        """모델 응답을 전달하고 모든 도구 결과가 준비된 경우에만 후속 요청을 수행한다."""
        self.partial.status = "running"
        self._operation.start()
        failure_reported = False
        try:
            while True:
                self.request_index += 1
                request = self.partial.request
                token = current_operation_id.set(self.partial.request_id)
                try:
                    stream = self._stream(
                        self.partial.messages,
                        tools=request.tools,
                        model=request.model,
                        hyperparameters=request.hyperparameters,
                        request_id=f"{self.partial.request_id}:model:{self.request_index}",
                        **request.params,
                    )
                finally:
                    current_operation_id.reset(token)
                self.partial._read_response = lambda stream=stream: stream.result
                try:
                    async for delta in stream:
                        yield delta
                    response = stream.result
                except CompletionBridgeError:
                    failure_reported = True
                    raise
                finally:
                    await stream.aclose()
                    self.partial.active_response = stream.result
                if response is None:
                    self.partial.status = "completed"
                    break
                self.partial.responses.append(response)
                self.partial.active_response = None
                self.partial.generated_messages.append(HubMessage.of_response(response))
                batch = self._create_drafts(response)
                if not batch:
                    self.partial.status = "completed"
                    break
                await self._process_tools(batch)
                if self.partial.status != "running":
                    break
            self._finished = True
        except asyncio.CancelledError as exc:
            self.partial.status = "cancelled"
            raise RuntimeCancelled(self.partial) from exc
        except Exception as exc:
            self.partial.status = "error"
            if not failure_reported:
                self._operation.emit(logging.ERROR, "failed", error_type=type(exc).__name__)
            raise RuntimeErrorWithPartial(self.partial) from exc
        finally:
            if self.partial.status == "running":
                self.partial.status = "cancelled"
            self._operation.finish(self.partial.status, model_calls=self.request_index + 1)

    def _create_drafts(self, response: HubResponse) -> list[HitlDraft]:
        """모든 호출을 먼저 검증하고 원본 호출 ID·순서가 있는 기록을 원자적으로 추가한다."""
        batch: list[HitlDraft] = []
        call_ids: set[str] = set()
        for ordinal in range(len(response.content)):
            block = response.content[ordinal]
            if not isinstance(block, ToolUseBlock):
                continue
            arguments = block.input
            if arguments is None and not block.input_json:
                arguments = {}
            if not isinstance(arguments, dict):
                raise MappingError("Tool arguments must be a JSON object")
            call = ToolCall(
                ordinal=ordinal,
                tool_call_id=block.id,
                tool_name=block.name,
                arguments=copy.deepcopy(arguments),
            )
            if call.tool_call_id in call_ids:
                raise MappingError("A model response contains duplicate tool call IDs")
            call_ids.add(call.tool_call_id)
            batch.append(HitlDraft(call=call, created_at=datetime.now(UTC)))
        self.partial.tasks.extend(batch)
        return batch

    async def _process_tools(self, batch: list[HitlDraft]) -> None:
        """콜백이 제공한 기록과 결과를 보존하고 미해결 호출 또는 제한에서 종료한다."""
        batch_start = len(self.partial.tasks) - len(batch)
        for index in range(len(batch)):
            draft = batch[index]
            if batch_start + index >= self._max_tool_calls:
                self.partial.status = "limit_reached"
                self._operation.emit(
                    logging.WARNING,
                    "tool_limit_reached",
                    limit=self._max_tool_calls,
                    recovery="execution_stopped",
                )
                return
            operation = OperationLog(
                "tool",
                request_id=f"{self.partial.request_id}:tool:{batch_start + index}",
                parent_request_id=self.partial.request_id,
            )
            operation.start(ordinal=draft.call.ordinal)
            outcome = "error"
            token = current_operation_id.set(operation.request_id)
            try:
                output = await self._execute_tool(
                    draft.call.tool_name, copy.deepcopy(draft.call.arguments)
                )
                if not isinstance(output, ToolOutput):
                    raise TypeError("execute_tool must return ToolOutput")
                if output.result is not None and not isinstance(output.result, ToolResultBlock):
                    raise TypeError("ToolOutput.result must be ToolResultBlock or None")
                if output.draft is not None and not isinstance(output.draft, HitlDraft):
                    raise TypeError("ToolOutput.draft must be HitlDraft or None")
                if output.execution is not None and not isinstance(output.execution, Execution):
                    raise TypeError("ToolOutput.execution must be Execution or None")
                if output.task is not None and not isinstance(output.task, asyncio.Task):
                    raise TypeError("ToolOutput.task must be asyncio.Task or None")
                if type(output.final) is not bool or (output.final and output.result is None):
                    raise ValueError("ToolOutput.final must be bool and requires result when true")
                if output.draft is not None:
                    supplied = output.draft.model_copy()
                    task = supplied.task
                    supplied.task = None
                    supplied = supplied.model_copy(deep=True)
                    supplied.task = task
                    supplied.call = draft.call
                    draft = supplied
                    self.partial.tasks[batch_start + index] = draft
                if output.execution is not None:
                    draft.execution = output.execution.model_copy(deep=True)
                if output.task is not None:
                    draft.task = output.task
                    draft.background = True
                if output.result is None:
                    draft.result = None
                    self.partial.status = "hitl_required"
                    outcome = "hitl_required"
                    return
                result = output.result.model_copy(deep=True)
                result.tool_use_id = draft.call.tool_call_id
                result.name = draft.call.tool_name
                draft.result = result
                self.partial.generated_messages.append(HubMessage(role="tool", content=[result]))
                outcome = "completed"
                if output.final:
                    self.partial.final_result = result
                    self.partial.status = "completed"
                    return
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as exc:
                draft.error = type(exc).__name__
                raise
            finally:
                operation.finish(outcome)
                current_operation_id.reset(token)
