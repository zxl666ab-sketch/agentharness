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
        result = dict(row)
        result["result"] = (
            json.loads(result.pop("result_json"))
            if result.get("result_json") is not None
            else None
        )
        return result

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
                result = dict(row)
                if result["payload_sha256"] != payload_sha256:
                    raise ValueError("operation payload conflict")
                result["result"] = (
                    json.loads(result.pop("result_json"))
                    if result.get("result_json") is not None
                    else None
                )
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
            "status": "accepted",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    def complete(self, operation_id: str, result: dict[str, Any]) -> None:
        now = _utcnow()
        with self.core.transaction():
            self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'completed', result_json = ?, error = NULL, updated_at = ?
                   WHERE operation_id = ?""",
                (json.dumps(result, ensure_ascii=False, default=str), now, operation_id),
            )

    def fail(self, operation_id: str, error: str) -> None:
        now = _utcnow()
        with self.core.transaction():
            self.core.conn.execute(
                """UPDATE internal_operations
                   SET status = 'failed', error = ?, updated_at = ?
                   WHERE operation_id = ?""",
                (error[:2000], now, operation_id),
            )
