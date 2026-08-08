"""Hybrid retriever: FTS5 + structured-spec recall, then deterministic rerank.

Pipeline (locked design):
    1. Candidate recall: FTS5 keyword (LIKE fallback) + structured spec
       tolerance matching; union by chunk_sha256.
    2. Coarse ranking: keyword hit / structured hit score -> top-20.
    3. Rerank (top-20 -> top-5): spec match x time decay x supplier
       reputation x data quality. Spec-mismatched chunks are excluded.
No embeddings are used; the result is fully deterministic and offline.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from agentharness.rag.chunking import canonical_color, canonical_material, specification_summary

WIDTH = "width_mm"
LENGTH = "length_mm"
THICKNESS = "thickness_um"
MATERIAL = "material"
COLOR = "color"
PRINT_COLORS = "print_colors"

SPEC_DIMENSIONS = (WIDTH, LENGTH, THICKNESS, MATERIAL, COLOR, PRINT_COLORS)

DEFAULT_SIZE_TOLERANCE_MM = Decimal("2")
DEFAULT_THICKNESS_TOLERANCE_UM = Decimal("3")

_WEIGHT_SPEC = Decimal("0.50")
_WEIGHT_TIME = Decimal("0.15")
_WEIGHT_REPUTATION = Decimal("0.20")
_WEIGHT_QUALITY = Decimal("0.15")

_TIME_HALF_LIFE_DAYS = 180
_MISSING_FIELD_PENALTY = Decimal("0.1")
_LOW_QUALITY_SCORE = Decimal("0.6")
_CONFLICT_QUALITY_SCORE = Decimal("0.7")
_KEYWORD_BONUS = Decimal("0.1")
_STRUCTURED_SCAN_PAGE_SIZE = 5_000


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _norm_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _structured_hits(
    request_specs: dict[str, Any],
    chunk_specs: dict[str, Any],
    *,
    size_tolerance_mm: Decimal,
    thickness_tolerance_um: Decimal,
) -> tuple[dict[str, bool], int, int]:
    """Per-dimension spec hits. Returns (hits, matched, missing)."""
    hits: dict[str, bool] = {}
    matched = 0
    missing = 0
    for dimension in SPEC_DIMENSIONS:
        expected = request_specs.get(dimension)
        actual = chunk_specs.get(dimension)
        if expected is None or actual is None or str(expected) == "" or str(actual) == "":
            hits[dimension] = False
            missing += 1
            continue
        if dimension == MATERIAL:
            left = canonical_material(expected)
            right = canonical_material(actual)
            hit = (
                left == right
                if left is not None and right is not None
                else _norm_text(expected) == _norm_text(actual)
            )
        elif dimension == COLOR:
            left = canonical_color(expected)
            right = canonical_color(actual)
            hit = (
                left == right
                if left is not None and right is not None
                else _norm_text(expected) == _norm_text(actual)
            )
        elif dimension == PRINT_COLORS:
            hit = _decimal(expected) == _decimal(actual) and _decimal(expected) is not None
        elif dimension == WIDTH or dimension == LENGTH:
            expected_value = _decimal(expected)
            actual_value = _decimal(actual)
            hit = (
                expected_value is not None
                and actual_value is not None
                and abs(actual_value - expected_value) <= size_tolerance_mm
            )
        else:  # THICKNESS
            expected_value = _decimal(expected)
            actual_value = _decimal(actual)
            hit = (
                expected_value is not None
                and actual_value is not None
                and abs(actual_value - expected_value) <= thickness_tolerance_um
            )
        hits[dimension] = hit
        if hit:
            matched += 1
    return hits, matched, missing


def _spec_score(matched: int, missing: int) -> Decimal:
    base = Decimal(matched) / Decimal(len(SPEC_DIMENSIONS))
    penalty = Decimal(missing) * _MISSING_FIELD_PENALTY
    return max(Decimal("0"), base - penalty)


def _time_decay(decision_at: str, now: date) -> Decimal:
    try:
        parsed = datetime.fromisoformat(str(decision_at)).date()
    except (TypeError, ValueError):
        return Decimal("0.25")
    days = max(0, (now - parsed).days)
    return Decimal("0.5") ** (Decimal(days) / Decimal(_TIME_HALF_LIFE_DAYS))


def _reputation_score(adopted_counts: dict[str, int], supplier_name: str) -> Decimal:
    count = int(adopted_counts.get(supplier_name, 0) or 0)
    if count <= 0:
        return Decimal("0.5")
    return min(Decimal("1"), Decimal("0.5") + Decimal(count) * Decimal("0.1"))


def _quality_score(flags: list[str]) -> Decimal:
    score = Decimal("1.0")
    if "low_confidence" in flags:
        score *= _LOW_QUALITY_SCORE
    if "conflict_evidence" in flags:
        score *= _CONFLICT_QUALITY_SCORE
    return score


class Retriever:
    """Deterministic hybrid retriever over the local rag_chunks index."""

    def __init__(self, storage: Any) -> None:
        self.rag = storage.rag

    def retrieve(
        self,
        *,
        request: dict[str, Any],
        limit: int = 5,
        candidate_limit: int = 20,
        now: date | None = None,
        adopted_counts: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return top-k knowledge references for a procurement request."""
        if limit < 1 or candidate_limit < limit:
            raise ValueError("limit 必须为正且不大于 candidate_limit")
        as_of = now or date.today()
        request_id = str(request["id"])
        request_specs = request.get("specifications", {})
        constraints = request.get("constraints", {})
        size_tolerance = _decimal(
            constraints.get("size_tolerance_mm", DEFAULT_SIZE_TOLERANCE_MM)
        ) or DEFAULT_SIZE_TOLERANCE_MM
        thickness_tolerance = _decimal(
            constraints.get("thickness_tolerance_um", DEFAULT_THICKNESS_TOLERANCE_UM)
        ) or DEFAULT_THICKNESS_TOLERANCE_UM
        counts = adopted_counts or {}

        keyword_query = " ".join(
            term
            for term in (
                str(request.get("item_name") or ""),
                str(request_specs.get(MATERIAL) or ""),
                str(request_specs.get(COLOR) or ""),
            )
            if term
        )
        keyword_hits = {
            str(chunk["chunk_sha256"]): chunk
            for chunk in self.rag.fts_search(keyword_query, limit=200)
            if str(chunk.get("request_id")) != request_id
        }

        structured_hits: dict[str, dict[str, Any]] = {}
        # Page through the whole index with a stable order; a single
        # list_chunks(limit=100_000) call would silently drop anything older
        # than the newest 100k rows.
        scan_offset = 0
        while True:
            page = self.rag.list_chunks(
                limit=_STRUCTURED_SCAN_PAGE_SIZE, offset=scan_offset
            )
            if not page:
                break
            for chunk in page:
                if str(chunk.get("request_id")) == request_id:
                    continue
                hits, matched, missing = _structured_hits(
                    request_specs,
                    chunk.get("specifications", {}),
                    size_tolerance_mm=size_tolerance,
                    thickness_tolerance_um=thickness_tolerance,
                )
                if matched == 0:
                    continue
                structured_hits[str(chunk["chunk_sha256"])] = {
                    **chunk,
                    "_spec_hits": hits,
                    "_spec_matched": matched,
                    "_spec_missing": missing,
                }
            scan_offset += len(page)
            if len(page) < _STRUCTURED_SCAN_PAGE_SIZE:
                break

        candidates: dict[str, dict[str, Any]] = {}
        for chunk_sha, chunk in keyword_hits.items():
            if chunk_sha in structured_hits:
                candidates[chunk_sha] = structured_hits[chunk_sha]
            else:
                hits, matched, missing = _structured_hits(
                    request_specs,
                    chunk.get("specifications", {}),
                    size_tolerance_mm=size_tolerance,
                    thickness_tolerance_um=thickness_tolerance,
                )
                candidates[chunk_sha] = {
                    **chunk,
                    "_spec_hits": hits,
                    "_spec_matched": matched,
                    "_spec_missing": missing,
                }
        for chunk_sha, chunk in structured_hits.items():
            candidates.setdefault(chunk_sha, chunk)

        coarse = []
        for chunk_sha, chunk in candidates.items():
            structured_score = _spec_score(
                int(chunk["_spec_matched"]), int(chunk["_spec_missing"])
            )
            keyword_bonus = _KEYWORD_BONUS if chunk_sha in keyword_hits else Decimal("0")
            coarse.append(
                (structured_score + keyword_bonus, chunk, chunk_sha)
            )
        coarse.sort(
            key=lambda item: (
                item[0],
                str(item[1].get("decision_at") or ""),
                item[2],
            ),
            reverse=True,
        )
        top_candidates = coarse[:candidate_limit]

        reranked: list[dict[str, Any]] = []
        for _coarse_score, chunk, chunk_sha in top_candidates:
            spec_score = _spec_score(
                int(chunk["_spec_matched"]), int(chunk["_spec_missing"])
            )
            if spec_score <= 0:
                # Spec-mismatched history must never be injected as a reference.
                continue
            time_score = _time_decay(str(chunk.get("decision_at") or ""), as_of)
            reputation = _reputation_score(counts, str(chunk.get("supplier_name") or ""))
            quality = _quality_score(list(chunk.get("quality_flags") or []))
            final_score = (
                _WEIGHT_SPEC * spec_score
                + _WEIGHT_TIME * time_score
                + _WEIGHT_REPUTATION * reputation
                + _WEIGHT_QUALITY * quality
            )
            reranked.append(
                {
                    **chunk,
                    "chunk_sha256": chunk_sha,
                    "score": format(final_score.quantize(Decimal("0.0001")), "f"),
                    "specification_summary": specification_summary(
                        chunk.get("specifications", {})
                    ),
                    "spec_match": {
                        dimension: bool(chunk["_spec_hits"].get(dimension))
                        for dimension in SPEC_DIMENSIONS
                    },
                }
            )
        reranked.sort(
            key=lambda item: (
                Decimal(item["score"]),
                str(item.get("decision_at") or ""),
                str(item["chunk_sha256"]),
            ),
            reverse=True,
        )
        return reranked[:limit]


__all__ = [
    "COLOR",
    "LENGTH",
    "MATERIAL",
    "PRINT_COLORS",
    "Retriever",
    "SPEC_DIMENSIONS",
    "THICKNESS",
    "WIDTH",
    "_reputation_score",
    "_spec_score",
    "_time_decay",
]
