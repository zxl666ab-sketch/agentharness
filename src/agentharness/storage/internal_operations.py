"""Persistent idempotency results for Java-to-Python internal commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from agentharness.storage.core import StorageCore


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class InternalOperationRepo:
    def __init__(self, core: StorageCore) -> None:
        self.core = core

    def get(self, operation_id: str) -> dict[str, Any] | None:
        row = self.core.reader().execute(
            "SELECT * FROM internal_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return self._record(row)

    def accept(
        self,
        *,
        operation_id: str,
        payload_sha256: str,
        operation_type: str,
        aggregate_id: str,
    ) -> dict[str, Any]:
        now = _utcnow()
        with self.core.transaction():
            row = self.core.conn.execute(
                "SELECT * FROM internal_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                result = self._record(row)
                if result["payload_sha256"] != payload_sha256:
                    raise ValueError("operation payload conflict")
                return result
            self.core.conn.execute(
                """INSERT INTO internal_operations(
                       operation_id, payload_sha256, operation_type, aggregate_id,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'accepted', ?, ?)""",
                (
                    operation_id,
                    payload_sha256,
                    operation_type,
                    aggregate_id,
                    now,
                    now,
                ),
            )
        return {
            "operation_id": operation_id,
            "payload_sha256": payload_sha256,
            "operation_type": operation_type,
            "aggregate_id": aggregate_id,
            "run_id": None,
            "status": "accepted",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    def claim(self, operation_id: str) -> dict[str, Any] | None:
        """Atomically move ``accepted`` → ``executing`` (P-M4 compare-and-set).

        Returns the claimed record, or ``None`` when the row is not claimable —
        either a dispatcher already owns it (concurrent replay of the same
        ``operation_id``) or it reached a terminal state. Callers must then hand
        back the existing record instead of dispatching the side effects twice.
        """
        now = _utcnow()
        with self.core.transaction():
            cursor = self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'executing', updated_at = ?
                   WHERE operation_id = ? AND status = 'accepted'""",
                (now, operation_id),
            )
            if cursor.rowcount != 1:
                return None
            row = self.core.conn.execute(
                "SELECT * FROM internal_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._record(row)

    def recover_abandoned_claims(self) -> int:
        """Release ``executing`` claims left behind by a crashed process.

        A claim is only meaningful while its dispatcher is alive; at process
        start nothing of ours can be in flight, so an ``executing`` row is
        abandoned and becomes re-claimable (same idempotency rationale as
        ``Storage.recover_expired_run_leases``). Returns the reopened count.
        """
        now = _utcnow()
        with self.core.transaction():
            cursor = self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'accepted', updated_at = ?
                   WHERE status = 'executing'""",
                (now,),
            )
        return int(cursor.rowcount)

    def set_run_id(self, operation_id: str, run_id: str) -> None:
        """Bind the run created for this operation (indexed idempotent-replay lookup)."""
        with self.core.transaction():
            self.core.conn.execute(
                "UPDATE internal_operations SET run_id = ?, updated_at = ? WHERE operation_id = ?",
                (run_id, _utcnow(), operation_id),
            )

    def complete(self, operation_id: str, result: dict[str, Any]) -> bool:
        """Finalize a claimed operation; only ``executing`` may complete."""
        now = _utcnow()
        with self.core.transaction():
            cursor = self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'completed', result_json = ?, error = NULL, updated_at = ?
                   WHERE operation_id = ? AND status = 'executing'""",
                (json.dumps(result, ensure_ascii=False, default=str), now, operation_id),
            )
        return cursor.rowcount == 1

    def fail(self, operation_id: str, error: str) -> bool:
        """Fail a claimed operation; only ``executing`` may fail."""
        now = _utcnow()
        with self.core.transaction():
            cursor = self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'failed', error = ?, updated_at = ?
                   WHERE operation_id = ? AND status = 'executing'""",
                (error[:2000], now, operation_id),
            )
        return cursor.rowcount == 1

    def reopen_failed(self, operation_id: str, payload_sha256: str) -> bool:
        """Atomically reopen the same durable operation for an explicit retry.

        The operation id and payload stay unchanged, so downstream Run/message
        recovery can remain idempotent across Java/Python restarts.
        """

        now = _utcnow()
        with self.core.transaction():
            cursor = self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'accepted', error = NULL, updated_at = ?
                   WHERE operation_id = ? AND payload_sha256 = ? AND status = 'failed'""",
                (now, operation_id, payload_sha256),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["result"] = (
            json.loads(result.pop("result_json"))
            if result.get("result_json") is not None
            else None
        )
        return result
