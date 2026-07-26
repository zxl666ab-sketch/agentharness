"""Run leases: single-writer ownership, heartbeat, and crash recovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agentharness.contracts import EventEnvelope, EventType, RunStatus
from agentharness.storage.core import StorageCore, _dumps, _utcnow
from agentharness.storage.events import EventRepo


class LeaseRepo:
    """Lease rows gate which process may drive a run; recovery marks expired
    active leases as ``interrupted(process_lost)`` in one transaction."""

    def __init__(self, core: StorageCore, *, events: EventRepo) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self._events = events

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
                    self._events.append_unlocked([event])
                self._conn.execute(
                    "DELETE FROM run_leases WHERE expires_at <= ?",
                    (now,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return recovered
