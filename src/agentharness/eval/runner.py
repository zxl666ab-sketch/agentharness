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
from agentharness.eval.dataset import EvalCase, EvalSuite
from agentharness.eval.graders import (
    CompositeGrader,
    DeterministicGrader,
    GradeResult,
    JudgeAdapter,
    LLMJudgeGrader,
    Trajectory,
    TrajectoryGrader,
)
from agentharness.harness import Harness


@dataclass
class CaseResult:
    case_id: str
    provider: str
    model: str | None
    passed: bool
    status: str
    score: float
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
    def mean_score(self) -> float:
        return self.score_sum / self.total if self.total else 0.0


@dataclass
class SuiteReport:
    suite: str
    results: list[CaseResult]
    groups: list[GroupMetrics]
    data_dir: str | None = None

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
    def mean_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

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


def _build_grader(judge: JudgeAdapter | None) -> CompositeGrader:
    graders: list[Any] = [DeterministicGrader(), TrajectoryGrader()]
    if judge is not None:
        graders.append(LLMJudgeGrader(judge, redactor=None))
    return CompositeGrader(graders)


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


def _trajectory_from_result(
    harness: Harness,
    result: Any,
    *,
    latency_s: float,
) -> Trajectory:
    run_id = getattr(result, "run_id", "") or ""
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    usage = result.usage
    return Trajectory(
        status=status,
        output=result.output or "",
        total_tokens=int(usage.total_tokens) if usage else 0,
        steps=int(result.steps or 0),
        latency_s=latency_s,
        tools_ordered=_tools_ordered(harness, run_id),
        messages=_messages_dicts(harness, run_id),
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

    grader = _build_grader(judge)
    # When a judge is present, wire harness redactor so secrets match process redactor.
    if judge is not None:
        # Rebuild with harness redactor for judge path.
        graders: list[Any] = [DeterministicGrader(), TrajectoryGrader()]
        graders.append(LLMJudgeGrader(judge, redactor=harness.redactor))
        grader = CompositeGrader(graders)

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

        traj = _trajectory_from_result(harness, result, latency_s=latency)
        grade: GradeResult = grader.grade(case, traj)
        u = result.usage
        return CaseResult(
            case_id=result_id,
            logical_case_id=logical_id,
            provider=prov,
            model=mdl,
            passed=grade.passed,
            status=result.status.value,
            score=float(grade.score),
            reasons=list(grade.reasons),
            latency_s=latency,
            input_tokens=int(u.input_tokens) if u else 0,
            output_tokens=int(u.output_tokens) if u else 0,
            total_tokens=int(u.total_tokens) if u else 0,
            steps=int(result.steps or 0),
            run_id=result.run_id,
            tags=list(case.tags),
            grader_evidence=dict(grade.evidence or {}),
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
        g.score_sum += r.score

    return SuiteReport(
        suite=suite.name,
        results=results,
        groups=list(grouped.values()),
        data_dir=str(resolved_data_dir) if resolved_data_dir is not None else None,
    )
