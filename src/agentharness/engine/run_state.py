"""Shared per-run in-memory state for the engine and its collaborators."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agentharness.contracts import ContextState, Message, ToolCall


@dataclass
class RunContext:
    """All per-run in-memory engine state, grouped so a run's state is created and
    torn down atomically. Replacing 13 parallel ``dict[run_id, ...]`` maps with one
    ``dict[run_id, RunContext]`` means cleanup is a single ``pop`` — a newly added
    field cannot be forgotten in ``_cleanup_run_state``, and a failure mid-teardown
    cannot leave some fields lingering while others are freed.
    """

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_scopes: set[str] = field(default_factory=set)
    child_runs: list[str] = field(default_factory=list)
    delta_buf: list[str] = field(default_factory=list)
    delta_buf_size: int = 0
    delta_last_flush: float = 0.0
    # Completed tool ids must survive interrupt/cancel checkpoints for resume.
    completed_tool_ids: set[str] = field(default_factory=set)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    stop_mode: str | None = None  # "cancel" | "interrupt"
    # In-memory message list (incl. session-history splice) for resume-safe checkpoints.
    run_messages: list[Message] = field(default_factory=list)
    # Opaque ContextPlanner selection; persisted in every checkpoint.
    context_state: ContextState | None = None
    verification_attempt: int = 0
    provider_owner_task: asyncio.Task[Any] | None = None
    lease_heartbeat_task: asyncio.Task[Any] | None = None
    tool_call_count: int = 0
    indeterminate_reason: str | None = None
    # Wall-clock budget: wall_started anchors active time; wall_paused_s
    # accumulates time parked at a human approval gate (waiting_approval)
    # so waiting for the buyer never consumes the run budget.
    wall_started: float = 0.0
    wall_paused_s: float = 0.0
    wall_pause_started: float | None = None


def ensure_ctx(runs: dict[str, RunContext], run_id: str) -> RunContext:
    """Return (creating if needed) the RunContext for a run.

    The registry stays a plain dict shared by the engine and its collaborators so
    per-run state has exactly one owner and teardown remains a single ``pop``.
    """
    ctx = runs.get(run_id)
    if ctx is None:
        ctx = RunContext()
        runs[run_id] = ctx
    return ctx
