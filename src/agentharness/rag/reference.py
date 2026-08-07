"""Sanitized, truncated knowledge references for injection (untrusted data).

Injected history is treated as untrusted: every field is redacted and
truncated before it reaches the model prompt or the web UI. Chunk hashes and
scores are safe to expose; note/remark content is capped hard.
"""

from __future__ import annotations

from typing import Any

from agentharness.security.redaction import Redactor, default_redactor

MAX_SUPPLIER_NAME = 50
MAX_ITEM_NAME = 50
MAX_SPECIFICATION_SUMMARY = 120
MAX_REQUEST_REFERENCE = 40
MAX_NOTE = 200
MAX_REFERENCE_TEXT = 500

# Token-budget assertion for tiered injection (auto top-3 -> UI top-5).
INJECTED_TOP_K = 3
EXPANDED_TOP_K = 5
KNOWLEDGE_INJECTION_MAX_CHARS = 2000


def truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def sanitize_reference(
    chunk: dict[str, Any],
    redactor: Redactor | None = None,
) -> dict[str, Any]:
    """Project a chunk into the public evidence shape (redacted + truncated)."""
    safe = (redactor or default_redactor).redact_public_obj(
        {
            "chunk_id": str(chunk["chunk_sha256"])[:16],
            "chunk_sha256": str(chunk["chunk_sha256"]),
            "request_reference": truncate(chunk.get("request_reference"), MAX_REQUEST_REFERENCE),
            "decision_at": str(chunk.get("decision_at") or ""),
            "supplier_name": truncate(chunk.get("supplier_name"), MAX_SUPPLIER_NAME),
            "item_name": truncate(chunk.get("item_name"), MAX_ITEM_NAME),
            "specification_summary": truncate(
                chunk.get("specification_summary")
                or _spec_summary(chunk.get("specifications", {})),
                MAX_SPECIFICATION_SUMMARY,
            ),
            "unit_price": str(chunk.get("unit_price") or ""),
            "currency": str(chunk.get("currency") or ""),
            "landed_unit_cost": str(chunk.get("landed_unit_cost") or ""),
            "lead_days": chunk.get("lead_days"),
            "moq": chunk.get("moq"),
            "decision": str(chunk.get("decision") or ""),
            "source_sha256": str(chunk.get("artifact_sha256") or ""),
            "score": str(chunk.get("score") or ""),
            "quality_flags": list(chunk.get("quality_flags") or []),
            "note": truncate(chunk.get("note"), MAX_NOTE) if chunk.get("note") else None,
        }
    )
    safe["text"] = reference_text(safe)
    return safe


def _spec_summary(specifications: dict[str, Any]) -> str:
    from agentharness.rag.chunking import specification_summary

    return specification_summary(specifications)


def reference_text(reference: dict[str, Any]) -> str:
    """Compact one-line model-facing text for a knowledge reference."""
    parts = [
        f"{reference.get('request_reference') or ''}",
        f"{reference.get('supplier_name') or ''}",
        f"{reference.get('specification_summary') or ''}",
    ]
    if reference.get("unit_price"):
        parts.append(f"成交价 {reference['unit_price']}")
    if reference.get("currency"):
        parts.append(str(reference["currency"]))
    if reference.get("landed_unit_cost"):
        parts.append(f"到货单价 {reference['landed_unit_cost']}")
    if reference.get("decision_at"):
        parts.append(f"成交日期 {str(reference['decision_at'])[:10]}")
    text = "，".join(part for part in parts if part)
    return truncate(text, MAX_REFERENCE_TEXT)


def injected_text(references: list[dict[str, Any]], *, top_k: int = INJECTED_TOP_K) -> str:
    """Total model-facing text for the auto-injected top-k references."""
    return "\n".join(
        str(reference.get("text") or "") for reference in references[:top_k]
    )


__all__ = [
    "EXPANDED_TOP_K",
    "INJECTED_TOP_K",
    "KNOWLEDGE_INJECTION_MAX_CHARS",
    "MAX_ITEM_NAME",
    "MAX_NOTE",
    "MAX_REFERENCE_TEXT",
    "MAX_REQUEST_REFERENCE",
    "MAX_SPECIFICATION_SUMMARY",
    "MAX_SUPPLIER_NAME",
    "injected_text",
    "reference_text",
    "sanitize_reference",
    "truncate",
]
