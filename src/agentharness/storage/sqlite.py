"""SQLite WAL storage — sessions, runs, messages, events, approvals, memories, artifacts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    Checkpoint,
    EventEnvelope,
    EventType,
    Message,
    RunStatus,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
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


def _upgrade_checkpoint_payload(payload: Any) -> Any:
    """Give pre-v8 tool calls stable invocation ids before Pydantic supplies random ones."""
    if not isinstance(payload, dict):
        return payload
    run_id = str(payload.get("run_id") or "legacy")
    step = int(payload.get("step") or 0)

    pending = payload.get("pending_tool_calls")
    if isinstance(pending, list):
        for index, call in enumerate(pending):
            if not isinstance(call, dict) or call.get("invocation_id"):
                continue
            ordinal = int(call.get("ordinal", index) or 0)
            provider_id = str(call.get("id") or "")
            seed = f"{run_id}:pending:{step}:{ordinal}:{index}:{provider_id}"
            call["invocation_id"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message_index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_index, call in enumerate(tool_calls):
                if not isinstance(call, dict) or call.get("invocation_id"):
                    continue
                provider_id = str(call.get("id") or "")
                seed = f"{run_id}:message:{message_index}:{tool_index}:{provider_id}"
                call["invocation_id"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return payload


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
            self._backfill_memory_metadata_unlocked()
        # Per-thread read-only connections. WAL lets readers run concurrently with the
        # single writer (self._conn) without acquiring self._lock, so API reads no longer
        # contend with child-run event writes. Each thread gets its own RO connection
        # because a single sqlite3 connection is not safe for concurrent use.
        self._read_local = threading.local()
        self._read_conns: list[sqlite3.Connection] = []
        self._read_conns_lock = threading.Lock()

    def _reader(self) -> sqlite3.Connection:
        """Return this thread's read-only connection, creating it on first use."""
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        with self._read_conns_lock:
            for conn in self._read_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._read_conns.clear()
        self._read_local = threading.local()

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

    # -- run ownership / lifecycle ----------------------------------------

    def acquire_run_lease(self, run_id: str, owner_id: str, *, ttl_s: float) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=ttl_s)).isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT owner_id, expires_at FROM run_leases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row and row[0] != owner_id and str(row[1]) > now:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(
                    """INSERT INTO run_leases(
                           run_id, owner_id, acquired_at, heartbeat_at, expires_at
                       ) VALUES(?,?,?,?,?)
                       ON CONFLICT(run_id) DO UPDATE SET
                           owner_id=excluded.owner_id,
                           acquired_at=excluded.acquired_at,
                           heartbeat_at=excluded.heartbeat_at,
                           expires_at=excluded.expires_at""",
                    (run_id, owner_id, now, now, expires),
                )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def heartbeat_run_lease(self, run_id: str, owner_id: str, *, ttl_s: float) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=ttl_s)).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE run_leases
                   SET heartbeat_at = ?, expires_at = ?
                   WHERE run_id = ? AND owner_id = ?""",
                (now, expires, run_id, owner_id),
            )
        return cursor.rowcount == 1

    def release_run_lease(self, run_id: str, owner_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM run_leases WHERE run_id = ? AND owner_id = ?",
                (run_id, owner_id),
            )

    def recover_expired_run_leases(self) -> list[str]:
        """Mark only expired, actively running leases as process-lost."""
        now = _utcnow()
        recovered: list[str] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT r.* FROM runs AS r
                       JOIN run_leases AS lease ON lease.run_id = r.id
                       WHERE lease.expires_at <= ?
                         AND r.status IN ('pending', 'running', 'waiting_approval')""",
                    (now,),
                ).fetchall()
                for row in rows:
                    run = dict(row)
                    run_id = str(run["id"])
                    recovered.append(run_id)
                    self._conn.execute(
                        """UPDATE runs
                           SET status = 'interrupted', error = 'process_lost',
                               updated_at = ?, finished_at = ?
                           WHERE id = ?""",
                        (now, now, run_id),
                    )
                    self._conn.execute(
                        """UPDATE approvals
                           SET status = 'expired', resolved_at = ?
                           WHERE run_id = ? AND status = 'pending' AND decision IS NULL""",
                        (now, run_id),
                    )
                    self._conn.execute(
                        """UPDATE tool_attempts
                           SET status = 'indeterminate',
                               error_code = 'outcome_indeterminate',
                               error_category = 'recovery', finished_at = ?
                           WHERE status = 'running' AND invocation_id IN (
                               SELECT id FROM tool_invocations
                               WHERE run_id = ? AND status = 'running'
                                 AND replay_policy = 'never'
                           )""",
                        (now, run_id),
                    )
                    self._conn.execute(
                        """UPDATE tool_invocations
                           SET status = 'indeterminate',
                               error_code = 'outcome_indeterminate',
                               error_category = 'recovery',
                               updated_at = ?, finished_at = ?
                           WHERE run_id = ? AND status = 'running'
                             AND replay_policy = 'never'""",
                        (now, now, run_id),
                    )
                    checkpoint = self._conn.execute(
                        "SELECT data_json FROM checkpoints WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    if checkpoint:
                        try:
                            payload = json.loads(checkpoint[0] or "{}")
                        except (TypeError, json.JSONDecodeError):
                            payload = {}
                        if isinstance(payload, dict):
                            payload["status"] = RunStatus.interrupted.value
                            metadata = payload.get("metadata")
                            if not isinstance(metadata, dict):
                                metadata = {}
                            metadata["interruption_reason"] = "process_lost"
                            payload["metadata"] = metadata
                            self._conn.execute(
                                "UPDATE checkpoints SET data_json = ?, created_at = ? WHERE run_id = ?",
                                (_dumps(payload), now, run_id),
                            )
                    event = EventEnvelope(
                        session_id=str(run["session_id"]),
                        root_run_id=str(run["root_run_id"]),
                        run_id=run_id,
                        parent_run_id=run.get("parent_run_id"),
                        type=EventType.run_interrupted,
                        payload={"reason": "process_lost"},
                    )
                    self._append_events_unlocked([event])
                self._conn.execute(
                    "DELETE FROM run_leases WHERE expires_at <= ?",
                    (now,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return recovered

    def pin_run(self, run_id: str, note: str | None = None) -> None:
        if self.get_run(run_id) is None:
            raise KeyError(run_id)
        with self._lock:
            self._conn.execute(
                """INSERT INTO run_pins(run_id, pinned_at, note) VALUES(?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       pinned_at=excluded.pinned_at, note=excluded.note""",
                (run_id, _utcnow(), self.redactor.redact_text(note or "") or None),
            )

    def unpin_run(self, run_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM run_pins WHERE run_id = ?", (run_id,)
            )
        return cursor.rowcount == 1

    def list_pins(self) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            "SELECT * FROM run_pins ORDER BY pinned_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

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
        # Enrich the latest top-level run (status/id/error) in the SAME query via a
        # join on the recent-activity rowid, so the observer UI left column needs no
        # per-session follow-up query (was an N+1 in Harness._enrich_session). Reader
        # connection — no writer-lock contention.
        rows = self._reader().execute(
            """
            SELECT sessions.*,
                   COALESCE(
                       (
                           SELECT m.content
                           FROM runs AS first_run
                           JOIN messages AS m ON m.run_id = first_run.id
                           WHERE first_run.session_id = sessions.id
                             AND (first_run.parent_run_id IS NULL OR first_run.parent_run_id = '')
                             AND m.role = 'user'
                           ORDER BY first_run.rowid ASC, m.seq ASC
                           LIMIT 1
                       ),
                       sessions.title
                   ) AS display_title,
                   latest.id AS latest_run_id,
                   latest.status AS latest_status,
                   latest.error AS latest_error,
                   COALESCE(recent.run_count, 0) AS run_count
            FROM sessions
            LEFT JOIN (
                SELECT session_id,
                       MAX(rowid) AS activity_order,
                       COUNT(*) AS run_count
                FROM runs
                WHERE parent_run_id IS NULL
                GROUP BY session_id
            ) AS recent ON recent.session_id = sessions.id
            LEFT JOIN runs AS latest ON latest.rowid = recent.activity_order
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
                   ORDER BY created_at ASC, rowid ASC""",
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
                   ORDER BY created_at ASC, rowid ASC""",
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
        clear_error: bool = False,
        clear_finished_at: bool = False,
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
                if clear_error:
                    fields.append("error = NULL")
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
                elif clear_finished_at:
                    fields.append("finished_at = NULL")
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

    def merge_run_metadata(self, run_id: str, patch: dict[str, Any]) -> None:
        """Merge keys into an existing run's metadata_json (overwrite on collision).

        Terminal runs remain mutable for stable operational metadata. The full
        metadata object is re-redacted on write.
        """
        if not patch:
            return
        now = _utcnow()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT metadata_json FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if not row:
                    self._conn.execute("ROLLBACK")
                    return
                try:
                    meta = json.loads(row[0] or "{}")
                    if not isinstance(meta, dict):
                        meta = {}
                except (TypeError, json.JSONDecodeError):
                    meta = {}
                meta.update(patch)
                self._conn.execute(
                    "UPDATE runs SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (_dumps(self.redactor.redact_obj(meta)), now, run_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # Single enrichment projection reused by get_run / list_runs / get_run_tree so
    # child_count + user_summary + depth come from one statement (no per-row N+1)
    # and every read runs on the RO connection (no writer-lock contention).
    _RUN_PROJECTION = """
        SELECT r.*,
               substr((
                   SELECT m.content FROM messages AS m
                   WHERE m.run_id = r.id AND m.role = 'user'
                   ORDER BY m.seq ASC LIMIT 1
               ), 1, 500) AS user_summary,
               COALESCE(r.delegate_depth, 0) AS depth,
               (
                   SELECT COUNT(*) FROM runs AS child
                   WHERE child.parent_run_id = r.id
               ) AS child_count
        FROM runs AS r
    """

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            self._RUN_PROJECTION + " WHERE r.id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        self._decorate_run_observability(result)
        return result

    def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        reader = self._reader()
        if session_id:
            rows = reader.execute(
                self._RUN_PROJECTION
                + " WHERE r.session_id = ? "
                "ORDER BY r.created_at DESC, r.rowid DESC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        else:
            rows = reader.execute(
                self._RUN_PROJECTION
                + " ORDER BY r.created_at DESC, r.rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            self._decorate_run_observability(row)
        return result

    def _decorate_run_observability(self, row: dict[str, Any]) -> None:
        metadata: dict[str, Any] = {}
        try:
            decoded = json.loads(row.get("metadata_json") or "{}")
            if isinstance(decoded, dict):
                metadata = decoded
        except (TypeError, json.JSONDecodeError):
            pass
        row.setdefault(
            "actor",
            metadata.get("actor") or ("delegate" if row.get("parent_run_id") else "user"),
        )
        row.setdefault("depth", int(row.get("delegate_depth") or 0))
        if "child_count" not in row:
            with self._lock:
                child = self._conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE parent_run_id = ?", (row["id"],)
                ).fetchone()
            row["child_count"] = int(child[0]) if child else 0
        if "user_summary" not in row:
            with self._lock:
                summary = self._conn.execute(
                    """SELECT substr(content, 1, 500) FROM messages
                       WHERE run_id = ? AND role = 'user'
                       ORDER BY seq ASC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
            row["user_summary"] = summary[0] if summary else None

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        """Return run and all descendants (reader path, enriched in one query each)."""
        reader = self._reader()
        head = reader.execute(
            "SELECT root_run_id FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not head:
            return []
        root = head["root_run_id"] or run_id
        rows = reader.execute(
            self._RUN_PROJECTION
            + " WHERE r.root_run_id = ? ORDER BY r.created_at ASC, r.rowid ASC",
            (root,),
        ).fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            self._decorate_run_observability(row)
        return result

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
        # Hot-path watcher poll: use the RO connection so polling never contends
        # with the writer lock. WAL gives the reader the latest committed value.
        row = self._reader().execute(
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

    # -- messages -----------------------------------------------------------

    def save_message(self, run_id: str, session_id: str, message: Message, seq: int) -> None:
        content = self.redactor.redact_text(message.content)
        tool_calls = (
            self.redactor.redact_obj([tc.model_dump() for tc in message.tool_calls])
            if message.tool_calls
            else None
        )
        tool_result = (
            self.redactor.redact_obj(message.tool_result.model_dump(mode="json"))
            if message.tool_result
            else None
        )
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO messages(
                    id, run_id, session_id, role, content, tool_call_id, name,
                    tool_calls_json, tool_result_json, seq, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    message.id,
                    run_id,
                    session_id,
                    message.role.value if hasattr(message.role, "value") else message.role,
                    content,
                    message.tool_call_id,
                    message.name,
                    _dumps(tool_calls) if tool_calls is not None else None,
                    _dumps(tool_result) if tool_result is not None else None,
                    seq,
                    message.created_at.isoformat()
                    if hasattr(message.created_at, "isoformat")
                    else str(message.created_at),
                ),
            )

    def delete_messages(self, run_id: str, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        placeholders = ",".join("?" for _ in message_ids)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM messages WHERE run_id = ? AND id IN ({placeholders})",
                [run_id, *message_ids],
            )
        return cursor.rowcount

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
            created = r["created_at"] if "created_at" in r.keys() else None
            msg_kwargs: dict = dict(
                id=r["id"],
                role=r["role"],
                content=r["content"] or "",
                tool_call_id=r["tool_call_id"],
                name=r["name"],
                tool_calls=tcs,
                tool_result=(
                    ToolResult.model_validate(json.loads(r["tool_result_json"]))
                    if "tool_result_json" in r.keys() and r["tool_result_json"]
                    else None
                ),
            )
            if created:
                from datetime import datetime

                if isinstance(created, str):
                    try:
                        msg_kwargs["created_at"] = datetime.fromisoformat(created)
                    except ValueError:
                        pass
                else:
                    msg_kwargs["created_at"] = created
            result.append(Message(**msg_kwargs))
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
        payload = _upgrade_checkpoint_payload(json.loads(row["data_json"]))
        return Checkpoint.model_validate(payload)

    # -- approvals ----------------------------------------------------------

    # -- tool invocations ---------------------------------------------------

    def save_tool_invocation(self, invocation: ToolInvocationRecord) -> None:
        arguments = self.redactor.redact_obj(invocation.arguments)
        result = (
            self.redactor.redact_obj(invocation.result.model_dump(mode="json"))
            if invocation.result
            else None
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_invocations(
                       id, run_id, session_id, step, ordinal, provider_call_id,
                       tool_name, tool_version, status, effect, replay_policy,
                       arguments_json, arguments_sha256, approval_id, attempt_count,
                       result_json, error_code, error_category, created_at, updated_at,
                       started_at, finished_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       status=excluded.status,
                       approval_id=excluded.approval_id,
                       attempt_count=excluded.attempt_count,
                       result_json=excluded.result_json,
                       error_code=excluded.error_code,
                       error_category=excluded.error_category,
                       updated_at=excluded.updated_at,
                       started_at=COALESCE(excluded.started_at, tool_invocations.started_at),
                       finished_at=excluded.finished_at""",
                (
                    invocation.id,
                    invocation.run_id,
                    invocation.session_id,
                    invocation.step,
                    invocation.ordinal,
                    invocation.provider_call_id,
                    invocation.tool_name,
                    invocation.tool_version,
                    invocation.status.value,
                    invocation.effect.value,
                    invocation.replay_policy.value,
                    _dumps(arguments),
                    invocation.arguments_sha256,
                    invocation.approval_id,
                    invocation.attempt_count,
                    _dumps(result) if result is not None else None,
                    invocation.error_code,
                    invocation.error_category,
                    invocation.created_at.isoformat(),
                    invocation.updated_at.isoformat(),
                    invocation.started_at.isoformat() if invocation.started_at else None,
                    invocation.finished_at.isoformat() if invocation.finished_at else None,
                ),
            )

    def resolve_indeterminate_tool_invocation(
        self,
        invocation: ToolInvocationRecord,
        *,
        expected_arguments_sha256: str,
    ) -> bool:
        result = (
            self.redactor.redact_obj(invocation.result.model_dump(mode="json"))
            if invocation.result
            else None
        )
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE tool_invocations
                   SET status = ?, result_json = ?, error_code = ?, error_category = ?,
                       approval_id = ?, updated_at = ?, finished_at = ?
                   WHERE id = ? AND status = ? AND arguments_sha256 = ?""",
                (
                    invocation.status.value,
                    _dumps(result) if result is not None else None,
                    invocation.error_code,
                    invocation.error_category,
                    invocation.approval_id,
                    invocation.updated_at.isoformat(),
                    invocation.finished_at.isoformat() if invocation.finished_at else None,
                    invocation.id,
                    ToolInvocationStatus.indeterminate.value,
                    expected_arguments_sha256,
                ),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _tool_invocation_from_row(row: sqlite3.Row) -> ToolInvocationRecord:
        return ToolInvocationRecord(
            id=row["id"],
            run_id=row["run_id"],
            session_id=row["session_id"],
            step=row["step"],
            ordinal=row["ordinal"],
            provider_call_id=row["provider_call_id"],
            tool_name=row["tool_name"],
            tool_version=row["tool_version"],
            status=row["status"],
            effect=row["effect"],
            replay_policy=row["replay_policy"],
            arguments=json.loads(row["arguments_json"] or "{}"),
            arguments_sha256=row["arguments_sha256"],
            approval_id=row["approval_id"],
            attempt_count=row["attempt_count"],
            result=(ToolResult.model_validate(json.loads(row["result_json"])) if row["result_json"] else None),
            error_code=row["error_code"],
            error_category=row["error_category"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            finished_at=(datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None),
        )

    def get_tool_invocation(self, invocation_id: str) -> ToolInvocationRecord | None:
        row = self._reader().execute(
            "SELECT * FROM tool_invocations WHERE id = ?", (invocation_id,)
        ).fetchone()
        return self._tool_invocation_from_row(row) if row else None

    def list_tool_invocations(self, run_id: str) -> list[ToolInvocationRecord]:
        rows = self._reader().execute(
            "SELECT * FROM tool_invocations WHERE run_id = ? ORDER BY step, ordinal",
            (run_id,),
        ).fetchall()
        return [self._tool_invocation_from_row(row) for row in rows]

    def start_tool_attempt(self, invocation_id: str, attempt: int) -> str:
        attempt_id = new_id()
        with self._lock:
            self._conn.execute(
                """INSERT INTO tool_attempts(
                       id, invocation_id, attempt, status, started_at
                   ) VALUES(?,?,?,?,?)""",
                (attempt_id, invocation_id, attempt, "running", _utcnow()),
            )
        return attempt_id

    def finish_tool_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        duration_ms: float,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE tool_attempts
                   SET status = ?, error_code = ?, error_category = ?,
                       duration_ms = ?, finished_at = ? WHERE id = ?""",
                (status, error_code, error_category, duration_ms, _utcnow(), attempt_id),
            )

    def finish_running_tool_attempts(
        self,
        invocation_id: str,
        *,
        status: str,
        error_code: str,
        error_category: str,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE tool_attempts
                   SET status = ?, error_code = ?, error_category = ?, finished_at = ?
                   WHERE invocation_id = ? AND status = 'running'""",
                (status, error_code, error_category, _utcnow(), invocation_id),
            )
        return cursor.rowcount

    def list_tool_attempts(self, invocation_id: str) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            "SELECT * FROM tool_attempts WHERE invocation_id = ? ORDER BY attempt",
            (invocation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_running_invocations_indeterminate(self, run_id: str) -> list[str]:
        now = _utcnow()
        with self._lock:
            rows = self._conn.execute(
                """SELECT id FROM tool_invocations
                   WHERE run_id = ? AND status = ? AND replay_policy = ?""",
                (run_id, ToolInvocationStatus.running.value, "never"),
            ).fetchall()
            ids = [str(row[0]) for row in rows]
            if ids:
                self._conn.executemany(
                    """UPDATE tool_invocations
                       SET status = ?, error_code = ?, error_category = ?,
                           updated_at = ?, finished_at = ? WHERE id = ?""",
                    [
                        (
                            ToolInvocationStatus.indeterminate.value,
                            "outcome_indeterminate",
                            "recovery",
                            now,
                            now,
                            invocation_id,
                        )
                        for invocation_id in ids
                    ],
                )
        return ids

    # -- approvals ----------------------------------------------------------

    def save_approval(self, approval: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO approvals(
                    id, run_id, tool_call_id, tool_name, effect,
                    arguments_summary, requires_confirmation, decision,
                    created_at, resolved_at, invocation_id, tool_version,
                    arguments_sha256, approval_scope, status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval["id"],
                    approval["run_id"],
                    approval["tool_call_id"],
                    approval["tool_name"],
                    approval["effect"],
                    self.redactor.redact_text(approval.get("arguments_summary") or ""),
                    int(bool(approval.get("requires_confirmation", False))),
                    approval.get("decision"),
                    approval.get("created_at", _utcnow()),
                    approval.get("resolved_at"),
                    approval.get("invocation_id"),
                    approval.get("tool_version", "1"),
                    approval.get("arguments_sha256", ""),
                    self.redactor.redact_text(approval.get("approval_scope", "")),
                    approval.get("status", "resolved" if approval.get("decision") else "pending"),
                ),
            )

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        *,
        invocation_id: str | None = None,
        arguments_sha256: str | None = None,
    ) -> bool:
        where = "id = ? AND status = 'pending' AND decision IS NULL"
        values: list[Any] = [decision, _utcnow(), approval_id]
        if invocation_id is not None:
            where += " AND invocation_id = ?"
            values.append(invocation_id)
        if arguments_sha256 is not None:
            where += " AND arguments_sha256 = ?"
            values.append(arguments_sha256)
        with self._lock:
            cursor = self._conn.execute(
                f"""UPDATE approvals SET decision = ?, status = 'resolved', resolved_at = ?
                    WHERE {where}""",
                values,
            )
        return cursor.rowcount == 1

    def expire_pending_approvals(self, run_id: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE approvals SET status = 'expired', resolved_at = ?
                   WHERE run_id = ? AND status = 'pending' AND decision IS NULL""",
                (_utcnow(), run_id),
            )
        return cursor.rowcount

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        approvals = [dict(row) for row in rows]
        for approval in approvals:
            approval["requires_confirmation"] = bool(
                approval.get("requires_confirmation", False)
            )
        return approvals

    # -- memories (FTS5) ----------------------------------------------------

    @staticmethod
    def _memory_content_hash(content: str) -> str:
        normalized = " ".join(content.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _backfill_memory_metadata_unlocked(self) -> None:
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM memories WHERE content_hash = ''"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                """UPDATE memories
                   SET content_hash = ?, updated_at = COALESCE(updated_at, created_at)
                   WHERE id = ?""",
                (self._memory_content_hash(str(row["content"])), row["id"]),
            )

    def _delete_memory_fts_unlocked(self, row: sqlite3.Row) -> None:
        self._conn.execute(
            """INSERT INTO memories_fts(
                   memories_fts, rowid, content, source, scope
               ) VALUES('delete', ?, ?, ?, ?)""",
            (row["rowid"], row["content"], row["source"], row["scope"]),
        )

    def add_memory(
        self,
        content: str,
        *,
        source: str = "tool",
        scope: str = "global",
        memory_id: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        mid = memory_id or new_id()
        now = _utcnow()
        content = self.redactor.redact_text(content)
        source = self.redactor.redact_text(source)
        scope = self.redactor.redact_text(scope).strip() or "global"
        content_hash = self._memory_content_hash(content)
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                existing = self._conn.execute(
                    """SELECT id FROM memories
                       WHERE scope = ? AND content_hash = ?
                       ORDER BY created_at ASC LIMIT 1""",
                    (scope, content_hash),
                ).fetchone()
                if existing:
                    self._conn.execute("COMMIT")
                    return str(existing["id"])
                self._conn.execute(
                    """INSERT INTO memories(
                           id, content, source, scope, created_at, last_used_at,
                           content_hash, updated_at, expires_at, use_count
                       ) VALUES(?,?,?,?,?,?,?,?,?,0)""",
                    (
                        mid,
                        content,
                        source,
                        scope,
                        now,
                        now,
                        content_hash,
                        now,
                        expires_at,
                    ),
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

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_memories(
        self,
        *,
        scope: str | None = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        values: list[Any] = []
        if scope is not None:
            where.append("scope = ?")
            values.append(scope)
        if not include_expired:
            where.append("(expires_at IS NULL OR expires_at > ?)")
            values.append(_utcnow())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        values.append(max(1, min(limit, 1000)))
        rows = self._reader().execute(
            f"""SELECT * FROM memories {clause}
                ORDER BY updated_at DESC, created_at DESC LIMIT ?""",
            values,
        ).fetchall()
        return [dict(row) for row in rows]

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        source: str | None = None,
        scope: str | None = None,
        expires_at: str | None = None,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT rowid, * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(memory_id)
                if expected_hash and str(row["content_hash"]) != expected_hash.removeprefix(
                    "sha256:"
                ):
                    raise ValueError("memory version conflict")
                new_content = self.redactor.redact_text(content or str(row["content"]))
                new_source = self.redactor.redact_text(source or str(row["source"] or "tool"))
                new_scope = self.redactor.redact_text(scope or str(row["scope"] or "global"))
                new_hash = self._memory_content_hash(new_content)
                new_expiry = row["expires_at"] if expires_at is None else (expires_at or None)
                duplicate = self._conn.execute(
                    """SELECT id FROM memories
                       WHERE scope = ? AND content_hash = ? AND id <> ? LIMIT 1""",
                    (new_scope, new_hash, memory_id),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"duplicate memory: {duplicate['id']}")
                self._delete_memory_fts_unlocked(row)
                self._conn.execute(
                    """UPDATE memories SET
                           content = ?, source = ?, scope = ?, content_hash = ?,
                           updated_at = ?, expires_at = ?
                       WHERE id = ?""",
                    (
                        new_content,
                        new_source,
                        new_scope,
                        new_hash,
                        _utcnow(),
                        new_expiry,
                        memory_id,
                    ),
                )
                self._conn.execute(
                    """INSERT INTO memories_fts(rowid, content, source, scope)
                       SELECT rowid, content, source, scope FROM memories WHERE id = ?""",
                    (memory_id,),
                )
                updated = self._conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        assert updated is not None
        return dict(updated)

    def delete_memory(self, memory_id: str, *, expected_hash: str | None = None) -> bool:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT rowid, * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return False
                if expected_hash and str(row["content_hash"]) != expected_hash.removeprefix(
                    "sha256:"
                ):
                    raise ValueError("memory version conflict")
                self._delete_memory_fts_unlocked(row)
                self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def search_memories(
        self,
        query: str,
        limit: int = 5,
        *,
        scopes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        requested_scopes = list(dict.fromkeys(scopes or ["global"]))
        if not requested_scopes:
            return []
        max_results = max(1, min(limit, 100))
        found: list[sqlite3.Row] = []
        with self._lock:
            for scope_name in requested_scopes:
                remaining = max_results - len(found)
                if remaining <= 0:
                    break
                try:
                    rows = self._conn.execute(
                        """SELECT m.*, bm25(memories_fts) AS bm25_score,
                                  (1.0 / (1.0 + MAX(
                                      0.0, julianday('now') - julianday(m.last_used_at)
                                  ))) AS freshness_score,
                                  (bm25(memories_fts) - 0.1 * (1.0 / (1.0 + MAX(
                                      0.0, julianday('now') - julianday(m.last_used_at)
                                  )))) AS rank_score
                           FROM memories AS m
                           JOIN memories_fts ON m.rowid = memories_fts.rowid
                           WHERE memories_fts MATCH ? AND m.scope = ?
                             AND (m.expires_at IS NULL OR m.expires_at > ?)
                           ORDER BY rank_score ASC, m.updated_at DESC
                           LIMIT ?""",
                        (query, scope_name, _utcnow(), remaining),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = self._conn.execute(
                        """SELECT *, NULL AS bm25_score, 0.0 AS freshness_score,
                                  0.0 AS rank_score
                           FROM memories
                           WHERE content LIKE ? AND scope = ?
                             AND (expires_at IS NULL OR expires_at > ?)
                           ORDER BY updated_at DESC LIMIT ?""",
                        (f"%{query}%", scope_name, _utcnow(), remaining),
                    ).fetchall()
                found.extend(rows)
            for row in found:
                self._conn.execute(
                    """UPDATE memories
                       SET last_used_at = ?, use_count = use_count + 1
                       WHERE id = ?""",
                    (_utcnow(), row["id"]),
                )
        return [dict(row) for row in found]

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

    # -- explicit maintenance ---------------------------------------------

    def maintenance_stats(self) -> dict[str, Any]:
        reader = self._reader()
        counts = {
            table: int(reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "sessions",
                "runs",
                "events",
                "messages",
                "artifacts",
                "memories",
                "run_pins",
                "run_leases",
                "tool_invocations",
                "tool_attempts",
            )
        }
        artifact_bytes = int(
            reader.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts").fetchone()[0]
        )
        return {
            **counts,
            "artifact_bytes": artifact_bytes,
            "database_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "wal_bytes": self.db_path.with_name(self.db_path.name + "-wal").stat().st_size
            if self.db_path.with_name(self.db_path.name + "-wal").exists()
            else 0,
        }

    def _orphan_artifacts_unlocked(self) -> list[dict[str, Any]]:
        reference_rows: list[sqlite3.Row] = []
        for query in (
            "SELECT metadata_json, output_summary, error FROM runs",
            "SELECT content, tool_calls_json, tool_result_json FROM messages",
            "SELECT payload_json FROM events",
            "SELECT data_json FROM checkpoints",
            "SELECT result_json FROM tool_invocations",
        ):
            reference_rows.extend(self._conn.execute(query).fetchall())
        corpus = "\n".join(
            str(value)
            for row in reference_rows
            for value in row
            if value is not None
        )
        artifacts = self._conn.execute("SELECT * FROM artifacts").fetchall()
        return [
            dict(row)
            for row in artifacts
            if str(row["id"]) not in corpus and str(row["sha256"]) not in corpus
        ]

    def plan_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        if older_than_days < 0:
            raise ValueError("older_than_days must be non-negative")
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self._lock:
            run_rows = self._conn.execute(
                """SELECT id FROM runs
                   WHERE status IN ('completed', 'failed', 'cancelled', 'interrupted')
                     AND COALESCE(finished_at, updated_at, created_at) < ?
                     AND id NOT IN (SELECT run_id FROM run_pins)
                     AND id NOT IN (
                         SELECT run_id FROM run_leases WHERE expires_at > ?
                     )
                   ORDER BY created_at ASC""",
                (cutoff, _utcnow()),
            ).fetchall()
            orphan_artifacts = self._orphan_artifacts_unlocked()
        return {
            "dry_run": True,
            "older_than_days": older_than_days,
            "cutoff": cutoff,
            "run_ids": [str(row[0]) for row in run_rows],
            "run_count": len(run_rows),
            "orphan_artifact_ids": [str(row["id"]) for row in orphan_artifacts],
            "orphan_artifact_count": len(orphan_artifacts),
            "orphan_artifact_bytes": sum(
                int(row.get("size_bytes") or 0) for row in orphan_artifacts
            ),
        }

    def apply_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        plan = self.plan_gc(older_than_days=older_than_days)
        run_ids = list(plan["run_ids"])
        artifact_paths: list[str] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if run_ids:
                    placeholders = ",".join("?" for _ in run_ids)
                    self._conn.execute(
                        f"""DELETE FROM tool_attempts
                            WHERE invocation_id IN (
                                SELECT id FROM tool_invocations
                                WHERE run_id IN ({placeholders})
                            )""",
                        run_ids,
                    )
                    self._conn.execute(
                        f"DELETE FROM tool_invocations WHERE run_id IN ({placeholders})",
                        run_ids,
                    )
                    for table in (
                        "approvals",
                        "checkpoints",
                        "messages",
                        "events",
                        "stop_requests",
                        "run_leases",
                        "run_pins",
                    ):
                        self._conn.execute(
                            f"DELETE FROM {table} WHERE run_id IN ({placeholders})",
                            run_ids,
                        )
                    self._conn.execute(
                        f"DELETE FROM runs WHERE id IN ({placeholders})", run_ids
                    )
                orphan_artifacts = self._orphan_artifacts_unlocked()
                artifact_ids = [str(row["id"]) for row in orphan_artifacts]
                artifact_paths = [str(row["path"]) for row in orphan_artifacts]
                if artifact_ids:
                    placeholders = ",".join("?" for _ in artifact_ids)
                    self._conn.execute(
                        f"DELETE FROM artifacts WHERE id IN ({placeholders})",
                        artifact_ids,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        root = self.artifacts.root.resolve()
        removed_files = 0
        for raw_path in artifact_paths:
            path = Path(raw_path).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                path.unlink()
                removed_files += 1
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        return {
            **plan,
            "dry_run": False,
            "deleted_runs": len(run_ids),
            "deleted_artifacts": len(artifact_paths),
            "deleted_artifact_files": removed_files,
        }

    def compact(self) -> dict[str, int]:
        now = _utcnow()
        with self._lock:
            active = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM run_leases WHERE expires_at > ?", (now,)
                ).fetchone()[0]
            )
            if active:
                raise RuntimeError("cannot compact while active run leases exist")
        with self._read_conns_lock:
            for conn in self._read_conns:
                conn.close()
            self._read_conns.clear()
            self._read_local = threading.local()
        before = self.db_path.stat().st_size if self.db_path.exists() else 0
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
        after = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {"before_bytes": before, "after_bytes": after}
