"""운영 로그의 추적 관계·수준·비밀값 제외와 실패·취소 결과를 검증한다."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest

from completion_bridge import (
    Bridge,
    Execution,
    HitlDecision,
    HitlDraft,
    HubMessage,
    HubRequest,
    RuntimeCancelled,
    RuntimeErrorWithPartial,
    SyncBridge,
    ToolCall,
    ToolDefinition,
    ToolOutput,
    ToolPolicy,
    ToolResultBlock,
    TransportError,
)
from completion_bridge.logging import LOGGER_NAME, JsonFormatter, OperationLog
from completion_bridge.vendors import chat_completions


def model_payload(*, tool: bool = False) -> str:
    """본문 또는 함수 호출을 제공하는 모델 응답을 구성한다."""
    delta: dict[str, Any] = {"content": "private model output"}
    if tool:
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"secret":"private argument"}'},
                }
            ]
        }
    chunk = {"choices": [{"index": 0, "delta": delta}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"


def operations(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """로그 레코드에서 운영 정보만 선택한다."""
    records: list[dict[str, Any]] = []
    for record in caplog.records:
        if record.name == LOGGER_NAME:
            records.append(json.loads(JsonFormatter().format(record)))
    return records


@pytest.mark.parametrize("sync", [True, False])
async def test_model_operation_is_lazy_safe_and_correlated(caplog, sync):
    """소비 시에만 기록하며 ID·처리 시간은 포함하고 본문은 제외한다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
    if sync:
        with httpx.Client(transport=transport) as client:
            bridge = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            )
            stream = bridge.stream(["private input"], request_id="model-run")
            assert not operations(caplog)
            list(stream)
    else:
        async with httpx.AsyncClient(transport=transport) as client:
            bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
            stream = bridge.stream(["private input"], request_id="model-run")
            assert not operations(caplog)
            async for _ in stream:
                pass
    records = operations(caplog)
    assert len(records) == 2
    assert records[0]["event"] == "started"
    assert records[1]["outcome"] == "response"
    for record in records:
        assert record["request_id"] == "model-run"
        assert record["elapsed_ms"] >= 0
        assert record["level"] == "INFO"
    assert "private" not in json.dumps(records)


@pytest.mark.parametrize("sync", [True, False])
async def test_network_failure_is_wrapped_and_logged_once(caplog, sync):
    """네트워크 예외의 원인을 유지하고 비밀값 없이 실패를 한 번 기록한다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    def fail(request: httpx.Request) -> httpx.Response:
        """인증 정보가 포함될 수 있는 외부 네트워크 예외를 발생시킨다."""
        raise httpx.ReadTimeout("private failure with token", request=request)

    transport = httpx.MockTransport(fail)
    with pytest.raises(TransportError) as caught:
        if sync:
            with httpx.Client(transport=transport) as client:
                SyncBridge(
                    vendor=chat_completions, base_url="https://test", http_client=client
                ).complete(["private input"])
        else:
            async with httpx.AsyncClient(transport=transport) as client:
                await Bridge(
                    vendor=chat_completions, base_url="https://test", http_client=client
                ).complete(["private input"])
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert "private" not in str(caught.value)
    errors = []
    for record in operations(caplog):
        if record["level"] == "ERROR":
            errors.append(record)
    assert len(errors) == 1
    assert errors[0]["error_type"] == "TransportError"
    assert "private" not in json.dumps(operations(caplog))


@pytest.mark.parametrize("stage", ["allow", "approval", "review", "deny", "failure"])
async def test_runtime_traces_model_and_callback_without_payload(caplog, stage):
    """실행·모델·콜백 추적을 연결하고 외부 판정 및 오류의 본문을 기록하지 않는다."""
    from datetime import UTC, datetime

    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        """첫 요청에서는 도구 호출, 후속 요청에서는 답변을 반환한다."""
        nonlocal count
        count += 1
        return httpx.Response(200, text=model_payload(tool=count == 1))

    async def callback(name, arguments):
        """소비 앱이 실행과 판정을 마친 결과를 제공한다."""
        if stage == "failure":
            raise LookupError("private failure")
        draft = HitlDraft(
            call=ToolCall(ordinal=0, tool_call_id="external", tool_name=name, arguments=arguments),
            created_at=datetime.now(UTC),
        )
        if stage in ("approval", "review"):
            setattr(draft, stage, HitlDecision(decision=ToolPolicy.ASK, source="profile"))
            return ToolOutput(draft=draft)
        return ToolOutput(
            draft=draft,
            execution=Execution(data="private original result"),
            result=ToolResultBlock(content="private tool result", is_error=stage == "deny"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
        execution = bridge.run(
            HubRequest(
                messages=[HubMessage.user("private input")], tools=[ToolDefinition(name="lookup")]
            ),
            execute_tool=callback,
        )
        if stage == "failure":
            with pytest.raises(RuntimeErrorWithPartial) as caught:
                await execution
            run = caught.value.partial
        else:
            run = await execution
    records = operations(caplog)
    errors = []
    for record in records:
        if record["operation"] in ("completion", "tool"):
            assert record["parent_request_id"] == run.request_id
        if record["level"] == "ERROR":
            errors.append(record)
    assert len(errors) == (1 if stage == "failure" else 0)
    assert "private" not in json.dumps(records)
    assert records[-1]["outcome"] == run.status
    if stage in ("approval", "review"):
        assert run.status == "hitl_required"
        assert count == 1


async def test_cancel_during_callback_finishes_both_operation_records(caplog):
    """콜백 대기 중 취소돼도 도구·런타임 종료 기록과 부분 상태를 보존한다."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    entered = asyncio.Event()

    async def callback(name, arguments):
        """외부 처리를 기다리는 상태를 제공한다."""
        entered.set()
        await asyncio.Event().wait()
        return ToolOutput()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=model_payload(tool=True))
        )
    ) as client:
        bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
        pending = asyncio.ensure_future(bridge.run(HubRequest(), execute_tool=callback))
        await entered.wait()
        pending.cancel()
        with pytest.raises(RuntimeCancelled) as caught:
            await pending
    assert caught.value.partial.status == "cancelled"
    completed = []
    for record in operations(caplog):
        if record["event"] == "finished" and record["operation"] in ("runtime", "tool"):
            completed.append(record)
    assert len(completed) == 2
    for record in completed:
        assert record["outcome"] == "cancelled"


def test_zero_clock_start_and_duplicate_finish(monkeypatch, caplog):
    """시계가 0에서 시작해도 경과 시간을 계산하고 종료는 한 번만 기록한다."""
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    clock = [0.0]
    monkeypatch.setattr("completion_bridge.logging.perf_counter", lambda: clock[0])
    operation = OperationLog("test", request_id="clock")
    operation.start()
    clock[0] = 2.0
    operation.finish("completed")
    operation.finish("completed")
    records = operations(caplog)
    assert len(records) == 2
    assert records[-1]["elapsed_ms"] == 2000.0


def test_broken_operation_handler_does_not_change_model_result():
    """소비 앱 핸들러가 실패해도 모델 결과는 정상적으로 반환한다."""

    class BrokenHandler(logging.Handler):
        """기록 시 실패하는 소비 앱 핸들러."""

        def emit(self, record: logging.LogRecord) -> None:
            """파일 출력 실패를 재현한다."""
            raise OSError("private handler details")

    logger = logging.getLogger(LOGGER_NAME)
    handler = BrokenHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
        ) as client:
            result = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).complete([])
        assert result is not None
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_sync_second_consumer_does_not_interrupt_first():
    """중복 소비를 거부해도 최초 소비자는 남은 응답을 완료한다."""
    from completion_bridge import ExecutionStateError

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
    ) as client:
        stream = SyncBridge(
            vendor=chat_completions, base_url="https://test", http_client=client
        ).stream([])
        first = iter(stream)
        next(first)
        with pytest.raises(ExecutionStateError):
            next(iter(stream))
        list(first)
        assert stream.result is not None
        with pytest.raises(ExecutionStateError):
            list(stream)


async def test_async_second_consumer_does_not_interrupt_first():
    """비동기 스트림도 중복 소비를 거부하고 최초 소비를 보존한다."""
    from completion_bridge import ExecutionStateError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
    ) as client:
        stream = Bridge(
            vendor=chat_completions, base_url="https://test", http_client=client
        ).stream([])
        first = aiter(stream)
        await anext(first)
        with pytest.raises(ExecutionStateError):
            await anext(aiter(stream))
        async for delta in first:
            assert delta is not None
        assert stream.result is not None
        with pytest.raises(ExecutionStateError):
            await anext(aiter(stream))


@pytest.mark.parametrize("status", [200, 503])
def test_json_transport_exception_excludes_url_credentials(status):
    """HTTP 실패와 JSON 해석 실패 모두 URL 인증·쿼리 값을 제외한다."""
    from completion_bridge import MappingError
    from completion_bridge.http.client import HttpClient

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="private response body")
        )
    ) as client:
        with pytest.raises((MappingError, TransportError)) as caught:
            HttpClient(client).post_json(
                "https://user:private-password@test/path?key=private-key#private-fragment",
                json={},
                headers={},
            )
    message = str(caught.value)
    assert "private" not in message
    assert "user" not in message
    assert "https://test/path" in message
    if isinstance(caught.value, TransportError):
        assert caught.value.detail == "private response body"


@pytest.mark.parametrize("code", ["overloaded_error", "private custom error", 429])
def test_vendor_error_keeps_independent_details_without_exposing_message(code):
    """서버 상세를 복사해 제공하면서 예외 문자열에는 알려진 분류만 사용한다."""
    from completion_bridge import VendorStreamError

    original = {"type": code, "message": "private model input", "nested": [1]}
    error = VendorStreamError("messages", original)
    original["nested"].append(2)
    assert error.detail["nested"] == [1]
    assert "private" not in str(error)
    if code == "overloaded_error":
        assert code in str(error)


def test_native_factory_failure_preserves_hub_result(monkeypatch, caplog):
    """로그용 누적기 생성 실패는 경고로 기록하고 허브 응답은 보존한다."""
    caplog.set_level(logging.DEBUG, logger="completion_bridge.wire.calls")
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    def fail_factory(self):
        """원본 응답 누적기 생성 오류를 재현한다."""
        raise ValueError("private native details")

    monkeypatch.setattr(type(chat_completions), "to_native", fail_factory)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
    ) as client:
        result = SyncBridge(
            vendor=chat_completions, base_url="https://test", http_client=client
        ).complete([])
    assert result.text == "private model output"
    warnings = []
    for record in operations(caplog):
        if record["level"] == "WARNING":
            warnings.append(record)
    assert len(warnings) == 1
    assert warnings[0]["recovery"] == "hub_response_preserved"
    assert "private" not in json.dumps(operations(caplog))


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("sync", [False, True])
async def test_token_count_logs_success_or_network_error_once(caplog, failure, sync):
    """토큰 조회도 본문 없이 시작·종료를 기록하고 실패 원인을 유지한다."""
    from completion_bridge.vendors import messages

    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    def respond(request: httpx.Request) -> httpx.Response:
        """토큰 응답 또는 네트워크 오류를 제공한다."""
        if failure:
            raise httpx.ConnectError("private network details", request=request)
        return httpx.Response(200, json={"input_tokens": 3})

    async def invoke():
        """동기·비동기 토큰 조회 경로를 같은 계약으로 실행한다."""
        if sync:
            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                return SyncBridge(
                    vendor=messages, base_url="https://test", http_client=client
                ).count_tokens(["private input"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await Bridge(
                vendor=messages, base_url="https://test", http_client=client
            ).count_tokens(["private input"])

    if failure:
        with pytest.raises(TransportError) as caught:
            await invoke()
        assert isinstance(caught.value.__cause__, httpx.ConnectError)
    else:
        assert (await invoke()).input_tokens == 3
    records = operations(caplog)
    assert len(records) == 2
    assert records[0]["event"] == "started"
    assert records[1]["outcome"] == ("error" if failure else "completed")
    assert records[1]["level"] == ("ERROR" if failure else "INFO")
    assert "private" not in json.dumps(records)


@pytest.mark.parametrize("primary_failure", [False, True])
def test_sync_cleanup_failure_preserves_primary_error(caplog, primary_failure):
    """연결 정리 오류가 기존 해석 오류를 교체하지 않고 복구 결과를 기록한다."""
    from completion_bridge import MappingError
    from completion_bridge.bridge import SyncStream
    from completion_bridge.http.sse import SseEvent

    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    class Frames:
        """정리 실패와 선택적 해석 실패를 제공한다."""

        def __init__(self):
            self.sent = False

        def __iter__(self):
            return self

        def __next__(self) -> SseEvent:
            """한 번의 해석 실패 또는 정상 입력 종료를 제공한다."""
            if primary_failure and not self.sent:
                self.sent = True
                return SseEvent(data="invalid-json")
            raise StopIteration

        def close(self):
            """연결 정리 실패를 재현한다."""
            raise OSError("private cleanup details")

    stream = SyncStream(vendor=chat_completions, vocabularies=[], frames=Frames())
    expected = MappingError if primary_failure else OSError
    with pytest.raises(expected):
        list(stream)
    records = operations(caplog)
    assert records[-1]["event"] == "cleanup_failed"
    assert records[-1]["recovery"] == (
        "original_error_preserved" if primary_failure else "caller_notified"
    )
    assert records[-1]["level"] == ("WARNING" if primary_failure else "ERROR")
    assert "private" not in json.dumps(records)
