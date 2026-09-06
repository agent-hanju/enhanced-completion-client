"""HubMessage, HubResponse를 위한 벤더별 용어 정규화 규칙"""

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
    "developer": "system",
    "system": "user",
    "tool": "user",
    "model": "assistant",
}


def normalize_role(role: str | None) -> str:
    """벤더 role을 허브 role로 정규화. 알 수 없으면 ``assistant``."""
    # // TODO: None이나 커스텀 role을 assistant로 가이딩하는 특별한 이유가 없다면, 아래 다른 변환과 같이 커스텀은 통과시키고 None은 에러를 내는 방식으로 하면 안되는지?
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
    ## // TODO: 대응되는 어휘가 없으면 원본을 유지한다는 일관화 규칙을 지킬거면 그냥 lower를 안 해도 되는 것 아닌지?
    if not reason:
        return None
    return _GEMINI_STOP.get(reason, reason.lower())


def stop_reason_from_responses(status: str | None) -> str | None:
    """Responses ``status``. 표에 없으면 원본을 유지한다."""
    if not status:
        return None
    return _RESPONSES_STATUS.get(status, status)
