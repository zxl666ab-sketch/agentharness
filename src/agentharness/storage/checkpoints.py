"""Run checkpoints (one row per run, latest wins)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentharness.contracts import Checkpoint
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps, _utcnow


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


class CheckpointRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
