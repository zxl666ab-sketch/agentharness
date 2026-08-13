"""Shared procurement workbench wire enums and read models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProcurementStatus(StrEnum):
    DRAFT = "draft"
    COLLECTING = "collecting"
    REVIEW = "review"
    READY = "ready"
    ANALYZED = "analyzed"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    NO_AWARD = "no_award"
    CANCELLED = "cancelled"


class AiTaskStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


AI_STATUS_TRANSITIONS: dict[AiTaskStatus, frozenset[AiTaskStatus]] = {
    AiTaskStatus.PENDING: frozenset({AiTaskStatus.DISPATCHING, AiTaskStatus.CANCELLED}),
    AiTaskStatus.DISPATCHING: frozenset(
        {AiTaskStatus.RUNNING, AiTaskStatus.FAILED, AiTaskStatus.CANCELLED}
    ),
    AiTaskStatus.RUNNING: frozenset(
        {AiTaskStatus.SUCCEEDED, AiTaskStatus.FAILED, AiTaskStatus.CANCELLED}
    ),
    AiTaskStatus.SUCCEEDED: frozenset(),
    AiTaskStatus.FAILED: frozenset({AiTaskStatus.RETRYING, AiTaskStatus.CANCELLED}),
    AiTaskStatus.RETRYING: frozenset(
        {AiTaskStatus.RUNNING, AiTaskStatus.FAILED, AiTaskStatus.CANCELLED}
    ),
    AiTaskStatus.CANCELLED: frozenset(),
}


class AiTaskType(StrEnum):
    QUOTE_ANALYSIS = "QUOTE_ANALYSIS"


class AiTaskStep(StrEnum):
    INPUT_VALIDATE = "INPUT_VALIDATE"
    ARTIFACT_FETCH = "ARTIFACT_FETCH"
    QUOTE_PARSE = "QUOTE_PARSE"
    RULE_ANALYSIS = "RULE_ANALYSIS"
    EXPLANATION = "EXPLANATION"
    RESULT_PUBLISH = "RESULT_PUBLISH"


class AiErrorCategory(StrEnum):
    VALIDATION = "VALIDATION"
    BUSINESS = "BUSINESS"
    PROVIDER = "PROVIDER"
    TRANSPORT = "TRANSPORT"
    INTERNAL = "INTERNAL"


class AiTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    business_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    status: AiTaskStatus
    task_type: AiTaskType
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    current_step: AiTaskStep | None
    progress: float = Field(ge=0, le=1)
    retry_count: int = Field(ge=0)
    max_retries: int = Field(default=3, ge=0)
    retryable: bool
    operation_id: str | None = None
    result_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    stale: bool
    stale_reason: str | None = None
    error_category: AiErrorCategory | None = None
    error_code: str | None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


def can_transition(current: AiTaskStatus, target: AiTaskStatus) -> bool:
    return target in AI_STATUS_TRANSITIONS[current]
