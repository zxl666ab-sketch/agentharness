"""K5 历史报价 RAG：参考区间注入（软提示）与冻结评测扩展格式回归。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentharness.procurement.reference_prices import (
    FLAG_ABOVE,
    FLAG_BELOW,
    apply_reference_interval,
    unit_price,
)

ROOT = Path(__file__).resolve().parents[1]
JAVA_FROZEN_EVALUATION = (
    ROOT / "procurement-service" / "src" / "main" / "resources" / "frozen" / "frozen-evaluation.json"
)
EVALUATION_EXT = (
    ROOT / "procurement-service" / "src" / "main" / "resources" / "frozen" / "frozen-evaluation-ext.json"
)

INTERVAL = {
    "p25": "7500.00",
    "p75": "9400.00",
    "p25_unit": "0.5000",
    "p75_unit": "0.6267",
    "count": 3,
    "basis": "landed_total_base",
}


def _flat_quote(price: float, basis: float = 1.0) -> dict:
    return {"fields": {"unit_price": price, "price_basis": basis}}


def _context_quote(price: float, basis: float = 1.0) -> dict:
    return {
        "extracted": {
            "fields": {
                "unit_price": {"value": price, "confidence": 1, "status": "accepted"},
                "price_basis": {"value": basis, "confidence": 1, "status": "accepted"},
            }
        }
    }


def test_interval_is_injected_as_optional_field_with_soft_summary() -> None:
    structured = {"schema_version": 1, "summary": "已核对 3 份报价。", "risk_flags": [], "quote_count": 3}
    result = apply_reference_interval(structured, INTERVAL, [])

    assert result["reference_price_interval"] == {
        "p25": "7500.00",
        "p75": "9400.00",
        "p25_unit": "0.5000",
        "p75_unit": "0.6267",
        "count": 3,
        "basis": "landed_total_base",
    }
    assert "历史成交参考区间 7500.00–9400.00（3 条已批准成交，软提示不参与比价）" in result["summary"]
    assert result["risk_flags"] == []


def test_null_interval_injects_nothing() -> None:
    structured = {"schema_version": 1, "summary": "已核对 2 份报价。", "risk_flags": [], "quote_count": 2}
    result = apply_reference_interval(structured, None, [])

    assert "reference_price_interval" not in result
    assert result["summary"] == "已核对 2 份报价。"


def test_below_and_above_reference_flags_are_soft() -> None:
    quotes = [_flat_quote(0.30), _flat_quote(0.40), _flat_quote(0.90)]
    structured = {"summary": "", "risk_flags": [], "quote_count": 3}
    result = apply_reference_interval(structured, INTERVAL, quotes)

    assert FLAG_BELOW in result["risk_flags"]
    assert FLAG_ABOVE in result["risk_flags"]
    # 软提示：不排除任何报价（结构化结果不含排除字段）
    assert "excluded" not in result
    assert "eligible" not in result


def test_mid_range_quotes_produce_no_flags() -> None:
    quotes = [_flat_quote(0.55), _flat_quote(0.60)]
    structured = {"summary": "", "risk_flags": [], "quote_count": 2}
    result = apply_reference_interval(structured, INTERVAL, quotes)

    assert result["risk_flags"] == []


def test_unit_price_reads_both_field_shapes() -> None:
    assert unit_price(_flat_quote(520, 1000)) == 0.52
    assert unit_price(_context_quote(0.48, 1)) == 0.48
    assert unit_price({"fields": {"unit_price": None}}) is None
    assert unit_price({"fields": {"unit_price": "abc"}}) is None
    assert unit_price(None) is None


def test_unit_price_unwraps_wrapped_basis_and_rejects_zero_basis() -> None:
    assert unit_price(_context_quote(520, 1000)) == 0.52
    assert unit_price({"fields": {"unit_price": "5", "price_basis": 0}}) is None
    assert unit_price({"fields": {}}) is None


def test_malformed_interval_units_do_not_raise_flags() -> None:
    broken = dict(INTERVAL, p25_unit="abc", p75_unit="xyz")
    structured = {"summary": "", "risk_flags": [], "quote_count": 1}
    result = apply_reference_interval(structured, broken, [_flat_quote(0.30)])

    assert result["reference_price_interval"]["p25_unit"] == "abc"
    assert result["risk_flags"] == []


def test_flags_are_deduplicated() -> None:
    quotes = [_flat_quote(0.30), _flat_quote(0.31)]
    structured = {"summary": "", "risk_flags": ["EXISTING"], "quote_count": 2}
    result = apply_reference_interval(structured, INTERVAL, quotes)

    assert result["risk_flags"].count(FLAG_BELOW) == 1
    assert result["risk_flags"][0] == "EXISTING"


def test_frozen_evaluation_is_byte_identical() -> None:
    recorded_sha256 = "3acbf864a7488ef6490e7cc3dc2678d90bb0dd1af0c09da7976a06155a226d76"
    actual = hashlib.sha256(JAVA_FROZEN_EVALUATION.read_bytes()).hexdigest()
    assert actual == recorded_sha256, "冻结评测资源 frozen-evaluation.json 不允许改动"


def test_evaluation_ext_is_well_formed_v1_extension() -> None:
    ext = json.loads(EVALUATION_EXT.read_text(encoding="utf-8"))
    assert ext["schema_version"] == 1
    assert ext["extension"] == "reference-price-interval"
    assert ext["frozen_truth_sha256"] == "63647f520bff1ab20e9215cc65e1b246a6f27fcf88cdb226fe7eae72fd6c1ffb"
    assert len(ext["cases"]) >= 3
    for case in ext["cases"]:
        assert case["id"]
        assert case["description"]
        if "input_landed_totals" in case:
            totals = case["input_landed_totals"]
            expected = case["expect"]["interval"]
            if expected is None:
                assert len(totals) < 3
            else:
                assert len(totals) >= 3
                assert set(expected) >= {"p25", "p75", "count", "basis"}


def test_interval_percentile_semantics_match_ext_cases() -> None:
    """扩展用例 001：样本 3 条时 p25/p75 与区间文件一致（Java 侧口径复算）。"""
    ext = json.loads(EVALUATION_EXT.read_text(encoding="utf-8"))
    case = next(item for item in ext["cases"] if item["id"] == "ref-interval-001")
    totals = sorted(float(value) for value in case["input_landed_totals"])
    p25 = totals[int((len(totals) - 1) * 0.25)]
    p75 = totals[int((len(totals) - 1) * 0.75)]
    assert f"{p25:.2f}" == case["expect"]["interval"]["p25"]
    assert f"{p75:.2f}" == case["expect"]["interval"]["p75"]
