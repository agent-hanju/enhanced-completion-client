"""운영 추적과 선택적으로 활성화하는 원본 통신 로그 및 JSON 포맷터.

운영 기록에는 본문·인자를 포함하지 않는다. 원본 통신 기록은 별도 DEBUG 로거로 제공한다.
핸들러와 출력·보관 정책은 소비 앱이 관리한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

LOGGER_NAME = "completion_bridge.operations"
current_operation_id: ContextVar[str | None] = ContextVar(
    "completion_bridge.operation_id", default=None
)
logging.getLogger("completion_bridge").addHandler(logging.NullHandler())


class OperationLog:
    """작업별 추적 ID와 시작 시각을 유지하고 종료를 한 번만 기록한다.

    소비 앱이 핸들러와 수준을 설정한다. 예외 메시지·traceback·요청 및 결과 본문은
    기록하지 않으며, 호출자는 fields에 상태·횟수 같은 운영 정보만 제공해야 한다.
    """

    def __init__(
        self,
        operation: str,
        *,
        request_id: str | None = None,
        parent_request_id: str | None = None,
    ) -> None:
        """작업 종류·추적 관계를 보관하고 시작 전 상태를 준비한다."""
        self.operation = operation
        self.request_id = request_id if request_id is not None else str(uuid4())
        self.parent_request_id = (
            parent_request_id if parent_request_id is not None else current_operation_id.get()
        )
        self._started: float | None = None
        self._finished = False

    def start(self, **fields: Any) -> None:
        """소비 시작 시각과 시작 이벤트를 한 번 기록한다."""
        if self._started is None:
            self._started = perf_counter()
            self.emit(logging.INFO, "started", **fields)

    def emit(self, level: int, event: str, **fields: Any) -> None:
        """구조화 이벤트를 기록하며 소비 앱의 로깅 실패는 실행에 전파하지 않는다."""
        logger = logging.getLogger(LOGGER_NAME)
        if not logger.isEnabledFor(level):
            return
        record = {
            "event": event,
            "operation": self.operation,
            "request_id": self.request_id,
            "parent_request_id": self.parent_request_id,
            "elapsed_ms": (perf_counter() - self._started) * 1000
            if self._started is not None
            else 0.0,
            **fields,
        }
        try:
            logger.log(level, event, extra={"operation": record})
        except Exception:
            # 소비 앱 핸들러의 실패는 원래 작업의 성공·실패·취소 결과를 변경하지 않는다.
            return

    def finish(self, outcome: str, *, level: int = logging.INFO, **fields: Any) -> None:
        """시작한 작업의 종료 결과와 경과 시간을 한 번 기록한다."""
        if self._started is not None and not self._finished:
            self._finished = True
            self.emit(level, "finished", outcome=outcome, **fields)


CALLS_LOGGER = "completion_bridge.wire.calls"
SSE_LOGGER = "completion_bridge.wire.sse"


class JsonFormatter(logging.Formatter):
    """브리지의 구조화 이벤트를 JSON 한 줄로 직렬화한다."""

    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드의 wire 필드와 시각·수준·로거 이름을 JSON 한 줄로 직렬화한다."""
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                **getattr(record, "operation", {}),
                **getattr(record, "wire", {}),
            },
            ensure_ascii=False,
        )


class WireLog:
    """호출 식별자와 시각을 유지하며 로깅 실패를 생성에 전파하지 않는다."""

    def __init__(
        self, request_id: str | None, vendor: str, model: str | None, url: str, body: dict[str, Any]
    ) -> None:
        """호출 식별자를 지정하거나 생성하고 요청과 기록 시각 상태를 초기화한다."""
        self.request_id = request_id if request_id is not None else str(uuid4())
        self.vendor = vendor
        self.model = model
        self.url = url
        self.body = body
        self.started: float | None = None
        self.ended = False
        self.sequence = 0

    def calls_enabled(self) -> bool:
        """요청·응답 로거가 지금 DEBUG 기록을 받는지 돌려준다."""
        return logging.getLogger(CALLS_LOGGER).isEnabledFor(logging.DEBUG)

    def emit(self, event: str, payload: Callable[[], Any]) -> None:
        """활성화된 DEBUG 로거에 구조화 이벤트를 기록하며 로깅 실패는 생성에 전파하지 않는다."""
        logger = logging.getLogger(SSE_LOGGER if event == "sse" else CALLS_LOGGER)
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
            # 소비 앱의 핸들러·포매터 실패가 모델 생성을 중단시키지 않도록 한다.
            pass

    def start(self) -> None:
        """시작 시각을 한 번 기록하고 원본 요청 이벤트를 출력한다."""
        if self.started is None:
            self.started = perf_counter()
            self.emit("request", lambda: {"method": "POST", "url": self.url, "body": self.body})

    def finish(self, event: str, payload: Callable[[], Any]) -> None:
        """시작한 호출의 종료 이벤트를 한 번 기록한다."""
        if self.started is not None and not self.ended:
            self.ended = True
            self.emit(event, payload)
