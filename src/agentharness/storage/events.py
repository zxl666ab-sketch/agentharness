"""Append-only event log with per-run and global sequence numbers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from agentharness.contracts import EventEnvelope, EventType
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps


class EventRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

    def append_unlocked(self, events: list[EventEnvelope]) -> list[EventEnvelope]:
        assigned: list[EventEnvelope] = []
        for ev in events:
            payload = self.redactor.redact_obj(ev.payload)
            # next run_seq
            row = self._conn.execute(
                "SELECT COALESCE(MAX(run_seq), 0) FROM events WHERE run_id = ?",
                (ev.run_id,),
            ).fetchone()
            run_seq = int(row[0]) + 1
            cur = self._conn.execute(
                """INSERT INTO events(
                    event_id, run_seq, session_id, root_run_id, run_id,
                    parent_run_id, span_id, parent_span_id, type, timestamp,
                    payload_json, schema_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ev.event_id,
                    run_seq,
                    ev.session_id,
                    ev.root_run_id,
                    ev.run_id,
                    ev.parent_run_id,
                    ev.span_id,
                    ev.parent_span_id,
                    ev.type.value if isinstance(ev.type, EventType) else str(ev.type),
                    ev.timestamp.isoformat()
                    if hasattr(ev.timestamp, "isoformat")
                    else str(ev.timestamp),
                    _dumps(payload),
                    ev.schema_version,
                ),
            )
            global_seq = cur.lastrowid or 0
            out = ev.model_copy(
                update={
                    "global_seq": int(global_seq),
                    "run_seq": run_seq,
                    "payload": payload,
                }
            )
            assigned.append(out)
        if assigned:
            # Keep the durable watermark ahead of every assigned row: GC of the
            # events table (or a fresh read-only replica) must never be able to
            # pull the restart seed backwards (LIVE-1). Runs inside the caller's
            # transaction, so the row and the watermark commit together.
            self._bump_global_seq_unlocked(
                max(int(event.global_seq or 0) for event in assigned)
            )
        return assigned

    def _bump_global_seq_unlocked(self, seq: int) -> None:
        """MAX-upsert the watermark; caller owns the transaction."""
        self._conn.execute(
            """INSERT INTO global_seq_counter(id, seq) VALUES(1, ?)
               ON CONFLICT(id) DO UPDATE
                  SET seq = MAX(global_seq_counter.seq, excluded.seq)""",
            (max(0, int(seq)),),
        )

    def append_events(self, events: list[EventEnvelope]) -> list[EventEnvelope]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                assigned = self.append_unlocked(events)
                self._conn.execute("COMMIT")
                return assigned
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get_events(
        self,
        run_id: str | None = None,
        after_global_seq: int = 0,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        reader = self._reader()
        if run_id:
            rows = reader.execute(
                """SELECT * FROM events
                   WHERE run_id = ? AND global_seq > ?
                   ORDER BY global_seq ASC LIMIT ?""",
                (run_id, after_global_seq, limit),
            ).fetchall()
        else:
            rows = reader.execute(
                """SELECT * FROM events
                   WHERE global_seq > ?
                   ORDER BY global_seq ASC LIMIT ?""",
                (after_global_seq, limit),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_context_manifests(self, run_id: str) -> list[dict[str, Any]]:
        """Return redacted, ordered per-model-turn context manifests."""
        manifests: list[dict[str, Any]] = []
        for event in self.get_events(run_id=run_id, limit=10_000):
            event_type = event.type.value if isinstance(event.type, EventType) else str(event.type)
            if event_type != "context_manifest":
                continue
            raw = event.payload.get("manifest")
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["artifact_id"] = event.payload.get("artifact_id")
            item["event_id"] = event.event_id
            item["global_seq"] = event.global_seq
            manifests.append(self.redactor.redact_obj(item))
        return manifests

    def iter_events_after(self, after_global_seq: int = 0) -> Iterator[EventEnvelope]:
        yield from self.get_events(after_global_seq=after_global_seq, limit=10_000)

    def max_event_seq(self) -> int:
        """Highest ``global_seq`` present in the append-only events table."""
        row = self._reader().execute(
            "SELECT COALESCE(MAX(global_seq), 0) FROM events"
        ).fetchone()
        return int(row[0])

    def global_seq_watermark(self) -> int:
        """Persisted high-water mark of every *emitted* event (LIVE-1).

        Heartbeats and gateway events are published straight to Kafka and never
        land in the events table, so the events table alone cannot tell where
        the durable sequence stands after a restart.
        """
        row = self._reader().execute(
            "SELECT seq FROM global_seq_counter WHERE id = 1"
        ).fetchone()
        return int(row[0]) if row else 0

    def bump_global_seq(self, seq: int) -> int:
        """Raise the durable event high-water mark (single-row upsert, MAX).

        Returns the watermark actually stored, so a caller that raced another
        writer can adopt the higher value instead of regressing it. Called from
        inside an open transaction it joins that transaction instead of trying
        to nest a `BEGIN` (the writer lock is re-entrant per thread).
        """
        value = max(0, int(seq))
        with self._lock:
            if self._conn.in_transaction:
                self._bump_global_seq_unlocked(value)
                return self._current_global_seq_unlocked()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._bump_global_seq_unlocked(value)
                stored = self._current_global_seq_unlocked()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return stored

    def _current_global_seq_unlocked(self) -> int:
        row = self._conn.execute(
            "SELECT seq FROM global_seq_counter WHERE id = 1"
        ).fetchone()
        return int(row[0]) if row else 0

    def max_global_seq(self) -> int:
        """Durable high-water mark: event rows and emitted heartbeats."""
        return max(self.max_event_seq(), self.global_seq_watermark())

    def explain_query_plan(self, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
        """Return the EXPLAIN QUERY PLAN detail rows for a read query (test/diagnostics)."""
        rows = self._reader().execute(
            f"EXPLAIN QUERY PLAN {sql}", params
        ).fetchall()
        return [str(r["detail"]) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            schema_version=row["schema_version"],
            event_id=row["event_id"],
            global_seq=row["global_seq"],
            run_seq=row["run_seq"],
            session_id=row["session_id"],
            root_run_id=row["root_run_id"],
            run_id=row["run_id"],
            parent_run_id=row["parent_run_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            type=row["type"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            payload=json.loads(row["payload_json"]),
        )
