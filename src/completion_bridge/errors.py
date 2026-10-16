"""예외 계층.

메시지에는 프롬프트 원문, 응답 원문, 인증정보를 포함하지 않는다.
원본 응답 상세가 필요한 호출자는 TransportError와 VendorStreamError의 detail을
명시적으로 조회한다. detail은 운영 로그에 기록하지 않는다.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "CompletionBridgeError",
    "ExecutionStateError",
    "MappingError",
    "StreamNotFinished",
    "TransportError",
    "VendorStreamError",
]


class CompletionBridgeError(Exception):
    """이 라이브러리가 던지는 모든 예외의 뿌리."""


class ExecutionStateError(CompletionBridgeError):
    """이미 소비 중이거나 재사용할 수 없는 실행에 잘못된 작업을 요청했다."""


class TransportError(CompletionBridgeError):
    """HTTP 요청이 실패했다."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: str = "") -> None:
        """오류 설명과 HTTP 상태 코드·응답 상세를 보관한다."""
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class MappingError(CompletionBridgeError):
    """벤더 프레임을 허브 델타로 바꾸는 데 실패했다."""


class VendorStreamError(MappingError):
    """벤더가 반환한 오류. 원본은 명시적 진단용 detail에만 보존한다."""

    def __init__(self, vendor: str, detail: Any) -> None:
        """오류 본문을 복사하고 예외 메시지에는 알려진 오류 종류나 HTTP 코드만 사용한다."""
        self.vendor = vendor
        self.detail = copy.deepcopy(detail)
        label = ""
        if isinstance(detail, dict):
            code = detail.get("type", detail.get("code"))
            if isinstance(code, str) and code in {
                "overloaded_error",
                "rate_limit_error",
                "authentication_error",
                "permission_error",
                "invalid_request_error",
                "not_found_error",
                "api_error",
                "timeout_error",
            }:
                label = f" ({code})"
            elif type(code) is int and 100 <= code <= 599:
                label = f" ({code})"
        super().__init__(f"{vendor} stream reported an error{label}")


class StreamNotFinished(CompletionBridgeError):
    """스트림이 끝나기 전에 최종 결과를 읽으려 했다."""
