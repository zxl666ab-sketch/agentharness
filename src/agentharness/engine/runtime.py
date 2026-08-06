"""Asyncio run engine for procurement model turns, tools, checkpoints and recovery."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, suppress
from datetime import UTC, datetime
from typing import Any, cast

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    ContextState,
    EventEnvelope,
    EventType,
    Message,
    MessageRole,
    ModelAdapter,
    ModelRequest,
    PricingConfig,
    ProviderAttempt,
    ProviderRetryConfig,
    RunRequest,
    RunResult,
    RunStatus,
    ShellExecutionConfig,
    StreamItemType,
    ToolCall,
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
from agentharness.engine.compaction import (
    CompactionError,
    plan_compaction,
    render_transcript,
    summarize_history,
)
from agentharness.engine.context import ContextPlanner, billable_turn_usage, estimate_tokens
from agentharness.engine.events import EventEmitter
from agentharness.engine.lease import LeaseManager
from agentharness.engine.lifecycle import RESUMABLE_STATUSES, RunLifecycle
from agentharness.engine.run_state import RunContext, ensure_ctx
from agentharness.engine.tool_execution import (
    ApprovalCallback,
    ToolInvocationExecutor,
    canonical_arguments,
    enabled_tool_names,
    tool_call_completed,
)
from agentharness.engine.verification import VerificationLoop
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage

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


def _exception_retry_after_s(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    for headers in (getattr(response, "headers", None), getattr(exc, "headers", None)):
        if headers is None or not hasattr(headers, "get"):
            continue
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is None:
            continue
        try:
            return min(86_400.0, max(0.0, float(str(raw).strip())))
        except ValueError:
            return None
    return None


def _cost_usd(
    input_tokens: int,
    output_tokens: int,
    pricing: PricingConfig,
    *,
    cached_input_tokens: int = 0,
) -> float | None:
    if not pricing.known:
        return None
    assert pricing.input_per_million_usd is not None
    assert pricing.output_per_million_usd is not None
    cached = min(max(0, cached_input_tokens), max(0, input_tokens))
    cached_rate = pricing.cached_input_per_million_usd
    if cached and cached_rate is not None:
        input_cost = (
            (input_tokens - cached) * pricing.input_per_million_usd
            + cached * cached_rate
        )
    else:
        input_cost = input_tokens * pricing.input_per_million_usd
    return (input_cost + output_tokens * pricing.output_per_million_usd) / 1_000_000


def _update_usage_cost(usage: Usage, pricing: PricingConfig) -> None:
    cost = _cost_usd(
        usage.input_tokens,
        usage.output_tokens,
        pricing,
        cached_input_tokens=usage.cached_input_tokens,
    )
    usage.estimated_cost_usd = round(cost, 10) if cost is not None else None
    usage.cost_status = "estimated" if cost is not None else "unknown"


class RunEngine:
    def __init__(
        self,
        storage: Storage,
        providers: dict[str, ModelAdapter],
        tools: dict[str, Any],
        *,
        redactor: Redactor | None = None,
        approval_callback: ApprovalCallback | None = None,
        on_events: Callable[[list[EventEnvelope]], None] | None = None,
        lease_owner_id: str | None = None,
        lease_ttl_s: float = 60.0,
        lease_heartbeat_s: float = 10.0,
    ) -> None:
        self.storage = storage
        self.providers = providers
        self.tools = tools
        self.redactor = redactor or default_redactor
        self.lease = LeaseManager(
            storage,
            owner_id=lease_owner_id,
            ttl_s=lease_ttl_s,
            heartbeat_s=lease_heartbeat_s,
        )
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
            on_events=on_events,
        )
        self.lifecycle = RunLifecycle(
            storage=storage,
            runs=self._runs,
            events=self.events,
            redactor=self.redactor,
        )
        # Narrow surface handed to tools; they never see the Harness or the engine.
        self.spawner = RunSpawner(self)
        self.tool_executor = ToolInvocationExecutor(
            storage=storage,
            tools=tools,
            runs=self._runs,
            redactor=self.redactor,
            events=self.events,
            lifecycle=self.lifecycle,
            spawner=self.spawner,
            approval_callback=approval_callback,
        )
        self._active_run_ids: set[str] = set()
        self.active_run_id: str | None = None

    def _ctx(self, run_id: str) -> RunContext:
        """Return (creating if needed) the RunContext for a run."""
        return ensure_ctx(self._runs, run_id)

    @property
    def approval_callback(self) -> ApprovalCallback | None:
        """Owned by the tool executor; kept as an engine attribute for callers
        (Harness, Web supervisor) that read or swap the callback at runtime."""
        return self.tool_executor.approval_callback

    @approval_callback.setter
    def approval_callback(self, callback: ApprovalCallback | None) -> None:
        self.tool_executor.approval_callback = callback

    def child_run_ids(self, run_id: str) -> list[str]:
        """Child run ids, retained for persisted-run compatibility."""
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
        for ctx in self._runs.values():
            if run_id in ctx.child_runs:
                ctx.child_runs = [child for child in ctx.child_runs if child != run_id]

    def get_cancel_event(self, run_id: str) -> asyncio.Event:
        return self._ctx(run_id).cancel_event

    async def _kill_descendants(self, run_id: str) -> None:
        """Propagate cancellation to the procurement run and its children."""
        self.get_cancel_event(run_id).set()
        ctx = self._runs.get(run_id)
        for child_id in list(ctx.child_runs if ctx else []):
            await self._kill_descendants(child_id)
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
                "_agentharness_context_request": {
                    "original_goal": request.message,
                    "system": request.system,
                    "reasoning_effort": request.reasoning_effort,
                    "extra_dirs": request.extra_dirs,
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
        # Nested runs start with only their own task message.
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
            # Preserve completed tools for resume when the run is cancelled.
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
            reasoning_effort=stored_context_request.get("reasoning_effort"),
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
                    await self.tool_executor.execute_batch(
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
            tool_specs = self._tool_specs(request, run_id=run_id)
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

            # Fold oversized old history into the rolling summary before planning;
            # planner externalization stays the hard fallback if this is skipped.
            await self._maybe_compact(
                run_id=run_id,
                request=request,
                messages=messages,
                usage=usage,
                step=step,
                completed_tool_ids=completed_tool_ids,
                cancel=cancel,
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
                    reasoning_effort=request.reasoning_effort,
                    system=bundle.system,
                    max_tokens=max(1, min(attempt_remaining, output_token_limit)),
                    parallel_tool_calls=(
                        False if budget.max_tool_calls_per_turn == 1 else None
                    ),
                    metadata={"run_id": run_id},
                )
                text_parts = []
                tool_acc = {}
                order = []
                ended_tool_ids = set()
                turn_usage = Usage()
                turn_usage_charged = False
                error_msg = None
                error_kind = None
                retry_after_s: float | None = None
                streamed_length = 0
                had_provider_output = False
                provider_response_id: str | None = None
                provider_phase: str | None = None
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
                            turn_usage.cached_input_tokens += (
                                item.usage.cached_input_tokens
                            )
                            turn_usage.total_tokens = (
                                turn_usage.input_tokens + turn_usage.output_tokens
                            )
                            turn_usage.estimated = item.usage.estimated
                        elif item.type == StreamItemType.provider_context:
                            if item.provider_response_id:
                                provider_response_id = item.provider_response_id
                                had_provider_output = True
                            if item.provider_phase:
                                provider_phase = item.provider_phase
                                had_provider_output = True
                        elif item.type == StreamItemType.error:
                            error_msg = item.error or "provider error"
                            error_kind = item.error_kind or "provider"
                            retry_after_s = item.retry_after_s
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
                    retry_after_s = _exception_retry_after_s(exc)
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
                attempt_cached = min(turn_usage.cached_input_tokens, attempt_input)
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
                        cached_input_tokens=attempt_cached,
                        had_output=had_provider_output,
                        fallback=False,
                        estimated_cost_usd=_cost_usd(
                            attempt_input,
                            attempt_output,
                            request.pricing,
                            cached_input_tokens=attempt_cached,
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
                    usage.cached_input_tokens += attempt_cached
                    usage.estimated = usage.estimated or not turn_usage.total_tokens
                    _update_usage_cost(usage, request.pricing)
                    turn_usage_charged = True
                    delay = max(
                        _retry_delay(request.provider_retry, target_attempt),
                        retry_after_s or 0.0,
                    )
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
                                    "retry_after_s": retry_after_s,
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
                usage.cached_input_tokens += min(
                    turn_usage.cached_input_tokens, billable.input_tokens
                )
                usage.estimated = usage.estimated or billable.estimated
                usage.last_input_tokens = raw_in
                usage.last_output_tokens = raw_out
                usage.last_cached_input_tokens = turn_usage.cached_input_tokens
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
                provider_response_id=provider_response_id,
                provider_run_id=(run_id if provider_response_id else None),
                provider_phase=provider_phase,
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
            tool_results = await self.tool_executor.execute_batch(
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

            final_outputs = [
                result.final_output
                for result in tool_results
                if not result.is_error and result.final_output
            ]
            if final_outputs and all(not result.is_error for result in tool_results):
                if len(final_outputs) != 1:
                    return self.lifecycle.finish(
                        run_id,
                        session_id,
                        root_run_id,
                        parent_run_id,
                        RunStatus.failed,
                        "".join(output_parts),
                        usage,
                        step,
                        "multiple tools returned final output in one batch",
                        messages=messages,
                    )
                final_output = self.redactor.redact_text(final_outputs[0])
                available = budget.max_output_length - output_length
                if len(final_output) > available:
                    output_parts.append(final_output[: max(0, available)])
                    return self.lifecycle.finish(
                        run_id,
                        session_id,
                        root_run_id,
                        parent_run_id,
                        RunStatus.failed,
                        "".join(output_parts),
                        usage,
                        step,
                        "max_output_length exceeded",
                        messages=messages,
                    )
                output_parts.append(final_output)
                output_length += len(final_output)
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
                        step=step,
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
                            step,
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
                            step,
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
                            steps=step,
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
                            step,
                            error,
                            messages=messages,
                        )
                return self.lifecycle.finish(
                    run_id,
                    session_id,
                    root_run_id,
                    parent_run_id,
                    RunStatus.completed,
                    "".join(output_parts),
                    usage,
                    step,
                    None,
                    messages=messages,
                )

            if output_length > budget.max_output_length:
                return self.lifecycle.finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step,
                    "max_output_length exceeded",
                    messages=messages,
                )

    async def _maybe_compact(
        self,
        *,
        run_id: str,
        request: RunRequest,
        messages: list[Message],
        usage: Usage,
        step: int,
        completed_tool_ids: set[str],
        cancel: asyncio.Event,
    ) -> None:
        """Summarize old history into the planner state when it crosses the threshold.

        Every failure path degrades to "continue uncompacted": the planner's
        externalization still enforces the hard context budget, so compaction
        can never make a run fail that would otherwise succeed.
        """
        if cancel.is_set():
            return
        budget = request.budget or BudgetConfig()
        ctx = self._ctx(run_id)
        plan = plan_compaction(messages, ctx.context_state, budget)
        if plan is None:
            return
        run_row = self.storage.get_run(run_id)
        provider = self.providers.get(request.provider)
        if run_row is None or provider is None:
            return

        state = ctx.context_state
        if state is None:
            state = self.context_planner.select_state(
                request, system=request.system
            )
        prior_summary = ContextPlanner.summary_text(state)
        goal = str(
            request.metadata.get("_agentharness_original_goal") or request.message
        )
        transcript = render_transcript(
            plan.cover_messages, prior_summary=prior_summary, goal=goal
        )

        def emit_compaction(payload: dict[str, Any]) -> None:
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run_row,
                        EventType.context_compacted,
                        {
                            "step": step,
                            "tokens_before": plan.live_tokens_before,
                            "threshold_tokens": plan.threshold_tokens,
                            "messages_covered": len(plan.cover_ids),
                            "groups_covered": plan.groups_covered,
                            **payload,
                        },
                    )
                ],
            )

        # The summarization call itself must fit inside the run token budget.
        projected = estimate_tokens(transcript) + 2048
        if usage.total_tokens + projected >= budget.max_tokens:
            emit_compaction(
                {"status": "skipped", "reason": "insufficient token budget"}
            )
            return

        try:
            summary, summarize_usage = await summarize_history(
                provider, model=request.model, transcript=transcript
            )
        except CompactionError as exc:
            emit_compaction({"status": "skipped", "reason": str(exc)})
            return
        if cancel.is_set():
            return

        billable = billable_turn_usage(
            provider_usage=summarize_usage,
            local_input_estimate=estimate_tokens(transcript),
            output_text=summary,
        )
        usage.input_tokens += billable.input_tokens
        usage.output_tokens += billable.output_tokens
        usage.total_tokens += billable.total_tokens
        usage.cached_input_tokens += min(
            summarize_usage.cached_input_tokens, billable.input_tokens
        )
        usage.estimated = usage.estimated or billable.estimated
        _update_usage_cost(usage, request.pricing)

        artifact_id = self.context_planner.externalize_messages(plan.cover_messages)
        ctx.context_state = self.context_planner.apply_compaction(
            state,
            summary_text=summary,
            covered_ids=plan.cover_ids,
            artifact_id=artifact_id,
        )
        emit_compaction(
            {
                "status": "applied",
                "summary_tokens": estimate_tokens(summary),
                "artifact_id": artifact_id,
                "compaction_count": ctx.context_state.compaction_count,
                "usage": billable.model_dump(),
            }
        )
        # Persist the new context state so resume continues from the compacted view.
        self.lifecycle.checkpoint(
            run_id,
            phase="model_turn",
            step=step,
            messages=messages,
            pending=[],
            completed=completed_tool_ids,
            usage=usage,
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

        tools_ordered = [
            call.name
            for message in messages
            for call in (message.tool_calls or [])
        ]
        tools_succeeded = [
            str(message.name)
            for message in messages
            if message.role == MessageRole.tool
            and message.name
            and message.tool_result is not None
            and not message.tool_result.is_error
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
            tools_succeeded=tools_succeeded,
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
                    "passed": decision.action == "pass",
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
            usage.cached_input_tokens += min(
                int(raw.get("cached_input_tokens") or 0), input_tokens
            )

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

    def _tool_specs(self, request: RunRequest, *, run_id: str) -> list[ToolSpec]:
        names = enabled_tool_names(
            request,
            self.storage.list_tool_invocations(run_id),
            set(self.tools),
        )
        specs: list[ToolSpec] = []
        for name, tool in self.tools.items():
            if name not in names:
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


class RunSpawner:
    """Narrow runtime surface handed to tools via ``ToolContext.harness``.

    Retained tools receive only this narrow storage/run surface and never see
    the Harness or the engine directly.
    """

    def __init__(self, engine: RunEngine) -> None:
        self._engine = engine
        self.storage = engine.storage

    async def run(
        self, request: RunRequest, *, run_id: str | None = None
    ) -> RunResult:
        return await self._engine.run(request, run_id=run_id)

    def child_run_ids(self, run_id: str) -> list[str]:
        return self._engine.child_run_ids(run_id)
