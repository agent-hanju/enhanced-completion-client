"""작업 span의 부모 관계·상태와 운영 레코드의 수준·비밀값 제외, 실패·취소 결과를 검증한다."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from opentelemetry.trace import StatusCode

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
from completion_bridge.logging import OPERATIONS_LOGGER, OperationLog
from completion_bridge.vendors import chat_completions

_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)))


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
    """운영 레코드의 수준·이벤트 이름·메시지·속성을 dict로 모은다."""
    records: list[dict[str, Any]] = []
    for record in caplog.records:
        if record.name != OPERATIONS_LOGGER:
            continue
        item: dict[str, Any] = {"level": record.levelname, "message": record.getMessage()}
        for key in vars(record):
            if key not in _RECORD_FIELDS and key not in ("message", "asctime"):
                item[key] = getattr(record, key)
        records.append(item)
    return records


def span_values(spans) -> str:
    """끝난 span의 이름·속성·이벤트를 문자열 하나로 만든다. 비밀값 검사에 쓴다."""
    values = []
    for span in spans.get_finished_spans():
        events = [(event.name, dict(event.attributes or {})) for event in span.events]
        values.append([span.name, dict(span.attributes or {}), events])
    return json.dumps(values, ensure_ascii=False, default=str)


@pytest.mark.parametrize("sync", [True, False])
async def test_model_operation_is_lazy_and_has_no_payload(spans, sync):
    """소비할 때만 span을 만들고 span에는 본문을 넣지 않는다."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=model_payload()))
    if sync:
        with httpx.Client(transport=transport) as client:
            bridge = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            )
            stream = bridge.stream(["private input"])
            assert not spans.get_finished_spans()
            list(stream)
    else:
        async with httpx.AsyncClient(transport=transport) as client:
            bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
            stream = bridge.stream(["private input"])
            assert not spans.get_finished_spans()
            async for _ in stream:
                pass
    (span,) = spans.get_finished_spans()
    assert span.attributes["completion_bridge.operation"] == "completion"
    assert span.attributes["completion_bridge.outcome"] == "response"
    assert span.end_time >= span.start_time
    assert "private" not in span_values(spans)


@pytest.mark.parametrize("sync", [True, False])
async def test_network_failure_is_wrapped_and_logged_once(caplog, spans, sync):
    """네트워크 예외의 원인을 유지하고 비밀값 없이 실패를 한 번 기록한다."""
    caplog.set_level(logging.DEBUG, logger=OPERATIONS_LOGGER)

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
    assert errors[0]["event_name"] == "completion_bridge.finished"
    assert errors[0]["error.type"] == "TransportError"
    assert "private" not in json.dumps(operations(caplog), default=str)
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert "private" not in span_values(spans)


@pytest.mark.parametrize("stage", ["allow", "approval", "review", "deny", "failure"])
async def test_runtime_traces_model_and_callback_without_payload(caplog, spans, stage):
    """모델·콜백 span의 부모는 런타임 span이고, 외부 판정 및 오류의 본문은 기록하지 않는다."""
    from datetime import UTC, datetime

    caplog.set_level(logging.DEBUG, logger=OPERATIONS_LOGGER)
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
    finished = spans.get_finished_spans()
    runtime = next(span for span in finished if span.name == "runtime")
    children = [span for span in finished if span is not runtime]
    assert children
    for span in children:
        assert span.parent is not None
        assert span.parent.span_id == runtime.context.span_id
    tool = next(span for span in children if span.name == "execute_tool lookup")
    assert tool.attributes["gen_ai.tool.name"] == "lookup"
    assert tool.attributes["gen_ai.tool.call.id"] == "call-1"
    errors = []
    for record in operations(caplog):
        if record["level"] == "ERROR":
            errors.append(record)
    assert len(errors) == (1 if stage == "failure" else 0)
    assert "private" not in json.dumps(operations(caplog), default=str)
    assert "private" not in span_values(spans)
    assert runtime.attributes["completion_bridge.outcome"] == run.status
    if stage in ("approval", "review"):
        assert run.status == "hitl_required"
        assert count == 1


async def test_cancel_during_callback_finishes_both_operation_spans(spans):
    """콜백 대기 중 취소돼도 도구·런타임 span을 끝내고 부분 상태를 보존한다."""
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
    for span in spans.get_finished_spans():
        if span.attributes["completion_bridge.operation"] in ("runtime", "tool"):
            completed.append(span)
    assert len(completed) == 2
    for span in completed:
        assert span.attributes["completion_bridge.outcome"] == "cancelled"


def test_zero_clock_start_and_duplicate_finish(monkeypatch, spans):
    """시계가 0에서 시작해도 경과 시간을 계산하고 span은 한 번만 끝낸다."""
    clock = [0.0]
    monkeypatch.setattr("completion_bridge.logging.perf_counter", lambda: clock[0])
    operation = OperationLog("test")
    operation.start()
    clock[0] = 2.0
    assert operation.elapsed_ms() == 2000.0
    operation.finish("completed")
    operation.finish("completed")
    assert len(spans.get_finished_spans()) == 1


def test_broken_operation_handler_does_not_change_model_result(monkeypatch):
    """소비 앱 핸들러가 실패해도 모델 결과는 정상적으로 반환한다."""

    class BrokenHandler(logging.Handler):
        """기록 시 실패하는 소비 앱 핸들러."""

        def emit(self, record: logging.LogRecord) -> None:
            """파일 출력 실패를 재현한다."""
            raise OSError("private handler details")

    def fail_factory(self):
        """경고 레코드를 내게 하려고 원본 응답 누적기 생성을 실패시킨다."""
        raise ValueError("native details")

    monkeypatch.setattr(type(chat_completions), "to_native", fail_factory)
    logger = logging.getLogger(OPERATIONS_LOGGER)
    wire = logging.getLogger("completion_bridge.wire")
    handler = BrokenHandler()
    previous_level = logger.level
    previous_wire = wire.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    wire.setLevel(logging.DEBUG)
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
        wire.setLevel(previous_wire)


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
    caplog.set_level(logging.DEBUG, logger="completion_bridge.wire")
    caplog.set_level(logging.DEBUG, logger=OPERATIONS_LOGGER)

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
    assert warnings[0]["completion_bridge.recovery"] == "hub_response_preserved"
    assert "private" not in json.dumps(operations(caplog), default=str)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("sync", [False, True])
async def test_token_count_span_records_success_or_network_error_once(caplog, spans, failure, sync):
    """토큰 조회도 본문 없이 span 하나를 남기고 실패 원인을 유지한다."""
    from completion_bridge.vendors import messages

    caplog.set_level(logging.DEBUG, logger=OPERATIONS_LOGGER)

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
                    vendor=messages, base_url="https://test", model="m", http_client=client
                ).count_tokens(["private input"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await Bridge(
                vendor=messages, base_url="https://test", model="m", http_client=client
            ).count_tokens(["private input"])

    if failure:
        with pytest.raises(TransportError) as caught:
            await invoke()
        assert isinstance(caught.value.__cause__, httpx.ConnectError)
    else:
        assert (await invoke()).input_tokens == 3
    (span,) = spans.get_finished_spans()
    assert span.name == "count_tokens m"
    assert span.attributes["completion_bridge.outcome"] == ("error" if failure else "completed")
    assert span.status.status_code is (StatusCode.ERROR if failure else StatusCode.UNSET)
    errors = [record for record in operations(caplog) if record["level"] == "ERROR"]
    assert len(errors) == (1 if failure else 0)
    assert "private" not in span_values(spans)
    assert "private" not in json.dumps(operations(caplog), default=str)


@pytest.mark.parametrize("primary_failure", [False, True])
def test_sync_cleanup_failure_preserves_primary_error(caplog, primary_failure):
    """연결 정리 오류가 기존 해석 오류를 교체하지 않고 복구 결과를 기록한다."""
    from completion_bridge import MappingError
    from completion_bridge.bridge import SyncStream
    from completion_bridge.http.sse import SseEvent

    caplog.set_level(logging.DEBUG, logger=OPERATIONS_LOGGER)

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
    assert records[-1]["event_name"] == "completion_bridge.cleanup_failed"
    assert records[-1]["completion_bridge.recovery"] == (
        "original_error_preserved" if primary_failure else "caller_notified"
    )
    assert records[-1]["level"] == ("WARNING" if primary_failure else "ERROR")
    assert "private" not in json.dumps(records, default=str)
