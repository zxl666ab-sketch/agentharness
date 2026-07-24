"""JSON / JUnit report writers."""

from __future__ import annotations

import json
from pathlib import Path

from agentharness.eval.baseline import load_baseline
from agentharness.eval.report import (
    SCHEMA_VERSION,
    suite_report_to_dict,
    write_json_report,
    write_junit_xml,
)
from agentharness.eval.runner import CaseResult, GroupMetrics, SuiteReport


def _sample_report() -> SuiteReport:
    results = [
        CaseResult(
            case_id="ok",
            logical_case_id="ok",
            provider="fake",
            model="m",
            passed=True,
            status="completed",
            score=1.0,
            reasons=[],
            latency_s=0.12,
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
            steps=1,
            run_id="run-ok",
            tags=["smoke"],
        ),
        CaseResult(
            case_id="bad",
            logical_case_id="bad",
            provider="fake",
            model="m",
            passed=False,
            status="failed",
            score=0.0,
            reasons=["missing substring: x"],
            latency_s=0.2,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            steps=1,
            run_id="run-bad",
        ),
    ]
    g = GroupMetrics(provider="fake", model="m", total=2, passed=1, latency_s=0.32, total_tokens=7, steps=2, score_sum=1.0)
    return SuiteReport(suite="demo", results=results, groups=[g], data_dir="/tmp/x")


def test_json_report_fields_and_baseline_roundtrip(tmp_path: Path) -> None:
    report = _sample_report()
    path = write_json_report(report, tmp_path / "r.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["suite"] == "demo"
    assert data["total"] == 2
    assert data["passed"] == 1
    assert "pass_rate" in data and "mean_score" in data
    row = data["results"][0]
    for key in (
        "run_id",
        "case_id",
        "status",
        "passed",
        "score",
        "latency_s",
        "total_tokens",
        "steps",
        "reasons",
        "input_tokens",
        "output_tokens",
    ):
        assert key in row
    # stable field order: schema_version first
    assert list(data.keys())[0] == "schema_version"
    # can be re-read as baseline
    loaded = load_baseline(path)
    assert loaded["schema_version"] == 1


def test_junit_has_failure_for_failed_case(tmp_path: Path) -> None:
    path = write_junit_xml(_sample_report(), tmp_path / "j.xml")
    text = path.read_text(encoding="utf-8")
    assert 'tests="2"' in text
    assert 'failures="1"' in text
    assert 'name="ok"' in text
    assert 'name="bad"' in text
    assert "<failure" in text
    assert "missing substring" in text


def test_suite_report_to_dict_mean_score() -> None:
    d = suite_report_to_dict(_sample_report())
    assert d["mean_score"] == 0.5
