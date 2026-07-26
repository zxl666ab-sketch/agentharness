"""Persisted transcript messages per run."""

from __future__ import annotations

import json

from agentharness.contracts import Message, ToolResult
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps


class MessageRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
