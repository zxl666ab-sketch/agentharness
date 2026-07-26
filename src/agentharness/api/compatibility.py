"""Versioned contract shared by the Web API and its launch helpers."""

from __future__ import annotations

API_SCHEMA_VERSION = 6
API_CAPABILITIES = (
    "run_execution_v1",
    "interactive_approval_v1",
    "run_resume_v1",
    "sse_events_v1",
    "tool_execution_v2",
)
