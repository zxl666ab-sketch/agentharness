"""Run an eval suite through the Harness and aggregate metrics.

Concurrency-bounded, provider-agnostic. Each case runs in a fresh session so
runs stay independent. Success is scored from RunResult plus tool-order messages
via CompositeGrader. Metrics are grouped by (provider, model).
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentharness.contracts import ApprovalMode, BudgetConfig, RunRequest, new_id
from agentharness.eval.contracts import (
    AgentTrace,
    DiagnosisReport,
    EvaluationReport,
    RegressionCase,
    RerunStatistics,
)
from agentharness.eval.dataset import EvalCase, EvalSuite
from agentharness.eval.diagnosis import DiagnosisEngine
from agentharness.eval.graders import (
    JudgeAdapter,
    LLMJudgeGrader,
    Trajectory,
)
from agentharness.eval.regression import summarize_reruns
from agentharness.eval.replay import SnapshotStore
from agentharness.eval.trajectory import TrajectoryEvaluator, policy_from_assertions
from agentharness.harness import Harness


@dataclass
class CaseResult:
    case_id: str
    provider: str
    model: str | None
    passed: bool
    status: str
    score: float | None
    reasons: list[str]
    latency_s: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    steps: int
    run_id: str
    tags: list[str] = field(default_factory=list)
    logical_case_id: str = ""
    grader_evidence: dict[str, Any] = field(default_factory=dict)
    evaluation_report: EvaluationReport | None = None
    diagnosis: DiagnosisReport | None = None
    snapshot_id: str | None = None
    web_report_id: str | None = None
    baseline_diff: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupMetrics:
    provider: str
    model: str | None
    total: int = 0
    passed: int = 0
    latency_s: float = 0.0
    total_tokens: int = 0
    steps: int = 0
    score_sum: float = 0.0
    scored: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_latency_s(self) -> float:
        return self.latency_s / self.total if self.total else 0.0

    @property
    def avg_tokens(self) -> float:
        return self.total_tokens / self.total if self.total else 0.0

    @property
    def avg_steps(self) -> float:
        return self.steps / self.total if self.total else 0.0

    @property
    def mean_score(self) -> float | None:
        return self.score_sum / self.scored if self.scored else None


@dataclass
class SuiteReport:
    suite: str
    results: list[CaseResult]
    groups: list[GroupMetrics]
    data_dir: str | None = None
    gate_decision: Any | None = None
    rerun_statistics: RerunStatistics | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def mean_score(self) -> float | None:
        scores = [r.score for r in self.results if r.score is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.results)

    @property
    def mean_latency_s(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.latency_s for r in self.results) / len(self.results)

    def logical_case_passed(self, logical_case_id: str) -> bool:
        rows = [r for r in self.results if r.logical_case_id == logical_case_id]
        return bool(rows) and all(r.passed for r in rows)


def _budget_from_case(case: EvalCase) -> BudgetConfig:
    if not case.budget:
        return BudgetConfig()
    allowed = set(BudgetConfig.model_fields)
    cleaned = {k: v for k, v in case.budget.items() if k in allowed}
    return BudgetConfig(**cleaned)


def _tools_ordered(harness: Harness, run_id: str) -> list[str]:
    ordered: list[str] = []
    if not run_id:
        return ordered
    for msg in harness.get_run_messages(run_id):
        for tc in msg.tool_calls or []:
            if tc.name:
                ordered.append(tc.name)
    return ordered


def _messages_dicts(harness: Harness, run_id: str) -> list[dict[str, Any]]:
    if not run_id:
        return []
    out: list[dict[str, Any]] = []
    for msg in harness.get_run_messages(run_id):
        out.append(
            {
                "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                "content": msg.content,
                "name": msg.name,
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in (msg.tool_calls or [])
                ],
            }
        )
    return out


def _expand_cases(suite: EvalSuite) -> list[tuple[EvalCase, str, str]]:
    """Return (case, result_case_id, logical_case_id) for each expanded repeat."""
    expanded: list[tuple[EvalCase, str, str]] = []
    for case in suite.cases:
        logical = case.id
        n = max(1, case.repeat)
        for i in range(1, n + 1):
            rid = f"{case.id}#{i}" if n > 1 else case.id
            expanded.append((case, rid, logical))
    return expanded


def build_trajectory(
    harness: Harness,
    result: Any,
    *,
    latency_s: float,
) -> Trajectory:
    """Build a Trajectory from a finished RunResult (shared by suite + run-end)."""
    trace = build_agent_trace(harness, result)
    return Trajectory(
        status=trace.status,
        output=trace.final_output,
        total_tokens=trace.usage.total_tokens,
        steps=trace.steps,
        latency_s=latency_s,
        tools_ordered=[
            span.tool_name or span.name
            for span in trace.spans
            if span.kind == "tool"
        ],
        messages=[message.model_dump(mode="json") for message in trace.messages],
    )


def build_agent_trace(harness: Harness, result: Any) -> AgentTrace:
    """Project the persisted facts for a finished run into the canonical contract."""
    trace = harness.get_agent_trace(getattr(result, "run_id", "") or "")
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    return trace.model_copy(
        update={
            "status": status,
            "final_output": result.output or trace.final_output,
            "usage": result.usage or trace.usage,
            "steps": int(result.steps or trace.steps),
        }
    )


async def run_suite(
    suite: EvalSuite,
    *,
    provider: str | None = None,
    model: str | None = None,
    concurrency: int = 3,
    data_dir: str | Path | None = None,
    judge: JudgeAdapter | None = None,
    harness: Harness | None = None,
) -> SuiteReport:
    """Run every (expanded) case through harness.run and aggregate metrics.

    When ``data_dir`` is None and no harness is injected, a temporary directory
    is used so eval never writes to ``~/.agentharness``.
    """
    own_harness = harness is None
    tmp_holder: tempfile.TemporaryDirectory[str] | None = None
    resolved_data_dir: Path | None = None

    if harness is None:
        if data_dir is None:
            tmp_holder = tempfile.TemporaryDirectory(prefix="agentharness-eval-")
            resolved_data_dir = Path(tmp_holder.name)
        else:
            resolved_data_dir = Path(data_dir).expanduser()
            resolved_data_dir.mkdir(parents=True, exist_ok=True)
        harness = Harness(data_dir=resolved_data_dir)
    else:
        resolved_data_dir = Path(harness.data_dir)

    evaluator = TrajectoryEvaluator(storage=harness.storage)

    sem = asyncio.Semaphore(max(1, concurrency))
    expanded = _expand_cases(suite)

    async def _run_one(case: EvalCase, result_id: str, logical_id: str) -> CaseResult:
        # Resolution: case > CLI > suite defaults
        prov = case.provider or provider or suite.defaults.provider or "fake"
        mdl = case.model or model or suite.defaults.model
        req = RunRequest(
            message=case.prompt or "",
            provider=prov,
            model=mdl,
            system=case.system or suite.defaults.system,
            cwd=case.cwd,
            session_id=new_id(),
            approval=ApprovalMode.never,
            budget=_budget_from_case(case),
        )
        async with sem:
            t0 = time.monotonic()
            try:
                result = await harness.run(req)
            except Exception as exc:  # noqa: BLE001 — crash fails the case, not the suite
                latency = time.monotonic() - t0
                return CaseResult(
                    case_id=result_id,
                    logical_case_id=logical_id,
                    provider=prov,
                    model=mdl,
                    passed=False,
                    status="error",
                    score=0.0,
                    reasons=[f"exception: {exc}"],
                    latency_s=latency,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    steps=0,
                    run_id="",
                    tags=list(case.tags),
                )
            latency = time.monotonic() - t0

        trace = build_agent_trace(harness, result).model_copy(
            update={"duration_ms": latency * 1000.0}
        )
        policy_v2 = policy_from_assertions(
            case.assertions, policy_id=f"suite:{suite.name}:{logical_id}"
        )
        evaluation = evaluator.evaluate(trace, policy_v2)
        diagnosis = (
            DiagnosisEngine(storage=harness.storage).diagnose(trace, evaluation)
            if evaluation.passed is False
            else None
        )
        snapshot, snapshot_artifact = SnapshotStore(
            harness.storage, redactor=harness.redactor
        ).capture(
            result.run_id,
            evaluation_policy_version=policy_v2.version,
        )
        reasons = [
            check.message
            or f"{check.id}: expected {check.expected!r}, actual {check.actual!r}"
            for check in evaluation.checks
            if check.status in {"failed", "error"}
        ]
        passed = evaluation.passed is True
        score = evaluation.score
        evidence: dict[str, Any] = {"evaluation_report": evaluation.model_dump(mode="json")}
        if judge is not None:
            legacy_judge = LLMJudgeGrader(judge, redactor=harness.redactor).grade(
                case, build_trajectory(harness, result, latency_s=latency)
            )
            evidence["llm_judge"] = legacy_judge.evidence
            passed = passed and legacy_judge.passed
            if evaluation.hard_failures == 0 and score is not None:
                score = round((score + legacy_judge.score) / 2.0, 4)
            if not legacy_judge.passed:
                reasons.extend(legacy_judge.reasons)
        legacy_eval = {
            "schema_version": 1,
            "mode": "deterministic",
            "source": "suite",
            "passed": passed,
            "score": score,
            "reasons": reasons,
            "grader": "trajectory_evaluator_v2",
            "evaluation_mode": evaluation.mode,
            "failure_category": (
                "none"
                if evaluation.passed
                else next(
                    (
                        check.failure_category
                        for check in evaluation.checks
                        if check.status in {"failed", "error"}
                        and check.failure_category
                    ),
                    "execution_or_assertion",
                )
            ),
            "evaluation_report": evaluation.model_dump(mode="json"),
            "evaluation_report_id": evaluation.report_id,
            "diagnosis_id": diagnosis.diagnosis_id if diagnosis else None,
            "snapshot_id": snapshot.snapshot_id,
        }
        harness.retain_run_evaluation(
            result.run_id,
            report=evaluation,
            diagnosis=diagnosis,
            snapshot=snapshot,
            snapshot_artifact=snapshot_artifact,
            legacy_eval=legacy_eval,
            source="suite",
        )
        u = result.usage
        return CaseResult(
            case_id=result_id,
            logical_case_id=logical_id,
            provider=prov,
            model=mdl,
            passed=passed,
            status=result.status.value,
            score=score,
            reasons=reasons,
            latency_s=latency,
            input_tokens=int(u.input_tokens) if u else 0,
            output_tokens=int(u.output_tokens) if u else 0,
            total_tokens=int(u.total_tokens) if u else 0,
            steps=int(result.steps or 0),
            run_id=result.run_id,
            tags=list(case.tags),
            grader_evidence=evidence,
            evaluation_report=evaluation,
            diagnosis=diagnosis,
            snapshot_id=snapshot.snapshot_id,
            web_report_id=evaluation.report_id,
        )

    try:
        results = list(
            await asyncio.gather(
                *(_run_one(c, rid, lid) for c, rid, lid in expanded)
            )
        )
    finally:
        if own_harness and harness is not None:
            try:
                await harness.aclose()
            except Exception:  # noqa: BLE001
                pass
        if tmp_holder is not None:
            # Keep tmp only while harness is open; after aclose data is gone by design.
            tmp_holder.cleanup()

    grouped: dict[tuple[str, str | None], GroupMetrics] = {}
    for r in results:
        key = (r.provider, r.model)
        g = grouped.get(key)
        if g is None:
            g = GroupMetrics(provider=r.provider, model=r.model)
            grouped[key] = g
        g.total += 1
        g.passed += int(r.passed)
        g.latency_s += r.latency_s
        g.total_tokens += r.total_tokens
        g.steps += r.steps
        if r.score is not None:
            g.score_sum += r.score
            g.scored += 1

    logical_counts = {
        logical_id: sum(item.logical_case_id == logical_id for item in results)
        for logical_id in {item.logical_case_id for item in results}
    }
    rerun_rows = [
        RegressionCase(
            case_id=item.case_id,
            tags=item.tags,
            provider=item.provider,
            model=item.model,
            evaluation=item.evaluation_report,
            diagnosis=item.diagnosis,
            snapshot_id=item.snapshot_id,
            web_report_id=item.web_report_id,
            latency_ms=item.latency_s * 1000.0,
            total_tokens=item.total_tokens,
        )
        for item in results
        if logical_counts.get(item.logical_case_id, 0) > 1
        and item.evaluation_report is not None
    ]
    return SuiteReport(
        suite=suite.name,
        results=results,
        groups=list(grouped.values()),
        data_dir=str(resolved_data_dir) if resolved_data_dir is not None else None,
        rerun_statistics=summarize_reruns(rerun_rows) if rerun_rows else None,
    )
