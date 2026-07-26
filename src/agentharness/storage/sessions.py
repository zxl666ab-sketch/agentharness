"""Session rows and the session-scoped run listings."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import new_id
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _utcnow


class SessionRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
