"""Pure helpers for multi-turn session history assembly and session titles."""

from __future__ import annotations

import re
from typing import Any

from agentharness.contracts import Message, RunStatus

# Statuses whose messages are eligible for multi-turn context splicing.
HISTORY_ELIGIBLE_STATUSES = frozenset({RunStatus.completed.value})


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


_SESSION_TITLE_MAX_CHARS = 120


def session_title_from_message(content: str) -> str:
    """Return the first user message with whitespace collapsed, capped at 120 chars."""
    title = collapse_whitespace(content)
    if not title:
        return "session"
    if len(title) <= _SESSION_TITLE_MAX_CHARS:
        return title
    return title[: _SESSION_TITLE_MAX_CHARS - 3].rstrip() + "..."


def is_top_level_run(run: dict[str, Any]) -> bool:
    parent = run.get("parent_run_id")
    return parent is None or parent == ""


def is_history_eligible_run(run: dict[str, Any]) -> bool:
    """Completed top-level runs only — not delegates, not failed/cancelled/interrupted."""
    if not is_top_level_run(run):
        return False
    status = run.get("status") or ""
    return status in HISTORY_ELIGIBLE_STATUSES


def sort_runs_for_history(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order by created_at ascending (stable for same timestamp via id)."""
    return sorted(runs, key=lambda r: (r.get("created_at") or "", r.get("id") or ""))


def assemble_session_history_messages(
    runs: list[dict[str, Any]],
    messages_by_run: dict[str, list[Message]],
) -> list[Message]:
    """Load full messages from completed top-level runs, ordered by run then seq.

    `messages_by_run` values must already be ordered by seq ascending.
    """
    out: list[Message] = []
    for run in sort_runs_for_history(runs):
        if not is_history_eligible_run(run):
            continue
        rid = run["id"]
        msgs = messages_by_run.get(rid) or []
        out.extend(msgs)
    return out
