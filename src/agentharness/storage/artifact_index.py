"""SQLite index of artifact metadata (content stored by ArtifactStore)."""

from __future__ import annotations

from typing import Any

from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _utcnow


class ArtifactIndexRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

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
