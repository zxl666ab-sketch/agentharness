"""Trace-native regression comparison, CI decisions, and rerun statistics."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from agentharness.eval.contracts import (
    CheckResult,
    DiagnosisReport,
    EvaluationReport,
    EvidenceRef,
    GateDecision,
    RegressionCase,
    RegressionFinding,
    RegressionPolicy,
    RegressionReport,
    RegressionSet,
    RerunStatistics,
)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _rate(checks: list[CheckResult]) -> float | None:
    configured = [item for item in checks if item.status in {"passed", "failed", "error"}]
    if not configured:
        return None
    return sum(item.status == "passed" for item in configured) / len(configured)


def _metrics(cases: list[RegressionCase]) -> dict[str, Any]:
    if not cases:
        return {
            "case_count": 0,
            "pass_rate": None,
            "mean_score": None,
            "trajectory_compliance": None,
            "tool_argument_accuracy": None,
        }
    scores = [item.evaluation.score for item in cases if item.evaluation.score is not None]
    trajectory_checks = [
        check
        for item in cases
        for check in item.evaluation.checks
        if check.category == "trajectory"
    ]
    argument_checks = [
        check
        for item in cases
        for check in item.evaluation.checks
        if ".arguments" in check.id
    ]
    return {
        "case_count": len(cases),
        "pass_rate": sum(item.evaluation.passed is True for item in cases) / len(cases),
        "mean_score": statistics.fmean(scores) if scores else None,
        "trajectory_compliance": _rate(trajectory_checks),
        "tool_argument_accuracy": _rate(argument_checks),
    }


def _distribution(cases: list[RegressionCase]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in cases:
        divergence = item.evaluation.first_divergence
        if divergence is None:
            continue
        key = str(divergence.sequence if divergence.sequence is not None else "unknown")
        counts[key] += 1
    return dict(sorted(counts.items(), key=lambda pair: pair[0]))


def _series(cases: list[RegressionCase], field: str) -> dict[str, Any]:
    values = [float(getattr(item, field)) for item in cases]
    return {
        "mean": statistics.fmean(values) if values else None,
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def _latency(cases: list[RegressionCase]) -> dict[str, Any]:
    values = [float(item.latency_ms) for item in cases]
    return {
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
    }


def _ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return candidate / baseline - 1.0


def _evidence(case: RegressionCase | None) -> list[EvidenceRef]:
    if case is None or case.evaluation.first_divergence is None:
        return []
    return [case.evaluation.first_divergence]


def _synthesized_evaluation(row: dict[str, Any], case_id: str) -> EvaluationReport:
    raw = row.get("evaluation_report")
    if isinstance(raw, dict):
        return EvaluationReport.model_validate(raw)
    divergence_raw = row.get("first_divergence")
    divergence = (
        EvidenceRef.model_validate(divergence_raw)
        if isinstance(divergence_raw, dict)
        else None
    )
    passed = bool(row.get("passed"))
    score = float(row["score"]) if row.get("score") is not None else None
    reason = "; ".join(str(item) for item in row.get("reasons") or [])
    checks = [
        CheckResult(
            id="legacy.case",
            category="legacy",
            status="passed" if passed else "failed",
            score=score,
            message=reason,
            evidence=[divergence] if divergence else [],
            failure_category=None if passed else "legacy_failure",
        )
    ]
    return EvaluationReport(
        report_id=str(row.get("report_id") or f"legacy:{case_id}"),
        trace_id=str(row.get("trace_id") or row.get("run_id") or case_id),
        run_id=str(row.get("run_id") or case_id),
        policy_id="legacy-report",
        policy_version="1",
        mode="scored" if score is not None else "health_only",
        passed=passed,
        score=score,
        checks=checks,
        first_divergence=divergence,
        hard_failures=0 if passed else 1,
        passed_count=1 if passed else 0,
        failed_count=0 if passed else 1,
    )


def normalize_regression_set(value: Any, *, fallback_id: str) -> RegressionSet:
    if isinstance(value, RegressionSet):
        return value
    if hasattr(value, "results") and hasattr(value, "suite"):
        rows = []
        for item in value.results:
            evaluation = item.evaluation_report or _synthesized_evaluation(
                {
                    "case_id": item.case_id,
                    "run_id": item.run_id,
                    "passed": item.passed,
                    "score": item.score,
                    "reasons": item.reasons,
                },
                item.case_id,
            )
            rows.append(
                RegressionCase(
                    case_id=item.case_id,
                    tags=list(item.tags),
                    provider=item.provider,
                    model=item.model,
                    evaluation=evaluation,
                    diagnosis=item.diagnosis,
                    snapshot_id=item.snapshot_id,
                    web_report_id=item.web_report_id,
                    latency_ms=float(item.latency_s) * 1000.0,
                    total_tokens=int(item.total_tokens),
                )
            )
        return RegressionSet(set_id=str(value.suite), cases=rows)
    if not isinstance(value, dict):
        raise TypeError("regression input must be RegressionSet, SuiteReport, or report dict")
    if "cases" in value:
        return RegressionSet.model_validate(value)
    rows: list[RegressionCase] = []
    for raw in value.get("results") or []:
        if not isinstance(raw, dict):
            continue
        case_id = str(raw.get("case_id") or "")
        if not case_id:
            continue
        diagnosis = (
            DiagnosisReport.model_validate(raw["diagnosis"])
            if isinstance(raw.get("diagnosis"), dict)
            else None
        )
        rows.append(
            RegressionCase(
                case_id=case_id,
                tags=list(raw.get("tags") or []),
                provider=str(raw.get("provider") or ""),
                model=raw.get("model"),
                evaluation=_synthesized_evaluation(raw, case_id),
                diagnosis=diagnosis,
                snapshot_id=raw.get("snapshot_id"),
                web_report_id=raw.get("web_report_id"),
                latency_ms=float(raw.get("latency_ms") or float(raw.get("latency_s") or 0) * 1000),
                total_tokens=int(raw.get("total_tokens") or 0),
                cost=float(raw.get("cost") or 0.0),
            )
        )
    return RegressionSet(
        set_id=str(value.get("suite") or value.get("set_id") or fallback_id),
        cases=rows,
        metadata={"schema_version": value.get("schema_version")},
    )


class RegressionGate:
    """Compare baseline and candidate cases under one versioned CI policy."""

    @classmethod
    def compare(
        cls,
        baseline: RegressionSet | dict[str, Any] | Any,
        candidate: RegressionSet | dict[str, Any] | Any,
        policy: RegressionPolicy,
    ) -> GateDecision:
        base = normalize_regression_set(baseline, fallback_id="baseline")
        current = normalize_regression_set(candidate, fallback_id="candidate")
        base_index = {item.case_id: item for item in base.cases}
        current_index = {item.case_id: item for item in current.cases}
        findings: list[CheckResult] = []
        gates: list[RegressionFinding] = []
        new_failures: list[str] = []
        score_drops: list[dict[str, Any]] = []

        def gate(
            gate_id: str,
            passed: bool,
            *,
            expected: Any,
            actual: Any,
            case: RegressionCase | None = None,
            message: str,
        ) -> None:
            findings.append(
                CheckResult(
                    id=f"regression.{gate_id}",
                    category="regression",
                    status="passed" if passed else "failed",
                    expected=expected,
                    actual=actual,
                    hard=True,
                    score=1.0 if passed else 0.0,
                    evidence=_evidence(case),
                    failure_category=None if passed else "regression",
                    message=message,
                )
            )
            gates.append(
                RegressionFinding(
                    gate=gate_id,
                    triggered=not passed,
                    message=message,
                    baseline=expected,
                    current=actual,
                )
            )

        for case_id, item in current_index.items():
            previous = base_index.get(case_id)
            if previous and previous.evaluation.passed is True and item.evaluation.passed is not True:
                new_failures.append(case_id)
        gate(
            "new_failures",
            not new_failures,
            expected=[],
            actual=new_failures,
            case=current_index.get(new_failures[0]) if new_failures else None,
            message=(
                "no new failures"
                if not new_failures
                else f"{len(new_failures)} newly failing case(s): {', '.join(new_failures)}"
            ),
        )

        for case_id, item in current_index.items():
            previous = base_index.get(case_id)
            if (
                previous is None
                or previous.evaluation.score is None
                or item.evaluation.score is None
            ):
                continue
            drop = previous.evaluation.score - item.evaluation.score
            if drop > 0:
                score_drops.append(
                    {
                        "case_id": case_id,
                        "baseline": previous.evaluation.score,
                        "candidate": item.evaluation.score,
                        "delta": -drop,
                    }
                )
            if policy.max_score_drop is not None and drop > policy.max_score_drop:
                gate(
                    f"score_drop.{case_id}",
                    False,
                    expected={"max_drop": policy.max_score_drop},
                    actual=drop,
                    case=item,
                    message=(
                        f"case {case_id} score drop {drop:.4f} exceeds "
                        f"{policy.max_score_drop:.4f}"
                    ),
                )

        base_metrics = _metrics(base.cases)
        current_metrics = _metrics(current.cases)
        for name, minimum in (
            ("pass_rate", policy.min_pass_rate),
            ("mean_score", policy.min_mean_score),
            ("trajectory_compliance", policy.min_trajectory_compliance),
            ("tool_argument_accuracy", policy.min_tool_argument_accuracy),
        ):
            if minimum is None:
                continue
            actual = current_metrics[name]
            passed = actual is not None and actual >= minimum
            gate(
                name,
                passed,
                expected={"min": minimum},
                actual=actual,
                message=(
                    f"{name} {actual!r} {'meets' if passed else 'is below'} minimum {minimum}"
                ),
            )

        base_latency = _latency(base.cases)
        current_latency = _latency(current.cases)
        base_tokens = _series(base.cases, "total_tokens")
        current_tokens = _series(current.cases, "total_tokens")
        base_costs = _series(base.cases, "cost")
        current_costs = _series(current.cases, "cost")
        for name, maximum, baseline_value, current_value in (
            (
                "latency_ratio",
                policy.max_latency_ratio_increase,
                base_latency["mean_ms"],
                current_latency["mean_ms"],
            ),
            (
                "token_ratio",
                policy.max_token_ratio_increase,
                base_tokens["mean"],
                current_tokens["mean"],
            ),
            (
                "cost_ratio",
                policy.max_cost_ratio_increase,
                base_costs["mean"],
                current_costs["mean"],
            ),
        ):
            if maximum is None:
                continue
            ratio = _ratio(current_value, baseline_value)
            passed = ratio is None or ratio <= maximum
            gate(
                name,
                passed,
                expected={"max_ratio_increase": maximum, "baseline": baseline_value},
                actual={"ratio_increase": ratio, "candidate": current_value},
                message=(
                    f"{name} increase {ratio!r} {'within' if passed else 'exceeds'} {maximum}"
                ),
            )

        tag_metrics: dict[str, Any] = {}
        tags = sorted({tag for item in [*base.cases, *current.cases] for tag in item.tags})
        for tag in tags:
            tag_metrics[tag] = {
                "baseline": _metrics([item for item in base.cases if tag in item.tags]),
                "candidate": _metrics([item for item in current.cases if tag in item.tags]),
            }
        provider_model_metrics: dict[str, Any] = {}
        groups = sorted(
            {
                (item.provider, item.model or "")
                for item in [*base.cases, *current.cases]
            }
        )
        for provider, model in groups:
            key = f"{provider}/{model}"
            provider_model_metrics[key] = {
                "baseline": _metrics(
                    [item for item in base.cases if (item.provider, item.model or "") == (provider, model)]
                ),
                "candidate": _metrics(
                    [item for item in current.cases if (item.provider, item.model or "") == (provider, model)]
                ),
            }

        failed_cases = [
            {
                "case_id": item.case_id,
                "run_id": item.evaluation.run_id,
                "trace_id": item.evaluation.trace_id,
                "report_id": item.evaluation.report_id,
                "snapshot_id": item.snapshot_id,
                "web_report_id": item.web_report_id,
                "first_divergence": (
                    item.evaluation.first_divergence.model_dump(mode="json")
                    if item.evaluation.first_divergence
                    else None
                ),
                "diagnosis": item.diagnosis.model_dump(mode="json") if item.diagnosis else None,
            }
            for item in current.cases
            if item.evaluation.passed is not True
        ]
        report = RegressionReport(
            baseline_id=base.set_id,
            candidate_id=current.set_id,
            gates=gates,
            new_failures=new_failures,
            score_drops=score_drops,
            token_delta={
                "baseline": base_tokens["mean"],
                "current": current_tokens["mean"],
                "ratio_increase": _ratio(current_tokens["mean"], base_tokens["mean"]),
            },
            latency_delta={
                "baseline": base_latency["mean_ms"],
                "current": current_latency["mean_ms"],
                "ratio_increase": _ratio(current_latency["mean_ms"], base_latency["mean_ms"]),
            },
            summary={
                "baseline_pass_rate": base_metrics["pass_rate"],
                "current_pass_rate": current_metrics["pass_rate"],
                "new_case_count": sum(case_id not in base_index for case_id in current_index),
            },
            case_metrics={"baseline": base_metrics, "candidate": current_metrics},
            tag_metrics=tag_metrics,
            provider_model_metrics=provider_model_metrics,
            latency={"baseline": base_latency, "candidate": current_latency},
            tokens={"baseline": base_tokens, "candidate": current_tokens},
            costs={"baseline": base_costs, "candidate": current_costs},
            first_divergence_distribution=_distribution(current.cases),
            findings=findings,
            failed_cases=failed_cases,
        )
        failed_ids = [
            item.case_id for item in current.cases if item.evaluation.passed is not True
        ]
        passed = not any(item.status in {"failed", "error"} for item in findings)
        return GateDecision(
            passed=passed,
            reason="all regression gates passed" if passed else "regression gate failed",
            regression=report,
            failed_case_ids=failed_ids,
            exit_code=0 if passed else 1,
        )


def summarize_reruns(cases: Iterable[RegressionCase]) -> RerunStatistics:
    rows = list(cases)
    count = len(rows)
    if not rows:
        return RerunStatistics(sample_count=0)
    successes = sum(item.evaluation.passed is True for item in rows)
    rate = successes / count
    z = 1.96
    denominator = 1.0 + z**2 / count
    center = (rate + z**2 / (2 * count)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / count + z**2 / (4 * count**2))
        / denominator
    )
    scores = [item.evaluation.score for item in rows if item.evaluation.score is not None]
    latencies = [item.latency_ms for item in rows]
    return RerunStatistics(
        sample_count=count,
        success_rate=rate,
        wilson_low=max(0.0, center - margin),
        wilson_high=min(1.0, center + margin),
        mean_score=statistics.fmean(scores) if scores else None,
        score_variance=statistics.pvariance(scores) if len(scores) > 1 else 0.0 if scores else None,
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
    )
