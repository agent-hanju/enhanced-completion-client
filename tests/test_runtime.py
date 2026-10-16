"""HubRequest·클로저 콜백·HITL 종료·작업 핸들과 모델 반복 호출을 검증한다."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from completion_bridge import (
    Bridge,
    Execution,
    ExecutionStateError,
    HitlDecision,
    HitlDraft,
    HubMessage,
    HubRequest,
    HubResponse,
    Hyperparameters,
    RuntimeCancelled,
    RuntimeErrorWithPartial,
    RuntimeExecution,
    StreamNotFinished,
    TextBlock,
    ToolCall,
    ToolDefinition,
    ToolOutput,
    ToolPolicy,
    ToolResultBlock,
    ToolUseBlock,
)
from completion_bridge.vendors import chat_completions


class Stream:
    """모델 델타와 최종 응답 및 정리 여부를 제공한다."""

    def __init__(self, response: HubResponse, failure: Exception | None = None):
        self.response = response
        self.partial = HubResponse()
        self.failure = failure
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
    """호출한 설정·메시지와 모델 스트림을 보관한다."""

    def __init__(self, *responses: HubResponse, failure: Exception | None = None):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.streams: list[Stream] = []
        self.failure = failure

    def stream(self, messages, **params):
        self.calls.append({"messages": messages, **params})
        stream = Stream(self.responses.pop(0), self.failure)
        self.streams.append(stream)
        return stream


def call_response(count=1):
    """도구 호출 목록을 포함하는 모델 응답을 만든다."""
    content = []
    for index in range(count):
        content.append(ToolUseBlock(id=f"call-{index}", name="lookup", input={"nested": [1]}))
    return HubResponse(content=content)


def request():
    """독립 복사와 요청 설정 유지를 확인할 입력을 만든다."""
    return HubRequest(
        model="configured-model",
        messages=[HubMessage.user("original")],
        tools=[ToolDefinition(name="lookup", input_schema={"type": "object"})],
        hyperparameters=Hyperparameters(temperature=0.3),
        params={"seed": 17},
    )


def run(model, execute_tool, **kwargs):
    """모델 전송을 대체한 Bridge.run 진입점을 사용한다."""
    return Bridge.run(cast(Bridge, model), request(), execute_tool=execute_tool, **kwargs)


async def successful(name, arguments):
    """원본 기록과 모델용 결과를 제공하는 콜백."""
    return ToolOutput(
        execution=Execution(data={"original": arguments}), result=ToolResultBlock(content=name)
    )


async def test_lazy_snapshot_and_streamed_rounds(monkeypatch):
    """실제 Bridge.run은 즉시 입력을 복사하고 델타와 요청별 응답을 제공한다."""
    bridge = Bridge(vendor=chat_completions, base_url="https://test")
    model = Model(call_response(), HubResponse(content=[TextBlock(text="done")]))
    monkeypatch.setattr(bridge, "stream", model.stream)
    original = request()
    execution = bridge.run(original, execute_tool=successful)
    original.messages.clear()
    original.tools[0].name = "changed"
    original.hyperparameters.temperature = 0.9
    original.params["seed"] = 99
    assert not model.calls
    with pytest.raises(StreamNotFinished):
        _ = execution.result
    indexes = []
    async with execution:
        async for _ in execution:
            indexes.append(execution.request_index)
    result = execution.result
    assert indexes == [0, 1]
    assert result.status == "completed"
    assert result.request.messages == [HubMessage.user("original")]
    assert len(result.messages) == 4
    assert len(result.generated_messages) == 3
    assert result.tasks[0].execution is not None
    assert result.tasks[0].execution.data == {"original": {"nested": [1]}}
    assert result.tasks[0].result is not None
    assert result.tasks[0].result.tool_use_id == "call-0"
    for call in model.calls:
        assert call["model"] == "configured-model"
        assert call["tools"][0].name == "lookup"
        assert call["hyperparameters"].temperature == 0.3
        assert call["seed"] == 17
    assert model.calls[1]["messages"][-1].role == "tool"
    assert all(stream.closed for stream in model.streams)
    assert await execution is result


async def test_closure_passes_chat_context_to_manager():
    """콜백은 name·arguments만 받아도 외부 관리자와 정책·문맥을 사용할 수 있다."""
    context = {"actor_id": "user", "profile": {"policy": "allow"}}
    received = []

    class Manager:
        async def execute(self, name, arguments, chat_context):
            received.append((name, arguments, chat_context))
            return ToolOutput(result=ToolResultBlock(content="resolved"))

    manager = Manager()

    async def callback(name, arguments):
        return await manager.execute(name, arguments, context)

    result = await run(Model(call_response(), HubResponse()), callback)
    assert result.status == "completed"
    assert received == [("lookup", {"nested": [1]}, context)]
    assert received[0][2] is context


@pytest.mark.parametrize("stage", ["approval", "review", "no_result"])
async def test_unresolved_draft_stops_without_followup(stage):
    """콜백의 미해결 기록과 실행 결과를 보존하고 남은 호출은 실행하지 않는다."""
    model = Model(call_response(2))
    supplied = HitlDraft(
        call=ToolCall(ordinal=99, tool_call_id="external-id", tool_name="lookup", arguments={}),
        created_at=datetime.now(UTC),
    )
    if stage != "no_result":
        setattr(supplied, stage, HitlDecision(decision=ToolPolicy.ASK, source="profile"))
    supplied.execution = Execution(data="private result")
    calls = []

    async def callback(name, arguments):
        calls.append(name)
        return ToolOutput(draft=supplied)

    result = await run(model, callback)
    assert result.status == "hitl_required"
    assert len(result.responses) == 1
    assert len(result.tasks) == 2
    assert calls == ["lookup"]
    assert result.tasks[0].call.tool_call_id == "call-0"
    assert result.tasks[0].call.ordinal == 0
    assert result.tasks[0].execution is not None
    assert result.tasks[0].execution.data == "private result"
    assert result.tasks[1].execution is None
    assert result.tasks[0].result is None
    assert len(result.generated_messages) == 1
    assert supplied.call.tool_call_id == "external-id"


async def test_deny_is_a_callback_result_and_can_continue():
    """거절 결과를 모델에 전달할지는 콜백이 정하며 라이브러리는 정책을 판정하지 않는다."""

    async def callback(name, arguments):
        return ToolOutput(result=ToolResultBlock(content="denied", is_error=True))

    result = await run(Model(call_response(), HubResponse()), callback)
    assert result.status == "completed"
    assert result.tasks[0].result is not None
    assert result.tasks[0].result.is_error


async def test_background_handle_is_returned_without_waiting_or_cancellation():
    """접수 결과로 후속 요청을 수행하고 실제 작업 핸들은 완료 결과에 유지한다."""
    release = asyncio.Event()

    async def worker():
        await release.wait()
        return "background result"

    task = asyncio.create_task(worker())

    async def callback(name, arguments):
        return ToolOutput(task=task, result=ToolResultBlock(content="accepted"))

    execution = run(Model(call_response(), HubResponse()), callback)
    result = await execution
    assert result.tasks[0].task is task
    assert result.tasks[0].background
    assert not task.done()
    assert "task" not in result.tasks[0].model_dump()
    release.set()
    assert await task == "background result"
    await result.aclose()


async def test_draft_task_is_preserved_without_deepcopy_and_explicitly_cancelled():
    """외부 기록에 포함된 Task를 복사하지 않고 명시적 Run 정리로 취소한다."""
    task = asyncio.create_task(asyncio.Event().wait())
    draft = HitlDraft(
        call=ToolCall(ordinal=0, tool_call_id="external", tool_name="lookup", arguments={}),
        created_at=datetime.now(UTC),
        task=task,
        background=True,
    )

    async def callback(name, arguments):
        return ToolOutput(draft=draft)

    result = await run(Model(call_response()), callback)
    assert result.tasks[0].task is task
    assert result.status == "hitl_required"
    assert not task.done()
    await result.aclose()
    assert task.cancelled()


async def test_final_callback_result_avoids_followup():
    """명시적 최종 결과는 후속 모델 요청 없이 Run에 보존한다."""

    async def callback(name, arguments):
        return ToolOutput(result=ToolResultBlock(content="final"), final=True)

    result = await run(Model(call_response()), callback)
    assert result.final_result is not None
    assert result.final_result.content == "final"
    assert result.status == "completed"


async def test_callback_arguments_are_isolated():
    """콜백의 인자 수정이 원본 모델 응답과 기록을 수정하지 않는다."""

    async def callback(name, arguments):
        arguments["nested"].append(2)
        return ToolOutput(result=ToolResultBlock(content="ok"))

    result = await run(Model(call_response(), HubResponse()), callback)
    assert result.tasks[0].call.arguments == {"nested": [1]}
    assert isinstance(result.responses[0].content[0], ToolUseBlock)
    assert result.responses[0].content[0].input == {"nested": [1]}


@pytest.mark.parametrize("invalid", [-1, True, 1.5])
def test_invalid_limit_rejected_before_execution(invalid):
    with pytest.raises(ValueError):
        run(Model(), successful, max_tool_calls=invalid)


def test_invalid_request_and_callback_rejected():
    with pytest.raises(TypeError):
        RuntimeExecution(cast(Bridge, Model()).stream, cast(HubRequest, []), successful, 1)
    with pytest.raises(TypeError):
        run(Model(), None)
    original = request()
    original.params["tools"] = []
    with pytest.raises(ValueError, match="reserved"):
        Bridge.run(cast(Bridge, Model()), original, execute_tool=successful)


@pytest.mark.parametrize("count", [0, 1])
async def test_limit_shared_across_model_calls(count):
    model = Model(call_response(), call_response())
    result = await run(model, successful, max_tool_calls=count)
    assert result.status == "limit_reached"
    assert len(result.responses) == count + 1
    assert result.tasks[-1].result is None


@pytest.mark.parametrize("invalid", ["duplicate", "arguments"])
async def test_invalid_batch_is_rejected_atomically(invalid):
    response = call_response(2)
    assert isinstance(response.content[0], ToolUseBlock)
    assert isinstance(response.content[1], ToolUseBlock)
    if invalid == "duplicate":
        response.content[1].id = response.content[0].id
    else:
        response.content[1].input = None
        response.content[1].input_json = "[]"
    calls = []

    async def callback(name, arguments):
        calls.append(name)
        return ToolOutput()

    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await run(Model(response), callback)
    assert not calls
    assert not caught.value.partial.tasks


@pytest.mark.parametrize("field", ["output", "result", "execution", "draft", "task", "final"])
async def test_invalid_callback_return_contract(field):
    async def callback(name, arguments):
        if field == "output":
            return None
        output = ToolOutput()
        setattr(output, field, "invalid")
        return output

    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await run(Model(call_response()), callback)
    assert caught.value.partial.status == "error"
    assert caught.value.partial.tasks[0].error


async def test_callback_failure_keeps_partial_and_original_cause():
    async def callback(name, arguments):
        raise LookupError("private failure")

    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await run(Model(call_response()), callback)
    assert isinstance(caught.value.__cause__, LookupError)
    assert caught.value.partial.tasks[0].error == "LookupError"
    assert len(caught.value.partial.responses) == 1


async def test_model_failure_preserves_active_response():
    model = Model(
        HubResponse(content=[TextBlock(text="partial")]), failure=ValueError("broken stream")
    )
    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await run(model, successful)
    assert caught.value.partial.active_response is not None
    assert caught.value.partial.active_response.text == "partial"
    assert model.streams[0].closed


async def test_early_close_and_close_before_start():
    model = Model(HubResponse())
    execution = run(model, successful)
    async with execution:
        async for _ in execution:
            break
    assert execution.partial.status == "cancelled"
    assert model.streams[0].closed
    with pytest.raises(StreamNotFinished):
        _ = execution.result
    unopened = run(Model(), successful)
    await unopened.aclose()
    assert unopened.partial.status == "cancelled"


async def test_cancellation_and_concurrent_consumer_preserve_partial():
    entered = asyncio.Event()

    async def callback(name, arguments):
        entered.set()
        await asyncio.Event().wait()
        return ToolOutput()

    execution = run(Model(call_response()), callback)
    await anext(execution)
    pending = asyncio.create_task(anext(execution))
    await entered.wait()
    with pytest.raises(ExecutionStateError):
        await anext(execution)
    with pytest.raises(ExecutionStateError):
        await execution.aclose()
    pending.cancel()
    with pytest.raises(RuntimeCancelled) as caught:
        await pending
    assert caught.value.partial.status == "cancelled"
    assert caught.value.partial.tasks[0].call.tool_name == "lookup"
    await execution.aclose()


async def test_real_http_roundtrip_preserves_hub_request_and_tool_ids():
    """실제 SSE 변환과 요청 직렬화를 통해 콜백 기반 왕복 실행을 확인한다."""
    bodies = []

    def respond(req):
        body = json.loads(req.content)
        bodies.append(body)
        if len(bodies) == 1:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "real-call",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"q":"topic"}'},
                    }
                ]
            }
        else:
            delta = {"content": "done"}
        payload = {"choices": [{"index": 0, "delta": delta}]}
        return httpx.Response(200, text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
        result = await bridge.run(request(), execute_tool=successful)
    assert result.responses[-1].text == "done"
    assert result.tasks[0].call.tool_call_id == "real-call"
    assert bodies[1]["messages"][-1]["tool_call_id"] == "real-call"
    for body in bodies:
        assert body["model"] == "configured-model"
        assert body["temperature"] == 0.3
        assert body["seed"] == 17
        assert body["tools"][0]["function"]["name"] == "lookup"


@pytest.mark.parametrize("failure", [False, True])
async def test_partial_preserves_background_handles_after_later_failure(failure):
    """후속 모델 실패나 조기 종료가 이미 인계받은 외부 작업을 취소하지 않는다."""
    worker = asyncio.create_task(asyncio.Event().wait())
    model = Model(call_response(), HubResponse(), failure=None)
    if failure:
        model.responses.append(HubResponse())

    async def callback(name, arguments):
        if failure:
            model.failure = ValueError("model failed after receipt")
        return ToolOutput(task=worker, result=ToolResultBlock(content="accepted"))

    execution = run(model, callback)
    try:
        if failure:
            with pytest.raises(RuntimeErrorWithPartial) as caught:
                await execution
            assert caught.value.partial.tasks[0].task is worker
        else:
            await anext(execution)
            await anext(execution)
            await execution.aclose()
        assert not worker.done()
        assert execution.partial.tasks[0].task is worker
    finally:
        await execution.partial.aclose()
    assert worker.cancelled()


async def test_callback_metadata_and_result_are_copied():
    """도구 결과의 호출 ID 설정과 반환 후 기록 수정이 콜백 원본을 수정하지 않는다."""
    source = ToolResultBlock(tool_use_id="external", name="external", content="result")
    draft = HitlDraft(
        call=ToolCall(ordinal=9, tool_call_id="external", tool_name="lookup", arguments={}),
        created_at=datetime.now(UTC),
        execution=Execution(data={"values": [1]}),
    )

    async def callback(name, arguments):
        return ToolOutput(result=source, draft=draft)

    result = await run(Model(call_response(), HubResponse()), callback)
    assert result.tasks[0].result is not None
    assert result.tasks[0].result.tool_use_id == "call-0"
    assert result.tasks[0].execution is not None
    result.tasks[0].execution.data["values"].append(2)
    assert draft.execution is not None
    assert draft.execution.data == {"values": [1]}
    assert source.tool_use_id == "external"


async def test_final_without_result_is_rejected():
    """최종 결과 지정과 미해결 결과를 동시에 반환할 수 없다."""

    async def callback(name, arguments):
        return ToolOutput(final=True)

    with pytest.raises(RuntimeErrorWithPartial) as caught:
        await run(Model(call_response()), callback)
    assert isinstance(caught.value.__cause__, ValueError)
