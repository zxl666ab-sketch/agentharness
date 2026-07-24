from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentharness.eval.contracts import (
    CheckResult,
    DiagnosisReport,
    EvaluationReport,
    EvidenceRef,
    RegressionCase,
    RegressionPolicy,
    RegressionSet,
)
from agentharness.eval.regression import RegressionGate, summarize_reruns
from agentharness.eval.report import suite_report_to_dict, write_junit_xml
from agentharness.eval.runner import CaseResult, GroupMetrics, SuiteReport


def _evaluation(
    case_id: str,
    *,
    passed: bool = True,
    score: float = 1.0,
    sequence: int = 2,
    trajectory_ok: bool = True,
    arguments_ok: bool = True,
) -> EvaluationReport:
    ref = EvidenceRef(
        trace_id=f"trace-{case_id}",
        run_id=f"run-{case_id}",
        span_id=f"span-{sequence}",
        event_id=f"event-{sequence}",
        sequence=sequence,
    )
    checks = [
        CheckResult(
            id="trajectory.sequence",
            category="trajectory",
            status="passed" if trajectory_ok else "failed",
            score=1.0 if trajectory_ok else 0.0,
            evidence=[ref],
            failure_category=None if trajectory_ok else "wrong_tool_selection",
        ),
        CheckResult(
            id="tool.read_file.arguments_schema",
            category="tool",
            status="passed" if arguments_ok else "failed",
            score=1.0 if arguments_ok else 0.0,
            evidence=[ref],
            failure_category=None if arguments_ok else "invalid_tool_arguments",
        ),
    ]
    return EvaluationReport(
        report_id=f"report-{case_id}",
        trace_id=f"trace-{case_id}",
        run_id=f"run-{case_id}",
        policy_id="policy",
        policy_version="1",
        mode="scored",
        passed=passed,
        score=score,
        checks=checks,
        first_divergence=None if passed else ref,
        hard_failures=0 if passed else 1,
        passed_count=sum(check.status == "passed" for check in checks),
        failed_count=sum(check.status == "failed" for check in checks),
    )


def _case(
    case_id: str,
    *,
    passed: bool = True,
    score: float = 1.0,
    latency_ms: float = 100,
    tokens: int = 20,
    cost: float = 0.01,
    tags: list[str] | None = None,
    provider: str = "fake",
    model: str = "m",
    sequence: int = 2,
    trajectory_ok: bool = True,
    arguments_ok: bool = True,
) -> RegressionCase:
    evaluation = _evaluation(
        case_id,
        passed=passed,
        score=score,
        sequence=sequence,
        trajectory_ok=trajectory_ok,
        arguments_ok=arguments_ok,
    )
    diagnosis = None
    if not passed:
        diagnosis = DiagnosisReport(
            trace_id=evaluation.trace_id,
            report_id=evaluation.report_id,
            root_cause="invalid_tool_arguments",
            confidence=0.9,
            first_divergence=evaluation.first_divergence,
            evidence=[evaluation.first_divergence] if evaluation.first_divergence else [],
        )
    return RegressionCase(
        case_id=case_id,
        tags=tags or ["smoke"],
        provider=provider,
        model=model,
        evaluation=evaluation,
        diagnosis=diagnosis,
        latency_ms=latency_ms,
        total_tokens=tokens,
        cost=cost,
    )


def test_regression_gate_reports_case_group_metrics_and_first_divergence() -> None:
    baseline = RegressionSet(
        set_id="baseline",
        golden=True,
        cases=[
            _case("a", tags=["smoke", "tools"]),
            _case("b", tags=["tools"], provider="fake", model="m2"),
        ],
    )
    candidate = RegressionSet(
        set_id="candidate",
        cases=[
            _case("a", latency_ms=300, tokens=40, cost=0.02, tags=["smoke", "tools"]),
            _case(
                "b",
                passed=False,
                score=0.4,
                latency_ms=400,
                tokens=60,
                cost=0.03,
                tags=["tools"],
                provider="fake",
                model="m2",
                sequence=5,
                trajectory_ok=False,
                arguments_ok=False,
            ),
        ],
    )
    decision = RegressionGate.compare(
        baseline,
        candidate,
        RegressionPolicy(
            min_pass_rate=1.0,
            max_score_drop=0.1,
            min_trajectory_compliance=1.0,
            min_tool_argument_accuracy=1.0,
            max_latency_ratio_increase=0.5,
            max_token_ratio_increase=0.5,
            max_cost_ratio_increase=0.5,
        ),
    )
    assert decision.passed is False
    assert decision.exit_code == 1
    assert decision.failed_case_ids == ["b"]
    report = decision.regression
    assert report.new_failures == ["b"]
    assert report.score_drops[0]["case_id"] == "b"
    assert report.case_metrics["candidate"]["pass_rate"] == 0.5
    assert report.case_metrics["candidate"]["trajectory_compliance"] == 0.5
    assert report.case_metrics["candidate"]["tool_argument_accuracy"] == 0.5
    assert report.latency["candidate"]["p50_ms"] == 350
    assert report.latency["candidate"]["p95_ms"] == 395
    assert report.tokens["candidate"]["mean"] == 50
    assert report.costs["candidate"]["mean"] == 0.025
    assert report.first_divergence_distribution == {"5": 1}
    assert "tools" in report.tag_metrics
    assert "fake/m2" in report.provider_model_metrics
    assert report.failed_cases[0]["diagnosis"]["root_cause"] == "invalid_tool_arguments"
    assert any(finding.status == "failed" for finding in report.findings)


def test_repaired_candidate_passes_same_gate() -> None:
    baseline = RegressionSet(set_id="b", cases=[_case("a"), _case("b")])
    repaired = RegressionSet(set_id="c", cases=[_case("a"), _case("b")])
    decision = RegressionGate.compare(baseline, repaired, RegressionPolicy())
    assert decision.passed is True
    assert decision.exit_code == 0
    assert decision.failed_case_ids == []


def test_random_rerun_statistics_include_wilson_variance_and_percentiles() -> None:
    stats = summarize_reruns(
        [
            _case("r1", latency_ms=100, score=0.9),
            _case("r2", latency_ms=200, score=0.8),
            _case("r3", latency_ms=300, score=0.7),
            _case("r4", latency_ms=400, score=0.6),
            _case("r5", passed=False, score=0.2, latency_ms=500),
        ]
    )
    assert stats.sample_count == 5
    assert stats.success_rate == 0.8
    assert 0 < stats.wilson_low < stats.success_rate < stats.wilson_high < 1
    assert stats.mean_score == pytest.approx(0.64)
    assert stats.score_variance is not None and stats.score_variance > 0
    assert stats.p50_latency_ms == 300
    assert stats.p95_latency_ms == 480


def test_json_and_junit_include_evaluation_diff_diagnosis_and_web_ids(
    tmp_path: Path,
) -> None:
    evaluation = _evaluation("bad", passed=False, score=0.2, sequence=4)
    diagnosis = DiagnosisReport(
        trace_id=evaluation.trace_id,
        report_id=evaluation.report_id,
        root_cause="invalid_tool_arguments",
        confidence=0.9,
        first_divergence=evaluation.first_divergence,
        evidence=[evaluation.first_divergence] if evaluation.first_divergence else [],
    )
    case = CaseResult(
        case_id="bad",
        logical_case_id="bad",
        provider="fake",
        model="m",
        passed=False,
        status="completed",
        score=0.2,
        reasons=["bad args"],
        latency_s=0.1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        steps=2,
        run_id="run-bad",
        evaluation_report=evaluation,
        diagnosis=diagnosis,
        snapshot_id="snapshot-bad",
        web_report_id="report-bad",
        baseline_diff={"score_delta": -0.8},
    )
    suite = SuiteReport(
        suite="ci",
        results=[case],
        groups=[GroupMetrics(provider="fake", model="m", total=1)],
    )
    payload = suite_report_to_dict(suite)
    row = payload["results"][0]
    assert row["evaluation_report"]["first_divergence"]["sequence"] == 4
    assert row["diagnosis"]["root_cause"] == "invalid_tool_arguments"
    assert row["snapshot_id"] == "snapshot-bad"
    assert row["web_report_id"] == "report-bad"
    assert row["baseline_diff"] == {"score_delta": -0.8}

    junit = write_junit_xml(suite, tmp_path / "report.xml")
    text = junit.read_text(encoding="utf-8")
    assert "first_divergence" in text
    assert "invalid_tool_arguments" in text
    assert "run-bad" in text
    assert json.dumps({"score_delta": -0.8}) in text
