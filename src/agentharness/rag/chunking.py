"""Build rag_chunks from approved procurement facts (deterministic, offline).

Chunks are built only from formally approved decisions. The chunk_sha256 is
derived from canonical business facts so any business change (for example a
human field correction) produces a new hash and the old chunk can be replaced
without touching historical commits.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

QUALITY_LOW_CONFIDENCE = "low_confidence"
QUALITY_CONFLICT_EVIDENCE = "conflict_evidence"
QUALITY_CORRECTED = "corrected"

_LOW_CONFIDENCE_THRESHOLD = 0.8


def canonical_facts(
    *,
    request: dict[str, Any],
    quote: dict[str, Any],
    decision: dict[str, Any],
    landed_unit_cost: str | None,
    unit_price: str | None,
    currency: str | None,
    lead_days: int | None,
    moq: int | None,
) -> dict[str, Any]:
    """Canonical business facts that identify a chunk (sorted JSON -> sha256)."""
    return {
        "request_id": request["id"],
        "quote_id": quote["id"],
        "request_reference": request["reference"],
        "supplier_name": quote["supplier_name"],
        "item_name": request["item_name"],
        "category": request["category"],
        "specifications": request.get("specifications", {}),
        "unit_price": unit_price,
        "currency": currency,
        "landed_unit_cost": landed_unit_cost,
        "lead_days": lead_days,
        "moq": moq,
        "decision": decision["decision"],
        "decision_at": decision["created_at"],
        "note": decision.get("note"),
        "artifact_sha256": quote["source_sha256"],
    }


def chunk_sha256(facts: dict[str, Any]) -> str:
    data = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def quality_flags_for_quote(extracted: dict[str, Any]) -> list[str]:
    """Low-confidence / conflicting / corrected evidence markers for rerank."""
    flags: list[str] = []
    fields = extracted.get("fields", {}) if isinstance(extracted, dict) else {}
    for entry in fields.values():
        if not isinstance(entry, dict):
            continue
        confidence = entry.get("confidence")
        if (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and confidence < _LOW_CONFIDENCE_THRESHOLD
        ):
            flags.append(QUALITY_LOW_CONFIDENCE)
            break
    for entry in fields.values():
        if isinstance(entry, dict) and entry.get("conflicts"):
            flags.append(QUALITY_CONFLICT_EVIDENCE)
            break
    for entry in fields.values():
        if isinstance(entry, dict) and entry.get("correction"):
            flags.append(QUALITY_CORRECTED)
            break
    return sorted(set(flags))


def specification_summary(specifications: dict[str, Any]) -> str:
    width = specifications.get("width_mm")
    length = specifications.get("length_mm")
    thickness = specifications.get("thickness_um")
    material = specifications.get("material")
    color = specifications.get("color")
    print_colors = specifications.get("print_colors")
    parts = []
    if width is not None and length is not None:
        parts.append(f"{width}×{length}mm")
    if thickness is not None:
        parts.append(f"{thickness}μm")
    if material:
        parts.append(str(material))
    if color:
        parts.append(str(color))
    if print_colors is not None:
        parts.append(f"{print_colors}色")
    return " / ".join(parts) or "未填写规格"


def _field_value(extracted: dict[str, Any], name: str) -> Any:
    fields = extracted.get("fields", {}) if isinstance(extracted, dict) else {}
    entry = fields.get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def build_chunk(
    *,
    request: dict[str, Any],
    quote: dict[str, Any],
    decision: dict[str, Any],
    snapshot_result: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a rag_chunk row from an approved decision + its snapshot result."""
    extracted = quote.get("extracted", {})
    unit_price = _field_value(extracted, "unit_price")
    currency = _field_value(extracted, "currency")
    lead_days = _field_value(extracted, "lead_time_days")
    moq = _field_value(extracted, "moq")
    landed_unit_cost: str | None = None
    base_currency: str | None = None
    for item in snapshot_result.get("quotes", []):
        if item.get("quote_id") == quote["id"]:
            cost = item.get("cost", {})
            landed_unit_cost = cost.get("landed_unit_base")
            base_currency = cost.get("base_currency")
            if currency is None:
                currency = base_currency
            if lead_days is None:
                lead_days = item.get("commercial", {}).get("lead_time_days")
            if moq is None:
                moq = item.get("commercial", {}).get("moq")
            break
    facts = canonical_facts(
        request=request,
        quote=quote,
        decision=decision,
        landed_unit_cost=str(landed_unit_cost) if landed_unit_cost is not None else None,
        unit_price=str(unit_price) if unit_price is not None else None,
        currency=str(currency) if currency is not None else None,
        lead_days=int(lead_days) if lead_days is not None else None,
        moq=int(moq) if moq is not None else None,
    )
    specifications = request.get("specifications", {})
    summary = specification_summary(specifications)
    note = decision.get("note")
    content = (
        f"{request['reference']} {request['item_name']} {summary} "
        f"供应商 {quote['supplier_name']}"
    )
    if unit_price is not None:
        content += f" 成交价 {unit_price}"
    if currency is not None:
        content += f" {currency}"
    if landed_unit_cost is not None:
        content += f" 到货单价 {landed_unit_cost}"
    if lead_days is not None:
        content += f" 交期 {lead_days} 天"
    if moq is not None:
        content += f" MOQ {moq}"
    if note:
        content += f" 备注 {note}"
    return {
        "chunk_sha256": chunk_sha256(facts),
        "request_id": request["id"],
        "quote_id": quote["id"],
        "artifact_id": quote["source_artifact_id"],
        "artifact_sha256": quote["source_sha256"],
        "request_reference": request["reference"],
        "supplier_name": quote["supplier_name"],
        "item_name": request["item_name"],
        "category": request["category"],
        "specifications": specifications,
        "unit_price": str(unit_price) if unit_price is not None else None,
        "currency": str(currency) if currency is not None else None,
        "landed_unit_cost": str(landed_unit_cost) if landed_unit_cost is not None else None,
        "lead_days": int(lead_days) if lead_days is not None else None,
        "moq": int(moq) if moq is not None else None,
        "decision": decision["decision"],
        "decision_at": decision["created_at"],
        "content": content,
        "quality_flags": quality_flags_for_quote(extracted),
        "created_at": created_at or decision["created_at"],
        "updated_at": created_at or decision["created_at"],
    }


__all__ = [
    "QUALITY_CONFLICT_EVIDENCE",
    "QUALITY_CORRECTED",
    "QUALITY_LOW_CONFIDENCE",
    "build_chunk",
    "canonical_facts",
    "chunk_sha256",
    "quality_flags_for_quote",
    "specification_summary",
]
