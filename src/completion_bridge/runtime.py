"""모델 반복 호출과 호출별 HITL 실행 수명 관리."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .blocks import ToolResultBlock, ToolUseBlock
from .bridge import AsyncStream, Bridge
from .errors import CompletionBridgeError, StreamNotFinished
from .hub import HubMessage, HubResponse
from .runtime_models import (
    Execution,
    HitlDecision,
    HitlDraft,
    PolicyEvaluator,
    ToolCall,
    ToolExecutor,
    ToolOutput,
    ToolPolicy,
)

RunStatus = Literal[
    "pending", "running", "completed", "approval_required", "limit_reached", "error", "cancelled"
]


@dataclass
class Run:
    """독립 복사한 입력과 생성 결과 및 호출별 작업을 보관한다."""

    messages: list[HubMessage]
    generated_messages: list[HubMessage] = field(default_factory=list)
    responses: list[HubResponse] = field(default_factory=list)
    tasks: list[HitlTask] = field(default_factory=list)
    status: RunStatus = "pending"
    active_response: HubResponse | None = None
    final_result: ToolResultBlock | None = None

    async def aclose(self) -> None:
        """자식 실행을 포함한 모든 작업을 취소하고 자원을 회수한다."""
        pending = []
        for task in self.tasks:
            task.cancel()
            if task._task is not None:
                pending.append(task._task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in self.tasks:
            if task.child_run is not None:
                await task.child_run.aclose()


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


class HitlTask:
    """호출 하나의 기록과 실제 실행 및 모델용 결과를 관리한다."""

    def __init__(self, draft: HitlDraft) -> None:
        self.draft = draft
        self.result: ToolResultBlock | None = None
        self.final = False
        self.failure: BaseException | None = None
        self.child_run: Run | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def status(self) -> str:
        """현재 작업의 실행 또는 승인 필요 상태를 반환한다."""
        if self._task is not None and self._task.cancelled():
            return "cancelled"
        if self.failure is not None:
            return "error"
        for decision in (self.draft.approval, self.draft.review):
            if decision is not None and decision.decision == ToolPolicy.ASK:
                return "approval_required"
        if self._task is None:
            if self.result is not None:
                return "completed"
            return "pending"
        return "completed" if self._task.done() else "running"

    async def wait(self) -> HitlDraft:
        """작업과 검토가 끝날 때까지 기다린다. 대기 취소는 작업을 취소하지 않는다."""
        if self._task is not None:
            await asyncio.shield(self._task)
        if self.failure is not None:
            raise self.failure
        return self.draft

    def cancel(self) -> None:
        """실제 실행을 취소하도록 요청한다. 이미 발생한 효과는 유지한다."""
        if self._task is not None:
            self._task.cancel()

    async def _execute(self, tools: ToolExecutor, policy: PolicyEvaluator) -> None:
        """도구 실패를 기록하고 성공 결과에 실행 후 정책을 적용한다."""
        try:
            try:
                output = await tools.execute(self.draft.call.model_copy(deep=True))
            except RuntimeErrorWithPartial as exc:
                self.child_run = exc.partial
                raise
            except Exception as exc:
                self.draft.error = type(exc).__name__
                output = ToolOutput(
                    execution=Execution(is_error=True, error=type(exc).__name__),
                    result=self._message("Tool execution failed", is_error=True),
                )
            self.child_run = output.child_run
            self.draft.execution = output.execution.model_copy(deep=True)
            if self.child_run is not None and self.child_run.status == "approval_required":
                self.draft.review = HitlDecision(
                    decision=ToolPolicy.ASK,
                    source="default",
                )
                self.draft.review_reason = "Child agent requires approval"
                return
            self.draft.review = await policy.review(
                self.draft.call.model_copy(deep=True),
                self.draft.execution.model_copy(deep=True),
            )
            if self.draft.review.decision == ToolPolicy.ALLOW:
                self.result = output.result.model_copy(deep=True)
                self.result.tool_use_id = self.draft.call.tool_call_id
                self.result.name = self.draft.call.tool_name
                self.final = output.final
            elif self.draft.review.decision == ToolPolicy.DENY:
                self.result = self._message("Tool result denied by policy", is_error=True)
        except asyncio.CancelledError as exc:
            if isinstance(exc, RuntimeCancelled):
                self.child_run = exc.partial
            self.failure = exc
            raise
        except Exception as exc:
            self.draft.error = type(exc).__name__
            self.failure = exc

    def _message(self, content: str, *, is_error: bool = False) -> ToolResultBlock:
        """원본 호출 식별자를 유지하는 모델용 결과를 생성한다."""
        return ToolResultBlock(
            tool_use_id=self.draft.call.tool_call_id,
            name=self.draft.call.tool_name,
            content=content,
            is_error=is_error,
        )


class RuntimeExecution:
    """첫 소비부터 실행하며 명시적 정리와 요청별 부분 결과를 제공한다.

    조기 반복 종료에는 ``async with`` 또는 ``aclose()``를 사용해야 한다.
    정상 종료 이후의 백그라운드 작업은 호출자가 관리한다.
    """

    def __init__(
        self,
        bridge: Bridge,
        messages: Sequence[HubMessage],
        tools: ToolExecutor,
        policy: PolicyEvaluator,
        background_tools: Sequence[str],
        max_tool_calls: int,
    ) -> None:
        self.partial = Run(messages=copy.deepcopy(list(messages)))
        self.request_index = -1
        self._bridge = bridge
        self._tools = tools
        self._definitions = copy.deepcopy(list(tools.definitions))
        self._policy = policy
        self._background_tools = frozenset(background_tools)
        self._tool_names = set()
        for definition in self._definitions:
            self._tool_names.add(definition.name)
        self._max_tool_calls = max_tool_calls
        self._stream: AsyncStream | None = None
        self._iterator = self._iterate()
        self._finished = False
        self._closed = False

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
        return await self._iterator.__anext__()

    async def __aenter__(self) -> RuntimeExecution:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """조기 종료 시 연결과 미완료 작업을 정리한다. 정상 종료 작업은 유지한다."""
        await self._iterator.aclose()
        if not self._finished:
            if self.partial.status in ("pending", "running"):
                self.partial.status = "cancelled"
            await self.partial.aclose()
        self._closed = True

    async def _iterate(self) -> AsyncGenerator[HubResponse, None]:
        """요청별 응답을 분리해 보존하고 모든 도구 결과 준비 후 다음 요청을 시작한다."""
        self.partial.status = "running"
        try:
            while True:
                self.request_index += 1
                self._stream = self._bridge.stream(
                    self.partial.messages + self.partial.generated_messages,
                    tools=self._definitions,
                )
                try:
                    async for delta in self._stream:
                        self.partial.active_response = self._stream.partial.model_copy(deep=True)
                        yield delta
                    response = self._stream.result.model_copy(deep=True)
                finally:
                    await self._stream.aclose()
                    self.partial.active_response = self._stream.partial.model_copy(deep=True)
                self.partial.responses.append(response)
                self.partial.active_response = None
                self.partial.generated_messages.append(HubMessage.of_response(response))
                batch = self._create_tasks(response)
                if not batch:
                    self.partial.status = "completed"
                    break
                await self._process_tasks(batch)
                if self.partial.status != "running":
                    break
            self._finished = True
        except asyncio.CancelledError as exc:
            self.partial.status = "cancelled"
            raise RuntimeCancelled(self.partial) from exc
        except Exception as exc:
            self.partial.status = "error"
            raise RuntimeErrorWithPartial(self.partial) from exc
        finally:
            if not self._finished:
                if self.partial.status == "running":
                    self.partial.status = "cancelled"
                await self.partial.aclose()

    def _create_tasks(self, response: HubResponse) -> list[HitlTask]:
        """모든 호출을 실행 전에 기록해 처리하지 않은 호출도 확인할 수 있게 한다."""
        batch = []
        for ordinal in range(len(response.content)):
            block = response.content[ordinal]
            if isinstance(block, ToolUseBlock):
                arguments = block.input
                if arguments is None and not block.input_json:
                    arguments = {}
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                call = ToolCall(
                    ordinal=ordinal,
                    tool_call_id=block.id,
                    tool_name=block.name,
                    arguments=copy.deepcopy(arguments),
                )
                draft = HitlDraft(
                    call=call,
                    created_at=datetime.now(UTC),
                    background=call.tool_name in self._background_tools,
                )
                task = HitlTask(draft)
                batch.append(task)
                self.partial.tasks.append(task)
        return batch

    async def _process_tasks(self, batch: list[HitlTask]) -> None:
        """호출 순서대로 판정 및 실행하고 사용자 승인 또는 제한에서 종료한다."""
        for task in batch:
            call_index = self.partial.tasks.index(task)
            if call_index >= self._max_tool_calls:
                self.partial.status = "limit_reached"
                return
            task.draft.approval = await self._policy.approve(task.draft.call.model_copy(deep=True))
            decision = task.draft.approval.decision
            if decision == ToolPolicy.ASK:
                self.partial.status = "approval_required"
                return
            if decision == ToolPolicy.DENY:
                task.result = task._message("Tool call denied by policy", is_error=True)
            elif task.draft.call.tool_name not in self._tool_names:
                task.draft.error = "Unknown tool"
                task.result = task._message("Unknown tool", is_error=True)
            else:
                task._task = asyncio.create_task(task._execute(self._tools, self._policy))
                if task.draft.background:
                    receipt = task._message("Background tool accepted")
                    message = HubMessage(role="tool", content=[receipt])
                    self.partial.generated_messages.append(message)
                    continue
                await task.wait()
                if task.status == "approval_required":
                    self.partial.status = "approval_required"
                    return
            if task.result is not None:
                result = task.result.model_copy(deep=True)
                self.partial.generated_messages.append(HubMessage(role="tool", content=[result]))
                if task.final:
                    self.partial.final_result = result
                    self.partial.status = "completed"
                    return


class CompletionRuntime:
    """기존 Bridge를 사용하는 독립 실행을 생성한다."""

    def __init__(
        self,
        *,
        bridge: Bridge,
        max_tool_calls: int = 32,
        background_tools: Sequence[str] = (),
    ) -> None:
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a nonnegative integer")
        self._bridge = bridge
        self._max_tool_calls = max_tool_calls
        self._background_tools = tuple(background_tools)

    def run(
        self,
        messages: Sequence[HubMessage],
        *,
        tools: ToolExecutor,
        policy: PolicyEvaluator,
    ) -> RuntimeExecution:
        """입력을 즉시 복사하고 첫 비동기 소비 때 실행을 시작한다."""
        names = set()
        for definition in tools.definitions:
            if definition.name in names:
                raise ValueError("Duplicate tool definition")
            names.add(definition.name)
        if not set(self._background_tools).issubset(names):
            raise ValueError("background_tools contains an undefined tool")
        return RuntimeExecution(
            self._bridge,
            messages,
            tools,
            policy,
            self._background_tools,
            self._max_tool_calls,
        )

    async def complete(
        self,
        messages: Sequence[HubMessage],
        *,
        tools: ToolExecutor,
        policy: PolicyEvaluator,
    ) -> Run:
        """동일한 스트림을 끝까지 소비하고 작업 핸들을 포함한 실행 결과를 반환한다."""
        execution = self.run(messages, tools=tools, policy=policy)
        async with execution:
            async for _ in execution:
                pass
        return execution.result
