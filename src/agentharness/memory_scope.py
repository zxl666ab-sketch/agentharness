"""Opaque scope identifiers for workspace- and session-bound long-term memory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def workspace_memory_scope(cwd: str | None) -> str | None:
    if not cwd:
        return None
    normalized = str(Path(cwd).expanduser().resolve())
    if os.name == "nt":
        normalized = normalized.casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"workspace:{digest}"


def session_memory_scope(session_id: str | None) -> str | None:
    return f"session:{session_id}" if session_id else None


def resolve_memory_scope(
    requested: str | None,
    *,
    cwd: str | None,
    session_id: str | None,
) -> str:
    value = (requested or "workspace").strip()
    if value == "workspace":
        return workspace_memory_scope(cwd) or "global"
    if value == "session":
        return session_memory_scope(session_id) or "global"
    return value
