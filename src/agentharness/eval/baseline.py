"""Baseline comparison and regression gates for eval reports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentharness.eval.dataset import EvalConfigError

SUPPORTED_SCHEMA_VERSIONS = frozenset({1, "1"})


@dataclass
class BaselineGates:
    """Thresholds applied when comparing current results to a baseline report."""

    min_pass_rate: float | None = None
    min_mean_score: float | None = None
    max_score_regression: float | None = None  # absolute drop per case_id
    max_token_regression: float | None = None  # ratio: current/baseline total tokens - 1
    max_latency_regression: float | None = None  # ratio: current/baseline mean latency - 1


@dataclass
class GateFinding:
    gate: str
    triggered: bool
    message: str
    baseline: Any = None
    current: Any = None
    delta: Any = None


@dataclass
class RegressionReport:
    baseline_path: str
    gates: list[GateFinding] = field(default_factory=list)
    new_failures: list[str] = field(default_factory=list)
    score_drops: list[dict[str, Any]] = field(default_factory=list)
    token_delta: dict[str, Any] = field(default_factory=dict)
    latency_delta: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        if self.new_failures:
            return True
        return any(g.triggered for g in self.gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_path": self.baseline_path,
            "failed": self.failed,
            "new_failures": list(self.new_failures),
            "score_drops": list(self.score_drops),
            "token_delta": dict(self.token_delta),
            "latency_delta": dict(self.latency_delta),
            "summary": dict(self.summary),
            "gates": [
                {
                    "gate": g.gate,
                    "triggered": g.triggered,
                    "message": g.message,
                    "baseline": g.baseline,
                    "current": g.current,
                    "delta": g.delta,
                }
                for g in self.gates
            ],
        }


def _load_report(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EvalConfigError(f"baseline report not found: {p}") from exc
    except OSError as exc:
        raise EvalConfigError(f"cannot read baseline report {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvalConfigError(f"baseline is not valid JSON: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalConfigError(f"baseline schema not recognized: expected object in {p}")
    schema = data.get("schema_version")
    if schema is None:
        raise EvalConfigError(
            f"baseline schema not recognized: missing schema_version in {p}"
        )
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise EvalConfigError(
            f"baseline schema not recognized: schema_version={schema!r} in {p}"
        )
    if "results" not in data or not isinstance(data["results"], list):
        raise EvalConfigError(
            f"baseline schema not recognized: missing results[] in {p}"
        )
    return data


def _index_results(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in report.get("results") or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("case_id")
        if isinstance(cid, str) and cid:
            indexed[cid] = row
    return indexed


def _mean_latency(report: dict[str, Any]) -> float:
    if "mean_latency_s" in report and report["mean_latency_s"] is not None:
        return float(report["mean_latency_s"])
    results = report.get("results") or []
    if not results:
        return 0.0
    return sum(float(r.get("latency_s") or 0.0) for r in results) / len(results)


def _total_tokens(report: dict[str, Any]) -> int:
    if "total_tokens" in report and report["total_tokens"] is not None:
        return int(report["total_tokens"])
    return sum(int(r.get("total_tokens") or 0) for r in (report.get("results") or []))


def _pass_rate(report: dict[str, Any]) -> float:
    if "pass_rate" in report and report["pass_rate"] is not None:
        return float(report["pass_rate"])
    results = report.get("results") or []
    if not results:
        return 0.0
    passed = sum(1 for r in results if r.get("passed"))
    return passed / len(results)


def _mean_score(report: dict[str, Any]) -> float:
    if "mean_score" in report and report["mean_score"] is not None:
        return float(report["mean_score"])
    results = report.get("results") or []
    if not results:
        return 0.0
    return sum(float(r.get("score") or 0.0) for r in results) / len(results)


def compare_to_baseline(
    current: dict[str, Any],
    baseline_path: str | Path,
    gates: BaselineGates | None = None,
) -> RegressionReport:
    """Compare a current report dict to a baseline JSON report.

    Rules:
    - New failures (baseline passed, current failed) are unconditional regressions.
    - New cases (absent from baseline) only affect aggregate metrics; no score-drop gate.
    - Missing/unrecognized baseline schema raises EvalConfigError.
    """
    gates = gates or BaselineGates()
    baseline = _load_report(baseline_path)
    report = RegressionReport(baseline_path=str(Path(baseline_path)))

    base_idx = _index_results(baseline)
    cur_idx = _index_results(current)

    # Highest priority: new failures.
    for cid, cur in cur_idx.items():
        base = base_idx.get(cid)
        if base is None:
            continue
        if base.get("passed") and not cur.get("passed"):
            report.new_failures.append(cid)

    if report.new_failures:
        report.gates.append(
            GateFinding(
                gate="new_failures",
                triggered=True,
                message=(
                    f"{len(report.new_failures)} case(s) newly failed: "
                    + ", ".join(report.new_failures[:10])
                ),
                baseline="passed",
                current="failed",
                delta=list(report.new_failures),
            )
        )
    else:
        report.gates.append(
            GateFinding(
                gate="new_failures",
                triggered=False,
                message="no new failures",
            )
        )

    # Per-case score regression (only for cases present in baseline).
    max_drop = gates.max_score_regression
    for cid, cur in cur_idx.items():
        base = base_idx.get(cid)
        if base is None:
            continue
        b_score = float(base.get("score") or 0.0)
        c_score = float(cur.get("score") or 0.0)
        drop = b_score - c_score
        if drop > 0:
            report.score_drops.append(
                {
                    "case_id": cid,
                    "baseline": b_score,
                    "current": c_score,
                    "delta": round(-drop, 4),
                }
            )
        if max_drop is not None and drop > max_drop:
            report.gates.append(
                GateFinding(
                    gate="max_score_regression",
                    triggered=True,
                    message=(
                        f"case {cid}: score drop {drop:.4f} > max {max_drop}"
                    ),
                    baseline=b_score,
                    current=c_score,
                    delta=-drop,
                )
            )

    if max_drop is not None and not any(
        g.gate == "max_score_regression" and g.triggered for g in report.gates
    ):
        report.gates.append(
            GateFinding(
                gate="max_score_regression",
                triggered=False,
                message=f"no case score drop exceeded {max_drop}",
                baseline=max_drop,
            )
        )

    cur_pr = _pass_rate(current)
    base_pr = _pass_rate(baseline)
    if gates.min_pass_rate is not None:
        triggered = cur_pr < gates.min_pass_rate
        report.gates.append(
            GateFinding(
                gate="min_pass_rate",
                triggered=triggered,
                message=(
                    f"pass_rate {cur_pr:.4f} < min {gates.min_pass_rate}"
                    if triggered
                    else f"pass_rate {cur_pr:.4f} >= min {gates.min_pass_rate}"
                ),
                baseline=base_pr,
                current=cur_pr,
                delta=round(cur_pr - base_pr, 4),
            )
        )

    cur_ms = _mean_score(current)
    base_ms = _mean_score(baseline)
    if gates.min_mean_score is not None:
        triggered = cur_ms < gates.min_mean_score
        report.gates.append(
            GateFinding(
                gate="min_mean_score",
                triggered=triggered,
                message=(
                    f"mean_score {cur_ms:.4f} < min {gates.min_mean_score}"
                    if triggered
                    else f"mean_score {cur_ms:.4f} >= min {gates.min_mean_score}"
                ),
                baseline=base_ms,
                current=cur_ms,
                delta=round(cur_ms - base_ms, 4),
            )
        )

    base_tok = _total_tokens(baseline)
    cur_tok = _total_tokens(current)
    tok_ratio = (cur_tok / base_tok - 1.0) if base_tok > 0 else 0.0
    report.token_delta = {
        "baseline": base_tok,
        "current": cur_tok,
        "ratio_increase": round(tok_ratio, 4),
    }
    if gates.max_token_regression is not None:
        triggered = base_tok > 0 and tok_ratio > gates.max_token_regression
        report.gates.append(
            GateFinding(
                gate="max_token_regression",
                triggered=triggered,
                message=(
                    f"token increase {tok_ratio:.4f} > max {gates.max_token_regression}"
                    if triggered
                    else (
                        f"token increase {tok_ratio:.4f} "
                        f"<= max {gates.max_token_regression}"
                    )
                ),
                baseline=base_tok,
                current=cur_tok,
                delta=round(tok_ratio, 4),
            )
        )

    base_lat = _mean_latency(baseline)
    cur_lat = _mean_latency(current)
    lat_ratio = (cur_lat / base_lat - 1.0) if base_lat > 0 else 0.0
    report.latency_delta = {
        "baseline": round(base_lat, 4),
        "current": round(cur_lat, 4),
        "ratio_increase": round(lat_ratio, 4),
    }
    if gates.max_latency_regression is not None:
        triggered = base_lat > 0 and lat_ratio > gates.max_latency_regression
        report.gates.append(
            GateFinding(
                gate="max_latency_regression",
                triggered=triggered,
                message=(
                    f"latency increase {lat_ratio:.4f} "
                    f"> max {gates.max_latency_regression}"
                    if triggered
                    else (
                        f"latency increase {lat_ratio:.4f} "
                        f"<= max {gates.max_latency_regression}"
                    )
                ),
                baseline=base_lat,
                current=cur_lat,
                delta=round(lat_ratio, 4),
            )
        )

    report.summary = {
        "baseline_pass_rate": round(base_pr, 4),
        "current_pass_rate": round(cur_pr, 4),
        "baseline_mean_score": round(base_ms, 4),
        "current_mean_score": round(cur_ms, 4),
        "baseline_total_tokens": base_tok,
        "current_total_tokens": cur_tok,
        "baseline_mean_latency_s": round(base_lat, 4),
        "current_mean_latency_s": round(cur_lat, 4),
        "new_case_count": sum(1 for c in cur_idx if c not in base_idx),
    }
    return report


def load_baseline(path: str | Path) -> dict[str, Any]:
    """Public helper: load and validate a baseline report."""
    return _load_report(path)
