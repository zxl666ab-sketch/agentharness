"""Run rows, stop requests, pins, and the run observability projection."""

from __future__ import annotations

import json
from typing import Any

from agentharness.contracts import EventEnvelope, RunStatus, Usage
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps, _utcnow
from agentharness.storage.events import EventRepo


class RunRepo:
    """Run status is updated and its events appended in one transaction so an
    observer can never see a status without the event that explains it."""

    def __init__(self, core: StorageCore, redactor: Redactor, *, events: EventRepo) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor
        self._events = events

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
                    assigned = self._events.append_unlocked(events)
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
