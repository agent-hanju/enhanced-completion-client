"""예외 계층.

메시지에 프롬프트 원문, 응답 원문, 인증정보를 넣지 않는다. 예외는 로그로 흘러가고 로그는
남기 때문이다. 상태 코드와 짧은 detail만 남긴다.
"""

from __future__ import annotations

__all__ = [
    "EnhancedCompletionError",
    "MappingError",
    "StreamNotFinished",
    "TransportError",
]


class EnhancedCompletionError(Exception):
    """이 라이브러리가 던지는 모든 예외의 뿌리."""


class TransportError(EnhancedCompletionError):
    """HTTP 요청이 실패했다."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class MappingError(EnhancedCompletionError):
    """벤더 프레임을 허브 델타로 바꾸는 데 실패했다."""


class StreamNotFinished(EnhancedCompletionError):
    """스트림이 끝나기 전에 최종 결과를 읽으려 했다."""
