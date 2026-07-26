"""Durable tool invocations and their per-attempt audit rows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from agentharness.contracts import (
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
    new_id,
)
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps, _utcnow


class ToolInvocationRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
