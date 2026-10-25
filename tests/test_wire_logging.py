"""원본 통신 로깅이 벤더 요청을 바꾸지 않고 호출 span 안에서 호출을 기록하는지 검증한다."""

import json
import logging
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from completion_bridge import Bridge, SyncBridge
from completion_bridge.logging import WIRE_LOGGER, OperationLog, WireLog
from completion_bridge.vendors import chat_completions, messages

PAYLOAD = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'


class SpanCapture(logging.Handler):
    """레코드와 그 레코드를 만들 때의 현재 span context를 함께 모은다."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.items: list[tuple[logging.LogRecord, trace.SpanContext]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.items.append((record, trace.get_current_span().get_span_context()))


@pytest.fixture
def wire() -> Generator[SpanCapture]:
    """원본 통신 로거를 DEBUG로 켜고 레코드를 모은다."""
    logger = logging.getLogger(WIRE_LOGGER)
    handler = SpanCapture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def events(capture: SpanCapture) -> list[str]:
    return [record.event_name for record, _ in capture.items]  # type: ignore[attr-defined]


@pytest.mark.parametrize("sync", [True, False])
async def test_wire_records_are_raw_json_objects_inside_the_call_span(wire, spans, sync):
    """요청·응답 본문은 JSON 객체 그대로이고, 두 레코드 모두 같은 호출 span 안에서 만든다."""

    def handler(request):
        assert "request_id" not in json.loads(request.content)
        return httpx.Response(200, text=PAYLOAD)

    if sync:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            bridge = SyncBridge(
                vendor=chat_completions, base_url="https://test", model="m", http_client=client
            )
            result = bridge.complete(["hi"])
    else:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bridge = Bridge(
                vendor=chat_completions, base_url="https://test", model="m", http_client=client
            )
            result = await bridge.complete(["hi"])
    assert result.text == "hello"
    assert events(wire) == ["completion_bridge.request", "completion_bridge.response"]
    request, response = wire.items[0][0], wire.items[1][0]
    assert isinstance(request.msg, dict)
    assert request.msg["messages"] == [{"role": "user", "content": "hi"}]
    assert getattr(request, "url.full") == "https://test/v1/chat/completions"
    assert getattr(request, "http.request.method") == "POST"
    assert getattr(request, "gen_ai.request.model") == "m"
    assert getattr(request, "completion_bridge.api") == "chat_completions"
    assert response.msg == {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"content": "hello"}}],
    }
    (span,) = spans.get_finished_spans()
    for _, context in wire.items:
        assert context.span_id == span.context.span_id
        assert context.trace_id == span.context.trace_id


async def test_call_span_follows_genai_conventions_and_caller_context(spans):
    """호출 span은 ``chat {model}`` CLIENT span이고 부모는 호출한 쪽의 현재 span이다."""
    chunk = {
        "id": "chatcmpl-1",
        "model": "m-served",
        "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    payload = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
    tracer = trace.get_tracer("test")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=payload))
    ) as client:
        bridge = Bridge(
            vendor=chat_completions, base_url="https://test", model="m", http_client=client
        )
        with tracer.start_as_current_span("caller") as caller:
            await bridge.complete(["hi"])
    call = next(span for span in spans.get_finished_spans() if span.name == "chat m")
    assert call.kind is SpanKind.CLIENT
    assert call.parent is not None
    assert call.parent.span_id == caller.get_span_context().span_id
    assert call.attributes["gen_ai.operation.name"] == "chat"
    assert call.attributes["gen_ai.request.model"] == "m"
    assert call.attributes["gen_ai.response.id"] == "chatcmpl-1"
    assert call.attributes["gen_ai.response.model"] == "m-served"
    assert call.attributes["gen_ai.usage.input_tokens"] == 3
    assert call.attributes["gen_ai.usage.output_tokens"] == 1
    assert call.attributes["completion_bridge.outcome"] == "response"
    assert call.status.status_code is StatusCode.UNSET


@pytest.mark.parametrize("sync", [True, False])
async def test_close_records_cancelled(wire, sync):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
    if sync:
        with httpx.Client(transport=transport) as client:
            stream = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).stream(["hi"])
            iterator = iter(stream)
            next(iterator)
            stream.close()
            assert isinstance(iterator, Generator)
            iterator.close()
    else:
        async with httpx.AsyncClient(transport=transport) as client:
            stream = Bridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).stream(["hi"])
            iterator = stream.__aiter__()
            await anext(iterator)
            await stream.aclose()
            assert isinstance(iterator, AsyncGenerator)
            await iterator.aclose()
    assert events(wire) == ["completion_bridge.request", "completion_bridge.cancelled"]


def test_failure_and_lazy_start(wire, spans):
    """소비하기 전에는 기록하지 않고, 실패는 error 레코드와 ERROR 상태의 span이 된다."""
    from completion_bridge import TransportError

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        bridge = SyncBridge(vendor=chat_completions, base_url="https://test", http_client=client)
        stream = bridge.stream(["hi"])
        assert not wire.items
        assert not spans.get_finished_spans()
        with pytest.raises(TransportError):
            list(stream)
    assert events(wire) == ["completion_bridge.request", "completion_bridge.error"]
    assert wire.items[-1][0].msg == {"error_type": "TransportError"}
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "TransportError"


def test_sse_events_are_not_recorded(caplog):
    """SSE 이벤트 기록은 없다. 스트리밍 호출도 요청과 조립한 응답 두 레코드만 남는다."""
    caplog.set_level(logging.DEBUG, logger="completion_bridge")
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
    ) as client:
        SyncBridge(vendor=chat_completions, base_url="https://test", http_client=client).complete(
            ["hi"]
        )
    names = {record.name for record in caplog.records}
    assert names == {WIRE_LOGGER}
    assert len(caplog.records) == 2


def test_disabled_logging_skips_payload(monkeypatch):
    monkeypatch.setattr(logging.Logger, "isEnabledFor", lambda self, level: False)
    log = WireLog(OperationLog("completion"), "test", "m", "https://test", {})
    log.start()

    def payload():
        raise AssertionError("must not serialize disabled logs")

    log.finish("response", payload)


def test_merge_failure_is_logged_without_breaking_completion(wire):
    """허브 변환은 받아들이지만 원본 누적 규약에 맞지 않는 스트림은 merge_error로 남는다."""
    # content_block_start 없이 index 0의 delta가 온다.
    payload = (
        'event: message_start\ndata: {"type":"message_start","message":'
        '{"id":"msg_1","role":"assistant","content":[]}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"hello"}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=payload))
    with httpx.Client(transport=transport) as client:
        result = SyncBridge(vendor=messages, base_url="https://test", http_client=client).complete(
            ["hi"]
        )
    assert result.text == "hello"
    assert events(wire)[-1] == "completion_bridge.merge_error"
    assert wire.items[-1][0].msg == {
        "error_type": "MappingError",
        "message": "messages content_block_delta references unstarted 0",
    }


def test_disabled_calls_logging_skips_accumulation(monkeypatch):
    def forbidden():
        raise AssertionError("must not accumulate when call logging is disabled")

    monkeypatch.setattr(logging.Logger, "isEnabledFor", lambda self, level: False)
    monkeypatch.setattr(chat_completions, "to_native", forbidden)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
    with httpx.Client(transport=transport) as client:
        result = SyncBridge(
            vendor=chat_completions, base_url="https://test", http_client=client
        ).complete(["hi"])
    assert result.text == "hello"


@pytest.mark.parametrize("sync", [True, False])
async def test_response_model_validation_failure_does_not_break_completion(wire, sync):
    """원본 응답 타입 검증 실패는 생성 결과 대신 merge_error 기록에만 영향을 준다."""
    chunk = {
        "choices": [{"index": 0, "delta": {"content": "hello"}}],
        "usage": {"prompt_tokens": "3"},
    }
    payload = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    if sync:
        with httpx.Client(transport=transport) as client:
            result = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).complete(["hi"])
    else:
        async with httpx.AsyncClient(transport=transport) as client:
            result = await Bridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).complete(["hi"])
    assert result.text == "hello"
    assert events(wire)[-1] == "completion_bridge.merge_error"
    assert wire.items[-1][0].msg["error_type"] == "ValidationError"


def test_broken_handler_does_not_break_completion():
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise RuntimeError("disk failure")

    logger = logging.getLogger(WIRE_LOGGER)
    old_level = logger.level
    handler = BrokenHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
        ) as client:
            result = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).complete(["hi"])
            assert result.text == "hello"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
