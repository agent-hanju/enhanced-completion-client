"""Opt-in structured wire logs; applications own handlers and storage."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4


class JsonFormatter(logging.Formatter):
    """Format bridge wire records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                **getattr(record, "wire", {}),
            },
            ensure_ascii=False,
        )


class WireLog:
    """Per-call context. Logging failures must not interrupt generation."""

    def __init__(
        self, request_id: str | None, vendor: str, model: str | None, url: str, body: dict[str, Any]
    ) -> None:
        self.request_id = request_id if request_id is not None else str(uuid4())
        self.vendor = vendor
        self.model = model
        self.url = url
        self.body = body
        self.started: float | None = None
        self.ended = False
        self.sequence = 0

    def emit(self, event: str, payload: Callable[[], Any]) -> None:
        logger = logging.getLogger(
            "completion_bridge.wire.sse" if event == "sse" else "completion_bridge.wire.calls"
        )
        if not logger.isEnabledFor(logging.DEBUG):
            return
        try:
            logger.debug(
                event,
                extra={
                    "wire": {
                        "event": event,
                        "request_id": self.request_id,
                        "vendor": self.vendor,
                        "model": self.model,
                        "sequence": self.sequence,
                        "elapsed_ms": (perf_counter() - self.started) * 1000 if self.started else 0,
                        "payload": payload(),
                    }
                },
            )
        except Exception:
            # A broken application handler/formatter must not break the API call.
            pass

    def start(self) -> None:
        if self.started is None:
            self.started = perf_counter()
            self.emit("request", lambda: {"method": "POST", "url": self.url, "body": self.body})

    def finish(self, event: str, payload: Callable[[], Any]) -> None:
        if self.started is not None and not self.ended:
            self.ended = True
            self.emit(event, payload)
