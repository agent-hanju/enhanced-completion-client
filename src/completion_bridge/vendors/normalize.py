"""HubMessage, HubResponse를 위한 벤더별 용어 정규화 규칙"""

from __future__ import annotations

__all__ = [
    "INSTRUCTION_ROLES",
    "REFUSAL_PREFIX",
    "normalize_role",
    "stop_reason_from_chat",
    "stop_reason_from_gemini",
    "stop_reason_from_responses",
]

#: 거부를 본문에 실을 때 붙이는 표시. 거부도 사용자가 봐야 하지만 답변과 구분되어야 한다.
REFUSAL_PREFIX = "[Refused] "

#: 턴이 아니라 지시문인 role. 대화 목록 안에 이 role을 둘 수 있는 API는 위치와 role을 그대로
#: 보존한다. Anthropic ``messages``(user/assistant)와 Gemini ``contents``(user/model)는 목록
#: 안에서 표현할 수 없으므로 그 둘만 전용 필드로 hoist한다. hoist는 위치와 role 구분을 잃는
#: 축약이라 API가 표현하지 못할 때만 쓴다.
INSTRUCTION_ROLES = frozenset({"system", "developer"})

_CHAT_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "content_filter",
}

#: Gemini ``FinishReason``에서 허브 어휘에 **1:1 대응물이 있는 값만** 옮긴다.
#:
#: 나머지 15개(``RECITATION``, ``LANGUAGE``, ``OTHER``, ``BLOCKLIST``,
#: ``PROHIBITED_CONTENT``, ``SPII``, ``MALFORMED_FUNCTION_CALL``, ``IMAGE_SAFETY``,
#: ``UNEXPECTED_TOOL_CALL``, ``TOO_MANY_TOOL_CALLS``, ``IMAGE_PROHIBITED_CONTENT``,
#: ``NO_IMAGE``, ``IMAGE_RECITATION``, ``IMAGE_OTHER``, ``FINISH_REASON_UNSPECIFIED``)는
#: 원문을 유지한다. 케이스만 바꾸면 허브 어휘도 원문도 아닌 값이 된다.
#:
#: ``BLOCKLIST``/``PROHIBITED_CONTENT``/``SPII`` 계열을 ``content_filter``로 접지 않는 것은
#: 서로 다른 사유이기 때문이다. 금칙어와 PII 검출은 소비 앱이 다르게 다뤄야 한다.
_GEMINI_STOP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "content_filter",
}

_RESPONSES_STATUS = {
    "completed": "end_turn",
    "failed": "error",
    "incomplete": "max_tokens",
    "cancelled": "cancelled",
}

#: 응답 방향 전용. 요청 방향의 ``system``/``developer``는 각 어댑터가 wire 밖으로 hoist한다.
_ROLES = {
    "user": "user",
    "assistant": "assistant",
    "model": "assistant",
}


def normalize_role(role: str | None) -> str:
    """벤더 응답 role을 허브 role로 정규화. 알 수 없으면 ``assistant``.

    알 수 없는 role의 원문은 보존하지 않는다. 호출부가 Gemini 응답 하나뿐이고 그 API는
    ``user``/``model`` 외의 role을 응답에 내보내지 않으므로 지금은 도달하지 않는 경로다.
    새 어댑터가 실제로 무엇을 내보내는지 보고 정하는 편이 근거가 있다.
    """
    if not role:
        return "assistant"
    return _ROLES.get(role, "assistant")


def stop_reason_from_chat(reason: str | None) -> str | None:
    """chat completions ``finish_reason``. 표에 없으면 원본을 유지한다."""
    if not reason:
        return None
    return _CHAT_STOP.get(reason, reason)


def stop_reason_from_gemini(reason: str | None) -> str | None:
    """Gemini ``finishReason``. 표에 없으면 원본을 유지한다."""
    if not reason:
        return None
    return _GEMINI_STOP.get(reason, reason)


def stop_reason_from_responses(status: str | None) -> str | None:
    """Responses ``status``. 표에 없으면 원본을 유지한다."""
    if not status:
        return None
    return _RESPONSES_STATUS.get(status, status)
