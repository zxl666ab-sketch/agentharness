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
    require_human = "require_human"
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
    context_manifest = "context_manifest"
    verification_started = "verification_started"
    verification_result = "verification_result"
    verification_feedback = "verification_feedback"
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
    """Token accounting for a run.

    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` are **cumulative**
    across model turns (used for budget enforcement). ``last_*`` describe only
    the most recent model call; ``last_local_estimate`` is the harness-side
    context size estimate (~4 chars/token) for that call — useful when a
    provider gateway reports inflated prompt_tokens.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    last_local_estimate: int = 0
    model_turns: int = 0


def format_usage_brief(usage: Usage | None, *, budget_max: int | None = None) -> str:
    """Human-readable usage line for CLI status (empty when nothing to show)."""
    if usage is None:
        return ""
    if not (
        usage.input_tokens
        or usage.output_tokens
        or usage.last_input_tokens
        or usage.last_local_estimate
    ):
        return ""
    parts = [f"tokens={usage.input_tokens}/{usage.output_tokens}"]
    if usage.model_turns > 0 or usage.last_input_tokens or usage.last_output_tokens:
        parts.append(f"last={usage.last_input_tokens}/{usage.last_output_tokens}")
    if usage.last_local_estimate:
        parts.append(f"est≈{usage.last_local_estimate}")
    if usage.model_turns:
        parts.append(f"turns={usage.model_turns}")
    if budget_max is not None and budget_max > 0:
        parts.append(f"budget={usage.total_tokens}/{budget_max}")
    if usage.estimated and not usage.input_tokens:
        parts.append("estimated")
    elif usage.estimated:
        parts.append("est-fallback")
    return "  ".join(parts)


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
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool = False
    recovery_hint: str | None = None


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


class ContextPinnedItem(BaseModel):
    """Opaque, redacted run-scoped context selection persisted across turns/resume."""

    section: str = ""
    source: str = ""
    content: str = ""
    content_hash: str = ""
    token_estimate: int = 0
    selected: bool = True
    reason: str = "selected"


class ContextState(BaseModel):
    """Planner-owned state. Callers persist it but do not interpret it."""

    schema_version: int = 1
    items: list[ContextPinnedItem] = Field(default_factory=list)


class ContextManifestItem(BaseModel):
    section: str = ""
    source: str = ""
    content_hash: str = ""
    token_estimate: int = 0
    included: bool = True
    reason: str = "selected"
    compression: Literal["none", "summarized", "externalized", "excluded"] = "none"
    artifact_id: str | None = None
    preview: str = ""


class ContextManifest(BaseModel):
    """Redaction-safe evidence of the exact context used for one model turn."""

    schema_version: int = 1
    run_id: str = ""
    model_turn: int = 0
    budget_tokens: int = 100_000
    total_tokens: int = 0
    token_method: str = "estimate"
    prefix_fingerprint: str = ""
    compacted: bool = False
    items: list[ContextManifestItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class ContextBundle(BaseModel):
    """Complete provider input plus its auditable manifest and opaque stable state."""

    system: str | None = None
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    manifest: ContextManifest = Field(default_factory=ContextManifest)
    state: ContextState = Field(default_factory=ContextState)


class VerificationFailure(BaseModel):
    validator: str = ""
    error_code: str = "verification_failed"
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = True
    recovery_hint: str | None = None


class VerificationCheck(BaseModel):
    kind: Literal["eval_assert", "file", "command", "ai"]
    assertions: dict[str, Any] = Field(default_factory=dict)
    path: str | None = None
    exists: bool = True
    contains: list[str] = Field(default_factory=list)
    command: str | None = None
    min_score: float = 0.8


class VerificationPolicy(BaseModel):
    validators: list[VerificationCheck] = Field(default_factory=list)
    max_retries: int = Field(default=2, ge=0)
    on_exhausted: Literal["failed", "require_human", "checkpoint"] = "failed"
    evaluator_provider: str | None = None
    evaluator_model: str | None = None


class VerificationCandidate(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    run_id: str = ""
    goal: str = ""
    output: str = ""
    cwd: str = "."
    extra_dirs: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    steps: int = 0
    latency_s: float = 0.0
    tools_ordered: list[str] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    eval_assert: dict[str, Any] | None = None
    executor_provider: str | None = None
    executor_adapter: Any = None
    cancel_event: Any = None
    trace: Any = None


class VerificationDecision(BaseModel):
    action: Literal["pass", "retry", "require_human", "stop"] = "pass"
    feedback: str | None = None
    feedback_message: Message | None = None
    failures: list[VerificationFailure] = Field(default_factory=list)
    attempt: int = 0
    evidence: dict[str, Any] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    max_steps: int = 50
    max_wall_time_s: float = 600.0
    max_tokens: int = 200_000
    max_context_tokens: int = 100_000
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
    verification: VerificationPolicy | None = None
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
    evaluation: dict[str, Any] | None = None


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
