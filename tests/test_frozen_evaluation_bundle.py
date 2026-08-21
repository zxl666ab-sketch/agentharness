"""The Java control plane serves the frozen evaluation bundle in kafka/demo mode.

This test keeps procurement-service/src/main/resources/frozen/frozen-evaluation.json
byte-equivalent (modulo wall-clock processing timing) to the canonical Python
computation, so the bundled artifact can never drift silently.

Regenerate with: uv run python scripts/export_frozen_evaluation.py
"""

from __future__ import annotations

import json
from pathlib import Path

from agentharness.procurement.evaluation import evaluate_frozen_cases

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = (
    ROOT
    / "procurement-service"
    / "src"
    / "main"
    / "resources"
    / "frozen"
    / "frozen-evaluation.json"
)


def _normalize_processing(value):
    """Zero wall-clock timing that legitimately varies between runs."""
    if isinstance(value, dict):
        processed = {}
        for key, item in value.items():
            if key == "processing_ms":
                item = 0
            elif key == "processing" and isinstance(item, dict):
                item = dict(item)
                item["total_ms"] = 0
                item["average_ms_per_quote"] = 0
            processed[key] = _normalize_processing(item)
        return processed
    if isinstance(value, list):
        return [_normalize_processing(item) for item in value]
    return value


def test_bundled_frozen_evaluation_matches_python_computation():
    assert BUNDLE.is_file(), f"missing bundled evaluation: {BUNDLE}"
    bundled = json.loads(BUNDLE.read_text(encoding="utf-8"))
    computed = evaluate_frozen_cases()
    assert _normalize_processing(bundled) == _normalize_processing(computed)


def test_bundled_evaluation_is_frozen_and_complete():
    bundled = json.loads(BUNDLE.read_text(encoding="utf-8"))
    assert bundled.get("frozen") is True
    assert bundled.get("case_count") == 31
    assert bundled["metrics"]["field_extraction"]["total"] == 620
    assert bundled["metrics"]["field_extraction"]["correct"] >= 617
    assert len(bundled.get("truth_sha256", "")) == 64
