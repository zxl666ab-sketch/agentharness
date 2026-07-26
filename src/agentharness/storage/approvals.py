"""Approval requests and their resolution audit trail."""

from __future__ import annotations

from typing import Any

from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _utcnow


class ApprovalRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
