"""예외 계층.

메시지에 프롬프트 원문, 응답 원문, 인증정보를 넣지 않는다. 예외는 로그로 흘러가고 로그는
남기 때문이다. 상태 코드와 짧은 detail만 남긴다.
"""

from __future__ import annotations

__all__ = [
    "CompletionBridgeError",
    "ExtractionError",
    "MappingError",
    "StreamNotFinished",
    "TransportError",
]


class CompletionBridgeError(Exception):
    """이 라이브러리가 던지는 모든 예외의 뿌리."""


class TransportError(CompletionBridgeError):
    """HTTP 요청이 실패했다."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class MappingError(CompletionBridgeError):
    """벤더 프레임을 허브 델타로 바꾸는 데 실패했다."""


class StreamNotFinished(CompletionBridgeError):
    """스트림이 끝나기 전에 최종 결과를 읽으려 했다."""


class ExtractionError(CompletionBridgeError):
    """등록된 전처리기가 콘텐츠를 텍스트로 뽑다가 실패했다.

    전처리기가 **없는** 것과 다르다. 없는 것은 사전에 알 수 있는 설정 상태이므로 전달 불가
    표시로 degrade한다. 실패는 특정 콘텐츠에서 난 런타임 오류이고 호출자가 고칠 수 있는
    일이다(재인코딩, 다른 파서, 의도적 제외). 둘을 같은 태그로 뭉개면 호출자 코드의 결함이
    전달 불가 표시 뒤에 숨는다.

    대화 로직에서 잡아 해소하도록 명시적으로 올린다.
    """

    def __init__(self, message: str, *, media_type: str | None = None) -> None:
        super().__init__(message)
        self.media_type = media_type
