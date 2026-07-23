"""Public domain contracts — stable Pydantic types for the harness surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


class EffectKind(StrEnum):
    pure = "pure"
    workspace_read = "workspace_read"
    workspace_write = "workspace_write"
    process = "process"
    network = "network"
    destructive = "destructive"


class ApprovalMode(StrEnum):
    ask = "ask"
    auto = "auto"
    never = "never"


class ApprovalDecision(StrEnum):
    allow_once = "allow_once"
    allow_run = "allow_run"
    deny = "deny"


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class StreamItemType(StrEnum):
    text_delta = "text_delta"
    tool_call_start = "tool_call_start"
    tool_call_delta = "tool_call_delta"
    tool_call_end = "tool_call_end"
    usage = "usage"
    error = "error"
    done = "done"


class EventType(StrEnum):
    run_started = "run_started"
    run_status = "run_status"
    run_completed = "run_completed"
    run_failed = "run_failed"
    run_cancelled = "run_cancelled"
    run_interrupted = "run_interrupted"
    model_turn_start = "model_turn_start"
    model_turn_end = "model_turn_end"
    text_delta = "text_delta"
    tool_call_start = "tool_call_start"
    tool_call_end = "tool_call_end"
    tool_result = "tool_result"
    approval_requested = "approval_requested"
    approval_resolved = "approval_resolved"
    checkpoint = "checkpoint"
    span_start = "span_start"
    span_end = "span_end"
    child_run_started = "child_run_started"
    child_run_ended = "child_run_ended"
    budget_warning = "budget_warning"
    redaction = "redaction"
    heartbeat = "heartbeat"
    error = "error"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False


class Message(BaseModel):
    id: str = Field(default_factory=new_id)
    role: MessageRole
    content: str = ""
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ToolCall(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_raw: str = ""
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    artifact_id: str | None = None
    duration_ms: float | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    effect: EffectKind = EffectKind.pure
    requires_approval: bool = False


class ModelStreamItem(BaseModel):
    """Normalized stream item — provider raw payloads must not leak past adapters."""

    type: StreamItemType
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    arguments: dict[str, Any] | None = None
    usage: Usage | None = None
    error: str | None = None
    error_kind: str | None = None  # rate_limit | timeout | cancelled | provider | unknown


class ModelTurn(BaseModel):
    id: str = Field(default_factory=new_id)
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    finished: bool = False


class ModelRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system: str | None = None
    stop: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    max_steps: int = 50
    max_wall_time_s: float = 600.0
    max_tokens: int = 200_000
    max_output_length: int = 500_000
    max_delegate_depth: int = 3
    max_concurrent_children: int = 4


class RunRequest(BaseModel):
    message: str
    session_id: str | None = None
    system: str | None = None
    model: str | None = None
    provider: str = "fake"
    approval: ApprovalMode = ApprovalMode.ask
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    cwd: str | None = None
    extra_dirs: list[str] = Field(default_factory=list)
    skills_dirs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None
    root_run_id: str | None = None
    delegate_depth: int = 0
    allow_write: bool = True
    tools: list[str] | None = None  # None = all registered


class RunResult(BaseModel):
    run_id: str
    session_id: str
    status: RunStatus
    output: str = ""
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)
    steps: int = 0
    parent_run_id: str | None = None
    root_run_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    """One top-level dialogue turn (user message + final assistant output)."""

    run_id: str
    session_id: str
    user_content: str = ""
    assistant_content: str = ""
    status: RunStatus | str
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None


class EventEnvelope(BaseModel):
    schema_version: int = 1
    event_id: str = Field(default_factory=new_id)
    global_seq: int = 0
    run_seq: int = 0
    session_id: str
    root_run_id: str
    run_id: str
    parent_run_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    type: EventType | str
    timestamp: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class Checkpoint(BaseModel):
    run_id: str
    phase: Literal[
        "model_turn",
        "tool_batch",
        "waiting_approval",
        "terminal",
    ]
    step: int
    messages: list[Message]
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    completed_tool_call_ids: list[str] = Field(default_factory=list)
    partial_text: str = ""
    usage: Usage = Field(default_factory=Usage)
    status: RunStatus = RunStatus.running
    approval_token: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=new_id)
    run_id: str
    tool_call_id: str
    tool_name: str
    effect: EffectKind
    arguments_summary: str
    decision: ApprovalDecision | None = None
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Protocols (stable interfaces)
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelAdapter(Protocol):
    """Provider surface: only stream() — raw provider payloads never leave the adapter."""

    name: str

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]: ...


class ToolContext(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    run_id: str
    session_id: str
    cwd: str
    extra_dirs: list[str] = Field(default_factory=list)
    data_dir: str
    allow_write: bool = True
    cancel_event: Any = None  # asyncio.Event
    approval_mode: ApprovalMode = ApprovalMode.ask
    metadata: dict[str, Any] = Field(default_factory=dict)
    harness: Any = None  # forward ref to Harness for delegate


@runtime_checkable
class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult: ...
