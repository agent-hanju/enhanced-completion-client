"""런타임 반복 실행, 정책, 부분 상태와 실제 태스크 수명을 검증한다."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from completion_bridge import (
    AgentToolExecutor,
    Bridge,
    CompletionRuntime,
    Execution,
    HitlDecision,
    HubMessage,
    HubResponse,
    RuntimeCancelled,
    RuntimeErrorWithPartial,
    StreamNotFinished,
    TextBlock,
    ToolCall,
    ToolDefinition,
    ToolOutput,
    ToolPolicy,
    ToolResultBlock,
    ToolUseBlock,
)


class Stream:
    def __init__(self, response, failure=None):
        self.response = response
        self.failure = failure
        self.partial = HubResponse()
        self.closed = False

    async def __aiter__(self):
        self.partial = self.response
        yield self.response
        if self.failure:
            raise self.failure

    @property
    def result(self):
        return self.response

    async def aclose(self):
        self.closed = True


class Model:
    def __init__(self, *responses, failure=None):
        self.responses = list(responses)
        self.calls = []
        self.streams = []
        self.failure = failure

    def stream(self, messages, *, tools):
        self.calls.append(messages)
        stream = Stream(self.responses.pop(0), self.failure)
        self.streams.append(stream)
        return stream


class Tools:
    definitions = [ToolDefinition(name="test")]

    def __init__(self, *, fail=False, block=False, final=False):
        self.calls = []
        self.fail = fail
        self.block = block
        self.final = final
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, call: ToolCall):
        self.calls.append(call)
        self.started.set()
        if self.fail:
            raise ValueError("secret")
        if self.block:
            try:
                await self.release.wait()
            finally:
                self.cancelled.set()
        return ToolOutput(
            execution=Execution(data="private"),
            result=ToolResultBlock(content="public"),
            final=self.final,
        )


class Policy:
    def __init__(self, approval=ToolPolicy.ALLOW, review=ToolPolicy.ALLOW):
        self.approval = approval
        self.review_value = review

    async def approve(self, call):
        return HitlDecision(decision=self.approval, source="default")

    async def review(self, call, execution):
        return HitlDecision(decision=self.review_value, source="default")


def call_response(count=1):
    content = []
    for index in range(count):
        content.append(ToolUseBlock(id=str(index), name="test", input={"nested": [1]}))
    return HubResponse(content=content)


def runtime(model, **kwargs):
    return CompletionRuntime(bridge=cast(Bridge, model), **kwargs)


async def test_lazy_copy_and_request_results():
    model = Model(call_response(), HubResponse(content=[TextBlock(text="done")]))
    tools = Tools()
    messages = [HubMessage.user("original")]
    execution = runtime(model).run(messages, tools=tools, policy=Policy())
    messages[0].content[0].text = "changed"
    assert not model.calls
    with pytest.raises(StreamNotFinished):
        _ = execution.result
    indexes = []
    async for _delta in execution:
        indexes.append(execution.request_index)
    run = execution.result
    assert indexes == [0, 1]
    assert run.messages[0].content[0].text == "original"
    assert len(run.responses) == 2
    assert len(run.tasks) == 1
    assert run.tasks[0].draft.execution.data == "private"
    assert model.calls[1][-1].content[0].content == "public"
    assert all(stream.closed for stream in model.streams)


@pytest.mark.parametrize("stage", ["approval", "review"])
async def test_ask_stops_batch_and_preserves_pending(stage):
    model = Model(call_response(2))
    tools = Tools()
    policy = Policy(**{stage: ToolPolicy.ASK})
    run = await runtime(model).complete([], tools=tools, policy=policy)
    assert run.status == "approval_required"
    assert len(run.tasks) == 2
    assert run.tasks[1].status == "pending"
    assert run.tasks[1].draft.approval is None
    assert len(tools.calls) == (0 if stage == "approval" else 1)
    assert len(run.generated_messages) == 1
    if stage == "review":
        assert run.tasks[0].draft.execution.data == "private"


@pytest.mark.parametrize("stage", ["approval", "review"])
async def test_deny_hides_original_result(stage):
    model = Model(call_response(), HubResponse())
    tools = Tools()
    run = await runtime(model).complete(
        [],
        tools=tools,
        policy=Policy(**{stage: ToolPolicy.DENY}),
    )
    assert run.status == "completed"
    assert run.tasks[0].status == "completed"
    result = model.calls[1][-1].content[0]
    assert result.is_error
    assert "private" not in result.model_dump_json()
    assert len(tools.calls) == (0 if stage == "approval" else 1)


async def test_tool_failure_is_recorded_without_exception_text():
    model = Model(call_response(), HubResponse())
    run = await runtime(model).complete([], tools=Tools(fail=True), policy=Policy())
    draft = run.tasks[0].draft
    assert draft.execution.is_error
    assert draft.error == "ValueError"
    assert "secret" not in draft.model_dump_json()


async def test_limit_and_final_result():
    model = Model(call_response(2))
    run = await runtime(model, max_tool_calls=1).complete([], tools=Tools(), policy=Policy())
    assert run.status == "limit_reached"
    assert run.tasks[1].status == "pending"
    model = Model(call_response())
    run = await runtime(model).complete([], tools=Tools(final=True), policy=Policy())
    assert run.final_result.content == "public"
    assert len(model.calls) == 1


async def test_background_survives_main_and_wait_can_be_cancelled():
    model = Model(call_response(), HubResponse())
    tools = Tools(block=True)
    run = await runtime(model, background_tools=["test"]).complete(
        [],
        tools=tools,
        policy=Policy(),
    )
    await tools.started.wait()
    task = run.tasks[0]
    assert task.status == "running"
    waiter = asyncio.create_task(task.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert task.status == "running"
    tools.release.set()
    draft = await task.wait()
    assert draft is task.draft
    assert draft.execution.data == "private"
    assert task.status == "completed"
    assert len(run.generated_messages) == 3
    await run.aclose()


async def test_background_review_ask_and_cancellation():
    model = Model(call_response(), HubResponse())
    run = await runtime(model, background_tools=["test"]).complete(
        [],
        tools=Tools(),
        policy=Policy(review=ToolPolicy.ASK),
    )
    await run.tasks[0].wait()
    assert run.tasks[0].status == "approval_required"
    assert run.tasks[0].result is None
    assert run.status == "completed"
    tools = Tools(block=True)
    model = Model(call_response(), HubResponse())
    run = await runtime(model, background_tools=["test"]).complete(
        [],
        tools=tools,
        policy=Policy(),
    )
    await tools.started.wait()
    await run.aclose()
    assert tools.cancelled.is_set()
    assert run.tasks[0].status == "cancelled"


async def test_failure_partial_and_early_close():
    model = Model(HubResponse(content=[TextBlock(text="partial")]), failure=OSError())
    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await runtime(model).complete([], tools=Tools(), policy=Policy())
    assert caught.value.partial.status == "error"
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.partial.active_response.text == "partial"
    assert model.streams[0].closed
    model = Model(HubResponse())
    execution = runtime(model).run([], tools=Tools(), policy=Policy())
    async with execution:
        async for _ in execution:
            break
    assert execution.partial.status == "cancelled"
    assert model.streams[0].closed


async def test_cancellation_exposes_partial_and_cleans_tool():
    model = Model(call_response())
    tools = Tools(block=True)
    pending = asyncio.create_task(runtime(model).complete([], tools=tools, policy=Policy()))
    await tools.started.wait()
    pending.cancel()
    with pytest.raises(RuntimeCancelled) as caught:
        await pending
    assert caught.value.partial.status == "cancelled"
    assert tools.cancelled.is_set()
    assert caught.value.partial.tasks[0].status == "cancelled"


async def test_child_agent_propagates_approval():
    child = runtime(Model(call_response()))
    agent = AgentToolExecutor(
        definition=ToolDefinition(name="test"),
        runtime=child,
        messages=lambda call: [HubMessage.user("child")],
        tools=Tools(),
        policy=Policy(approval=ToolPolicy.ASK),
    )
    parent = await runtime(Model(call_response())).complete([], tools=agent, policy=Policy())
    assert parent.status == "approval_required"
    assert parent.tasks[0].child_run.status == "approval_required"
    assert parent.tasks[0].child_run.tasks[0].draft.approval.decision == ToolPolicy.ASK
    await parent.aclose()


async def test_actual_bridge_roundtrip_matches_stream_and_complete():
    import json

    import httpx
    import respx

    from completion_bridge.vendors import chat_completions

    def wire(delta, reason):
        event = {
            "id": "response",
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": reason},
            ],
        }
        return ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()

    first = wire(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call",
                    "type": "function",
                    "function": {"name": "test", "arguments": "{}"},
                }
            ]
        },
        "tool_calls",
    )
    second = wire({"content": "done"}, "stop")
    with respx.mock:
        route = respx.post("https://model.test/v1/chat/completions")
        route.side_effect = [
            httpx.Response(200, content=first),
            httpx.Response(200, content=second),
            httpx.Response(200, content=first),
            httpx.Response(200, content=second),
        ]
        async with Bridge(
            vendor=chat_completions,
            base_url="https://model.test",
            model="test",
        ) as bridge:
            service = CompletionRuntime(bridge=bridge)
            execution = service.run([], tools=Tools(), policy=Policy())
            async with execution:
                async for _ in execution:
                    pass
            streamed = execution.result
            complete = await service.complete([], tools=Tools(), policy=Policy())
        assert streamed.responses == complete.responses
        assert streamed.generated_messages == complete.generated_messages
        assert len(streamed.responses) == 2
        followup = json.loads(route.calls[1].request.content)
        assert followup["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call",
            "content": "public",
        }


async def test_background_policy_failure_is_observable():
    class BrokenPolicy(Policy):
        async def review(self, call, execution):
            raise LookupError("broken policy")

    run = await runtime(Model(call_response(), HubResponse()), background_tools=["test"]).complete(
        [],
        tools=Tools(),
        policy=BrokenPolicy(),
    )
    with pytest.raises(LookupError):
        await run.tasks[0].wait()
    assert run.tasks[0].status == "error"
    assert run.tasks[0].draft.error == "LookupError"
    await run.aclose()


async def test_early_close_cancels_background_work():
    tools = Tools(block=True)
    execution = runtime(Model(call_response(), HubResponse()), background_tools=["test"]).run(
        [],
        tools=tools,
        policy=Policy(),
    )
    async with execution:
        async for _ in execution:
            if execution.request_index == 1:
                await tools.started.wait()
                break
    assert execution.partial.status == "cancelled"
    assert tools.cancelled.is_set()
    assert execution.partial.tasks[0].status == "cancelled"


async def test_malformed_arguments_and_unknown_tool():
    response = HubResponse(content=[ToolUseBlock(id="bad", name="test", input_json="{")])
    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await runtime(Model(response)).complete([], tools=Tools(), policy=Policy())
    assert isinstance(caught.value.__cause__, ValueError)
    response = HubResponse(content=[ToolUseBlock(id="unknown", name="other", input={})])
    tools = Tools()
    run = await runtime(Model(response, HubResponse())).complete([], tools=tools, policy=Policy())
    assert not tools.calls
    assert run.tasks[0].result.is_error


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_invalid_limit(limit):
    with pytest.raises(ValueError):
        runtime(Model(), max_tool_calls=limit)


async def test_cancel_during_review_preserves_completed_execution():
    entered_review = asyncio.Event()

    class WaitingPolicy(Policy):
        async def review(self, call, execution):
            entered_review.set()
            await asyncio.Event().wait()

    pending = asyncio.create_task(
        runtime(Model(call_response())).complete([], tools=Tools(), policy=WaitingPolicy())
    )
    await entered_review.wait()
    pending.cancel()
    with pytest.raises(RuntimeCancelled) as caught:
        await pending
    draft = caught.value.partial.tasks[0].draft
    assert draft.execution.data == "private"
    assert draft.review is None


async def test_parent_cleanup_includes_child_background_tasks():
    tools = Tools(block=True)
    child = runtime(Model(call_response(), HubResponse()), background_tools=["test"])
    agent = AgentToolExecutor(
        definition=ToolDefinition(name="test"),
        runtime=child,
        messages=lambda call: [],
        tools=tools,
        policy=Policy(),
    )
    parent = await runtime(Model(call_response(), HubResponse())).complete(
        [],
        tools=agent,
        policy=Policy(),
    )
    await tools.started.wait()
    child_task = parent.tasks[0].child_run.tasks[0]
    assert child_task.status == "running"
    await parent.aclose()
    assert child_task.status == "cancelled"
    assert tools.cancelled.is_set()


async def test_call_limit_is_shared_across_model_requests():
    model = Model(call_response(), call_response())
    tools = Tools()
    run = await runtime(model, max_tool_calls=1).complete([], tools=tools, policy=Policy())
    assert run.status == "limit_reached"
    assert len(model.calls) == 2
    assert len(tools.calls) == 1
    assert len(run.tasks) == 2


async def test_inputs_include_independent_nested_tool_arguments():
    original = call_response()
    model = Model(original, HubResponse())
    run = await runtime(model).complete([], tools=Tools(), policy=Policy())
    original.content[0].input["nested"].append(2)
    assert run.tasks[0].draft.call.arguments == {"nested": [1]}
    assert run.responses[0].content[0].input == {"nested": [1]}
