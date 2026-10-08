"""Wire logging observes calls without changing the vendor request."""

import json
import logging
import logging.config
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from completion_bridge import Bridge, SyncBridge
from completion_bridge.vendors import chat_completions

PAYLOAD = 'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'


@pytest.mark.parametrize("sync", [True, False])
@pytest.mark.parametrize("request_id", [None, "provided-id"])
async def test_wire_records(caplog, sync, request_id):
    caplog.set_level(logging.DEBUG, logger="completion_bridge.wire")

    def handler(request):
        assert "request_id" not in json.loads(request.content)
        return httpx.Response(200, text=PAYLOAD)

    if sync:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            bridge = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            )
            result = bridge.complete(["hi"], request_id=request_id)
    else:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bridge = Bridge(vendor=chat_completions, base_url="https://test", http_client=client)
            result = await bridge.complete(["hi"], request_id=request_id)
    records = [r.wire for r in caplog.records if hasattr(r, "wire")]
    assert [r["event"] for r in records] == ["request", "sse", "sse", "response"]
    assert len({r["request_id"] for r in records}) == 1
    if request_id is None:
        assert UUID(records[0]["request_id"]).version == 4
    else:
        assert records[0]["request_id"] == request_id
    assert records[1]["payload"]["data"] == '{"choices":[{"delta":{"content":"hello"}}]}'
    assert records[2]["sequence"] == 2
    assert records[-1]["payload"] == result.model_dump(mode="json")


@pytest.mark.parametrize("sync", [True, False])
async def test_close_records_cancelled(caplog, sync):
    caplog.set_level(logging.DEBUG, logger="completion_bridge.wire")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
    if sync:
        with httpx.Client(transport=transport) as client:
            stream = SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).stream(["hi"])
            iterator = iter(stream)
            next(iterator)
            stream.close()
            iterator.close()
    else:
        async with httpx.AsyncClient(transport=transport) as client:
            stream = Bridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).stream(["hi"])
            iterator = stream.__aiter__()
            await anext(iterator)
            await stream.aclose()
            await iterator.aclose()
    events = [r.wire["event"] for r in caplog.records if hasattr(r, "wire")]
    assert events == ["request", "sse", "cancelled"]


def test_failure_and_lazy_start(caplog):
    caplog.set_level(logging.DEBUG, logger="completion_bridge.wire")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        bridge = SyncBridge(vendor=chat_completions, base_url="https://test", http_client=client)
        stream = bridge.stream(["hi"])
        assert not [r for r in caplog.records if hasattr(r, "wire")]
        from completion_bridge import TransportError

        with pytest.raises(TransportError):
            list(stream)
    assert [r.wire["event"] for r in caplog.records if hasattr(r, "wire")] == ["request", "error"]


def test_file_configuration(tmp_path):
    config = json.loads(Path("examples/logging.common-hitl-chat.json").read_text())
    for handler in config["handlers"].values():
        handler["filename"] = str(tmp_path / Path(handler["filename"]).name)
    loggers = [logging.getLogger(name) for name in config["loggers"]]
    old = [
        (logger.level, logger.handlers[:], logger.propagate, logger.disabled) for logger in loggers
    ]
    try:
        logging.config.dictConfig(config)
        with httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAYLOAD))
        ) as client:
            SyncBridge(
                vendor=chat_completions, base_url="https://test", http_client=client
            ).complete(["hi"], request_id="file-test")
        calls = [json.loads(s) for s in (tmp_path / "bridge-calls.jsonl").read_text().splitlines()]
        sse = [json.loads(s) for s in (tmp_path / "bridge-sse.jsonl").read_text().splitlines()]
        assert [r["event"] for r in calls] == ["request", "response"]
        assert [r["event"] for r in sse] == ["sse", "sse"]
        assert all(r["request_id"] == "file-test" for r in calls + sse)
    finally:
        for logger, (level, handlers, propagate, disabled) in zip(loggers, old, strict=True):
            for handler in logger.handlers:
                handler.close()
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def test_disabled_logging_skips_payload(monkeypatch):
    from completion_bridge.wire_logging import WireLog

    monkeypatch.setattr(logging.Logger, "isEnabledFor", lambda self, level: False)
    wire = WireLog(None, "test", "m", "https://test", {})
    wire.start()

    def payload():
        raise AssertionError("must not serialize disabled logs")

    wire.finish("response", payload)


def test_broken_handler_does_not_break_completion():
    class BrokenHandler(logging.Handler):
        def emit(self, record):
            raise RuntimeError("disk failure")

    logger = logging.getLogger("completion_bridge.wire.calls")
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
