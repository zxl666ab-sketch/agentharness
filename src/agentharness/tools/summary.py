"""Shared tool-argument summarizer.

Single source of truth for the compact ``args_summary`` embedded in ``tool_call_start``
event payloads and rendered by the run report / web inspector. Used by
``engine.tool_execution`` when building invocation records and tool-start events.
"""

from __future__ import annotations

from typing import Any

# Fields surfaced first, in priority order. Keep in sync with the TS `preferred` list.
_PREFERRED_KEYS = (
    "action",
    "url",
    "path",
    "command",
    "query",
    "method",
    "selector",
    "name",
    "skill",
    "memory",
    "context_id",
)

# Keys whose values are redacted when falling back to generic key rendering.
_SECRET_KEYS = frozenset(
    {"api_key", "token", "password", "authorization", "secret", "key"}
)

_MAX_LEN = 160


def summarize_tool_arguments(arguments: Any) -> str:
    """Compact tool args for event payloads (no large blobs / secrets)."""
    if arguments is None:
        return ""
    if not isinstance(arguments, dict):
        text = str(arguments)
        return text if len(text) <= _MAX_LEN else text[: _MAX_LEN - 1] + "…"
    parts: list[str] = []
    for key in _PREFERRED_KEYS:
        if key in arguments and arguments[key] not in (None, ""):
            val = arguments[key]
            if isinstance(val, str) and len(val) > 80:
                val = val[:79] + "…"
            parts.append(f"{key}={val}")
    if not parts:
        for key, val in list(arguments.items())[:4]:
            kl = str(key).lower()
            if kl in _SECRET_KEYS or "token" in kl:
                parts.append(f"{key}=[REDACTED]")
            else:
                rendered = val if not isinstance(val, str) or len(val) <= 60 else val[:59] + "…"
                parts.append(f"{key}={rendered}")
    text = " ".join(parts)
    return text if len(text) <= _MAX_LEN else text[: _MAX_LEN - 1] + "…"
