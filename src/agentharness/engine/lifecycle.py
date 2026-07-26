"""Run checkpointing and terminal-state handling."""

from __future__ import annotations

from datetime import UTC, datetime

from agentharness.contracts import (
    Checkpoint,
    EventType,
    Message,
    RunResult,
    RunStatus,
    ToolCall,
    Usage,
)
from agentharness.engine.events import EventEmitter
from agentharness.engine.run_state import RunContext, ensure_ctx
from agentharness.engine.tool_execution import tool_call_completed
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage

RESUMABLE_STATUSES = frozenset(
    {
        RunStatus.interrupted,
        RunStatus.cancelled,
        RunStatus.waiting_approval,
        RunStatus.require_human,
    }
)


class RunLifecycle:
    """Persists run progress (checkpoints) and finalizes terminal state.

    Every checkpoint must keep resume invariants: completed tool ids are never
    wiped, pending tool calls shrink only when completed, and the message list
    retains spliced multi-turn session history.
    """

    def __init__(
        self,
        *,
        storage: Storage,
        runs: dict[str, RunContext],
        events: EventEmitter,
        redactor: Redactor,
    ) -> None:
        self.storage = storage
        self._runs = runs
        self.events = events
        self.redactor = redactor

    def _ctx(self, run_id: str) -> RunContext:
        return ensure_ctx(self._runs, run_id)

    def checkpoint_messages(self, run_id: str) -> list[Message]:
        """Prefer in-memory (session-history-aware) messages over storage-only rows.

        Storage only holds this run's messages; multi-turn history lives in the
        in-memory list / prior checkpoint and must survive interrupt/resume.
        """
        ctx = self._runs.get(run_id)
        mem = ctx.run_messages if ctx else None
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

    def preserve_checkpoint(
        self,
        run_id: str,
        *,
        status: RunStatus,
        phase: str = "tool_batch",
    ) -> None:
        """Checkpoint without wiping completed tool ids (resume safety)."""
        cp = self.storage.load_checkpoint(run_id)
        ctx = self._ctx(run_id)
        completed = set(ctx.completed_tool_ids)
        pending = list(ctx.pending_tool_calls)
        step = 0
        usage = Usage()
        if cp:
            completed |= set(cp.completed_tool_call_ids)
            if not pending:
                pending = [
                    tc
                    for tc in cp.pending_tool_calls
                    if not tool_call_completed(tc, completed)
                ]
            step = cp.step
            usage = cp.usage
        messages = self.checkpoint_messages(run_id)
        metadata = dict(cp.metadata) if cp else {}
        if ctx.context_state is not None:
            metadata["context_state"] = ctx.context_state.model_dump(mode="json")
        metadata["verification_attempt"] = ctx.verification_attempt
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
                metadata=metadata,
            )
        )
        ctx.completed_tool_ids = completed

    def checkpoint(
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
        current = self.storage.load_checkpoint(run_id)
        metadata = dict(current.metadata) if current else {}
        context_state = self._ctx(run_id).context_state
        if context_state is not None:
            metadata["context_state"] = context_state.model_dump(mode="json")
        metadata["verification_attempt"] = self._ctx(run_id).verification_attempt
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
            metadata=metadata,
        )
        self.storage.save_checkpoint(cp)
        run = self.storage.get_run(run_id)
        if run:
            self.events.emit_and_update(
                run_id,
                events=[
                    self.events.event(
                        run,
                        EventType.checkpoint,
                        {"phase": phase, "step": step},
                    )
                ],
            )

    def finish(
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
            finish_ctx = self._runs.get(run_id)
            # Never wipe completed tool ids — resume must skip finished tools.
            completed = set(finish_ctx.completed_tool_ids if finish_ctx else set())
            cp = self.storage.load_checkpoint(run_id)
            if cp:
                completed |= set(cp.completed_tool_call_ids)
            pending: list[ToolCall] = []
            if status in RESUMABLE_STATUSES:
                pending = [
                    tc
                    for tc in (
                        (finish_ctx.pending_tool_calls if finish_ctx else None)
                        or (cp.pending_tool_calls if cp else [])
                    )
                    if not tool_call_completed(tc, completed)
                ]
            # Keep multi-turn session history in the terminal checkpoint for resume.
            # Do NOT replace with storage-only rows (they lack prior-run context).
            if messages is not None:
                self._ctx(run_id).run_messages = messages
            finish_messages = self.checkpoint_messages(run_id)
            if messages is not None and len(messages) >= len(finish_messages):
                finish_messages = list(messages)
            self.checkpoint(
                run_id,
                phase="terminal" if status == RunStatus.completed else "tool_batch",
                step=steps,
                messages=finish_messages,
                pending=pending,
                completed=completed,
                usage=usage,
                status=status,
            )
            self.events.emit_and_update(
                run_id,
                status=status,
                finished=True,
                error=error,
                output_summary=output[:2000],
                usage=usage,
                steps=steps,
                events=[
                    self.events.event(
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

    def mark_interrupted(self, run_id: str, reason: str) -> None:
        run = self.storage.get_run(run_id)
        if not run:
            return
        self.events.emit_and_update(
            run_id,
            status=RunStatus.interrupted,
            finished=True,
            error=reason,
            events=[
                self.events.event(run, EventType.run_interrupted, {"reason": reason})
            ],
        )
        self.storage.clear_stop_request(run_id)

    def mark_failed(self, run_id: str, error: str) -> None:
        run = self.storage.get_run(run_id)
        if not run:
            return
        self.events.emit_and_update(
            run_id,
            status=RunStatus.failed,
            finished=True,
            error=error,
            events=[self.events.event(run, EventType.run_failed, {"error": error})],
        )
        self.storage.clear_stop_request(run_id)
