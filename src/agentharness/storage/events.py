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
        run_sequences: dict[str, int] = {}
        run_ids = list(dict.fromkeys(event.run_id for event in events))
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            rows = self._conn.execute(
                f"""SELECT run_id, COALESCE(MAX(run_seq), 0) AS max_run_seq
                    FROM events WHERE run_id IN ({placeholders}) GROUP BY run_id""",
                run_ids,
            ).fetchall()
            run_sequences = {str(row["run_id"]): int(row["max_run_seq"]) for row in rows}
        for ev in events:
            payload = self.redactor.redact_obj(ev.payload)
            run_seq = run_sequences.get(ev.run_id, 0) + 1
            run_sequences[ev.run_id] = run_seq
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
        return assigned

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

    def count_events(self, run_id: str) -> int:
        """True per-run event count, independent of any read limit."""
        row = self._reader().execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

    def count_events_by_type(self, event_type: str) -> int:
        """Global count of events of a type (used by the metrics summary)."""
        row = self._reader().execute(
            "SELECT COUNT(*) FROM events WHERE type = ?", (event_type,)
        ).fetchone()
        return int(row[0]) if row else 0

    def get_context_manifests(self, run_id: str) -> list[dict[str, Any]]:
        """Return redacted, ordered per-model-turn context manifests.

        Uses tail semantics: on very long runs the newest window of events is
        what still matters (the same window the report timeline shows).
        """
        manifests: list[dict[str, Any]] = []
        for event in self.get_events_tail(run_id=run_id, limit=10_000):
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

    def get_events_tail(self, run_id: str, limit: int = 500) -> list[EventEnvelope]:
        """Return the newest ``limit`` events for a run in ascending seq order.

        This is the tail semantic used by bounded report windows: a plain
        ``get_events(run_id=..., limit=N)`` returns the *oldest* N events and
        silently drops the terminal events of a run longer than N.
        """
        if limit <= 0:
            return []
        rows = self._reader().execute(
            """SELECT * FROM events
               WHERE run_id = ?
               ORDER BY global_seq DESC LIMIT ?""",
            (run_id, limit),
        ).fetchall()
        return [self._row_to_event(row) for row in reversed(rows)]

    def iter_events_after(
        self, after_global_seq: int = 0, *, page_size: int = 10_000
    ) -> Iterator[EventEnvelope]:
        """Yield every event after ``after_global_seq``, paging by ``page_size``.

        The previous single-page implementation silently stopped at 10k events;
        callers that need the full log (SSE replays, evidence derivation) now
        get every row regardless of length.
        """
        after = after_global_seq
        while True:
            page = self.get_events(after_global_seq=after, limit=page_size)
            if not page:
                break
            yield from page
            after = int(page[-1].global_seq)
            if len(page) < page_size:
                break

    def max_global_seq(self) -> int:
        row = self._reader().execute(
            "SELECT COALESCE(MAX(global_seq), 0) FROM events"
        ).fetchone()
        return int(row[0])

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
