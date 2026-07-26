"""Event envelope construction, persisted fan-out, and text-delta buffering."""

from __future__ import annotations

import time
from typing import Any

from agentharness.contracts import EventEnvelope, EventType, RunStatus, Usage
from agentharness.engine.run_state import RunContext, ensure_ctx
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage


class EventEmitter:
    """Builds event envelopes, persists them together with run updates, and fans
    them out to live observers via the harness notify hook. Also owns the per-run
    text-delta buffer so streamed model output becomes bounded ``text_delta``
    events instead of one event per token.
    """

    def __init__(
        self,
        *,
        storage: Storage,
        runs: dict[str, RunContext],
        redactor: Redactor,
        harness: Any = None,
    ) -> None:
        self.storage = storage
        self._runs = runs
        self.redactor = redactor
        self.harness = harness

    def event(
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

    def emit_and_update(
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

    async def buffer_delta(
        self, run_id: str, run_row: dict[str, Any], text: str, span_id: str
    ) -> None:
        ctx = ensure_ctx(self._runs, run_id)
        ctx.delta_buf.append(text)
        ctx.delta_buf_size += len(text)
        now = time.monotonic()
        if ctx.delta_buf_size >= 256 or (now - ctx.delta_last_flush) >= 0.15:
            await self.flush_delta(run_id, run_row, span_id)

    async def flush_delta(
        self, run_id: str, run_row: dict[str, Any], span_id: str | None
    ) -> None:
        ctx = self._runs.get(run_id)
        buf = ctx.delta_buf if ctx else []
        if not buf or ctx is None:
            return
        text = "".join(buf)
        ctx.delta_buf = []
        ctx.delta_buf_size = 0
        ctx.delta_last_flush = time.monotonic()
        self.emit_and_update(
            run_id,
            events=[
                self.event(
                    run_row,
                    EventType.text_delta,
                    {"text": self.redactor.redact_text(text)},
                    span_id=span_id,
                )
            ],
        )
