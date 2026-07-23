"""Asyncio agent run engine — stream, tools, checkpoint, resume, cancel, delegate."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    BudgetConfig,
    Checkpoint,
    EffectKind,
    EventEnvelope,
    EventType,
    Message,
    MessageRole,
    ModelAdapter,
    ModelRequest,
    RunRequest,
    RunResult,
    RunStatus,
    StreamItemType,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
    Usage,
    new_id,
)
from agentharness.engine.context import assemble_context, estimate_tokens
from agentharness.engine.scheduler import EffectScheduler
from agentharness.security.approval import auto_decision
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage

ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]

_RESUMABLE_STATUSES = frozenset(
    {
        RunStatus.interrupted,
        RunStatus.cancelled,
        RunStatus.waiting_approval,
    }
)


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
    ) -> None:
        self.storage = storage
        self.providers = providers
        self.tools = tools
        self.redactor = redactor or default_redactor
        self.approval_callback = approval_callback
        self.harness = harness
        self.scheduler = EffectScheduler()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._run_allow_effects: dict[str, set[EffectKind]] = {}
        self._active_processes: dict[str, list[Any]] = {}  # run_id -> process handles
        self._child_runs: dict[str, list[str]] = {}
        self._delta_buf: dict[str, list[str]] = {}
        self._delta_buf_size: dict[str, int] = {}
        self._delta_last_flush: dict[str, float] = {}
        # Per-run completed tool ids (must survive interrupt/cancel checkpoints for resume)
        self._completed_tool_ids: dict[str, set[str]] = {}
        self._pending_tool_calls: dict[str, list[ToolCall]] = {}
        self._stop_mode: dict[str, str] = {}  # run_id -> "cancel" | "interrupt"
        # In-memory message lists (include session history splice) for resume-safe checkpoints
        self._run_messages: dict[str, list[Message]] = {}
        self._provider_owner_tasks: dict[str, asyncio.Task[Any]] = {}
        self._active_run_ids: set[str] = set()
        self.active_run_id: str | None = None

    def _activate_run(self, run_id: str) -> None:
        self._active_run_ids.add(run_id)
        self.active_run_id = run_id

    def _deactivate_run(self, run_id: str) -> None:
        self._active_run_ids.discard(run_id)
        if self.active_run_id == run_id:
            self.active_run_id = next(iter(self._active_run_ids), None)

    async def _cleanup_run_state(self, run_id: str) -> None:
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
        for mapping in (
            self._cancel_events,
            self._run_allow_effects,
            self._active_processes,
            self._delta_buf,
            self._delta_buf_size,
            self._delta_last_flush,
            self._completed_tool_ids,
            self._pending_tool_calls,
            self._stop_mode,
            self._run_messages,
            self._provider_owner_tasks,
        ):
            mapping.pop(run_id, None)
        self._child_runs.pop(run_id, None)
        for parent_id, children in list(self._child_runs.items()):
            if run_id in children:
                self._child_runs[parent_id] = [child for child in children if child != run_id]

    def get_cancel_event(self, run_id: str) -> asyncio.Event:
        if run_id not in self._cancel_events:
            self._cancel_events[run_id] = asyncio.Event()
        return self._cancel_events[run_id]

    async def _kill_descendants(self, run_id: str) -> None:
        """Propagate cancel signal, kill shell trees, clear process registry."""
        self.get_cancel_event(run_id).set()
        for child_id in list(self._child_runs.get(run_id, [])):
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
        provider_owner = self._provider_owner_tasks.get(run_id)
        if provider_owner is not None and provider_owner is not asyncio.current_task():
            provider_owner.cancel()

    async def _watch_stop_request(self, run_id: str, cancel: asyncio.Event) -> None:
        while not cancel.is_set():
            mode = self.storage.get_stop_request(run_id)
            if mode:
                self._stop_mode[run_id] = mode
                await self._kill_descendants(run_id)
                return
            await asyncio.sleep(0.05)

    def _stop_status(self, run_id: str) -> RunStatus:
        mode = self._stop_mode.get(run_id, "cancel")
        return RunStatus.interrupted if mode == "interrupt" else RunStatus.cancelled

    def _checkpoint_messages_for_run(self, run_id: str) -> list[Message]:
        """Prefer in-memory (session-history-aware) messages over storage-only rows.

        Storage only holds this run's messages; multi-turn history lives in the
        in-memory list / prior checkpoint and must survive interrupt/resume.
        """
        mem = self._run_messages.get(run_id)
        cp = self.storage.load_checkpoint(run_id)
        stored = self.storage.get_messages(run_id)
        candidates: list[list[Message]] = []
        if mem:
            candidates.append(list(mem))
        if cp and cp.messages:
            candidates.append(list(cp.messages))
        if stored:
            candidates.append(list(stored))
        if not candidates:
            return []
        # Longest list wins (history splice makes the in-memory list longer).
        return max(candidates, key=len)

    def _preserve_checkpoint(
        self,
        run_id: str,
        *,
        status: RunStatus,
        phase: str = "tool_batch",
    ) -> None:
        """Checkpoint without wiping completed tool ids (resume safety)."""
        cp = self.storage.load_checkpoint(run_id)
        completed = set(self._completed_tool_ids.get(run_id, set()))
        pending = list(self._pending_tool_calls.get(run_id, []))
        step = 0
        usage = Usage()
        if cp:
            completed |= set(cp.completed_tool_call_ids)
            if not pending:
                pending = [tc for tc in cp.pending_tool_calls if tc.id not in completed]
            step = cp.step
            usage = cp.usage
        messages = self._checkpoint_messages_for_run(run_id)
        self.storage.save_checkpoint(
            Checkpoint(
                run_id=run_id,
                phase=phase,  # type: ignore[arg-type]
                step=step,
                messages=messages,
                pending_tool_calls=pending,
                completed_tool_call_ids=list(completed),
                usage=usage,
                status=status,
            )
        )
        self._completed_tool_ids[run_id] = completed

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
        self._stop_mode[run_id] = "cancel"
        self.storage.request_stop(run_id, "cancel")
        await self._kill_descendants(run_id)
        self._preserve_checkpoint(run_id, status=RunStatus.cancelled)
        # If no active loop owns this run, finalize status here
        if run_id not in self._active_run_ids:
            run = self.storage.get_run(run_id)
            if run and run["status"] in (
                RunStatus.running.value,
                RunStatus.waiting_approval.value,
                RunStatus.pending.value,
            ):
                self._emit_and_update(
                    run_id,
                    status=RunStatus.cancelled,
                    finished=True,
                    events=[
                        self._event(
                            run,
                            EventType.run_cancelled,
                            {"reason": "cancel"},
                        )
                    ],
                )

    async def interrupt(self, run_id: str, reason: str = "interrupted") -> None:
        """Ctrl+C / CancelledError: kill trees, preserve resume state, finish as interrupted."""
        self._stop_mode[run_id] = "interrupt"
        self.storage.request_stop(run_id, "interrupt")
        await self._kill_descendants(run_id)
        self._preserve_checkpoint(run_id, status=RunStatus.interrupted)
        if run_id not in self._active_run_ids:
            self._mark_interrupted(run_id, reason)

    async def run(self, request: RunRequest) -> RunResult:
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

        run_id = new_id()
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
            },
        )
        if parent_run_id:
            self._child_runs.setdefault(parent_run_id, []).append(run_id)
        elif is_top_level:
            # Top-level dialogue updates session sort order; delegates do not.
            self.storage.update_session(session_id, touch=True)

        cancel = self.get_cancel_event(run_id)
        self._completed_tool_ids[run_id] = set()
        self._pending_tool_calls[run_id] = []
        self._activate_run(run_id)

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
        self._run_messages[run_id] = messages

        run_row = self.storage.get_run(run_id)
        assert run_row is not None
        self._emit_and_update(
            run_id,
            events=[
                self._event(
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
                    completed_tool_ids=self._completed_tool_ids[run_id],
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
            self._mark_interrupted(run_id, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(run_id, str(exc))
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
        if status not in _RESUMABLE_STATUSES:
            allowed = ", ".join(sorted(item.value for item in _RESUMABLE_STATUSES))
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
        request = RunRequest(
            message=input or "",
            session_id=run["session_id"],
            provider=run.get("provider") or "fake",
            model=run.get("model"),
            approval=ApprovalMode(run.get("approval") or "ask"),
            cwd=run.get("cwd"),
            parent_run_id=run.get("parent_run_id"),
            root_run_id=run.get("root_run_id"),
            delegate_depth=int(run.get("delegate_depth") or 0),
            allow_write=bool(run.get("allow_write", 1)),
            metadata=stored_metadata,
            budget=BudgetConfig.model_validate(stored_budget or {}),
        )
        messages = list(cp.messages)
        if input:
            msg = Message(role=MessageRole.user, content=input)
            messages.append(msg)
            self.storage.save_message(run_id, run["session_id"], msg, seq=len(messages))

        completed = set(cp.completed_tool_call_ids)
        # If interrupted mid tool batch, only re-run incomplete tool calls
        pending = [tc for tc in cp.pending_tool_calls if tc.id not in completed]

        self._activate_run(run_id)
        self._run_messages[run_id] = messages
        self._completed_tool_ids[run_id] = completed
        self._pending_tool_calls[run_id] = list(pending)

        self.storage.update_run(run_id, status=RunStatus.running)
        self._emit_and_update(
            run_id,
            events=[
                self._event(run, EventType.run_status, {"status": "running", "resumed": True})
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
            self._mark_interrupted(run_id, "cancelled")
            raise
        finally:
            stop_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await stop_watcher
            self._deactivate_run(run_id)
            await self._cleanup_run_state(run_id)

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
        started = time.monotonic()
        provider = self.providers.get(request.provider)
        if provider is None:
            raise RuntimeError(f"unknown provider: {request.provider}")

        tool_specs = self._tool_specs(request)
        output_parts: list[str] = []
        output_length = 0
        # Keep reference so interrupt/cancel checkpoints retain multi-turn context.
        self._run_messages[run_id] = messages

        while True:
            if cancel.is_set():
                stop = self._stop_status(run_id)
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    stop, "".join(output_parts), usage, step, stop.value,
                    messages=messages,
                )
            if step >= budget.max_steps:
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_steps exceeded",
                    messages=messages,
                )
            if time.monotonic() - started > budget.max_wall_time_s:
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_wall_time exceeded",
                    messages=messages,
                )
            if usage.total_tokens >= budget.max_tokens:
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step, "max_tokens exceeded",
                    messages=messages,
                )
            if output_length >= budget.max_output_length:
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step,
                    "max_output_length exceeded",
                    messages=messages,
                )

            run_row = self.storage.get_run(run_id)
            assert run_row is not None

            # Assemble context
            skills_text = self._load_skills(request)
            memories = self._retrieve_memories(request.message if step == 0 else "")
            system, ctx_messages, ctx_meta = assemble_context(
                system=request.system or self._default_system(request),
                skills=skills_text,
                memories=memories,
                summary=None,
                messages=messages,
                tools=tool_specs,
                max_tokens=min(100_000, budget.max_tokens),
            )

            span_id = new_id()
            self._emit_and_update(
                run_id,
                events=[
                    self._event(
                        run_row,
                        EventType.model_turn_start,
                        {"step": step, "context": ctx_meta},
                        span_id=span_id,
                    ),
                    self._event(
                        run_row,
                        EventType.span_start,
                        {"kind": "model", "step": step},
                        span_id=span_id,
                    ),
                ],
            )

            remaining_tokens = budget.max_tokens - usage.total_tokens
            remaining_chars = budget.max_output_length - output_length
            output_token_limit = max(1, (remaining_chars + 3) // 4)
            model_req = ModelRequest(
                messages=ctx_messages,
                tools=tool_specs,
                model=request.model,
                system=system,
                max_tokens=max(1, min(remaining_tokens, output_token_limit)),
            )

            text_parts: list[str] = []
            tool_acc: dict[str, ToolCall] = {}
            order: list[str] = []
            turn_usage = Usage()
            error_msg: str | None = None
            error_kind: str | None = None
            streamed_length = 0

            stream = provider.stream(model_req).__aiter__()
            provider_owner = asyncio.current_task()
            if provider_owner is not None:
                self._provider_owner_tasks[run_id] = provider_owner
            try:
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
                        await self._buffer_delta(run_id, run_row, chunk, span_id)
                        if len(item.text) > available:
                            error_msg = "max_output_length exceeded"
                            error_kind = "budget"
                            break
                    elif item.type == StreamItemType.tool_call_start:
                        tc_id = item.tool_call_id or new_id()
                        tc = ToolCall(
                            id=tc_id,
                            name=item.tool_name or "",
                            arguments_raw="",
                        )
                        tool_acc[tc_id] = tc
                        order.append(tc_id)
                        self._emit_and_update(
                            run_id,
                            events=[
                                self._event(
                                    run_row,
                                    EventType.tool_call_start,
                                    {"tool_call_id": tc_id, "name": tc.name},
                                    span_id=new_id(),
                                    parent_span_id=span_id,
                                )
                            ],
                        )
                    elif item.type == StreamItemType.tool_call_delta:
                        tc_id = item.tool_call_id or ""
                        if tc_id in tool_acc and item.arguments_delta:
                            tool_acc[tc_id].arguments_raw += item.arguments_delta
                            if item.tool_name:
                                tool_acc[tc_id].name = item.tool_name
                    elif item.type == StreamItemType.tool_call_end:
                        tc_id = item.tool_call_id or ""
                        if tc_id not in tool_acc:
                            tool_acc[tc_id] = ToolCall(
                                id=tc_id, name=item.tool_name or "", arguments_raw=""
                            )
                            order.append(tc_id)
                        tc = tool_acc[tc_id]
                        if item.tool_name:
                            tc.name = item.tool_name
                        if item.arguments is not None:
                            tc.arguments = item.arguments
                        elif tc.arguments_raw:
                            try:
                                tc.arguments = json.loads(tc.arguments_raw)
                            except json.JSONDecodeError:
                                tc.arguments = {"_raw": tc.arguments_raw}
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
                error_kind = "provider"
            finally:
                if self._provider_owner_tasks.get(run_id) is provider_owner:
                    self._provider_owner_tasks.pop(run_id, None)
                close_stream = getattr(stream, "aclose", None)
                if callable(close_stream):
                    with suppress(Exception):
                        await cast(Callable[[], Awaitable[None]], close_stream)()

            await self._flush_delta(run_id, run_row, span_id)

            text = "".join(text_parts)
            if not turn_usage.total_tokens:
                # Deterministic estimate
                turn_usage = Usage(
                    input_tokens=estimate_tokens(system or "") + sum(
                        estimate_tokens(m.content) for m in ctx_messages
                    ),
                    output_tokens=estimate_tokens(text),
                    total_tokens=0,
                    estimated=True,
                )
                turn_usage.total_tokens = turn_usage.input_tokens + turn_usage.output_tokens

            usage.input_tokens += turn_usage.input_tokens
            usage.output_tokens += turn_usage.output_tokens
            usage.total_tokens += turn_usage.total_tokens
            usage.estimated = usage.estimated or turn_usage.estimated

            if usage.total_tokens > budget.max_tokens and error_msg is None:
                error_msg = "max_tokens exceeded"
                error_kind = "budget"

            tool_calls = [tool_acc[i] for i in order if i in tool_acc]

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

            self._emit_and_update(
                run_id,
                events=[
                    self._event(
                        run_row,
                        EventType.model_turn_end,
                        {
                            "step": step,
                            "text_len": len(text),
                            "tool_calls": [tc.name for tc in tool_calls],
                            "usage": turn_usage.model_dump(),
                        },
                        span_id=span_id,
                    ),
                    self._event(
                        run_row,
                        EventType.span_end,
                        {"kind": "model", "step": step},
                        span_id=span_id,
                    ),
                ],
            )

            # Checkpoint after model turn
            self._checkpoint(
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
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    status, "".join(output_parts), usage, step, error_msg,
                    messages=messages,
                )

            if not tool_calls:
                return self._finish(
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
            step += 1

            if output_length > budget.max_output_length:
                return self._finish(
                    run_id, session_id, root_run_id, parent_run_id,
                    RunStatus.failed, "".join(output_parts), usage, step,
                    "max_output_length exceeded",
                    messages=messages,
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
    ) -> None:
        run_row = self.storage.get_run(run_id)
        assert run_row is not None

        # Filter already completed (resume safety)
        pending = [tc for tc in tool_calls if tc.id not in completed_tool_ids]
        if not pending:
            return

        self._completed_tool_ids[run_id] = set(completed_tool_ids)
        self._pending_tool_calls[run_id] = list(pending)

        self._checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=pending,
            completed=completed_tool_ids,
            usage=usage,
        )

        # Approval gating
        allowed: list[ToolCall] = []
        for tc in pending:
            tool = self.tools.get(tc.name)
            if tool is None:
                result = ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"Unknown tool: {tc.name}",
                    is_error=True,
                )
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                completed_tool_ids.add(tc.id)
                continue

            spec: ToolSpec = tool.spec
            effect = spec.effect
            # Child runs default readonly — block write effects without grant
            if not request.allow_write and effect in (
                EffectKind.workspace_write,
                EffectKind.destructive,
            ):
                result = ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="Write permission not granted for this run",
                    is_error=True,
                )
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                completed_tool_ids.add(tc.id)
                continue

            decision = auto_decision(effect, request.approval)
            # Run-level allow list
            if decision is None and effect in self._run_allow_effects.get(run_id, set()):
                decision = ApprovalDecision.allow_once

            if decision is None:
                # Interactive approval
                apr = ApprovalRequest(
                    run_id=run_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    effect=effect,
                    arguments_summary=self.redactor.redact_text(
                        json.dumps(tc.arguments, ensure_ascii=False)[:500]
                    ),
                )
                self.storage.save_approval(apr.model_dump(mode="json"))
                self.storage.update_run(run_id, status=RunStatus.waiting_approval)
                self._emit_and_update(
                    run_id,
                    events=[
                        self._event(
                            run_row,
                            EventType.approval_requested,
                            {
                                "approval_id": apr.id,
                                "tool": tc.name,
                                "effect": effect.value,
                                "arguments_summary": apr.arguments_summary,
                            },
                        )
                    ],
                )
                self._checkpoint(
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
                self._emit_and_update(
                    run_id,
                    events=[
                        self._event(
                            run_row,
                            EventType.approval_resolved,
                            {
                                "approval_id": apr.id,
                                "decision": decision.value,
                                "tool": tc.name,
                            },
                        )
                    ],
                )
                if decision == ApprovalDecision.allow_run:
                    self._run_allow_effects.setdefault(run_id, set()).add(effect)
                if cancelled_while_waiting:
                    return

            if decision == ApprovalDecision.deny:
                result = ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="Approval denied",
                    is_error=True,
                )
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                completed_tool_ids.add(tc.id)
                continue

            allowed.append(tc)

        # Schedule execution with concurrency rules
        async def make_runner(tc: ToolCall) -> ToolResult:
            """Execute one tool. Concurrency is applied by EffectScheduler outside —
            do not nest scheduler.run here (asyncio.Lock is not reentrant)."""
            if cancel.is_set():
                return ToolResult(
                    tool_call_id=tc.id, name=tc.name, content="cancelled", is_error=True
                )
            tool = self.tools[tc.name]
            span_id = new_id()
            t0 = time.monotonic()
            self._emit_and_update(
                run_id,
                events=[
                    self._event(
                        run_row,
                        EventType.span_start,
                        {"kind": "tool", "name": tc.name, "tool_call_id": tc.id},
                        span_id=span_id,
                    )
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
                metadata={
                    "root_run_id": root_run_id,
                    "parent_run_id": parent_run_id,
                    "delegate_depth": request.delegate_depth,
                    "budget": request.budget.model_dump(),
                    "provider": request.provider,
                    "model": request.model,
                    "skills_dirs": request.skills_dirs,
                },
                harness=self.harness,
            )

            try:
                result = await tool.run(ctx, tc.arguments)
            except Exception as exc:  # noqa: BLE001
                result = ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"Tool error: {exc}",
                    is_error=True,
                )
            if result.tool_call_id != tc.id:
                result = result.model_copy(update={"tool_call_id": tc.id, "name": tc.name})
            duration = (time.monotonic() - t0) * 1000
            result = result.model_copy(update={"duration_ms": duration})
            # Large results → artifact
            if len(result.content) > 4000:
                meta = self.storage.artifacts.put(
                    result.content, content_type="text/plain", summary=result.content[:200]
                )
                meta["id"] = self.storage.register_artifact(meta)
                result = result.model_copy(
                    update={
                        "artifact_id": meta["id"],
                        "content": result.content[:2000]
                        + f"\n...[artifact:{meta['id']} sha={meta['sha256'][:12]}]",
                    }
                )
            self._emit_and_update(
                run_id,
                events=[
                    self._event(
                        run_row,
                        EventType.tool_result,
                        {
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "is_error": result.is_error,
                            "content_preview": self.redactor.redact_text(result.content[:300]),
                            "duration_ms": duration,
                            "artifact_id": result.artifact_id,
                        },
                        span_id=span_id,
                    ),
                    self._event(
                        run_row,
                        EventType.span_end,
                        {"kind": "tool", "name": tc.name},
                        span_id=span_id,
                    ),
                    self._event(
                        run_row,
                        EventType.tool_call_end,
                        {"tool_call_id": tc.id, "name": tc.name, "is_error": result.is_error},
                        span_id=span_id,
                    ),
                ],
            )
            return result

        # Batch with concurrency rules (scheduler wraps make_runner once)
        items: list[tuple[EffectKind, Any, str | None]] = []
        for tc in allowed:
            tool = self.tools[tc.name]
            effect = tool.spec.effect
            browser_id = self._resolve_browser_context_id(tc)
            items.append((effect, lambda tc=tc: make_runner(tc), browser_id))

        if items:
            results = await self.scheduler.run_batch(items)
            for result in results:
                self._append_tool_result(run_id, session_id, messages, result, run_row)
                # Cancel/interrupt mid-flight: error results are incomplete — keep pending
                # so resume re-runs them. Successful tools (and non-cancel failures) complete.
                incomplete = cancel.is_set() and result.is_error
                if incomplete:
                    continue
                completed_tool_ids.add(result.tool_call_id)
                self._completed_tool_ids.setdefault(run_id, set()).add(result.tool_call_id)

        # Drop finished tools from pending; incomplete stay for resume
        self._pending_tool_calls[run_id] = [
            tc for tc in pending if tc.id not in completed_tool_ids
        ]
        self._completed_tool_ids[run_id] = set(completed_tool_ids)

        self._checkpoint(
            run_id,
            phase="tool_batch",
            step=step,
            messages=messages,
            pending=self._pending_tool_calls[run_id],
            completed=completed_tool_ids,
            usage=usage,
        )

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
        content = self.redactor.redact_text(result.content)
        msg = Message(
            role=MessageRole.tool,
            content=content,
            tool_call_id=result.tool_call_id,
            name=result.name,
        )
        messages.append(msg)
        self.storage.save_message(run_id, session_id, msg, seq=len(messages))

    def _tool_specs(self, request: RunRequest) -> list[ToolSpec]:
        names = request.tools
        specs: list[ToolSpec] = []
        for name, tool in self.tools.items():
            if names is not None and name not in names:
                continue
            # Child readonly: still expose write tools but engine will deny
            specs.append(tool.spec)
        return specs

    def _default_system(self, request: RunRequest) -> str:
        return (
            "You are a capable agent running inside Agent Harness. "
            "Use tools when needed. Be concise and accurate. "
            f"Workspace cwd: {request.cwd or '.'}."
        )

    def _load_skills(self, request: RunRequest) -> list[str]:
        try:
            from agentharness.tools.skills import load_matching_skills

            return load_matching_skills(request.skills_dirs, request.message)
        except Exception:  # noqa: BLE001
            return []

    def _retrieve_memories(self, query: str) -> list[str]:
        if not query:
            return []
        try:
            rows = self.storage.search_memories(query, limit=5)
            return [r["content"] for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def _checkpoint(
        self,
        run_id: str,
        *,
        phase: str,
        step: int,
        messages: list[Message],
        pending: list[ToolCall],
        completed: set[str],
        usage: Usage,
        status: RunStatus = RunStatus.running,
        approval_token: str | None = None,
    ) -> None:
        cp = Checkpoint(
            run_id=run_id,
            phase=phase,  # type: ignore[arg-type]
            step=step,
            messages=messages,
            pending_tool_calls=pending,
            completed_tool_call_ids=list(completed),
            usage=usage,
            status=status,
            approval_token=approval_token,
        )
        self.storage.save_checkpoint(cp)
        run = self.storage.get_run(run_id)
        if run:
            self._emit_and_update(
                run_id,
                events=[
                    self._event(
                        run,
                        EventType.checkpoint,
                        {"phase": phase, "step": step},
                    )
                ],
            )

    def _event(
        self,
        run: dict[str, Any],
        etype: EventType,
        payload: dict[str, Any],
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> EventEnvelope:
        return EventEnvelope(
            session_id=run["session_id"],
            root_run_id=run["root_run_id"],
            run_id=run["id"],
            parent_run_id=run.get("parent_run_id"),
            span_id=span_id,
            parent_span_id=parent_span_id,
            type=etype,
            payload=payload,
        )

    def _emit_and_update(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        finished: bool = False,
        error: str | None = None,
        output_summary: str | None = None,
        usage: Usage | None = None,
        steps: int | None = None,
        events: list[EventEnvelope] | None = None,
    ) -> list[EventEnvelope]:
        assigned = self.storage.update_run(
            run_id,
            status=status,
            finished=finished,
            error=error,
            output_summary=output_summary,
            usage=usage,
            steps=steps,
            events=events,
        )
        # Fan out redacted events to live CLI and Web observers.
        if assigned and self.harness is not None:
            notify = getattr(self.harness, "_notify_events", None)
            if callable(notify):
                notify(assigned)
        return assigned

    async def _buffer_delta(
        self, run_id: str, run_row: dict[str, Any], text: str, span_id: str
    ) -> None:
        buf = self._delta_buf.setdefault(run_id, [])
        buf.append(text)
        size = self._delta_buf_size.get(run_id, 0) + len(text)
        self._delta_buf_size[run_id] = size
        now = time.monotonic()
        last = self._delta_last_flush.get(run_id, 0.0)
        if size >= 256 or (now - last) >= 0.15:
            await self._flush_delta(run_id, run_row, span_id)

    async def _flush_delta(
        self, run_id: str, run_row: dict[str, Any], span_id: str | None
    ) -> None:
        buf = self._delta_buf.get(run_id) or []
        if not buf:
            return
        text = "".join(buf)
        self._delta_buf[run_id] = []
        self._delta_buf_size[run_id] = 0
        self._delta_last_flush[run_id] = time.monotonic()
        self._emit_and_update(
            run_id,
            events=[
                self._event(
                    run_row,
                    EventType.text_delta,
                    {"text": self.redactor.redact_text(text)},
                    span_id=span_id,
                )
            ],
        )

    async def _finish_wall_timeout(
        self,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
    ) -> RunResult:
        await self._kill_descendants(run_id)
        checkpoint = self.storage.load_checkpoint(run_id)
        messages = self._checkpoint_messages_for_run(run_id)
        current_run_messages = self.storage.get_messages(run_id)
        output = "".join(
            message.content
            for message in current_run_messages
            if message.role == MessageRole.assistant
        )
        return self._finish(
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

    def _finish(
        self,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None,
        status: RunStatus,
        output: str,
        usage: Usage,
        steps: int,
        error: str | None,
        messages: list[Message] | None = None,
    ) -> RunResult:
        run = self.storage.get_run(run_id)
        etype = {
            RunStatus.completed: EventType.run_completed,
            RunStatus.failed: EventType.run_failed,
            RunStatus.cancelled: EventType.run_cancelled,
            RunStatus.interrupted: EventType.run_interrupted,
        }.get(status, EventType.run_status)
        if run:
            # Never wipe completed tool ids — resume must skip finished tools.
            completed = set(self._completed_tool_ids.get(run_id, set()))
            cp = self.storage.load_checkpoint(run_id)
            if cp:
                completed |= set(cp.completed_tool_call_ids)
            pending: list[ToolCall] = []
            if status in (RunStatus.interrupted, RunStatus.cancelled):
                pending = [
                    tc
                    for tc in (
                        self._pending_tool_calls.get(run_id)
                        or (cp.pending_tool_calls if cp else [])
                    )
                    if tc.id not in completed
                ]
            # Keep multi-turn session history in the terminal checkpoint for resume.
            # Do NOT replace with storage-only rows (they lack prior-run context).
            if messages is not None:
                self._run_messages[run_id] = messages
            finish_messages = self._checkpoint_messages_for_run(run_id)
            if messages is not None and len(messages) >= len(finish_messages):
                finish_messages = list(messages)
            self._checkpoint(
                run_id,
                phase="terminal" if status == RunStatus.completed else "tool_batch",
                step=steps,
                messages=finish_messages,
                pending=pending,
                completed=completed,
                usage=usage,
                status=status,
            )
            self._emit_and_update(
                run_id,
                status=status,
                finished=True,
                error=error,
                output_summary=output[:2000],
                usage=usage,
                steps=steps,
                events=[
                    self._event(
                        run,
                        etype,
                        {
                            "status": status.value,
                            "error": error,
                            "output_len": len(output),
                            "usage": usage.model_dump(),
                        },
                    )
                ],
            )
            self.storage.clear_stop_request(run_id)
        return RunResult(
            run_id=run_id,
            session_id=session_id,
            status=status,
            output=self.redactor.redact_text(output),
            error=error,
            usage=usage,
            steps=steps,
            parent_run_id=parent_run_id,
            root_run_id=root_run_id,
            finished_at=datetime.now(UTC),
        )

    def _mark_interrupted(self, run_id: str, reason: str) -> None:
        run = self.storage.get_run(run_id)
        if not run:
            return
        self._emit_and_update(
            run_id,
            status=RunStatus.interrupted,
            finished=True,
            error=reason,
            events=[
                self._event(run, EventType.run_interrupted, {"reason": reason})
            ],
        )
        self.storage.clear_stop_request(run_id)

    def _mark_failed(self, run_id: str, error: str) -> None:
        run = self.storage.get_run(run_id)
        if not run:
            return
        self._emit_and_update(
            run_id,
            status=RunStatus.failed,
            finished=True,
            error=error,
            events=[self._event(run, EventType.run_failed, {"error": error})],
        )
        self.storage.clear_stop_request(run_id)
