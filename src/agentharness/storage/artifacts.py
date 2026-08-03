"""SHA-256 content-addressed artifact store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentharness.security.redaction import Redactor, default_redactor


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _is_text_content_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type.startswith("text/") or media_type in {
        "application/json",
        "application/ld+json",
        "application/x-ndjson",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
    } or media_type.endswith(("+json", "+xml"))


def _is_json_content_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type in {
        "application/json",
        "application/ld+json",
        "application/x-ndjson",
    } or media_type.endswith("+json")


class ArtifactStore:
    def __init__(self, root: Path | str, redactor: Redactor | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or default_redactor

    def put(
        self,
        data: bytes | str,
        *,
        content_type: str = "text/plain",
        summary: str | None = None,
    ) -> dict[str, Any]:
        text: str | None = data if isinstance(data, str) else None
        if text is None and _is_text_content_type(content_type):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
        if text is not None:
            if _is_json_content_type(content_type):
                try:
                    parsed = json.loads(text)
                    text = json.dumps(
                        self.redactor.redact_obj(parsed),
                        ensure_ascii=False,
                        default=str,
                    )
                except json.JSONDecodeError:
                    text = self.redactor.redact_text(text)
            else:
                text = self.redactor.redact_text(text)
            data = text.encode("utf-8")

        sha = hashlib.sha256(data).hexdigest()
        # shard by first 2 hex chars
        shard = self.root / sha[:2]
        shard.mkdir(parents=True, exist_ok=True)
        path = shard / sha
        if not path.exists():
            path.write_bytes(data)

        art_id = uuid4().hex
        if summary is None:
            try:
                sum_text = self.redactor.redact_text(data.decode("utf-8")[:200])
            except UnicodeDecodeError:
                sum_text = f"<binary {len(data)} bytes>"
        else:
            # Always redact caller-supplied summaries (never store secrets in metadata)
            sum_text = self.redactor.redact_text(str(summary))[:2000]

        return {
            "id": art_id,
            "sha256": sha,
            "content_type": content_type,
            "size_bytes": len(data),
            "summary": sum_text,
            "path": str(path),
            "created_at": _utcnow(),
        }

    def put_json(self, obj: Any, **kwargs: Any) -> dict[str, Any]:
        redacted = self.redactor.redact_obj(obj)
        return self.put(
            json.dumps(redacted, ensure_ascii=False, default=str),
            content_type="application/json",
            **kwargs,
        )

    def get_bytes(self, sha256: str) -> bytes | None:
        path = self.root / sha256[:2] / sha256
        if path.exists():
            return path.read_bytes()
        return None

    def get_text(self, sha256: str) -> str | None:
        data = self.get_bytes(sha256)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")
