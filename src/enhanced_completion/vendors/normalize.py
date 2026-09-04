"""허브 정규형. 값의 어휘를 Anthropic Messages로 모은다.

``streambind-base``의 세 매퍼가 확립한 규칙이다. 자세한 표는 ``docs/Support-Matrix.md``에
있다.

**타입만 맞추고 값을 벤더별로 흘려보내면 허브가 아니다.** 소비 앱이 ``stop_reason``을 읽으려고
어느 벤더에서 왔는지 알아야 하면 추상화가 새는 것이다. 그래서 이름 어휘까지 한쪽으로 모은다.

허브 어휘를 Anthropic으로 고른 이유는 허브 모델 자체가 Messages 모양이기 때문이다.
"""

from __future__ import annotations

__all__ = [
    "REFUSAL_PREFIX",
    "normalize_role",
    "stop_reason_from_chat",
    "stop_reason_from_gemini",
    "stop_reason_from_responses",
]

#: 거부를 본문에 실을 때 붙이는 표시. 거부도 사용자가 봐야 하지만 답변과 구분되어야 한다.
REFUSAL_PREFIX = "[Refused] "

_CHAT_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "content_filter",
}

_GEMINI_STOP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "content_filter",
    "RECITATION": "recitation",
    "OTHER": "other",
}

_RESPONSES_STATUS = {
    "completed": "end_turn",
    "failed": "error",
    "incomplete": "max_tokens",
    "cancelled": "cancelled",
}

_ROLES = {
    "user": "user",
    "assistant": "assistant",
    # Anthropic messages에 system role이 없고 도구 결과는 user 메시지다.
    "system": "user",
    "tool": "user",
    # Gemini는 assistant를 model이라 부른다.
    "model": "assistant",
}


def normalize_role(role: str | None) -> str:
    """벤더 role을 허브 어휘로. 알 수 없으면 ``assistant``."""
    if not role:
        return "assistant"
    return _ROLES.get(role, "assistant")


def stop_reason_from_chat(reason: str | None) -> str | None:
    """chat completions ``finish_reason``. 표에 없으면 원본을 유지한다."""
    if not reason:
        return None
    return _CHAT_STOP.get(reason, reason)


def stop_reason_from_gemini(reason: str | None) -> str | None:
    """Gemini ``finishReason``. 표에 없으면 소문자화한다."""
    if not reason:
        return None
    return _GEMINI_STOP.get(reason, reason.lower())


def stop_reason_from_responses(status: str | None) -> str | None:
    """Responses ``status``. 표에 없으면 원본을 유지한다."""
    if not status:
        return None
    return _RESPONSES_STATUS.get(status, status)
