"""주입 실행기로 사용하는 독립 자식 에이전트."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .blocks import ToolResultBlock
from .hub import HubMessage, ToolDefinition
from .runtime import CompletionRuntime
from .runtime_models import Execution, PolicyEvaluator, ToolCall, ToolExecutor, ToolOutput


class AgentToolExecutor:
    """도구 호출을 독립 입력의 자식 런타임으로 실행한다.

    자식 실행은 부모 작업의 child_run으로 제공한다. 자식 승인 필요 상태는 부모에서도
    승인 필요로 처리한다. 자식 백그라운드 작업은 자식 Run.tasks로 관리한다.
    """

    def __init__(
        self,
        *,
        definition: ToolDefinition,
        runtime: CompletionRuntime,
        messages: Callable[[ToolCall], Sequence[HubMessage]],
        tools: ToolExecutor,
        policy: PolicyEvaluator,
    ) -> None:
        self._definition = definition.model_copy(deep=True)
        self._runtime = runtime
        self._messages = messages
        self._tools = tools
        self._policy = policy

    @property
    def definitions(self) -> Sequence[ToolDefinition]:
        """자식 에이전트를 호출하는 단일 도구 정의를 제공한다."""
        return [self._definition.model_copy(deep=True)]

    async def execute(self, call: ToolCall) -> ToolOutput:
        """독립 실행 결과와 부모 모델용 최종 답변을 반환한다."""
        child = await self._runtime.complete(
            self._messages(call),
            tools=self._tools,
            policy=self._policy,
        )
        text = ""
        if child.final_result is not None:
            text = child.final_result.content
        elif child.responses:
            text = child.responses[-1].text
        return ToolOutput(
            execution=Execution(data={"status": child.status}),
            result=ToolResultBlock(tool_use_id=call.tool_call_id, content=text),
            child_run=child,
        )
