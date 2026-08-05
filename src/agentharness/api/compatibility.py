"""Versioned contract shared by the Web API and its launch helpers."""

from __future__ import annotations

API_SCHEMA_VERSION = 12
API_CAPABILITIES = (
    "sse_events_v1",
    "procurement_agent_loop_v1",
    "procurement_audit_v1",
    "procurement_evaluation_v1",
)
