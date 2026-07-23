"""SQLite WAL storage — sessions, runs, messages, events, approvals, memories, artifacts."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    Checkpoint,
    EventEnvelope,
    EventType,
    Message,
    RunStatus,
    Usage,
    new_id,
)
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.artifacts import ArtifactStore
from agentharness.storage.migrations import apply_migrations


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Storage:
    """Thread-safe SQLite store with WAL. Single-writer transactions for status+events."""

    def __init__(
        self,
        data_dir: Path | str,
        redactor: Redactor | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "agentharness.db"
        self.redactor = redactor or default_redactor
        self.artifacts = ArtifactStore(self.data_dir / "artifacts", redactor=self.redactor)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # manual transactions
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            apply_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def integrity_check(self) -> str:
        with self._lock:
            row = self._conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row[0]) if row else 0

    # -- sessions -----------------------------------------------------------

    def create_session(self, session_id: str | None = None, title: str | None = None) -> str:
        sid = session_id or new_id()
        now = _utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
                (sid, self.redactor.redact_text(title or "session"), now, now),
            )
        return sid

    def session_exists(self, session_id: str) -> bool:
        return self.get_session(session_id) is not None

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        touch: bool = False,
    ) -> None:
        """Update session title and/or bump updated_at (top-level dialogue only)."""
        now = _utcnow()
        with self._lock:
            if title is not None and touch:
                self._conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (self.redactor.redact_text(title), now, session_id),
                )
            elif title is not None:
                self._conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ?",
                    (self.redactor.redact_text(title), session_id),
                )
            elif touch:
                self._conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sessions.*
                FROM sessions
                LEFT JOIN (
                    SELECT session_id, MAX(rowid) AS activity_order
                    FROM runs
                    WHERE parent_run_id IS NULL
                    GROUP BY session_id
                ) AS recent ON recent.session_id = sessions.id
                ORDER BY COALESCE(recent.activity_order, 0) DESC, sessions.rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_top_level_runs(self, session_id: str) -> list[dict[str, Any]]:
        """Top-level runs for a session ordered by created_at ascending."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM runs
                   WHERE session_id = ?
                     AND (parent_run_id IS NULL OR parent_run_id = '')
                   ORDER BY created_at ASC, id ASC""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_completed_top_level_runs(self, session_id: str) -> list[dict[str, Any]]:
        """Completed top-level runs only (eligible for multi-turn context history)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM runs
                   WHERE session_id = ?
                     AND (parent_run_id IS NULL OR parent_run_id = '')
                     AND status = 'completed'
                   ORDER BY created_at ASC, id ASC""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session_history_messages(self, session_id: str) -> list[Message]:
        """Full messages from all completed top-level runs, ordered by run then seq.

        Excludes failed/cancelled/interrupted and any run with non-null parent_run_id.
        """
        from agentharness.session_history import assemble_session_history_messages

        runs = self.list_completed_top_level_runs(session_id)
        messages_by_run: dict[str, list[Message]] = {}
        for run in runs:
            messages_by_run[run["id"]] = self.get_messages(run["id"])
        return assemble_session_history_messages(runs, messages_by_run)

    # -- runs ---------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None = None,
        status: RunStatus = RunStatus.pending,
        provider: str | None = None,
        model: str | None = None,
        approval: str | None = None,
        cwd: str | None = None,
        delegate_depth: int = 0,
        allow_write: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _utcnow()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    """INSERT INTO runs(
                        id, session_id, parent_run_id, root_run_id, status,
                        provider, model, approval, cwd, delegate_depth, allow_write,
                        metadata_json, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        session_id,
                        parent_run_id,
                        root_run_id,
                        status.value,
                        self.redactor.redact_text(provider or "") or None,
                        self.redactor.redact_text(model or "") or None,
                        self.redactor.redact_text(approval or "") or None,
                        self.redactor.redact_text(cwd or "") or None,
                        delegate_depth,
                        1 if allow_write else 0,
                        _dumps(self.redactor.redact_obj(metadata or {})),
                        now,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        error: str | None = None,
        output_summary: str | None = None,
        usage: Usage | None = None,
        steps: int | None = None,
        finished: bool = False,
        events: list[EventEnvelope] | None = None,
    ) -> list[EventEnvelope]:
        """Update run status and optionally append events in one transaction.

        Returns the events with assigned global_seq / run_seq.
        """
        now = _utcnow()
        assigned: list[EventEnvelope] = []
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                fields: list[str] = ["updated_at = ?"]
                values: list[Any] = [now]
                if status is not None:
                    fields.append("status = ?")
                    values.append(status.value)
                if error is not None:
                    fields.append("error = ?")
                    values.append(self.redactor.redact_text(error))
                if output_summary is not None:
                    fields.append("output_summary = ?")
                    values.append(self.redactor.redact_text(output_summary)[:2000])
                if usage is not None:
                    fields.append("usage_json = ?")
                    values.append(usage.model_dump_json())
                if steps is not None:
                    fields.append("steps = ?")
                    values.append(steps)
                if finished:
                    fields.append("finished_at = ?")
                    values.append(now)
                values.append(run_id)
                self._conn.execute(
                    f"UPDATE runs SET {', '.join(fields)} WHERE id = ?",
                    values,
                )
                if events:
                    assigned = self._append_events_unlocked(events)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return assigned

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        """Return run and all descendants."""
        run = self.get_run(run_id)
        if not run:
            return []
        root = run["root_run_id"] or run_id
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE root_run_id = ? ORDER BY created_at ASC",
                (root,),
            ).fetchall()
        return [dict(r) for r in rows]

    def request_stop(self, run_id: str, mode: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO stop_requests(run_id, mode, requested_at)
                   VALUES(?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     mode=excluded.mode, requested_at=excluded.requested_at""",
                (run_id, mode, _utcnow()),
            )

    def get_stop_request(self, run_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT mode FROM stop_requests WHERE run_id = ?", (run_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def clear_stop_request(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM stop_requests WHERE run_id = ?", (run_id,))

    # -- events -------------------------------------------------------------

    def _append_events_unlocked(self, events: list[EventEnvelope]) -> list[EventEnvelope]:
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
        return assigned

    def append_events(self, events: list[EventEnvelope]) -> list[EventEnvelope]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                assigned = self._append_events_unlocked(events)
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
        with self._lock:
            if run_id:
                rows = self._conn.execute(
                    """SELECT * FROM events
                       WHERE run_id = ? AND global_seq > ?
                       ORDER BY global_seq ASC LIMIT ?""",
                    (run_id, after_global_seq, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM events
                       WHERE global_seq > ?
                       ORDER BY global_seq ASC LIMIT ?""",
                    (after_global_seq, limit),
                ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def iter_events_after(self, after_global_seq: int = 0) -> Iterator[EventEnvelope]:
        yield from self.get_events(after_global_seq=after_global_seq, limit=10_000)

    def max_global_seq(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(global_seq), 0) FROM events").fetchone()
        return int(row[0])

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

    # -- messages -----------------------------------------------------------

    def save_message(self, run_id: str, session_id: str, message: Message, seq: int) -> None:
        content = self.redactor.redact_text(message.content)
        tool_calls = (
            self.redactor.redact_obj([tc.model_dump() for tc in message.tool_calls])
            if message.tool_calls
            else None
        )
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO messages(
                    id, run_id, session_id, role, content, tool_call_id, name,
                    tool_calls_json, seq, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    message.id,
                    run_id,
                    session_id,
                    message.role.value if hasattr(message.role, "value") else message.role,
                    content,
                    message.tool_call_id,
                    message.name,
                    _dumps(tool_calls) if tool_calls is not None else None,
                    seq,
                    message.created_at.isoformat()
                    if hasattr(message.created_at, "isoformat")
                    else str(message.created_at),
                ),
            )

    def get_messages(self, run_id: str) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
        result: list[Message] = []
        for r in rows:
            tcs = None
            if r["tool_calls_json"]:
                from agentharness.contracts import ToolCall

                tcs = [ToolCall.model_validate(x) for x in json.loads(r["tool_calls_json"])]
            result.append(
                Message(
                    id=r["id"],
                    role=r["role"],
                    content=r["content"] or "",
                    tool_call_id=r["tool_call_id"],
                    name=r["name"],
                    tool_calls=tcs,
                )
            )
        return result

    # -- checkpoints --------------------------------------------------------

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        data = self.redactor.redact_obj(checkpoint.model_dump(mode="json"))
        with self._lock:
            self._conn.execute(
                """INSERT INTO checkpoints(run_id, phase, step, data_json, created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     phase=excluded.phase, step=excluded.step,
                     data_json=excluded.data_json, created_at=excluded.created_at""",
                (
                    checkpoint.run_id,
                    checkpoint.phase,
                    checkpoint.step,
                    _dumps(data),
                    _utcnow(),
                ),
            )

    def load_checkpoint(self, run_id: str) -> Checkpoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return Checkpoint.model_validate(json.loads(row["data_json"]))

    # -- approvals ----------------------------------------------------------

    def save_approval(self, approval: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO approvals(
                    id, run_id, tool_call_id, tool_name, effect,
                    arguments_summary, decision, created_at, resolved_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    approval["id"],
                    approval["run_id"],
                    approval["tool_call_id"],
                    approval["tool_name"],
                    approval["effect"],
                    self.redactor.redact_text(approval.get("arguments_summary") or ""),
                    approval.get("decision"),
                    approval.get("created_at", _utcnow()),
                    approval.get("resolved_at"),
                ),
            )

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- memories (FTS5) ----------------------------------------------------

    def add_memory(
        self,
        content: str,
        *,
        source: str = "tool",
        scope: str = "global",
        memory_id: str | None = None,
    ) -> str:
        mid = memory_id or new_id()
        now = _utcnow()
        content = self.redactor.redact_text(content)
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    """INSERT INTO memories(id, content, source, scope, created_at, last_used_at)
                       VALUES(?,?,?,?,?,?)""",
                    (mid, content, source, scope, now, now),
                )
                # FTS content sync (external content table)
                self._conn.execute(
                    """INSERT INTO memories_fts(rowid, content, source, scope)
                       SELECT rowid, content, source, scope FROM memories WHERE id = ?""",
                    (mid,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return mid

    def search_memories(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT m.* FROM memories m
                       JOIN memories_fts f ON m.rowid = f.rowid
                       WHERE memories_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # fallback LIKE
                rows = self._conn.execute(
                    """SELECT * FROM memories WHERE content LIKE ? LIMIT ?""",
                    (f"%{query}%", limit),
                ).fetchall()
            # touch last_used
            for r in rows:
                self._conn.execute(
                    "UPDATE memories SET last_used_at = ? WHERE id = ?",
                    (_utcnow(), r["id"]),
                )
        return [dict(r) for r in rows]

    # -- artifacts meta -----------------------------------------------------

    def register_artifact(self, meta: dict[str, Any]) -> str:
        safe = self.redactor.redact_obj(meta)
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM artifacts WHERE sha256 = ?", (safe["sha256"],)
            ).fetchone()
            if existing:
                return str(existing[0])
            self._conn.execute(
                """INSERT OR IGNORE INTO artifacts(
                    id, sha256, content_type, size_bytes, summary, path, created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    safe["id"],
                    safe["sha256"],
                    safe.get("content_type"),
                    safe.get("size_bytes"),
                    safe.get("summary"),
                    safe["path"],
                    safe.get("created_at", _utcnow()),
                ),
            )
        return str(safe["id"])

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_artifact_by_sha(self, sha: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE sha256 = ?", (sha,)
            ).fetchone()
        return dict(row) if row else None
