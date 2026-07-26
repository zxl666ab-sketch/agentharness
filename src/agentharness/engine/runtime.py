"""Asyncio agent run engine — stream, tools, checkpoint, resume, cancel, delegate."""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from datetime import UTC, datetime
from typing import Any, cast

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    BudgetConfig,
    ContextState,
    EffectKind,
    EventType,
    Message,
    MessageRole,
    ModelAdapter,
    ModelRequest,
    PricingConfig,
    ProviderAttempt,
    ProviderRetryConfig,
    ReplayPolicy,
    RunRequest,
    RunResult,
    RunStatus,
    ShellExecutionConfig,
    StreamItemType,
    ToolCall,
    ToolContentPart,
    ToolContext,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolRecoveryDecision,
    ToolResult,
    ToolSpec,
    Usage,
    VerificationCandidate,
    VerificationCheck,
    VerificationDecision,
    VerificationPolicy,
    new_id,
)
from agentharness.engine.context import ContextPlanner, billable_turn_usage, estimate_tokens
from agentharness.engine.events import EventEmitter
from agentharness.engine.lease import LeaseManager
from agentharness.engine.lifecycle import RESUMABLE_STATUSES, RunLifecycle
from agentharness.engine.run_state import RunContext, ensure_ctx
from agentharness.engine.scheduler import EffectScheduler
from agentharness.engine.tool_execution import (
    approval_scope,
    arguments_sha256,
    canonical_arguments,
    invalid_arguments_result,
    resolved_parallel_safe,
    resolved_replay_policy,
    tool_call_completed,
    tool_result_model_content,
    validate_tool_arguments,
    validate_tool_spec,
)
from agentharness.engine.verification import VerificationLoop
from agentharness.security.approval import auto_decision
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage
from agentharness.tools.summary import summarize_tool_arguments

ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]

_RETRYABLE_PROVIDER_ERRORS = frozenset(
    {"rate_limit", "timeout", "connection", "server_error"}
)


def _provider_exception_kind(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    low = str(exc).lower()
    name = type(exc).__name__.lower()
    if status == 429 or "rate limit" in low:
        return "rate_limit"
    if "timeout" in low or "timeout" in name:
        return "timeout"
    if isinstance(status, int) and 500 <= status <= 599:
        return "server_error"
    if "connection" in low or "connect" in name:
        return "connection"
    return "provider"


def _retry_delay(config: ProviderRetryConfig, retry_number: int) -> float:
    base = min(config.max_delay_s, config.base_delay_s * (2 ** (retry_number - 1)))
    if base <= 0 or config.jitter_ratio <= 0:
        return base
    return max(0.0, base * random.uniform(1 - config.jitter_ratio, 1 + config.jitter_ratio))


def _cost_usd(input_tokens: int, output_tokens: int, pricing: PricingConfig) -> float | None:
    if not pricing.known:
        return None
    assert pricing.input_per_million_usd is not None
    assert pricing.output_per_million_usd is not None
    return (
        input_tokens * pricing.input_per_million_usd
        + output_tokens * pricing.output_per_million_usd
    ) / 1_000_000


def _update_usage_cost(usage: Usage, pricing: PricingConfig) -> None:
    cost = _cost_usd(usage.input_tokens, usage.output_tokens, pricing)
    usage.estimated_cost_usd = round(cost, 10) if cost is not None else None
    usage.cost_status = "estimated" if cost is not None else "unknown"


# Tool-argument summarizer lives in one place (shared with the CLI / event payloads);
# keep the private alias so existing call sites read unchanged.
_summarize_tool_arguments = summarize_tool_arguments


def _tool_result_messages_for_call(
    messages: list[Message], tool_call: ToolCall
) -> list[Message]:
    assistant_index = -1
    for index, message in enumerate(messages):
        if message.role != MessageRole.assistant or not message.tool_calls:
            continue
        if any(call.invocation_id == tool_call.invocation_id for call in message.tool_calls):
            assistant_index = index
    matches: list[Message] = []
    for message in messages[assistant_index + 1 :]:
        if message.role != MessageRole.tool:
            continue
        if message.tool_result is not None and message.tool_result.invocation_id:
            if message.tool_result.invocation_id == tool_call.invocation_id:
                matches.append(message)
            continue
        if message.tool_call_id == tool_call.id:
            matches.append(message)
    return matches


def _truncate_utf8(text: str, max_bytes: int, suffix: str = "") -> str:
    max_bytes = max(0, max_bytes)
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) > max_bytes:
        return suffix_bytes[:max_bytes].decode("utf-8", errors="ignore")
    encoded = text.encode("utf-8")
    if len(encoded) + len(suffix_bytes) <= max_bytes:
        return text + suffix
    available = max_bytes - len(suffix_bytes)
    return encoded[:available].decode("utf-8", errors="ignore") + suffix


def _serialized_tool_result(result: ToolResult) -> bytes:
    return json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class RunEngine:
    def __init__(
        self,
        storage: Storage,
        providers: dict[str, ModelAdapter],
        tools: dict[str, Any],
        *,
        redactor: Redactor | None = None,
        approval_callback: ApprovalCallback | None = None,
        harness: Any = None,
        lease_owner_id: str | None = None,
        lease_ttl_s: float = 60.0,
        lease_heartbeat_s: float = 10.0,
    ) -> None:
        self.storage = storage
        self.providers = providers
        self.tools = tools
        self.redactor = redactor or default_redactor
        self.approval_callback = approval_callback
        self.harness = harness
        self.lease = LeaseManager(
            storage,
            owner_id=lease_owner_id,
            ttl_s=lease_ttl_s,
            heartbeat_s=lease_heartbeat_s,
        )
        self.scheduler = EffectScheduler()
        self.context_planner = ContextPlanner(
            storage=storage,
            artifacts=storage.artifacts,
            redactor=self.redactor,
        )
        # One RunContext per active run; see RunContext for why this replaces 13 maps.
        self._runs: dict[str, RunContext] = {}
        self.events = EventEmitter(
            storage=storage,
            runs=self._runs,
            redactor=self.redactor,
            harness=harness,
        )
        self.lifecycle = RunLifecycle(
            storage=storage,
            runs=self._runs,
            events=self.events,
            redactor=self.redactor,
        )
        # Process handles per run. Kept separate from RunContext because the shell tools
        # share this dict by reference (harness wires its own registry in), registering
        # handles the engine later kills — a per-run field could not be shared that way.
        self._active_processes: dict[str, list[Any]] = {}
        self._active_run_ids: set[str] = set()
        self.active_run_id: str | None = None

    def _ctx(self, run_id: str) -> RunContext:
        """Return (creating if needed) the RunContext for a run."""
        return ensure_ctx(self._runs, run_id)

    def child_run_ids(self, run_id: str) -> list[str]:
        """Child run ids spawned by a run (used by the delegate concurrency limiter)."""
        ctx = self._runs.get(run_id)
        return list(ctx.child_runs) if ctx else []

    def _activate_run(self, run_id: str) -> None:
        self._active_run_ids.add(run_id)
        self.active_run_id = run_id

    def _start_run_lease(self, run_id: str) -> None:
        self.lease.acquire(run_id)
        self._ctx(run_id).lease_heartbeat_task = self.lease.start_heartbeat(
            run_id, on_lost=self._on_lease_lost
        )

    def _on_lease_lost(self, run_id: str) -> None:
        """Lost the single-writer lease: interrupt the run instead of double-writing."""
        ctx = self._runs.get(run_id)
        if ctx is not None:
            ctx.stop_mode = "interrupt"
            ctx.cancel_event.set()

    def _deactivate_run(self, run_id: str) -> None:
        self._active_run_ids.discard(run_id)
        if self.active_run_id == run_id:
            self.active_run_id = next(iter(self._active_run_ids), None)

    async def _cleanup_run_state(self, run_id: str) -> None:
        # ExitStack guarantees the RunContext pop and child-link scrub run even if a
        # tool's release_run raises. Per-run state is one dict entry, so teardown is
        # atomic and no field can be left dangling.
        ctx = self._runs.get(run_id)
        heartbeat = ctx.lease_heartbeat_task if ctx else None
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        self.lease.release(run_id)
        with ExitStack() as stack:
            stack.callback(self._forget_run, run_id)
            seen: set[int] = set()
            for tool in self.tools.values():
                if id(tool) in seen:
                    continue
                seen.add(id(tool))
                release_run = getattr(tool, "release_run", None)
                if callable(release_run):
                    try:
                        result = release_run(run_id)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:  # noqa: BLE001
                        pass

    def _forget_run(self, run_id: str) -> None:
        """Drop all in-memory state for a run and unlink it from any parent's children."""
        self._runs.pop(run_id, None)
        self._active_processes.pop(run_id, None)
        for ctx in self._runs.values():
            if run_id in ctx.child_runs:
                ctx.child_runs = [child for child in ctx.child_runs if child != run_id]

    def get_cancel_event(self, run_id: str) -> asyncio.Event:
        return self._ctx(run_id).cancel_event

    async def _kill_descendants(self, run_id: str) -> None:
        """Propagate cancel signal, kill shell trees, clear process registry."""
        self.get_cancel_event(run_id).set()
        ctx = self._runs.get(run_id)
        for child_id in list(ctx.child_runs if ctx else []):
            await self._kill_descendants(child_id)
        for proc in list(self._active_processes.get(run_id, [])):
            try:
                from agentharness.tools.shell import kill_process_tree

                await kill_process_tree(proc)
            except Exception:  # noqa: BLE001
                pass
        self._active_processes[run_id] = []
        for tool in dict.fromkeys(self.tools.values()):
            cancel_run = getattr(tool, "cancel_run", None)
            if callable(cancel_run):
                await cast(Callable[[str], Awaitable[None]], cancel_run)(run_id)
        provider_owner = ctx.provider_owner_task if ctx else None
        if provider_owner is not None and provider_owner is not asyncio.current_task():
            provider_owner.cancel()

    async def _watch_stop_request(self, run_id: str, cancel: asyncio.Event) -> None:
        # Poll external (cross-process) stop requests. The read is on the RO
        # connection now, so ~0.2s is responsive enough without spinning the
        # writer lock 20x/s. In-process cancel/interrupt set `cancel` directly
        # and don't wait on this loop.
        while not cancel.is_set():
            mode = self.storage.get_stop_request(run_id)
            if mode:
                self._ctx(run_id).stop_mode = mode
                await self._kill_descendants(run_id)
                return
            try:
                await asyncio.wait_for(cancel.wait(), timeout=0.2)
            except TimeoutError:
                pass

    def _stop_status(self, run_id: str) -> RunStatus:
        ctx = self._runs.get(run_id)
        mode = (ctx.stop_mode if ctx else None) or "cancel"
        return RunStatus.interrupted if mode == "interrupt" else RunStatus.cancelled

    async def cancel(self, run_id: str) -> None:
        """Signal cancel, kill process trees + children. Active loop finishes as cancelled."""
        run = self.storage.get_run(run_id)
        if not run:
            raise KeyError(f"run not found: {run_id}")
        status = RunStatus(run["status"])
        if status == RunStatus.cancelled:
            return
        if status in (RunStatus.completed, RunStatus.failed, RunStatus.interrupted):
            raise RuntimeError(
                f"run {run_id} is not cancellable from status {status.value}"
            )
        self._ctx(run_id).stop_mode = "cancel"
        self.storage.request_stop(run_id, "cancel")
        await self._kill_descendants(run_id)
        self.lifecycle.preserve_checkpoint(run_id, status=RunStatus.cancelled)
        # If no active loop owns this run, finalize status here
        if run_id not in self._active_run_ids:
            run = self.storage.get_run(run_id)
            if run and run["status"] in (
                RunStatus.running.value,
                RunStatus.waiting_approval.value,
                RunStatus.pending.value,
            ):
                self.events.emit_and_update(
                    run_id,
                    status=RunStatus.cancelled,
                    finished=True,
                    events=[
                        self.events.event(
                            run,
                            EventType.run_cancelled,
                            {"reason": "cancel"},
                        )
                    ],
                )

    async def interrupt(self, run_id: str, reason: str = "interrupted") -> None:
        """Ctrl+C / CancelledError: kill trees, preserve resume state, finish as interrupted."""
        self._ctx(run_id).stop_mode = "interrupt"
        self.storage.request_stop(run_id, "interrupt")
        await self._kill_descendants(run_id)
        self.lifecycle.preserve_checkpoint(run_id, status=RunStatus.interrupted)
        if run_id not in self._active_run_ids:
            self.lifecycle.mark_interrupted(run_id, reason)

    async def run(self, request: RunRequest, *, run_id: str | None = None) -> RunResult:
        from agentharness.session_history import session_title_from_message

        wall_started = time.monotonic()
        parent_run_id = request.parent_run_id
        is_top_level = parent_run_id is None

        # Existing session keeps its id; new id only when caller omitted session_id.
        existing_session = bool(
            request.session_id and self.storage.session_exists(request.session_id)
        )
        session_id = request.session_id or new_id()
        if not existing_session:
            self.storage.create_session(session_id)
            if is_top_level and (request.message or "").strip():
                self.storage.update_session(
                    session_id,
                    title=session_title_from_message(request.message),
                )
        else:
            # Only set title once from the first non-empty user message when still default.
            if is_top_level:
                sess = self.storage.get_session(session_id)
                if sess and (not sess.get("title") or sess.get("title") == "session"):
                    if (request.message or "").strip():
                        self.storage.update_session(
                            session_id,
                            title=session_title_from_message(request.message),
                        )

        run_id = run_id or new_id()
        if self.storage.get_run(run_id) is not None:
            raise ValueError(f"run id already exists: {run_id}")
        root_run_id = request.root_run_id or run_id

        self.storage.create_run(
            run_id=run_id,
            session_id=session_id,
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            status=RunStatus.running,
            provider=request.provider,
            model=request.model,
            approval=request.approval.value,
            cwd=request.cwd,
            delegate_depth=request.delegate_depth,
            allow_write=request.allow_write,
            metadata={
                **request.metadata,
                "_agentharness_budget": request.budget.model_dump(),
                "_agentharness_provider_retry": request.provider_retry.model_dump(),
                "_agentharness_pricing": request.pricing.model_dump(mode="json"),
                "_agentharness_shell": request.shell.model_dump(mode="json"),
                "_agentharness_context_request": {
                    "original_goal": request.message,
                    "system": request.system,
                    "extra_dirs": request.extra_dirs,
                    "skills_dirs": request.skills_dirs,
                    "tools": request.tools,
                },
                "_agentharness_verification_policy": (
                    request.verification.model_dump(mode="json")
                    if request.verification is not None
                    else None
                ),
            },
        )
        if parent_run_id:
            self._ctx(parent_run_id).child_runs.append(run_id)
            parent = self.storage.get_run(parent_run_id)
            if parent:
                self.events.emit_and_update(
                    parent_run_id,
                    events=[
                        self.events.event(
                            parent,
                            EventType.child_run_started,
                            {
                                "child_run_id": run_id,
                                "parent_tool_call_id": request.metadata.get(
                                    "parent_tool_call_id"
                                ),
                                "actor": request.metadata.get("actor", "delegate"),
                                "depth": request.delegate_depth,
                                "status": RunStatus.running.value,
                            },
                        )
                    ],
                )
        elif is_top_level:
            # Top-level dialogue updates session sort order; delegates do not.
            self.storage.update_session(session_id, touch=True)

        cancel = self.get_cancel_event(run_id)
        ctx = self._ctx(run_id)
        ctx.completed_tool_ids = set()
        ctx.pending_tool_calls = []
        self._activate_run(run_id)
        try:
            self._start_run_lease(run_id)
        except Exception:
            self._deactivate_run(run_id)
            await self._cleanup_run_state(run_id)
            raise

        # Multi-turn context: load completed top-level history when session already exists.
        # Resume uses its own checkpoint path and must not call this splice.
        # Delegate child runs start with only their own task message.
        messages: list[Message] = []
        if is_top_level and existing_session:
            messages = list(self.storage.get_session_history_messages(session_id))
        user_msg = Message(role=MessageRole.user, content=request.message)
        messages.append(user_msg)
        # Persist only this run's user message (history messages belong to prior runs).
        self.storage.save_message(run_id, session_id, user_msg, seq=0)
        # Register early so interrupt/cancel before _loop still keeps multi-turn context.
        ctx.run_messages = messages

        run_row = self.storage.get_run(run_id)
        assert run_row is not None
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.run_started,
                    {
                        "message": self.redactor.redact_text(request.message)[:500],
                        "provider": request.provider,
                        "model": request.model,
                    },
                )
            ],
        )

        self.lifecycle.checkpoint(
            run_id,
            phase="model_turn",
            step=0,
            messages=messages,
            pending=[],
            completed=set(),
            usage=Usage(),
        )

        stop_watcher = asyncio.create_task(self._watch_stop_request(run_id, cancel))
        try:
            remaining = request.budget.max_wall_time_s - (
                time.monotonic() - wall_started
            )
            async with asyncio.timeout(max(0.0, remaining)):
                return await self._loop(
                    run_id=run_id,
                    session_id=session_id,
                    root_run_id=root_run_id,
                    parent_run_id=parent_run_id,
                    request=request,
                    messages=messages,
                    step=0,
                    usage=Usage(),
                    completed_tool_ids=ctx.completed_tool_ids,
                    cancel=cancel,
                )
        except TimeoutError:
            return await self._finish_wall_timeout(
                run_id,
                session_id,
                root_run_id,
                parent_run_id,
            )
        except asyncio.CancelledError:
            # Kill shell trees + children; preserve completed tools for resume
            await self.interrupt(run_id, "cancelled")
            self.lifecycle.mark_interrupted(run_id, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            self.lifecycle.mark_failed(run_id, str(exc))
            return RunResult(
                run_id=run_id,
                session_id=session_id,
                status=RunStatus.failed,
                error=str(exc),
                parent_run_id=parent_run_id,
                root_run_id=root_run_id,
            )
        finally:
            stop_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await stop_watcher
            if parent_run_id:
                parent = self.storage.get_run(parent_run_id)
                child = self.storage.get_run(run_id)
                if parent and child:
                    self.events.emit_and_update(
                        parent_run_id,
                        events=[
                            self.events.event(
                                parent,
                                EventType.child_run_ended,
                                {
                                    "child_run_id": run_id,
                                    "parent_tool_call_id": request.metadata.get(
                                        "parent_tool_call_id"
                                    ),
                                    "actor": request.metadata.get("actor", "delegate"),
                                    "depth": request.delegate_depth,
                                    "status": child["status"],
                                },
                            )
                        ],
                    )
            self._deactivate_run(run_id)
            await self._cleanup_run_state(run_id)

    async def resume(self, run_id: str, input: str | None = None) -> RunResult:
        wall_started = time.monotonic()
        run = self.storage.get_run(run_id)
        if not run:
            raise KeyError(f"run not found: {run_id}")
        status = RunStatus(run["status"])
        if run_id in self._active_run_ids:
            raise RuntimeError(f"run {run_id} is active and cannot be resumed")
        if status not in RESUMABLE_STATUSES:
            allowed = ", ".join(sorted(item.value for item in RESUMABLE_STATUSES))
            raise RuntimeError(
                f"run {run_id} is not resumable from status {status.value}; "
                f"allowed statuses: {allowed}"
            )
        cp = self.storage.load_checkpoint(run_id)
        if not cp:
            raise RuntimeError(f"no checkpoint for run {run_id}")

        cancel = self.get_cancel_event(run_id)
        cancel.clear()
        self.storage.clear_stop_request(run_id)
        stored_metadata = json.loads(run.get("metadata_json") or "{}")
        stored_budget = stored_metadata.pop("_agentharness_budget", None)
        stored_provider_retry = stored_metadata.pop("_agentharness_provider_retry", None)
        # Discard routing metadata written by older multi-provider releases.
        for key in tuple(stored_metadata):
            if key.startswith("_agentharness_provider_") and key != "_agentharness_provider_retry":
                stored_metadata.pop(key, None)
        stored_pricing = stored_metadata.pop("_agentharness_pricing", None)
        stored_shell = stored_metadata.pop("_agentharness_shell", None)
        stored_context_request = stored_metadata.pop("_agentharness_context_request", {})
        stored_verification = stored_metadata.pop("_agentharness_verification_policy", None)
        if not isinstance(stored_context_request, dict):
            stored_context_request = {}
        provider_name = run.get("provider") or "openai"
        if provider_name not in self.providers:
            raise RuntimeError(
                f"run {run_id} uses unavailable provider {provider_name!r}; "
                "create a new run with the configured OpenAI provider"
            )
        request = RunRequest(
            message=input or "",
            session_id=run["session_id"],
            provider=provider_name,
            model=run.get("model"),
            approval=ApprovalMode(run.get("approval") or "ask"),
            cwd=run.get("cwd"),
            parent_run_id=run.get("parent_run_id"),
            root_run_id=run.get("root_run_id"),
            delegate_depth=int(run.get("delegate_depth") or 0),
            allow_write=bool(run.get("allow_write", 1)),
            system=stored_context_request.get("system"),
            extra_dirs=list(stored_context_request.get("extra_dirs") or []),
            skills_dirs=list(stored_context_request.get("skills_dirs") or []),
            tools=stored_context_request.get("tools"),
            verification=(
                VerificationPolicy.model_validate(stored_verification)
                if stored_verification
                else None
            ),
            metadata=stored_metadata,
            budget=BudgetConfig.model_validate(stored_budget or {}),
            provider_retry=ProviderRetryConfig.model_validate(stored_provider_retry or {}),
            pricing=PricingConfig.model_validate(stored_pricing or {}),
            shell=ShellExecutionConfig.model_validate(stored_shell or {}),
        )
        messages = list(cp.messages)
        if input:
            msg = Message(role=MessageRole.user, content=input)
            messages.append(msg)
            self.storage.save_message(run_id, run["session_id"], msg, seq=len(messages))

        completed = set(cp.completed_tool_call_ids)
        # If interrupted mid tool batch, only re-run incomplete tool calls
        pending = [
            tc for tc in cp.pending_tool_calls if not tool_call_completed(tc, completed)
        ]

        self._activate_run(run_id)
        try:
            self._start_run_lease(run_id)
        except Exception:
            self._deactivate_run(run_id)
            await self._cleanup_run_state(run_id)
            raise
        resume_ctx = self._ctx(run_id)
        resume_ctx.run_messages = messages
        resume_ctx.completed_tool_ids = completed
        resume_ctx.pending_tool_calls = list(pending)
        resume_ctx.tool_call_count = len(self.storage.list_tool_invocations(run_id))
        raw_context_state = cp.metadata.get("context_state")
        if raw_context_state:
            resume_ctx.context_state = ContextState.model_validate(raw_context_state)
        resume_ctx.verification_attempt = int(cp.metadata.get("verification_attempt") or 0)
        if stored_context_request.get("original_goal"):
            request.metadata["_agentharness_original_goal"] = stored_context_request[
                "original_goal"
            ]

        self.storage.update_run(
            run_id,
            status=RunStatus.running,
            clear_error=True,
            clear_finished_at=True,
        )
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(run, EventType.run_status, {"status": "running", "resumed": True})
            ],
        )

        stop_watcher = asyncio.create_task(self._watch_stop_request(run_id, cancel))
        try:
            remaining = request.budget.max_wall_time_s - (
                time.monotonic() - wall_started
            )
            async with asyncio.timeout(max(0.0, remaining)):
                if pending and cp.phase in ("tool_batch", "waiting_approval"):
                    # Continue pending tools first
                    await self._execute_tools(
                        run_id=run_id,
                        session_id=run["session_id"],
                        root_run_id=run["root_run_id"],
                        parent_run_id=run.get("parent_run_id"),
                        request=request,
                        messages=messages,
                        tool_calls=pending,
                        completed_tool_ids=completed,
                        usage=cp.usage,
                        step=cp.step,
                        cancel=cancel,
                    )
                    if resume_ctx.indeterminate_reason:
                        return self.lifecycle.finish(
                            run_id,
                            run["session_id"],
                            run["root_run_id"],
                            run.get("parent_run_id"),
                            RunStatus.require_human,
                            cp.partial_text,
                            cp.usage,
                            cp.step,
                            resume_ctx.indeterminate_reason,
                            messages=messages,
                        )

                return await self._loop(
                    run_id=run_id,
                    session_id=run["session_id"],
                    root_run_id=run["root_run_id"],
                    parent_run_id=run.get("parent_run_id"),
                    request=request,
                    messages=messages,
                    step=cp.step + (1 if pending else 0),
                    usage=cp.usage,
                    completed_tool_ids=completed,
                    cancel=cancel,
                )
        except TimeoutError:
            return await self._finish_wall_timeout(
                run_id,
                run["session_id"],
                run["root_run_id"],
                run.get("parent_run_id"),
            )
        except asyncio.CancelledError:
            await self.interrupt(run_id, "cancelled")
            self.lifecycle.mark_interrupted(run_id, "cancelled")
            raise
        finally:
            stop_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await stop_watcher
            self._deactivate_run(run_id)
            await self._cleanup_run_state(run_id)

    def resolve_indeterminate_tool(
        self,
        invocation_id: str,
        decision: ToolRecoveryDecision | str,
        *,
        arguments_sha256: str,
    ) -> ToolInvocationRecord:
        decision = ToolRecoveryDecision(decision)
        invocation = self.storage.get_tool_invocation(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        run = self.storage.get_run(invocation.run_id)
        if run is None:
            raise KeyError(invocation.run_id)
        if invocation.run_id in self._active_run_ids:
            raise RuntimeError("run is active; wait for it to require human review")
        if RunStatus(run["status"]) != RunStatus.require_human:
            raise RuntimeError("run is not waiting for human recovery")
        if invocation.status != ToolInvocationStatus.indeterminate:
            raise RuntimeError("tool invocation is not indeterminate")
        if invocation.arguments_sha256 != arguments_sha256:
            raise RuntimeError("tool recovery parameters do not match the invocation")

        now = datetime.now(UTC)
        if decision == ToolRecoveryDecision.mark_succeeded:
            result = ToolResult(
                tool_call_id=invocation.provider_call_id,
                invocation_id=invocation.id,
                name=invocation.tool_name,
                content="Human confirmed that the external operation completed",
                attempts=invocation.attempt_count,
                recovery_hint="Resume the run to continue with the confirmed result.",
            )
            updated = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.succeeded,
                    "result": result,
                    "error_code": None,
                    "error_category": None,
                    "updated_at": now,
                    "finished_at": now,
                }
            )
        elif decision == ToolRecoveryDecision.skip:
            result = ToolResult(
                tool_call_id=invocation.provider_call_id,
                invocation_id=invocation.id,
                name=invocation.tool_name,
                content="Human skipped the operation after reviewing its external state",
                is_error=True,
                error_code="outcome_skipped",
                error_category="recovery",
                retryable=False,
                recovery_hint="Continue the run without repeating this operation.",
                attempts=invocation.attempt_count,
            )
            updated = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.failed,
                    "result": result,
                    "error_code": result.error_code,
                    "error_category": result.error_category,
                    "updated_at": now,
                    "finished_at": now,
                }
            )
        else:
            updated = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.received,
                    "result": None,
                    "error_code": None,
                    "error_category": None,
                    "approval_id": None,
                    "updated_at": now,
                    "finished_at": None,
                }
            )

        if not self.storage.resolve_indeterminate_tool_invocation(
            updated,
            expected_arguments_sha256=arguments_sha256,
        ):
            raise RuntimeError("tool recovery was already resolved or became stale")
        self.events.emit_and_update(
            invocation.run_id,
            events=[
                self.events.event(
                    run,
                    EventType.tool_recovery_resolved,
                    {
                        "invocation_id": invocation.id,
                        "tool_call_id": invocation.provider_call_id,
                        "name": invocation.tool_name,
                        "decision": decision.value,
                        "arguments_sha256": arguments_sha256,
                    },
                )
            ],
        )
        return updated

    async def _loop(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        messages: list[Message],
        step: int,
        usage: Usage,
        completed_tool_ids: set[str],
        cancel: asyncio.Event,
    ) -> RunResult:
        budget = request.budget or BudgetConfig()
        _update_usage_cost(usage, request.pricing)
        started = time.monotonic()
        if request.provider not in self.providers:
            raise RuntimeError(f"unknown provider: {request.provider}")

        output_parts: list[str] = []
        output_length = 0
        # Keep reference so interrupt/cancel checkpoints retain multi-turn context.
        self._ctx(run_id).run_messages = messages

        while True:
            tool_specs = self._tool_specs(request)
            if cancel.is_set():
                stop = self._stop_status(run_id)
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    stop, "".join(output_parts), usage, step, stop.value,
                    messages=messages,
                )
            if step >= budget.max_steps:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_steps exceeded",
                    messages=messages,
                )
            if time.monotonic() - started > budget.max_wall_time_s:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_wall_time exceeded",
                    messages=messages,
                )
            if usage.total_tokens >= budget.max_tokens:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_tokens exceeded",
                    messages=messages,
                )
            if budget.max_cost_usd is not None:
                if usage.estimated_cost_usd is None:
                    return self.lifecycle.finish(
                        run_id,
                        session_id,
                        root_run_id,
                        parent_run_id,
                        RunStatus.failed,
                        "".join(output_parts),
                        usage,
                        step,
                        "max_cost_usd requires known input/output pricing",
                        messages=messages,
                    )
                if usage.estimated_cost_usd >= budget.max_cost_usd:
                    return self.lifecycle.finish(
                        run_id,
                        session_id,
                        root_run_id,
                        parent_run_id,
                        RunStatus.failed,
                        "".join(output_parts),
                        usage,
                        step,
                        "max_cost_usd exceeded",
                        messages=messages,
                    )
            if output_length >= budget.max_output_length:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step,
                    "max_output_length exceeded",
                    messages=messages,
                )

            run_row = self.storage.get_run(run_id)
            assert run_row is not None
            # ContextPlanner is the sole seam for selection, stable prefix,
            # budgeting/compaction, and the manifest used by Provider + tests.
            bundle = self.context_planner.plan(
                run_id=run_id,
                request=request,
                messages=messages,
                tools=tool_specs,
                model_turn=step,
                state=self._ctx(run_id).context_state,
                max_tokens=budget.max_context_tokens,
            )
            self._ctx(run_id).context_state = bundle.state
            manifest_payload = bundle.manifest.model_dump(mode="json")
            manifest_artifact = self.storage.artifacts.put_json(
                manifest_payload,
                summary=f"Context manifest for model turn {step}",
            )
            manifest_artifact["id"] = self.storage.register_artifact(manifest_artifact)
            ctx_meta = {
                "token_estimate": bundle.manifest.total_tokens,
                "token_method": bundle.manifest.token_method,
                "compacted": bundle.manifest.compacted,
                "budget_tokens": bundle.manifest.budget_tokens,
                "prefix_fingerprint": bundle.manifest.prefix_fingerprint,
                "manifest_artifact_id": manifest_artifact["id"],
            }

            span_id = new_id()
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.context_manifest,
                        {
                            "step": step,
                            "artifact_id": manifest_artifact["id"],
                            "manifest": manifest_payload,
                        },
                        span_id=span_id,
                    ),
                    self.events.event(
                        run_row,
                        EventType.model_turn_start,
                        {
                            "step": step,
                            "context": ctx_meta,
                            "provider": request.provider,
                            "model": request.model,
                        },
                        span_id=span_id,
                    ),
                    self.events.event(
                        run_row,
                        EventType.span_start,
                        {"kind": "model", "step": step},
                        span_id=span_id,
                    ),
                ],
            )

            remaining_chars = budget.max_output_length - output_length
            output_token_limit = max(1, (remaining_chars + 3) // 4)
            text_parts: list[str] = []
            tool_acc: dict[str, ToolCall] = {}
            order: list[str] = []
            ended_tool_ids: set[str] = set()
            turn_usage = Usage()
            error_msg: str | None = None
            error_kind: str | None = None
            streamed_length = 0
            target_attempt = 1
            turn_usage_charged = False

            while True:
                provider = self.providers[request.provider]
                attempt_remaining = budget.max_tokens - usage.total_tokens
                if attempt_remaining <= 0:
                    error_msg = "max_tokens exceeded during provider retry"
                    error_kind = "budget"
                    break
                if budget.max_cost_usd is not None:
                    assert usage.estimated_cost_usd is not None
                    assert request.pricing.input_per_million_usd is not None
                    assert request.pricing.output_per_million_usd is not None
                    input_cost = (
                        bundle.manifest.total_tokens
                        * request.pricing.input_per_million_usd
                        / 1_000_000
                    )
                    remaining_cost = (
                        budget.max_cost_usd
                        - usage.estimated_cost_usd
                        - input_cost
                    )
                    if remaining_cost <= 0:
                        error_msg = "max_cost_usd exceeded before provider call"
                        error_kind = "budget"
                        break
                    if request.pricing.output_per_million_usd > 0:
                        cost_token_limit = int(
                            remaining_cost
                            * 1_000_000
                            / request.pricing.output_per_million_usd
                        )
                        attempt_remaining = min(attempt_remaining, cost_token_limit)
                        if attempt_remaining <= 0:
                            error_msg = "max_cost_usd output allowance exhausted"
                            error_kind = "budget"
                            break
                model_req = ModelRequest(
                    messages=bundle.messages,
                    tools=bundle.tools,
                    model=request.model,
                    system=bundle.system,
                    max_tokens=max(1, min(attempt_remaining, output_token_limit)),
                )
                text_parts = []
                tool_acc = {}
                order = []
                ended_tool_ids = set()
                turn_usage = Usage()
                turn_usage_charged = False
                error_msg = None
                error_kind = None
                streamed_length = 0
                had_provider_output = False
                stream: Any = None
                provider_owner = asyncio.current_task()
                if provider_owner is not None:
                    self._ctx(run_id).provider_owner_task = provider_owner
                try:
                    stream = provider.stream(model_req).__aiter__()
                    async for item in stream:
                        if cancel.is_set():
                            stop = self._stop_status(run_id)
                            error_msg = stop.value
                            error_kind = (
                                "cancelled"
                                if stop == RunStatus.cancelled
                                else "interrupted"
                            )
                            break
                        if item.type == StreamItemType.text_delta and item.text:
                            had_provider_output = True
                            available = (
                                budget.max_output_length
                                - output_length
                                - streamed_length
                            )
                            if available <= 0:
                                error_msg = "max_output_length exceeded"
                                error_kind = "budget"
                                break
                            chunk = item.text[:available]
                            text_parts.append(chunk)
                            streamed_length += len(chunk)
                            await self.events.buffer_delta(run_id, run_row, chunk, span_id)
                            if len(item.text) > available:
                                error_msg = "max_output_length exceeded"
                                error_kind = "budget"
                                break
                        elif item.type == StreamItemType.tool_call_start:
                            had_provider_output = True
                            tc_id = item.tool_call_id or new_id()
                            if tc_id in tool_acc:
                                error_msg = f"duplicate tool call id: {tc_id}"
                                error_kind = "provider_protocol"
                                break
                            if len(order) >= budget.max_tool_calls_per_turn:
                                error_msg = "max_tool_calls_per_turn exceeded"
                                error_kind = "budget"
                                break
                            if self._ctx(run_id).tool_call_count >= budget.max_tool_calls:
                                error_msg = "max_tool_calls exceeded"
                                error_kind = "budget"
                                break
                            tc = ToolCall(
                                id=tc_id,
                                name=item.tool_name or "",
                                arguments_raw="",
                                ordinal=len(order),
                            )
                            tool_acc[tc_id] = tc
                            order.append(tc_id)
                            self._ctx(run_id).tool_call_count += 1
                            self.events.emit_and_update(
                                run_id,
                                events=[
                                    self.events.event(
                                        run_row,
                                        EventType.tool_call_start,
                                        {
                                            "tool_call_id": tc_id,
                                            "invocation_id": tc.invocation_id,
                                            "name": tc.name,
                                            "ordinal": tc.ordinal,
                                        },
                                        span_id=new_id(),
                                        parent_span_id=span_id,
                                    )
                                ],
                            )
                        elif item.type == StreamItemType.tool_call_delta:
                            had_provider_output = True
                            tc_id = item.tool_call_id or ""
                            if tc_id in tool_acc and item.arguments_delta:
                                raw_size = len(tool_acc[tc_id].arguments_raw.encode("utf-8"))
                                raw_size += len(item.arguments_delta.encode("utf-8"))
                                if raw_size > budget.max_tool_argument_bytes:
                                    error_msg = "max_tool_argument_bytes exceeded"
                                    error_kind = "budget"
                                    break
                                tool_acc[tc_id].arguments_raw += item.arguments_delta
                                if item.tool_name:
                                    tool_acc[tc_id].name = item.tool_name
                        elif item.type == StreamItemType.tool_call_end:
                            had_provider_output = True
                            tc_id = item.tool_call_id or new_id()
                            if tc_id in ended_tool_ids:
                                error_msg = f"duplicate tool call end: {tc_id}"
                                error_kind = "provider_protocol"
                                break
                            if tc_id not in tool_acc:
                                if len(order) >= budget.max_tool_calls_per_turn:
                                    error_msg = "max_tool_calls_per_turn exceeded"
                                    error_kind = "budget"
                                    break
                                if self._ctx(run_id).tool_call_count >= budget.max_tool_calls:
                                    error_msg = "max_tool_calls exceeded"
                                    error_kind = "budget"
                                    break
                                tool_acc[tc_id] = ToolCall(
                                    id=tc_id,
                                    name=item.tool_name or "",
                                    arguments_raw="",
                                    ordinal=len(order),
                                )
                                order.append(tc_id)
                                self._ctx(run_id).tool_call_count += 1
                            tc = tool_acc[tc_id]
                            if item.tool_name:
                                tc.name = item.tool_name
                            if item.arguments is not None:
                                if len(canonical_arguments(item.arguments).encode("utf-8")) > budget.max_tool_argument_bytes:
                                    error_msg = "max_tool_argument_bytes exceeded"
                                    error_kind = "budget"
                                    break
                                tc.arguments = item.arguments
                            elif tc.arguments_raw:
                                try:
                                    tc.arguments = json.loads(tc.arguments_raw)
                                except json.JSONDecodeError:
                                    tc.arguments = {"_raw": tc.arguments_raw}
                            ended_tool_ids.add(tc_id)
                        elif item.type == StreamItemType.usage and item.usage:
                            turn_usage.input_tokens += item.usage.input_tokens
                            turn_usage.output_tokens += item.usage.output_tokens
                            turn_usage.total_tokens = (
                                turn_usage.input_tokens + turn_usage.output_tokens
                            )
                            turn_usage.estimated = item.usage.estimated
                        elif item.type == StreamItemType.error:
                            error_msg = item.error or "provider error"
                            error_kind = item.error_kind or "provider"
                            break
                        elif item.type == StreamItemType.done:
                            break
                except asyncio.CancelledError:
                    if not cancel.is_set():
                        raise
                    stop = self._stop_status(run_id)
                    error_msg = stop.value
                    error_kind = (
                        "cancelled" if stop == RunStatus.cancelled else "interrupted"
                    )
                except Exception as exc:  # noqa: BLE001
                    error_msg = str(exc)
                    error_kind = _provider_exception_kind(exc)
                finally:
                    owner_ctx = self._runs.get(run_id)
                    if owner_ctx is not None and owner_ctx.provider_owner_task is provider_owner:
                        owner_ctx.provider_owner_task = None
                    close_stream = getattr(stream, "aclose", None)
                    if callable(close_stream):
                        with suppress(Exception):
                            await cast(Callable[[], Awaitable[None]], close_stream)()

                attempt_input = turn_usage.input_tokens or bundle.manifest.total_tokens
                attempt_output = turn_usage.output_tokens or estimate_tokens(
                    "".join(text_parts)
                )
                usage.provider_attempts.append(
                    ProviderAttempt(
                        provider=self.redactor.redact_text(request.provider),
                        model=(
                            self.redactor.redact_text(request.model)
                            if request.model
                            else None
                        ),
                        attempt=target_attempt,
                        status="error" if error_msg else "completed",
                        error_kind=error_kind,
                        input_tokens=attempt_input,
                        output_tokens=attempt_output,
                        had_output=had_provider_output,
                        fallback=False,
                        estimated_cost_usd=_cost_usd(
                            attempt_input, attempt_output, request.pricing
                        ),
                    )
                )
                if error_msg is None:
                    break

                retryable = error_kind in _RETRYABLE_PROVIDER_ERRORS
                can_replay = not had_provider_output
                if (
                    can_replay
                    and retryable
                    and target_attempt <= request.provider_retry.max_retries
                ):
                    usage.input_tokens += attempt_input
                    usage.output_tokens += attempt_output
                    usage.total_tokens += attempt_input + attempt_output
                    usage.estimated = usage.estimated or not turn_usage.total_tokens
                    _update_usage_cost(usage, request.pricing)
                    turn_usage_charged = True
                    delay = _retry_delay(request.provider_retry, target_attempt)
                    self.events.emit_and_update(
                        run_id,
                        events=[
                            self.events.event(
                                run_row,
                                EventType.provider_retry,
                                {
                                    "provider": request.provider,
                                    "model": request.model,
                                    "attempt": target_attempt,
                                    "next_attempt": target_attempt + 1,
                                    "error_kind": error_kind,
                                    "delay_s": round(delay, 3),
                                },
                                span_id=span_id,
                            )
                        ],
                    )
                    target_attempt += 1
                    if delay:
                        await asyncio.sleep(delay)
                    continue

                break

            await self.events.flush_delta(run_id, run_row, span_id)

            unfinished_tool_ids = [
                tool_id for tool_id in order if tool_id not in ended_tool_ids
            ]
            if unfinished_tool_ids and error_msg is None:
                error_msg = (
                    "provider ended before tool arguments completed: "
                    + ", ".join(unfinished_tool_ids[:4])
                )
                error_kind = "provider_protocol"

            text = "".join(text_parts)
            if not turn_usage.total_tokens and not turn_usage_charged:
                # Deterministic estimate
                turn_usage = Usage(
                    input_tokens=bundle.manifest.total_tokens,
                    output_tokens=estimate_tokens(text),
                    total_tokens=0,
                    estimated=True,
                )
                turn_usage.total_tokens = turn_usage.input_tokens + turn_usage.output_tokens

            local_est = bundle.manifest.total_tokens
            if not turn_usage_charged:
                # Preserve raw provider numbers for inspector last_*; charge budget with
                # de-inflated billable counts so gateway token lies cannot fail real work.
                raw_in = turn_usage.input_tokens
                raw_out = turn_usage.output_tokens
                billable = billable_turn_usage(
                    provider_usage=turn_usage,
                    local_input_estimate=local_est,
                    output_text=text,
                )
                usage.input_tokens += billable.input_tokens
                usage.output_tokens += billable.output_tokens
                usage.total_tokens += billable.total_tokens
                usage.estimated = usage.estimated or billable.estimated
                usage.last_input_tokens = raw_in
                usage.last_output_tokens = raw_out
                usage.last_local_estimate = local_est
                _update_usage_cost(usage, request.pricing)
            usage.model_turns = step + 1

            tool_calls = [tool_acc[i] for i in order if i in tool_acc]

            if usage.total_tokens > budget.max_tokens and error_msg is None:
                # A provider may ignore the requested output cap. Persist its partial
                # response as evidence, but never report an over-budget run as complete.
                error_msg = "max_tokens exceeded"
                error_kind = "budget"
            if (
                budget.max_cost_usd is not None
                and usage.estimated_cost_usd is not None
                and usage.estimated_cost_usd > budget.max_cost_usd
                and error_msg is None
            ):
                error_msg = "max_cost_usd exceeded"
                error_kind = "budget"

            assistant_msg = Message(
                role=MessageRole.assistant,
                content=text,
                tool_calls=tool_calls or None,
            )
            messages.append(assistant_msg)
            self.storage.save_message(run_id, session_id, assistant_msg, seq=len(messages))
            if text:
                output_parts.append(text)
                output_length += len(text)

            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.model_turn_end,
                        {
                            "step": step,
                            "text_len": len(text),
                            "tool_calls": [tc.name for tc in tool_calls],
                            "usage": turn_usage.model_dump(),
                            "provider": request.provider,
                            "model": request.model,
                            "provider_attempts": target_attempt,
                        },
                        span_id=span_id,
                    ),
                    self.events.event(
                        run_row,
                        EventType.span_end,
                        {"kind": "model", "step": step},
                        span_id=span_id,
                    ),
                ],
            )

            # Checkpoint after model turn
            self.lifecycle.checkpoint(
                run_id,
                phase="model_turn",
                step=step,
                messages=messages,
                pending=tool_calls,
                completed=completed_tool_ids,
                usage=usage,
            )

            if error_msg:
                if error_kind in ("cancelled", "interrupted"):
                    status = self._stop_status(run_id)
                elif error_kind == "timeout":
                    status = RunStatus.interrupted
                else:
                    status = RunStatus.failed
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    status, "".join(output_parts), usage, step, error_msg,
                    messages=messages,
                )

            if not tool_calls:
                policy = self._verification_policy(request)
                if policy is not None:
                    candidate_output = "".join(output_parts)
                    decision = await self._evaluate_candidate(
                        run_id=run_id,
                        session_id=session_id,
                        root_run_id=root_run_id,
                        parent_run_id=parent_run_id,
                        request=request,
                        messages=messages,
                        output=candidate_output,
                        usage=usage,
                        step=step + 1,
                        latency_s=time.monotonic() - started,
                        cancel=cancel,
                        model_span_id=span_id,
                    )
                    self._charge_verification_usage(usage, decision)
                    _update_usage_cost(usage, request.pricing)
                    if usage.total_tokens > budget.max_tokens:
                        return self.lifecycle.finish(
                            run_id,
                            session_id,
                            root_run_id,
                            parent_run_id,
                            RunStatus.failed,
                            candidate_output,
                            usage,
                            step + 1,
                            "max_tokens exceeded during verification",
                            messages=messages,
                        )
                    if (
                        budget.max_cost_usd is not None
                        and (
                            usage.estimated_cost_usd is None
                            or usage.estimated_cost_usd > budget.max_cost_usd
                        )
                    ):
                        return self.lifecycle.finish(
                            run_id,
                            session_id,
                            root_run_id,
                            parent_run_id,
                            RunStatus.failed,
                            candidate_output,
                            usage,
                            step + 1,
                            "max_cost_usd exceeded during verification",
                            messages=messages,
                        )
                    if decision.action == "retry" and decision.feedback_message is not None:
                        feedback = decision.feedback_message
                        messages.append(feedback)
                        self.storage.save_message(
                            run_id, session_id, feedback, seq=len(messages)
                        )
                        self._ctx(run_id).verification_attempt += 1
                        output_parts = []
                        output_length = 0
                        step += 1
                        self.lifecycle.checkpoint(
                            run_id,
                            phase="model_turn",
                            step=step,
                            messages=messages,
                            pending=[],
                            completed=completed_tool_ids,
                            usage=usage,
                        )
                        continue
                    if decision.action == "require_human":
                        return self._pause_for_verification_human(
                            run_id=run_id,
                            session_id=session_id,
                            root_run_id=root_run_id,
                            parent_run_id=parent_run_id,
                            output=candidate_output,
                            usage=usage,
                            steps=step + 1,
                            messages=messages,
                            decision=decision,
                        )
                    if decision.action == "stop":
                        error = "verification failed"
                        if decision.failures:
                            error += ": " + "; ".join(
                                failure.message for failure in decision.failures
                            )
                        return self.lifecycle.finish(
                            run_id,
                            session_id,
                            root_run_id,
                            parent_run_id,
                            RunStatus.failed,
                            candidate_output,
                            usage,
                            step + 1,
                            error,
                            messages=messages,
                        )
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.completed, "".join(output_parts), usage, step + 1, None,
                    messages=messages,
                )

            # Execute tools
            await self._execute_tools(
                run_id=run_id,
                session_id=session_id,
                root_run_id=root_run_id,
                parent_run_id=parent_run_id,
                request=request,
                messages=messages,
                tool_calls=tool_calls,
                completed_tool_ids=completed_tool_ids,
                usage=usage,
                step=step,
                cancel=cancel,
            )
            if self._ctx(run_id).indeterminate_reason:
                return self.lifecycle.finish(
                    run_id,
                    session_id,
                    root_run_id,
                    parent_run_id,
                    RunStatus.require_human,
                    "".join(output_parts),
                    usage,
                    step + 1,
                    self._ctx(run_id).indeterminate_reason,
                    messages=messages,
                )
            step += 1

            if output_length > budget.max_output_length:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step,
                    "max_output_length exceeded",
                    messages=messages,
                )

    def _verification_policy(self, request: RunRequest) -> VerificationPolicy | None:
        policy = request.verification
        if policy is None:
            raw = request.metadata.get("verification")
            if isinstance(raw, dict):
                policy = VerificationPolicy.model_validate(raw)
        if policy is None:
            return None
        output_assertions = request.metadata.get("verification_assertions")
        if isinstance(output_assertions, dict) and not any(
            check.kind == "output" for check in policy.validators
        ):
            policy = policy.model_copy(
                update={
                    "validators": [
                        VerificationCheck(kind="output", assertions=output_assertions),
                        *policy.validators,
                    ]
                }
            )
        return policy

    async def _evaluate_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        messages: list[Message],
        output: str,
        usage: Usage,
        step: int,
        latency_s: float,
        cancel: asyncio.Event,
        model_span_id: str,
    ) -> VerificationDecision:
        policy = self._verification_policy(request)
        assert policy is not None
        run_row = self.storage.get_run(run_id)
        assert run_row is not None
        attempt = self._ctx(run_id).verification_attempt
        verification_span_id = new_id()
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.verification_started,
                    {
                        "attempt": attempt,
                        "step": step,
                        "validators": [check.kind for check in policy.validators],
                        "max_retries": policy.max_retries,
                    },
                    span_id=verification_span_id,
                    parent_span_id=model_span_id,
                )
            ],
        )

        existing_ordinals = [
            invocation.ordinal
            for invocation in self.storage.list_tool_invocations(run_id)
            if invocation.step == step
        ]
        next_verification_ordinal = max(existing_ordinals, default=-1) + 1
        command_calls_this_turn = 0

        async def governed_command(
            candidate: VerificationCandidate, command: str
        ) -> ToolResult:
            nonlocal command_calls_this_turn, next_verification_ordinal
            if request.tools is not None and "shell" not in request.tools:
                return ToolResult(
                    tool_call_id="",
                    name="shell",
                    content="Shell tool is disabled for this run",
                    is_error=True,
                    error_code="tool_disabled",
                    error_category="configuration",
                    retryable=False,
                    recovery_hint="Enable shell explicitly or use file/deterministic validation.",
                )
            run_context = self._ctx(run_id)
            if command_calls_this_turn >= request.budget.max_tool_calls_per_turn:
                return ToolResult(
                    tool_call_id="",
                    name="shell",
                    content="Verification command budget for this turn was exceeded",
                    is_error=True,
                    error_code="max_tool_calls_per_turn",
                    error_category="budget",
                    retryable=False,
                    recovery_hint="Reduce command validators or split verification across runs.",
                    attempts=0,
                )
            if run_context.tool_call_count >= request.budget.max_tool_calls:
                return ToolResult(
                    tool_call_id="",
                    name="shell",
                    content="Run tool-call budget was exceeded during verification",
                    is_error=True,
                    error_code="max_tool_calls",
                    error_category="budget",
                    retryable=False,
                    recovery_hint="Start a new run with a smaller verification policy.",
                    attempts=0,
                )
            ordinal = next_verification_ordinal
            next_verification_ordinal += 1
            command_calls_this_turn += 1
            run_context.tool_call_count += 1
            call = ToolCall(
                id=new_id(),
                name="shell",
                arguments={"command": command},
                arguments_raw=json.dumps({"command": command}, ensure_ascii=False),
                ordinal=ordinal,
            )
            call_message = Message(
                role=MessageRole.assistant,
                content="[verification command validator]",
                tool_calls=[call],
            )
            messages.append(call_message)
            self.storage.save_message(run_id, session_id, call_message, seq=len(messages))
            results = await self._execute_tools(
                run_id=run_id,
                session_id=session_id,
                root_run_id=root_run_id,
                parent_run_id=parent_run_id,
                request=request,
                messages=messages,
                tool_calls=[call],
                completed_tool_ids=self._ctx(run_id).completed_tool_ids,
                usage=usage,
                step=step,
                cancel=cancel,
            )
            if results:
                return results[0]
            return ToolResult(
                tool_call_id=call.id,
                name="shell",
                content="Verification command was cancelled",
                is_error=True,
                error_code="cancelled",
                error_category="cancellation",
                retryable=True,
                recovery_hint="Resume the run and retry verification.",
            )

        tools_ordered = [
            call.name
            for message in messages
            for call in (message.tool_calls or [])
            if message.content != "[verification command validator]"
        ]
        candidate = VerificationCandidate(
            run_id=run_id,
            goal=str(
                request.metadata.get("_agentharness_original_goal") or request.message
            ),
            output=output,
            cwd=request.cwd or ".",
            extra_dirs=request.extra_dirs,
            usage=usage,
            steps=step,
            latency_s=latency_s,
            tools_ordered=tools_ordered,
            messages=messages,
            output_assertions=(
                request.metadata.get("verification_assertions")
                if isinstance(request.metadata.get("verification_assertions"), dict)
                else None
            ),
            executor_provider=request.provider,
            executor_adapter=self.providers.get(request.provider),
            cancel_event=cancel,
        )
        loop = VerificationLoop(
            redactor=self.redactor,
            command_runner=governed_command,
            evaluator_resolver=lambda name: self.providers.get(name),
        )
        decision = await loop.evaluate(candidate, policy, attempt=attempt)
        safe_decision = self.redactor.redact_obj(decision.model_dump(mode="json"))
        events = [
            self.events.event(
                run_row,
                EventType.verification_result,
                {
                    "attempt": attempt,
                    "step": step,
                    "action": decision.action,
                    "failures": safe_decision.get("failures", []),
                    "evidence": safe_decision.get("evidence", {}),
                },
                span_id=verification_span_id,
                parent_span_id=model_span_id,
            )
        ]
        if decision.feedback:
            events.append(
                self.events.event(
                    run_row,
                    EventType.verification_feedback,
                    {
                        "attempt": attempt,
                        "action": decision.action,
                        "feedback": self.redactor.redact_text(decision.feedback),
                    },
                    span_id=verification_span_id,
                    parent_span_id=model_span_id,
                )
            )
        self.events.emit_and_update(run_id, events=events)
        return decision

    @staticmethod
    def _charge_verification_usage(usage: Usage, decision: VerificationDecision) -> None:
        def visit(value: Any) -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            if isinstance(value, dict):
                raw_usage = value.get("usage")
                if isinstance(raw_usage, dict):
                    found.append(raw_usage)
                for item in value.values():
                    found.extend(visit(item))
            elif isinstance(value, list):
                for item in value:
                    found.extend(visit(item))
            return found

        for raw in visit(decision.evidence):
            input_tokens = int(raw.get("input_tokens") or 0)
            output_tokens = int(raw.get("output_tokens") or 0)
            total_tokens = int(raw.get("total_tokens") or input_tokens + output_tokens)
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.total_tokens += total_tokens

    def _pause_for_verification_human(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        output: str,
        usage: Usage,
        steps: int,
        messages: list[Message],
        decision: VerificationDecision,
    ) -> RunResult:
        reason = "verification requires human review"
        if decision.failures:
            reason += ": " + "; ".join(failure.message for failure in decision.failures)
        self.lifecycle.checkpoint(
            run_id,
            phase="model_turn",
            step=steps,
            messages=messages,
            pending=[],
            completed=self._ctx(run_id).completed_tool_ids,
            usage=usage,
            status=RunStatus.require_human,
        )
        run = self.storage.get_run(run_id)
        if run:
            self.events.emit_and_update(
                run_id,
                status=RunStatus.require_human,
                error=reason,
                output_summary=output[:2000],
                usage=usage,
                steps=steps,
                events=[
                    self.events.event(
                        run,
                        EventType.run_status,
                        {"status": RunStatus.require_human.value, "reason": reason},
                    )
                ],
            )
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            status=RunStatus.require_human,
            output=self.redactor.redact_text(output),
            error=self.redactor.redact_text(reason),
            usage=usage,
            steps=steps,
            parent_run_id=parent_run_id,
            root_run_id=root_run_id,
        )

    async def _execute_tools(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        messages: list[Message],
        tool_calls: list[ToolCall],
        completed_tool_ids: set[str],
        usage: Usage,
        step: int,
        cancel: asyncio.Event,
    ) -> list[ToolResult]:
        run_row = self.storage.get_run(run_id)
        assert run_row is not None
        collected_results: list[ToolResult] = []

        # Filter already completed (resume safety)
        pending = [
            tc for tc in tool_calls if not tool_call_completed(tc, completed_tool_ids)
        ]
        if not pending:
            return collected_results

        batch_ctx = self._ctx(run_id)
        batch_ctx.completed_tool_ids = set(completed_tool_ids)
        batch_ctx.pending_tool_calls = list(pending)

        self.lifecycle.checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=pending,
            completed=completed_tool_ids,
            usage=usage,
        )

        # Approval gating
        allowed: list[tuple[ToolCall, ToolInvocationRecord]] = []
        recovering_invocations: set[str] = set()
        for tc in pending:
            tool = self.tools.get(tc.name)
            if tool is None:
                invocation = ToolInvocationRecord(
                    id=tc.invocation_id,
                    run_id=run_id,
                    session_id=session_id,
                    step=step,
                    ordinal=tc.ordinal,
                    provider_call_id=tc.id,
                    tool_name=tc.name,
                    status=ToolInvocationStatus.failed,
                    effect=EffectKind.destructive,
                    replay_policy=ReplayPolicy.never,
                    arguments=tc.arguments,
                    arguments_sha256=arguments_sha256(tc.arguments),
                    attempt_count=0,
                    error_code="unknown_tool",
                    error_category="configuration",
                    finished_at=datetime.now(UTC),
                )
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=f"Unknown tool: {tc.name}",
                    is_error=True,
                    error_code="unknown_tool",
                    error_category="configuration",
                    retryable=False,
                    recovery_hint="Choose one of the enabled tool schemas.",
                    attempts=0,
                )
                invocation.result = result
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            effect = self._effect_for(tool, tc)
            spec = tool.spec
            replay_policy = self._replay_policy_for(tool, tc, effect)
            argument_hash = arguments_sha256(tc.arguments)
            invocation = self.storage.get_tool_invocation(tc.invocation_id)
            if invocation is None:
                invocation = ToolInvocationRecord(
                    id=tc.invocation_id,
                    run_id=run_id,
                    session_id=session_id,
                    step=step,
                    ordinal=tc.ordinal,
                    provider_call_id=tc.id,
                    tool_name=tc.name,
                    tool_version=spec.version,
                    status=ToolInvocationStatus.received,
                    effect=effect,
                    replay_policy=replay_policy,
                    arguments=tc.arguments,
                    arguments_sha256=argument_hash,
                )
                self.storage.save_tool_invocation(invocation)
            elif invocation.result is not None and invocation.status in (
                ToolInvocationStatus.succeeded,
                ToolInvocationStatus.failed,
            ):
                result = invocation.result
                existing_results = _tool_result_messages_for_call(messages, tc)
                if existing_results and not any(
                    message.tool_result == result for message in existing_results
                ):
                    self._drop_tool_result_messages(run_id, messages, tc)
                    existing_results = []
                if not existing_results:
                    self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue
            elif invocation.status == ToolInvocationStatus.indeterminate or (
                invocation.status == ToolInvocationStatus.running
                and invocation.replay_policy == ReplayPolicy.never
            ):
                self.storage.finish_running_tool_attempts(
                    invocation.id,
                    status="indeterminate",
                    error_code="outcome_indeterminate",
                    error_category="recovery",
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.indeterminate,
                        "error_code": "outcome_indeterminate",
                        "error_category": "recovery",
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._ctx(run_id).indeterminate_reason = (
                    f"Tool {tc.name} may have produced an external side effect before interruption"
                )
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.tool_execution_indeterminate,
                            {
                                "tool_call_id": tc.id,
                                "invocation_id": tc.invocation_id,
                                "name": tc.name,
                                "error_code": "outcome_indeterminate",
                            },
                        )
                    ],
                )
                self._drop_tool_result_messages(run_id, messages, tc)
                continue
            elif invocation.status in (
                ToolInvocationStatus.running,
                ToolInvocationStatus.cancelled,
            ):
                self._drop_tool_result_messages(run_id, messages, tc)
                recovering_invocations.add(invocation.id)

            validation_errors = validate_tool_arguments(spec, tc.arguments)
            if validation_errors:
                result = invalid_arguments_result(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    tool_name=tc.name,
                    errors=validation_errors,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.validated,
                    "effect": effect,
                    "replay_policy": replay_policy,
                    "arguments_sha256": argument_hash,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_call_validated,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "effect": effect.value,
                            "arguments_sha256": argument_hash,
                        },
                    )
                ],
            )
            requires_confirmation = bool(
                getattr(tool, "requires_confirmation", False)
            ) or effect == EffectKind.destructive
            # Child runs default readonly — block write effects without grant
            if not request.allow_write and effect in (
                EffectKind.workspace_write,
                EffectKind.destructive,
            ):
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Write permission not granted for this run",
                    is_error=True,
                    error_code="write_not_allowed",
                    error_category="permission",
                    retryable=False,
                    recovery_hint="Request a writable run or use read-only verification.",
                    attempts=0,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            decision = (
                ApprovalDecision.deny
                if requires_confirmation and request.approval == ApprovalMode.never
                else None
                if requires_confirmation
                else auto_decision(effect, request.approval)
            )
            # Run-level allow list
            run_ctx = self._runs.get(run_id)
            scope = approval_scope(tc.name, effect, tc.arguments)
            if (
                not requires_confirmation
                and decision is None
                and run_ctx is not None
                and scope in run_ctx.approval_scopes
            ):
                decision = ApprovalDecision.allow_once

            if decision is None:
                # Interactive approval
                apr = ApprovalRequest(
                    run_id=run_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    invocation_id=tc.invocation_id,
                    tool_version=spec.version,
                    effect=effect,
                    arguments_summary=self.redactor.redact_text(
                        json.dumps(tc.arguments, ensure_ascii=False)[:500]
                    ),
                    arguments_sha256=argument_hash,
                    approval_scope=scope,
                    requires_confirmation=requires_confirmation,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.waiting_approval,
                        "approval_id": apr.id,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self.storage.save_approval(apr.model_dump(mode="json"))
                self.storage.update_run(run_id, status=RunStatus.waiting_approval)
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.approval_requested,
                            {
                                "approval_id": apr.id,
                                "tool_call_id": tc.id,
                                "invocation_id": tc.invocation_id,
                                "tool": tc.name,
                                "effect": effect.value,
                                "arguments_summary": apr.arguments_summary,
                                "requires_confirmation": requires_confirmation,
                                "arguments_sha256": argument_hash,
                                "approval_scope": scope,
                            },
                        )
                    ],
                )
                self.lifecycle.checkpoint(
                    run_id,
                    phase="waiting_approval",
                    step=step,
                    messages=messages,
                    pending=pending,
                    completed=completed_tool_ids,
                    usage=usage,
                    status=RunStatus.waiting_approval,
                    approval_token=apr.id,
                )
                if self.approval_callback is None:
                    decision = ApprovalDecision.deny
                    cancelled_while_waiting = False
                else:
                    approval_task = asyncio.ensure_future(self.approval_callback(apr))
                    cancel_waiter = asyncio.create_task(cancel.wait())
                    try:
                        done, _ = await asyncio.wait(
                            {approval_task, cancel_waiter},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        cancelled_while_waiting = cancel_waiter in done
                        if cancelled_while_waiting:
                            approval_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await approval_task
                            decision = ApprovalDecision.deny
                        else:
                            try:
                                decision = approval_task.result()
                            except Exception:  # noqa: BLE001
                                decision = ApprovalDecision.deny
                    finally:
                        cancel_waiter.cancel()
                        with suppress(asyncio.CancelledError):
                            await cancel_waiter
                if requires_confirmation and decision == ApprovalDecision.allow_run:
                    decision = ApprovalDecision.allow_once
                apr.decision = decision
                self.storage.save_approval(
                    {
                        **apr.model_dump(mode="json"),
                        "decision": decision.value,
                        "resolved_at": datetime.now(UTC).isoformat(),
                    }
                )
                if not cancelled_while_waiting:
                    self.storage.update_run(run_id, status=RunStatus.running)
                self.events.emit_and_update(
                    run_id,
                    events=[
                        self.events.event(
                            run_row,
                            EventType.approval_resolved,
                            {
                                "approval_id": apr.id,
                                "tool_call_id": tc.id,
                                "decision": decision.value,
                                "tool": tc.name,
                            },
                        )
                    ],
                )
                if decision == ApprovalDecision.allow_run and not requires_confirmation:
                    self._ctx(run_id).approval_scopes.add(scope)
                if cancelled_while_waiting:
                    return collected_results

            if decision == ApprovalDecision.deny:
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Approval denied",
                    is_error=True,
                    error_code="approval_denied",
                    error_category="approval",
                    retryable=False,
                    recovery_hint="Ask a human to approve this governed action.",
                    attempts=0,
                )
                invocation = invocation.model_copy(
                    update={
                        "status": ToolInvocationStatus.failed,
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                collected_results.append(result)
                completed_tool_ids.add(tc.invocation_id)
                continue

            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.approved,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            allowed.append((tc, invocation))

        async def make_runner(
            tc: ToolCall, invocation: ToolInvocationRecord
        ) -> ToolResult:
            return await self._run_tool_invocation(
                run_id=run_id,
                session_id=session_id,
                root_run_id=root_run_id,
                parent_run_id=parent_run_id,
                request=request,
                usage=usage,
                cancel=cancel,
                run_row=run_row,
                tc=tc,
                invocation=invocation,
                recovering_invocations=recovering_invocations,
            )

        # Batch with concurrency rules (scheduler wraps make_runner once)
        items: list[tuple[Any, ...]] = []
        for tc, invocation in allowed:
            tool = self.tools[tc.name]
            effect = self._effect_for(tool, tc)
            browser_id = self._resolve_browser_context_id(tc)
            parallel_safe = self._parallel_safe_for(tool, tc, effect)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_execution_queued,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "effect": effect.value,
                            "parallel_safe": parallel_safe,
                        },
                    )
                ],
            )
            items.append(
                (
                    effect,
                    lambda tc=tc, invocation=invocation: make_runner(tc, invocation),
                    browser_id,
                    parallel_safe,
                )
            )

        if items:
            results = await self.scheduler.run_batch(
                items, max_concurrency=request.budget.max_concurrent_tools
            )
            collected_results.extend(results)
            for result in results:
                # Cancel/interrupt mid-flight: error results are incomplete — keep pending
                # so resume re-runs them. Successful tools (and non-cancel failures) complete.
                saved = (
                    self.storage.get_tool_invocation(result.invocation_id)
                    if result.invocation_id
                    else None
                )
                incomplete = (
                    saved is not None
                    and saved.status == ToolInvocationStatus.indeterminate
                ) or (cancel.is_set() and result.is_error)
                if incomplete:
                    continue
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                completed_id = result.invocation_id or result.tool_call_id
                completed_tool_ids.add(completed_id)
                self._ctx(run_id).completed_tool_ids.add(completed_id)

        # Drop finished tools from pending; incomplete stay for resume
        batch_ctx = self._ctx(run_id)
        batch_ctx.pending_tool_calls = [
            tc for tc in pending if not tool_call_completed(tc, completed_tool_ids)
        ]
        batch_ctx.completed_tool_ids = set(completed_tool_ids)

        self.lifecycle.checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=batch_ctx.pending_tool_calls,
            completed=completed_tool_ids,
            usage=usage,
        )
        return collected_results

    async def _run_tool_invocation(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        request: RunRequest,
        usage: Usage,
        cancel: asyncio.Event,
        run_row: dict[str, Any],
        tc: ToolCall,
        invocation: ToolInvocationRecord,
        recovering_invocations: set[str],
    ) -> ToolResult:
        """Execute one tool. Concurrency is applied by EffectScheduler outside —
        do not nest scheduler.run here (asyncio.Lock is not reentrant)."""
        if cancel.is_set():
            return ToolResult(
                tool_call_id=tc.id,
                invocation_id=tc.invocation_id,
                name=tc.name,
                content="cancelled",
                is_error=True,
                error_code="cancelled",
                error_category="cancellation",
                retryable=True,
                recovery_hint="Resume the run when cancellation is cleared.",
                attempts=0,
            )
        tool = self.tools[tc.name]
        spec = tool.spec
        span_id = new_id()
        t0 = time.monotonic()
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.tool_execution_started,
                    {
                        "kind": "tool",
                        "name": tc.name,
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        **(
                            {"executor": request.shell.executor}
                            if tc.name == "shell"
                            else {}
                        ),
                    },
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.span_start,
                    {
                        "kind": "tool",
                        "name": tc.name,
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                    },
                    span_id=span_id,
                ),
            ],
        )
        ctx = ToolContext(
            run_id=run_id,
            session_id=session_id,
            cwd=request.cwd or ".",
            extra_dirs=request.extra_dirs,
            data_dir=str(self.storage.data_dir),
            allow_write=request.allow_write,
            cancel_event=cancel,
            approval_mode=request.approval,
            shell=request.shell,
            metadata={
                "root_run_id": root_run_id,
                "parent_run_id": parent_run_id,
                "tool_call_id": tc.id,
                "delegate_depth": request.delegate_depth,
                "budget": request.budget.model_dump(),
                "pricing": request.pricing.model_dump(mode="json"),
                "shell": request.shell.model_dump(mode="json"),
                "provider": (
                    usage.provider_attempts[-1].provider
                    if usage.provider_attempts
                    else request.provider
                ),
                "model": (
                    usage.provider_attempts[-1].model
                    if usage.provider_attempts
                    else request.model
                ),
                "skills_dirs": request.skills_dirs,
            },
            harness=self.harness,
        )
        result = ToolResult(
            tool_call_id=tc.id,
            invocation_id=tc.invocation_id,
            name=tc.name,
            content="Tool did not run",
            is_error=True,
            error_code="tool_not_run",
            error_category="tool",
        )

        recovering = invocation.id in recovering_invocations
        if recovering:
            self.storage.finish_running_tool_attempts(
                invocation.id,
                status="interrupted",
                error_code="process_lost",
                error_category="recovery",
            )

        if (
            invocation.id in recovering_invocations
            and invocation.replay_policy == ReplayPolicy.reconcile
        ):
            reconciler = getattr(tool, "reconcile", None)
            if callable(reconciler):
                try:
                    reconciled = await self._invoke_reconciler(
                        reconciler,
                        ctx,
                        tc.arguments,
                        timeout_s=spec.timeout_s,
                        cancel=cancel,
                    )
                except TimeoutError:
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content=f"Reconciliation timed out after {spec.timeout_s:g}s",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
                except Exception as exc:  # noqa: BLE001 - fail closed on uncertain recovery
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content=f"Could not reconcile interrupted tool outcome: {exc}",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
            else:
                reconciled = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="Tool declares reconcile recovery but provides no reconciler",
                    is_error=True,
                    error_code="outcome_indeterminate",
                    error_category="recovery",
                    retryable=False,
                    recovery_hint="Inspect the target state before deciding whether to retry.",
                )
            if isinstance(reconciled, ToolResult):
                result = reconciled.model_copy(
                    update={
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "attempts": invocation.attempt_count,
                    }
                )
                reconciliation_failed = reconciled.is_error
                if reconciliation_failed:
                    self._ctx(run_id).indeterminate_reason = (
                        f"Could not reconcile the previous {tc.name} outcome"
                    )
                invocation = invocation.model_copy(
                    update={
                        "status": (
                            ToolInvocationStatus.indeterminate
                            if reconciliation_failed
                            else ToolInvocationStatus.succeeded
                        ),
                        "result": result,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "updated_at": datetime.now(UTC),
                        "finished_at": datetime.now(UTC),
                    }
                )
                self.storage.save_tool_invocation(invocation)
                return result

        max_attempts = spec.max_attempts
        first_attempt = invocation.attempt_count + 1
        for attempt_offset in range(max_attempts):
            attempt = first_attempt + attempt_offset
            attempt_started = time.monotonic()
            invocation = invocation.model_copy(
                update={
                    "status": ToolInvocationStatus.running,
                    "attempt_count": attempt,
                    "updated_at": datetime.now(UTC),
                    "started_at": invocation.started_at or datetime.now(UTC),
                }
            )
            self.storage.save_tool_invocation(invocation)
            attempt_id = self.storage.start_tool_attempt(invocation.id, attempt)
            try:
                async with asyncio.timeout(spec.timeout_s):
                    result = await tool.run(ctx, tc.arguments)
            except TimeoutError:
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=f"Tool timed out after {spec.timeout_s:g}s",
                    is_error=True,
                    error_code="tool_timeout",
                    error_category="timeout",
                    retryable=True,
                    recovery_hint="Retry only if the tool is safe or its outcome can be reconciled.",
                )
            except asyncio.CancelledError:
                if not cancel.is_set():
                    raise
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content="cancelled",
                    is_error=True,
                    error_code="cancelled",
                    error_category="cancellation",
                    retryable=True,
                )
            except Exception as exc:  # noqa: BLE001
                result = ToolResult(
                    tool_call_id=tc.id,
                    invocation_id=tc.invocation_id,
                    name=tc.name,
                    content=f"Tool error: {exc}",
                    is_error=True,
                    error_code="tool_exception",
                    error_category="tool",
                    retryable=False,
                    recovery_hint="Inspect the tool error before deciding whether to retry.",
                )

            result = result.model_copy(
                update={
                    "tool_call_id": tc.id,
                    "invocation_id": tc.invocation_id,
                    "name": tc.name,
                    "attempts": attempt,
                }
            )
            if (
                result.is_error
                and result.error_code in {"tool_timeout", "cancelled"}
                and invocation.replay_policy == ReplayPolicy.reconcile
            ):
                reconciler = getattr(tool, "reconcile", None)
                if callable(reconciler):
                    try:
                        reconciled = await self._invoke_reconciler(
                            reconciler,
                            ctx,
                            tc.arguments,
                            timeout_s=spec.timeout_s,
                            cancel=cancel,
                        )
                    except TimeoutError:
                        reconciled = ToolResult(
                            tool_call_id=tc.id,
                            invocation_id=tc.invocation_id,
                            name=tc.name,
                            content=f"Reconciliation timed out after {spec.timeout_s:g}s",
                            is_error=True,
                            error_code="outcome_indeterminate",
                            error_category="recovery",
                            retryable=False,
                            recovery_hint=(
                                "Inspect the target state before deciding whether to retry."
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 - uncertain side effect
                        reconciled = ToolResult(
                            tool_call_id=tc.id,
                            invocation_id=tc.invocation_id,
                            name=tc.name,
                            content=f"Could not reconcile timed out tool outcome: {exc}",
                            is_error=True,
                            error_code="outcome_indeterminate",
                            error_category="recovery",
                            retryable=False,
                            recovery_hint=(
                                "Inspect the target state before deciding whether to retry."
                            ),
                        )
                else:
                    reconciled = ToolResult(
                        tool_call_id=tc.id,
                        invocation_id=tc.invocation_id,
                        name=tc.name,
                        content="Tool declares reconcile recovery but provides no reconciler",
                        is_error=True,
                        error_code="outcome_indeterminate",
                        error_category="recovery",
                        retryable=False,
                        recovery_hint="Inspect the target state before deciding whether to retry.",
                    )
                if isinstance(reconciled, ToolResult):
                    result = reconciled.model_copy(
                        update={
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "attempts": attempt,
                            **(
                                {
                                    "error_code": "outcome_indeterminate",
                                    "error_category": "recovery",
                                    "retryable": False,
                                }
                                if reconciled.is_error
                                else {}
                            ),
                        }
                    )
                else:
                    result = result.model_copy(
                        update={
                            "retryable": False,
                            "recovery_hint": (
                                "Reconciliation found no completed side effect; retry explicitly."
                            ),
                        }
                    )
            if result.is_error and cancel.is_set() and not result.error_code:
                result = result.model_copy(
                    update={
                        "error_code": "cancelled",
                        "error_category": "cancellation",
                        "retryable": True,
                        "recovery_hint": result.recovery_hint
                        or "Resume the run to retry this incomplete tool call.",
                    }
                )
            if result.is_error and not result.error_code:
                result = result.model_copy(
                    update={
                        "error_code": "tool_failed",
                        "error_category": result.error_category or "tool",
                        "recovery_hint": result.recovery_hint
                        or "Inspect the tool result and retry with corrected arguments.",
                    }
                )
            attempt_duration = (time.monotonic() - attempt_started) * 1000
            self.storage.finish_tool_attempt(
                attempt_id,
                status="failed" if result.is_error else "succeeded",
                duration_ms=attempt_duration,
                error_code=result.error_code,
                error_category=result.error_category,
            )
            can_retry = (
                result.is_error
                and result.retryable
                and invocation.replay_policy == ReplayPolicy.safe
                and attempt_offset + 1 < max_attempts
                and not cancel.is_set()
            )
            if not can_retry:
                break
            delay = min(2.0, 0.25 * (2 ** (attempt - 1))) * random.uniform(0.5, 1.5)
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.tool_retry,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "attempt": attempt,
                            "delay_s": delay,
                            "error_code": result.error_code,
                        },
                        span_id=span_id,
                    )
                ],
            )
            await asyncio.sleep(delay)

        duration = (time.monotonic() - t0) * 1000
        result = result.model_copy(update={"duration_ms": duration})
        result = self._bound_tool_result(result, spec=spec, request=request)

        uncertain = result.error_code == "outcome_indeterminate" or (
            invocation.replay_policy == ReplayPolicy.never
            and result.is_error
            and result.error_code in {"tool_timeout", "cancelled"}
        )
        terminal_status = (
            ToolInvocationStatus.indeterminate
            if uncertain
            else ToolInvocationStatus.cancelled
            if result.error_code == "cancelled"
            else ToolInvocationStatus.failed
            if result.is_error
            else ToolInvocationStatus.succeeded
        )
        if uncertain:
            result = result.model_copy(
                update={
                    "error_code": "outcome_indeterminate",
                    "error_category": "recovery",
                    "retryable": False,
                    "recovery_hint": "Inspect the external system before deciding whether to retry.",
                }
            )
            self._ctx(run_id).indeterminate_reason = (
                f"Outcome of {tc.name} is indeterminate after timeout or cancellation"
            )
        invocation = invocation.model_copy(
            update={
                "status": terminal_status,
                "result": result,
                "error_code": result.error_code,
                "error_category": result.error_category,
                "updated_at": datetime.now(UTC),
                "finished_at": datetime.now(UTC),
            }
        )
        self.storage.save_tool_invocation(invocation)
        if terminal_status in (
            ToolInvocationStatus.cancelled,
            ToolInvocationStatus.indeterminate,
        ):
            lifecycle_type = (
                EventType.tool_execution_indeterminate
                if terminal_status == ToolInvocationStatus.indeterminate
                else EventType.tool_execution_cancelled
            )
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        lifecycle_type,
                        {
                            "tool_call_id": tc.id,
                            "invocation_id": tc.invocation_id,
                            "name": tc.name,
                            "error_code": result.error_code,
                        },
                        span_id=span_id,
                    )
                ],
            )
        self.events.emit_and_update(
            run_id,
            events=[
                self.events.event(
                    run_row,
                    EventType.tool_result,
                    {
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "is_error": result.is_error,
                        "content_preview": self.redactor.redact_text(result.content[:300]),
                        "duration_ms": duration,
                        "attempts": result.attempts,
                        "artifact_id": result.artifact_id,
                        "error_code": result.error_code,
                        "error_category": result.error_category,
                        "retryable": result.retryable,
                        "recovery_hint": self.redactor.redact_text(
                            result.recovery_hint or ""
                        ),
                    },
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.span_end,
                    {"kind": "tool", "name": tc.name, "status": terminal_status.value},
                    span_id=span_id,
                ),
                self.events.event(
                    run_row,
                    EventType.tool_call_end,
                    {
                        "tool_call_id": tc.id,
                        "invocation_id": tc.invocation_id,
                        "name": tc.name,
                        "is_error": result.is_error,
                        "arguments_summary": self.redactor.redact_text(
                            _summarize_tool_arguments(tc.arguments)
                        ),
                    },
                    span_id=span_id,
                ),
            ],
        )
        return result

    async def _invoke_reconciler(
        self,
        reconciler: Callable[[ToolContext, dict[str, Any]], Any],
        ctx: ToolContext,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        cancel: asyncio.Event,
    ) -> Any:
        async def invoke() -> Any:
            if inspect.iscoroutinefunction(reconciler):
                return await reconciler(ctx, arguments)
            value = await asyncio.to_thread(reconciler, ctx, arguments)
            return await value if inspect.isawaitable(value) else value

        reconcile_task = asyncio.create_task(invoke())
        cancel_waiter = (
            asyncio.create_task(cancel.wait()) if not cancel.is_set() else None
        )
        try:
            async with asyncio.timeout(timeout_s):
                if cancel_waiter is None:
                    return await reconcile_task
                done, _ = await asyncio.wait(
                    {reconcile_task, cancel_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_waiter in done:
                    reconcile_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await reconcile_task
                    raise RuntimeError("reconciliation cancelled")
                return reconcile_task.result()
        finally:
            if not reconcile_task.done():
                reconcile_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconcile_task
            if cancel_waiter is not None:
                cancel_waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_waiter

    def _bound_tool_result(
        self,
        result: ToolResult,
        *,
        spec: ToolSpec,
        request: RunRequest,
    ) -> ToolResult:
        content_limit = min(spec.max_result_bytes, request.budget.max_tool_result_bytes)
        content_marker = "\n...[tool result limit reached]"
        content = result.content
        if len(content.encode("utf-8")) > content_limit:
            content = _truncate_utf8(content, content_limit, content_marker)

        parts = result.parts or [ToolContentPart(type="text", text=content)]
        result = result.model_copy(
            update={
                "content": content,
                "parts": parts,
                "error_code": (
                    _truncate_utf8(result.error_code, 128) if result.error_code else None
                ),
                "error_category": (
                    _truncate_utf8(result.error_category, 128)
                    if result.error_category
                    else None
                ),
                "recovery_hint": (
                    _truncate_utf8(result.recovery_hint, 512)
                    if result.recovery_hint
                    else None
                ),
            }
        )

        total_limit = request.budget.max_tool_result_bytes
        serialized = _serialized_tool_result(result)
        if len(serialized) > total_limit:
            structured_marker = "\n...[structured tool result limit reached]"
            result = result.model_copy(
                update={
                    "content": _truncate_utf8(
                        result.content,
                        max(0, total_limit - 768),
                        structured_marker,
                    ),
                    "parts": [],
                }
            )
            serialized = _serialized_tool_result(result)
            if len(serialized) > total_limit:
                result = result.model_copy(
                    update={
                        "content": _truncate_utf8(
                            result.content, max(0, total_limit - 512)
                        ),
                        "recovery_hint": None,
                    }
                )
                serialized = _serialized_tool_result(result)

        inline_limit = request.budget.max_inline_tool_result_bytes
        if len(serialized) > inline_limit:
            meta = self.storage.artifacts.put(
                serialized,
                content_type="application/json",
                summary=result.content[:200],
            )
            meta["id"] = self.storage.register_artifact(meta)
            suffix = f"\n...[artifact:{meta['id']} sha={meta['sha256'][:12]}]"
            result = result.model_copy(
                update={
                    "artifact_id": meta["id"],
                    "content": _truncate_utf8(result.content, inline_limit, suffix),
                    "parts": [
                        ToolContentPart(
                            type="resource",
                            text="Full tool result stored as artifact",
                            mime_type="application/json",
                            artifact_id=meta["id"],
                        )
                    ],
                }
            )
        return result

    def _effect_for(self, tool: Any, tc: ToolCall) -> EffectKind:
        """Resolve the *dynamic* effect of a call, falling back to the static spec.

        Tools like MCP expose ``effect_for(arguments)`` so a bare ``list_tools`` is
        ``pure`` while ``call_tool`` is ``destructive``. Approval gating and the
        scheduler batch both use this so they act on the real blast radius, not the
        lowest-common-denominator spec label.
        """
        effect_for = getattr(tool, "effect_for", None)
        if callable(effect_for):
            args = tc.arguments if isinstance(tc.arguments, dict) else {}
            try:
                dynamic = effect_for(args)
            except Exception:  # noqa: BLE001
                dynamic = None
            if isinstance(dynamic, EffectKind):
                return dynamic
        return tool.spec.effect

    def _replay_policy_for(
        self, tool: Any, tc: ToolCall, effect: EffectKind
    ) -> ReplayPolicy:
        policy_for = getattr(tool, "replay_policy_for", None)
        if callable(policy_for):
            try:
                dynamic = policy_for(tc.arguments)
            except Exception:  # noqa: BLE001 - fail closed
                dynamic = None
            if isinstance(dynamic, ReplayPolicy):
                return dynamic
        return resolved_replay_policy(tool.spec, effect)

    def _parallel_safe_for(
        self, tool: Any, tc: ToolCall, effect: EffectKind
    ) -> bool:
        policy_for = getattr(tool, "parallel_safe_for", None)
        if callable(policy_for):
            try:
                dynamic = policy_for(tc.arguments)
            except Exception:  # noqa: BLE001 - fail closed
                return False
            if isinstance(dynamic, bool):
                return dynamic
        return resolved_parallel_safe(tool.spec, effect)

    def _resolve_browser_context_id(self, tc: ToolCall) -> str | None:
        """Browser ops always share a context key (default 'default' when omitted).

        Matches BrowserTool which uses ``arguments.get('context_id') or 'default'``.
        Without this, concurrent browser tools without an explicit context_id skip
        the scheduler lock and race on the same Playwright context.
        """
        tool = self.tools.get(tc.name)
        is_browser = tc.name == "browser" or (
            tool is not None and getattr(tool, "browser_bound", False)
        )
        args = tc.arguments if isinstance(tc.arguments, dict) else {}
        if is_browser:
            return str(args.get("context_id") or "default")
        if args.get("context_id"):
            return str(args["context_id"])
        return None

    def _append_tool_result(
        self,
        run_id: str,
        session_id: str,
        messages: list[Message],
        result: ToolResult,
        run_row: dict[str, Any],
    ) -> None:
        content = self.redactor.redact_text(tool_result_model_content(result))
        msg = Message(
            role=MessageRole.tool,
            content=content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            tool_result=result.model_copy(update={"content": self.redactor.redact_text(result.content)}),
        )
        messages.append(msg)
        self.storage.save_message(run_id, session_id, msg, seq=len(messages))

    def _drop_tool_result_messages(
        self,
        run_id: str,
        messages: list[Message],
        tool_call: ToolCall,
    ) -> None:
        stale = _tool_result_messages_for_call(messages, tool_call)
        if not stale:
            return
        stale_ids = {message.id for message in stale}
        messages[:] = [message for message in messages if message.id not in stale_ids]
        self.storage.delete_messages(run_id, list(stale_ids))

    def _tool_specs(self, request: RunRequest) -> list[ToolSpec]:
        bridge = getattr(self.harness, "mcp_bridge", None)
        proxy_factory = getattr(bridge, "proxy_tools", None)
        if callable(proxy_factory):
            proxies = proxy_factory()
            if not isinstance(proxies, dict):
                proxies = {}
            valid_proxies: dict[str, Any] = {}
            for name, proxy in proxies.items():
                try:
                    validate_tool_spec(proxy.spec)
                except ValueError:
                    continue
                valid_proxies[name] = proxy
            for name, tool in list(self.tools.items()):
                if getattr(tool, "mcp_proxy", False) and name not in valid_proxies:
                    del self.tools[name]
            for name, proxy in valid_proxies.items():
                existing = self.tools.get(name)
                if existing is not None and not getattr(existing, "mcp_proxy", False):
                    continue
                self.tools[name] = proxy
        names = request.tools
        specs: list[ToolSpec] = []
        for name, tool in self.tools.items():
            if names is not None and name not in names:
                continue
            # Child readonly: still expose write tools but engine will deny
            specs.append(tool.spec)
        return specs

    async def _finish_wall_timeout(
        self,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
    ) -> RunResult:
        await self._kill_descendants(run_id)
        checkpoint = self.storage.load_checkpoint(run_id)
        messages = self.lifecycle.checkpoint_messages(run_id)
        current_run_messages = self.storage.get_messages(run_id)
        output = "".join(
            message.content
            for message in current_run_messages
            if message.role == MessageRole.assistant
        )
        return self.lifecycle.finish(
            run_id,
            session_id,
            root_run_id,
            parent_run_id,
            RunStatus.failed,
            output,
            checkpoint.usage if checkpoint else Usage(),
            checkpoint.step if checkpoint else 0,
            "max_wall_time exceeded",
            messages=messages,
        )
