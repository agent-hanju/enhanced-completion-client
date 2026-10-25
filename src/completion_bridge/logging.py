"""작업별 OpenTelemetry span과 선택적으로 활성화하는 원본 통신 로그.

bridge는 ``opentelemetry-api``만 쓴다. ``TracerProvider``와 로그 핸들러·출력·보관 정책은 소비 앱이
설정한다. 소비 앱이 provider를 설정하지 않으면 span은 기록되지 않는다.

로그 레코드는 OpenTelemetry ``LoggingHandler``와 같은 변환 규칙을 따른다. ``msg``가 본문(body)이고
``extra``의 key가 속성(attributes)이다. ``event_name`` key는 이벤트 이름이다. 레코드는 그 작업의
span을 현재 span으로 둔 채 만들므로 소비 앱이 레코드에 trace·span ID를 연결할 수 있다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from time import perf_counter
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import INVALID_SPAN, Span, SpanKind, Status, StatusCode
from opentelemetry.util.types import AttributeValue

TRACER = trace.get_tracer("completion_bridge")
OPERATIONS_LOGGER = "completion_bridge.operations"
WIRE_LOGGER = "completion_bridge.wire"
logging.getLogger("completion_bridge").addHandler(logging.NullHandler())


def _attributes(fields: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """호출자가 준 운영 정보를 속성 이름으로 바꾼다. ``None`` 값은 뺀다.

    ``error_type``은 OpenTelemetry 의미 규약의 ``error.type``이고, 나머지는
    ``completion_bridge.`` 접두를 쓴다.
    """
    result: dict[str, AttributeValue] = {}
    for key in fields:
        value = fields[key]
        if value is None:
            continue
        name = "error.type" if key == "error_type" else f"completion_bridge.{key}"
        result[name] = value
    return result


class OperationLog:
    """작업 하나의 span을 시작하고 종료를 한 번만 기록한다.

    span의 부모는 이 객체를 만들 때의 OpenTelemetry context다. :meth:`start` 전에는 기록하지 않는
    span(``INVALID_SPAN``)을 가진다. 운영 기록에는 본문·인자·예외 메시지·traceback을 넣지 않는다.
    호출자는 상태·횟수 같은 운영 정보만 준다.
    """

    def __init__(
        self,
        operation: str,
        *,
        name: str | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue | None] | None = None,
    ) -> None:
        """작업 종류와 span 이름·종류·초기 속성을 보관하고 현재 context를 부모로 잡는다.

        Args:
            operation: 작업 종류. ``completion``, ``count_tokens``, ``runtime``, ``tool``이다.
            name: span 이름. 생략하면 ``operation``이다.
            kind: span 종류.
            attributes: span을 시작할 때 넣을 속성. 값이 ``None``인 속성은 넣지 않는다.
        """
        self.operation = operation
        self._name = name if name is not None else operation
        self._kind = kind
        self._attributes: dict[str, AttributeValue] = {}
        if attributes is not None:
            for key in attributes:
                value = attributes[key]
                if value is not None:
                    self._attributes[key] = value
        self._parent = otel_context.get_current()
        self.span: Span = INVALID_SPAN
        self._started: float | None = None
        self._finished = False

    def start(self, **fields: Any) -> None:
        """span을 한 번 시작한다. ``fields``는 :func:`_attributes` 규칙으로 속성이 된다."""
        if self._started is not None:
            return
        self._started = perf_counter()
        values: dict[str, AttributeValue] = {"completion_bridge.operation": self.operation}
        values.update(self._attributes)
        values.update(_attributes(fields))
        try:
            self.span = TRACER.start_span(
                self._name, context=self._parent, kind=self._kind, attributes=values
            )
        except Exception:
            # 소비 앱의 provider 실패는 원래 작업의 결과를 바꾸지 않는다.
            self.span = INVALID_SPAN

    def elapsed_ms(self) -> float:
        """시작 이후 경과 시간. 시작 전이면 0이다."""
        return (perf_counter() - self._started) * 1000 if self._started is not None else 0.0

    def emit(self, level: int, event: str, **fields: Any) -> None:
        """운영 이벤트를 span 이벤트와 ``completion_bridge.operations`` 레코드로 남긴다.

        소비 앱의 핸들러·provider 실패는 실행에 전파하지 않는다.
        """
        values = _attributes(fields)
        try:
            if self._started is not None and not self._finished:
                # 끝난 span에는 이벤트를 더할 수 없다. 종료 뒤의 정리 실패는 레코드로만 남는다.
                self.span.add_event(event, values)
            logger = logging.getLogger(OPERATIONS_LOGGER)
            if not logger.isEnabledFor(level):
                return
            extra: dict[str, Any] = {
                "event_name": f"completion_bridge.{event}",
                "completion_bridge.operation": self.operation,
                "elapsed_ms": self.elapsed_ms(),
            }
            extra.update(values)
            with trace.use_span(
                self.span, end_on_exit=False, record_exception=False, set_status_on_exception=False
            ):
                logger.log(level, event, extra=extra)
        except Exception:
            return

    def set_attributes(self, attributes: Mapping[str, AttributeValue | None]) -> None:
        """시작했고 아직 끝나지 않은 span에 속성을 더한다. 값이 ``None``인 속성은 넣지 않는다."""
        if self._started is None or self._finished:
            return
        values: dict[str, AttributeValue] = {}
        for key in attributes:
            value = attributes[key]
            if value is not None:
                values[key] = value
        try:
            self.span.set_attributes(values)
        except Exception:
            return

    def finish(self, outcome: str, *, level: int = logging.INFO, **fields: Any) -> None:
        """시작한 span을 한 번 끝낸다.

        ``level``이 WARNING 이상이면 같은 수준의 ``finished`` 레코드도 남긴다. ``level``이 ERROR
        이상이거나 ``outcome``이 ``error``이면 span 상태를 ERROR로 둔다. 실패를 이미 다른 레코드로
        남긴 호출자는 INFO로 끝내 레코드 중복 없이 상태만 남긴다. 상태 설명에는 예외 메시지를
        넣지 않는다.
        """
        if self._started is None or self._finished:
            return
        self._finished = True
        if level >= logging.WARNING:
            self.emit(level, "finished", outcome=outcome, **fields)
        try:
            self.span.set_attribute("completion_bridge.outcome", outcome)
            self.span.set_attributes(_attributes(fields))
            if level >= logging.ERROR or outcome == "error":
                self.span.set_status(Status(StatusCode.ERROR))
            self.span.end()
        except Exception:
            return


class WireLog:
    """호출 하나의 변환 후 원본 요청과 종료를 ``completion_bridge.wire``에 DEBUG로 기록한다.

    레코드는 호출 span(:attr:`OperationLog.span`)을 현재 span으로 두고 만든다. 로깅 실패는 생성에
    전파하지 않는다.
    """

    def __init__(
        self,
        operation: OperationLog,
        vendor: str,
        model: str | None,
        url: str,
        body: dict[str, Any],
    ) -> None:
        """호출 span과 요청 정보를 보관한다.

        Args:
            operation: 이 호출의 작업 기록. 레코드의 span과 경과 시간을 여기서 읽는다.
            vendor: 어댑터 이름(API 형식).
            model: 요청 모델. 어댑터 기본 모델을 쓰면 ``None``일 수 있다.
            url: 요청 주소.
            body: 변환 후 요청 본문.
        """
        self._operation = operation
        self.vendor = vendor
        self.model = model
        self.url = url
        self.body = body
        self.ended = False

    def calls_enabled(self) -> bool:
        """원본 통신 로거가 지금 DEBUG 기록을 받는지 돌려준다."""
        return logging.getLogger(WIRE_LOGGER).isEnabledFor(logging.DEBUG)

    def start(self) -> None:
        """원본 요청을 기록한다. 본문은 요청 JSON 객체 그대로다."""
        self._emit(
            "request", lambda: self.body, {"http.request.method": "POST", "url.full": self.url}
        )

    def finish(
        self,
        event: str,
        payload: Callable[[], Any],
        attributes: Mapping[str, AttributeValue | None] | None = None,
    ) -> None:
        """종료 레코드를 한 번 기록한다.

        Args:
            event: ``response``, ``merge_error``, ``error``, ``cancelled`` 중 하나.
            payload: 레코드 본문을 만드는 함수. 로거가 꺼져 있으면 부르지 않는다.
            attributes: 레코드에 더할 속성. 값이 ``None``인 속성은 넣지 않는다.
        """
        if self.ended:
            return
        self.ended = True
        self._emit(event, payload, attributes or {})

    def _emit(
        self,
        event: str,
        payload: Callable[[], Any],
        attributes: Mapping[str, AttributeValue | None],
    ) -> None:
        """활성화된 원본 통신 로거에 레코드 하나를 남긴다."""
        logger = logging.getLogger(WIRE_LOGGER)
        if not logger.isEnabledFor(logging.DEBUG):
            return
        try:
            extra: dict[str, Any] = {
                "event_name": f"completion_bridge.{event}",
                "completion_bridge.api": self.vendor,
                "elapsed_ms": self._operation.elapsed_ms(),
            }
            if self.model is not None:
                extra["gen_ai.request.model"] = self.model
            for key in attributes:
                value = attributes[key]
                if value is not None:
                    extra[key] = value
            with trace.use_span(
                self._operation.span,
                end_on_exit=False,
                record_exception=False,
                set_status_on_exception=False,
            ):
                logger.debug(payload(), extra=extra)
        except Exception:
            # 소비 앱의 핸들러·포매터 실패가 모델 생성을 중단시키지 않도록 한다.
            pass
