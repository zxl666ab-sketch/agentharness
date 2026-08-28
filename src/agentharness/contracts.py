"""Public domain contracts — stable Pydantic types for the harness surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, model_validator


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


class ReplayPolicy(StrEnum):
    safe = "safe"
    reconcile = "reconcile"
    never = "never"


class ToolRecoveryDecision(StrEnum):
    mark_succeeded = "mark_succeeded"
    skip = "skip"
    retry = "retry"


class ToolInvocationStatus(StrEnum):
    received = "received"
    validated = "validated"
    waiting_approval = "waiting_approval"
    approved = "approved"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    indeterminate = "indeterminate"


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
    context_compacted = "context_compacted"
    verification_started = "verification_started"
    verification_result = "verification_result"
    verification_feedback = "verification_feedback"
    text_delta = "text_delta"
    tool_call_start = "tool_call_start"
    tool_call_validated = "tool_call_validated"
    tool_execution_queued = "tool_execution_queued"
    tool_execution_started = "tool_execution_started"
    tool_retry = "tool_retry"
    tool_execution_cancelled = "tool_execution_cancelled"
    tool_execution_indeterminate = "tool_execution_indeterminate"
    tool_recovery_resolved = "tool_recovery_resolved"
    tool_call_end = "tool_call_end"
    tool_result = "tool_result"
    approval_requested = "approval_requested"
    approval_resolved = "approval_resolved"
    checkpoint = "checkpoint"
    span_start = "span_start"
    span_end = "span_end"
    child_run_started = "child_run_started"
    child_run_ended = "child_run_ended"
    # `budget_warning` has no emitter yet, but the Web SSE surface
    # (`web/src/useAgentStream.ts`, `viewModel.ts`) treats it as part of the
    # event-name contract, so the member stays.
    budget_warning = "budget_warning"
    provider_retry = "provider_retry"
    error = "error"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class ProviderAttempt(BaseModel):
    provider: str
    model: str | None = None
    attempt: int = 1
    status: Literal["completed", "error"] = "completed"
    error_kind: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    had_output: bool = False
    fallback: bool = False
    estimated_cost_usd: float | None = None


class Usage(BaseModel):
    """Token accounting for a run.

    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` are **cumulative**
    across model turns (used for budget enforcement). ``last_*`` describe only
    the most recent model call; ``last_local_estimate`` is the harness-side
    context size estimate (~4 chars/token) for that call — useful when a
    provider gateway reports inflated prompt_tokens. ``cached_input_tokens``
    counts the provider-reported prompt-cache reads inside ``input_tokens``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    estimated: bool = False
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    last_cached_input_tokens: int = 0
    last_local_estimate: int = 0
    model_turns: int = 0
    provider_attempts: list[ProviderAttempt] = Field(default_factory=list)
    estimated_cost_usd: float | None = None
    cost_status: Literal["unknown", "estimated"] = "unknown"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_hit_rate(self) -> float:
        """Fraction of cumulative input tokens served from the provider prompt cache."""
        if self.input_tokens <= 0 or self.cached_input_tokens <= 0:
            return 0.0
        return round(min(1.0, self.cached_input_tokens / self.input_tokens), 4)


class ProviderRetryConfig(BaseModel):
    max_retries: int = Field(default=3, ge=0, le=3)
    base_delay_s: float = Field(default=0.5, ge=0, le=60)
    max_delay_s: float = Field(default=8.0, ge=0, le=120)
    jitter_ratio: float = Field(default=0.25, ge=0, le=1)


class PricingConfig(BaseModel):
    input_per_million_usd: float | None = Field(default=None, ge=0)
    output_per_million_usd: float | None = Field(default=None, ge=0)
    cached_input_per_million_usd: float | None = Field(default=None, ge=0)

    @property
    def known(self) -> bool:
        return (
            self.input_per_million_usd is not None
            and self.output_per_million_usd is not None
        )


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
    tool_result: ToolResult | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ToolCall(BaseModel):
    id: str = Field(default_factory=new_id)
    invocation_id: str = Field(default_factory=new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_raw: str = ""
    ordinal: int = Field(default=0, ge=0)
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"


class ToolContentPart(BaseModel):
    type: Literal["text", "json", "image", "resource"] = "text"
    text: str | None = None
    data: Any = None
    mime_type: str | None = None
    artifact_id: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: str
    final_output: str | None = None
    # A tool can deliberately suspend a Run without claiming the business job is
    # complete.  Procurement uses this for persisted review and formal-approval
    # gates; ordinary tools leave both fields empty.
    pause_status: Literal["require_human", "waiting_approval"] | None = None
    pause_reason: str | None = None
    invocation_id: str | None = None
    is_error: bool = False
    artifact_id: str | None = None
    parts: list[ToolContentPart] = Field(default_factory=list)
    duration_ms: float | None = None
    attempts: int = Field(default=1, ge=0)
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
    version: str = "1"
    timeout_s: float = Field(default=60.0, gt=0, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=5)
    replay_policy: ReplayPolicy | None = None
    parallel_safe: bool | None = None
    max_result_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)


class ToolInvocationRecord(BaseModel):
    id: str
    run_id: str
    session_id: str
    step: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    provider_call_id: str
    tool_name: str
    tool_version: str = "1"
    status: ToolInvocationStatus = ToolInvocationStatus.received
    effect: EffectKind = EffectKind.pure
    replay_policy: ReplayPolicy = ReplayPolicy.never
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_sha256: str = ""
    approval_id: str | None = None
    attempt_count: int = 0
    result: ToolResult | None = None
    error_code: str | None = None
    error_category: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


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
    retry_after_s: float | None = Field(default=None, ge=0, le=86_400)


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
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    parallel_tool_calls: bool | None = None
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
    artifact_id: str | None = None


class ContextState(BaseModel):
    """Planner-owned state. Callers persist it but do not interpret it."""

    schema_version: int = 1
    items: list[ContextPinnedItem] = Field(default_factory=list)
    # Message ids folded into the `history_summary` pinned item by auto-compaction.
    summarized_message_ids: list[str] = Field(default_factory=list)
    compaction_count: int = 0


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
    kind: Literal["output", "file", "command", "ai"]
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
    tools_succeeded: list[str] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    output_assertions: dict[str, Any] | None = None
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
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tool_calls_per_turn: int = Field(default=16, ge=1, le=16)
    max_tool_calls: int = Field(default=128, ge=1, le=128)
    max_concurrent_tools: int = Field(default=4, ge=1, le=4)
    max_tool_argument_bytes: int = Field(default=262_144, ge=1024, le=262_144)
    max_tool_result_bytes: int = Field(default=1_048_576, ge=1024, le=1_048_576)
    max_inline_tool_result_bytes: int = Field(default=4096, ge=256, le=4096)
    # Auto-compaction: summarize old history once its estimate crosses
    # ratio × max_context_tokens; the newest groups stay verbatim.
    context_compact_enabled: bool = True
    context_compact_ratio: float = Field(default=0.8, ge=0.2, le=1.0)
    context_compact_keep_recent: int = Field(default=2, ge=0, le=16)


class ShellExecutionConfig(BaseModel):
    """Run-scoped shell backend selection; local remains the compatible default."""

    executor: Literal["local", "docker"] = "local"
    docker_image: str = "python:3.12.4-slim-bookworm"
    docker_network: bool = False
    docker_cpus: float = Field(default=1.0, gt=0, le=16)
    docker_memory_mb: int = Field(default=512, ge=64, le=32768)
    docker_pids_limit: int = Field(default=128, ge=16, le=4096)

    @model_validator(mode="after")
    def require_version_locked_docker_image(self) -> ShellExecutionConfig:
        if self.executor != "docker":
            return self
        image = self.docker_image.strip()
        leaf = image.rsplit("/", 1)[-1]
        has_digest = "@sha256:" in image
        has_version_tag = ":" in leaf and not leaf.lower().endswith(":latest")
        if not image or (not has_digest and not has_version_tag):
            raise ValueError("docker_image must use a version tag or sha256 digest")
        return self


class RunRequest(BaseModel):
    message: str
    session_id: str | None = None
    system: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    provider: str = "openai"
    approval: ApprovalMode = ApprovalMode.ask
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    provider_retry: ProviderRetryConfig = Field(default_factory=ProviderRetryConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    shell: ShellExecutionConfig = Field(default_factory=ShellExecutionConfig)
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
    invocation_id: str | None = None
    tool_version: str = "1"
    effect: EffectKind
    arguments_summary: str
    arguments_sha256: str = ""
    approval_scope: str = ""
    requires_confirmation: bool = False
    """Tool opted into a dedicated confirmation regardless of its effect kind.

    Approval callbacks must prompt when this is set, even under
    ``ApprovalMode.auto``: it is how tools such as long-term memory mutation ask
    for an explicit decision despite a non-destructive effect.
    """
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
    shell: ShellExecutionConfig = Field(default_factory=ShellExecutionConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)
    harness: Any = None  # forward ref to Harness for delegate


@runtime_checkable
class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult: ...
