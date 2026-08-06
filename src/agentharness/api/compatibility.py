"""Versioned procurement Web contract shared by the API and launch helpers."""

from __future__ import annotations

API_SCHEMA_VERSION = 11
API_CAPABILITIES = (
    "procurement_sourcing_v1",
    "procurement_approval_v1",
    "procurement_no_award_v1",
    "procurement_audit_v1",
    "procurement_stream_v1",
    "procurement_po_v1",
    "procurement_demo_v1",
)
