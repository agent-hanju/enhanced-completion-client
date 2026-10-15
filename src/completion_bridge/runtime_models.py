"""도구 호출의 정책 판정과 실행 기록 및 주입 계약."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .blocks import ToolResultBlock
from .hub import ToolDefinition

if TYPE_CHECKING:
    from .runtime import Run


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


@dataclass
class ToolOutput:
    """원본 기록과 모델용 결과 및 답변 대체 여부를 명시하는 실행기 반환값."""

    execution: Execution
    result: ToolResultBlock
    final: bool = False
    child_run: Run | None = None


class ToolExecutor(Protocol):
    """도구 목록과 실제 실행 기능을 제공한다."""

    @property
    def definitions(self) -> Sequence[ToolDefinition]:
        """모델에 제공할 정의를 반환한다."""
        ...

    async def execute(self, call: ToolCall) -> ToolOutput:
        """호출을 실행해 원본 및 모델용 결과를 반환한다."""
        ...


class PolicyEvaluator(Protocol):
    """각 호출의 실행 전후 판정을 제공한다."""

    async def approve(self, call: ToolCall) -> HitlDecision:
        """실행 전 정책을 판정한다."""
        ...

    async def review(self, call: ToolCall, execution: Execution) -> HitlDecision:
        """실제 실행 결과의 모델 제공 여부를 판정한다."""
        ...
