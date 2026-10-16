"""도구 호출의 정책 판정과 실행 기록 및 주입 계약."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from completion_bridge.models.blocks import ToolResultBlock


class ToolPolicy(StrEnum):
    """실행 전 승인과 실행 후 검토에 사용하는 판정."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"


class Execution(BaseModel):
    """모델용 결과와 독립적으로 보존하는 실제 실행 결과."""

    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: Any = None
    structured_content: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    error: str = ""
    is_error: bool = False


class HitlDecision(BaseModel):
    """정책 판정과 출처 및 결정 시각."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: ToolPolicy
    source: Literal["profile", "user", "human", "default", "unavailable"]
    decided_by: Annotated[str, Field(min_length=1, max_length=255)] | Literal[""] = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolCall(BaseModel):
    """원본 호출 식별자와 메시지 내 블록 순서."""

    model_config = ConfigDict(frozen=True)
    ordinal: int
    tool_call_id: str = Field(min_length=1, max_length=255)
    tool_name: str = Field(max_length=255)
    arguments: dict[str, Any]


class HitlDraft(BaseModel):
    """도구 호출의 실행 및 정책 판정 기록."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    call: ToolCall
    created_at: datetime
    approval: HitlDecision | None = None
    review: HitlDecision | None = None
    execution: Execution | None = None
    error: str = ""
    background: bool = False
    edited_arguments: dict[str, Any] | None = None
    edited_result: Any = None
    approval_reason: str = ""
    review_reason: str = ""
    result: ToolResultBlock | None = None
    task: asyncio.Task[Any] | None = Field(default=None, exclude=True)
    """콜백이 생성해 전달한 작업 핸들. 직렬화에서 제외하며 호출자가 수명을 관리한다."""


@dataclass
class ToolOutput:
    """콜백의 모델용 결과와 선택적 HITL 기록·원본 실행 결과·작업 핸들.

    result가 없으면 이번 실행을 종료한다. 백그라운드 작업을 계속하려면
    task와 함께 모델에 전달할 접수 result를 반환한다. 정책 판정은 콜백의 책임이다.
    draft.call은 모델이 보낸 실제 호출 정보로 교체한다.
    """

    result: ToolResultBlock | None = None
    draft: HitlDraft | None = None
    execution: Execution | None = None
    task: asyncio.Task[Any] | None = None
    final: bool = False


ToolCallback = Callable[[str, dict[str, Any]], Awaitable[ToolOutput]]
"""도구 이름과 인자만 받고 외부 실행·정책 처리를 수행하는 비동기 콜백."""
