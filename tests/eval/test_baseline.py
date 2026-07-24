"""Baseline comparison gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentharness.eval.baseline import BaselineGates, compare_to_baseline, load_baseline
from agentharness.eval.dataset import EvalConfigError


def _report(
    *,
    results: list[dict],
    pass_rate: float | None = None,
    mean_score: float | None = None,
    total_tokens: int | None = None,
    mean_latency_s: float | None = None,
) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    return {
        "schema_version": 1,
        "suite": "s",
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate if pass_rate is not None else (passed / total if total else 0.0),
        "mean_score": mean_score
        if mean_score is not None
        else (sum(float(r.get("score") or 0) for r in results) / total if total else 0.0),
        "total_tokens": total_tokens
        if total_tokens is not None
        else sum(int(r.get("total_tokens") or 0) for r in results),
        "mean_latency_s": mean_latency_s
        if mean_latency_s is not None
        else (
            sum(float(r.get("latency_s") or 0) for r in results) / total if total else 0.0
        ),
        "results": results,
    }


def _row(
    case_id: str,
    *,
    passed: bool = True,
    score: float = 1.0,
    total_tokens: int = 10,
    latency_s: float = 0.1,
) -> dict:
    return {
        "case_id": case_id,
        "run_id": f"run-{case_id}",
        "status": "completed" if passed else "failed",
        "passed": passed,
        "score": score,
        "total_tokens": total_tokens,
        "latency_s": latency_s,
        "steps": 1,
        "reasons": [] if passed else ["nope"],
    }


def test_new_failure_is_unconditional_regression(tmp_path: Path) -> None:
    base = _report(results=[_row("a"), _row("b")])
    cur = _report(results=[_row("a"), _row("b", passed=False, score=0.0)])
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    reg = compare_to_baseline(cur, bp, BaselineGates())
    assert reg.failed
    assert "b" in reg.new_failures
    assert any(g.gate == "new_failures" and g.triggered for g in reg.gates)


def test_min_pass_rate_and_mean_score_gates(tmp_path: Path) -> None:
    base = _report(results=[_row("a"), _row("b")])
    cur = _report(
        results=[_row("a"), _row("b", passed=False, score=0.0)],
        pass_rate=0.5,
        mean_score=0.5,
    )
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    reg = compare_to_baseline(
        cur,
        bp,
        BaselineGates(min_pass_rate=0.9, min_mean_score=0.9),
    )
    assert any(g.gate == "min_pass_rate" and g.triggered for g in reg.gates)
    assert any(g.gate == "min_mean_score" and g.triggered for g in reg.gates)


def test_max_score_regression_gate(tmp_path: Path) -> None:
    base = _report(results=[_row("a", score=1.0)])
    cur = _report(results=[_row("a", score=0.5)])
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    reg = compare_to_baseline(cur, bp, BaselineGates(max_score_regression=0.2))
    assert any(g.gate == "max_score_regression" and g.triggered for g in reg.gates)
    assert reg.score_drops


def test_token_and_latency_regression_gates(tmp_path: Path) -> None:
    base = _report(results=[_row("a", total_tokens=100, latency_s=1.0)])
    cur = _report(results=[_row("a", total_tokens=200, latency_s=2.0)])
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    reg = compare_to_baseline(
        cur,
        bp,
        BaselineGates(max_token_regression=0.2, max_latency_regression=0.2),
    )
    assert any(g.gate == "max_token_regression" and g.triggered for g in reg.gates)
    assert any(g.gate == "max_latency_regression" and g.triggered for g in reg.gates)


def test_new_case_not_in_score_drop_comparison(tmp_path: Path) -> None:
    base = _report(results=[_row("a", score=1.0)])
    cur = _report(results=[_row("a", score=1.0), _row("new", score=0.0, passed=False)])
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(base), encoding="utf-8")
    reg = compare_to_baseline(cur, bp, BaselineGates(max_score_regression=0.0))
    # new case failed but was not in baseline → not a "new failure" vs baseline pass
    assert "new" not in reg.new_failures
    assert not any(d["case_id"] == "new" for d in reg.score_drops)
    assert reg.summary["new_case_count"] == 1


def test_missing_baseline_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(EvalConfigError):
        load_baseline(tmp_path / "missing.json")


def test_unrecognized_schema_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"schema_version": 99, "results": []}), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="schema"):
        load_baseline(p)


def test_missing_schema_version_is_config_error(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"results": []}), encoding="utf-8")
    with pytest.raises(EvalConfigError, match="schema"):
        compare_to_baseline(_report(results=[_row("a")]), p)
