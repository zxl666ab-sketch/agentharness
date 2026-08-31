"""The Java control plane serves the frozen evaluation bundle in kafka/demo mode.

This test keeps procurement-service/src/main/resources/frozen/frozen-evaluation.json
byte-equivalent (modulo wall-clock timing and platform-dependent document hashes)
to the canonical Python computation, so the bundled artifact can never drift
silently.

Regenerate with: uv run python scripts/export_frozen_evaluation.py
"""

from __future__ import annotations

import json
import re
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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _normalize_processing(value):
    """Zero what legitimately varies between runs or platforms.

    ``document_sha256`` is platform-dependent by construction: zh-CN PDF
    cases embed whichever CJK font the host provides (Windows simhei vs
    Linux arphic/Noto), so document bytes — and their hash — differ across
    machines even though every parsed field and metric matches exactly.
    Semantic drift remains fully caught: all other fields are compared
    byte-for-byte, and the hash format is asserted in its own test.
    """
    if isinstance(value, dict):
        processed = {}
        for key, item in value.items():
            if key == "processing_ms":
                item = 0
            elif key == "processing" and isinstance(item, dict):
                item = dict(item)
                item["total_ms"] = 0
                item["average_ms_per_quote"] = 0
            elif key == "document_sha256":
                item = "<platform-dependent>"
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


def test_document_hashes_are_well_formed_sha256():
    """document_sha256 退出跨平台逐字节比对，但必须仍是合法 SHA-256 指纹。"""
    bundled = json.loads(BUNDLE.read_text(encoding="utf-8"))
    cases = bundled["approaches"]["agent_assisted"]["raw"]["cases"]
    assert len(cases) == 31
    for case in cases:
        assert _SHA256_RE.match(str(case["document_sha256"])), case["case_id"]


def test_bundled_evaluation_is_frozen_and_complete():
    bundled = json.loads(BUNDLE.read_text(encoding="utf-8"))
    assert bundled.get("frozen") is True
    assert bundled.get("case_count") == 31
    assert bundled["metrics"]["field_extraction"]["total"] == 620
    assert bundled["metrics"]["field_extraction"]["correct"] >= 617
    assert len(bundled.get("truth_sha256", "")) == 64
