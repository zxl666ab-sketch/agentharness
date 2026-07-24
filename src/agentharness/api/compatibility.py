"""Versioned contract shared by the Web API and its launch helpers."""

from __future__ import annotations

API_SCHEMA_VERSION = 4
API_CAPABILITIES = (
    "context_manifest_v1",
    "verification_events_v1",
    "trace_native_evaluation_v2",
)
