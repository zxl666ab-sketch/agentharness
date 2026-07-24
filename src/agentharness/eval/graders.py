"""Graders: score a case's trajectory against its assertions.

A :class:`Grader` maps an :class:`~agentharness.eval.dataset.EvalCase` plus a
:class:`Trajectory` to a :class:`GradeResult`. Determinism first: the
:class:`CompositeGrader` fails the case if any deterministic grader fails,
regardless of what an optional LLM judge says. The judge is default-off, is
injected as an adapter, receives only a redacted trajectory, and has no access
to tools, the filesystem, or the network.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentharness.eval.dataset import AssertionSpec, EvalCase
from agentharness.security.redaction import Redactor, default_redactor


@dataclass
class Trajectory:
    """Normalized, provider-agnostic view of one run for grading."""

    status: str
    output: str
    total_tokens: int
    steps: int
    latency_s: float
    tools_ordered: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tools_used(self) -> set[str]:
        return set(self.tools_ordered)


@dataclass
class GradeResult:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    grader: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Grader(Protocol):
    name: str

    def grade(self, case: EvalCase, traj: Trajectory) -> GradeResult: ...


class DeterministicGrader:
    """Output/status/budget assertions — exact, offline, no model calls."""

    name = "deterministic"

    def grade(self, case: EvalCase, traj: Trajectory) -> GradeResult:
        a: AssertionSpec = case.assertions
        reasons: list[str] = []

        if a.status is not None and traj.status != a.status:
            reasons.append(f"status={traj.status}, want {a.status}")

        out = traj.output or ""
        for needle in a.contains:
            if needle not in out:
                reasons.append(f"missing substring: {needle!r}")
        if a.contains_any and not any(n in out for n in a.contains_any):
            reasons.append(f"none of contains_any present: {a.contains_any}")
        if a.regex is not None and re.search(a.regex, out) is None:
            reasons.append(f"regex not matched: {a.regex!r}")

        for tool in a.tools_used:
            if tool not in traj.tools_used:
                reasons.append(f"tool not used: {tool}")

        if a.max_tokens is not None and traj.total_tokens > a.max_tokens:
            reasons.append(f"tokens {traj.total_tokens} > max {a.max_tokens}")
        if a.max_steps is not None and traj.steps > a.max_steps:
            reasons.append(f"steps {traj.steps} > max {a.max_steps}")
        if a.max_latency_s is not None and traj.latency_s > a.max_latency_s:
            reasons.append(f"latency {traj.latency_s:.3f}s > max {a.max_latency_s}s")

        passed = not reasons
        return GradeResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            reasons=reasons,
            grader=self.name,
        )


class TrajectoryGrader:
    """Tool-call ordering: assertion order must appear as a subsequence."""

    name = "trajectory"

    def grade(self, case: EvalCase, traj: Trajectory) -> GradeResult:
        want = case.assertions.tools_order
        if not want:
            return GradeResult(passed=True, score=1.0, grader=self.name)
        reasons: list[str] = []
        it = iter(traj.tools_ordered)
        matched = all(any(t == w for t in it) for w in want)
        if not matched:
            reasons.append(
                f"tool order {want} not a subsequence of {traj.tools_ordered}"
            )
        passed = not reasons
        return GradeResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            reasons=reasons,
            grader=self.name,
            evidence={"actual_order": list(traj.tools_ordered)},
        )


@dataclass
class JudgeVerdict:
    """Fixed structure an LLM judge must return."""

    score: float
    passed: bool
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)


@runtime_checkable
class JudgeAdapter(Protocol):
    """Injected LLM judge. Receives only a redacted trajectory dict + rubric.

    Implementations must not touch the filesystem, network, or tools; they see
    exactly the dict passed to ``judge`` and nothing else.
    """

    provider: str
    model: str

    def judge(self, rubric: str, trajectory: dict[str, Any]) -> JudgeVerdict: ...


class LLMJudgeGrader:
    """Optional, default-off judge. Scores a redacted trajectory via an adapter.

    Records judge provider/model, a rubric hash, and wall time. Adds no
    filesystem/network capability: the adapter only ever sees the redacted dict.
    """

    name = "llm_judge"

    def __init__(
        self,
        adapter: JudgeAdapter,
        *,
        redactor: Redactor | None = None,
        pass_threshold: float = 0.5,
    ) -> None:
        self._adapter = adapter
        self._redactor = redactor or default_redactor
        self._pass_threshold = pass_threshold

    def _redacted_trajectory(self, traj: Trajectory) -> dict[str, Any]:
        raw = {
            "status": traj.status,
            "output": traj.output,
            "total_tokens": traj.total_tokens,
            "steps": traj.steps,
            "tools_ordered": list(traj.tools_ordered),
            "messages": traj.messages,
        }
        return self._redactor.redact_obj(raw)

    def grade(self, case: EvalCase, traj: Trajectory) -> GradeResult:
        rubric = case.assertions.rubric or ""
        rubric_hash = hashlib.sha256(rubric.encode("utf-8")).hexdigest()[:16]
        payload = self._redacted_trajectory(traj)
        t0 = time.monotonic()
        try:
            verdict = self._adapter.judge(rubric, payload)
        except Exception as exc:  # noqa: BLE001 — a judge failure fails the case, not the suite
            return GradeResult(
                passed=False,
                score=0.0,
                reasons=[f"judge error: {exc}"],
                grader=self.name,
                evidence={
                    "judge_provider": getattr(self._adapter, "provider", "?"),
                    "judge_model": getattr(self._adapter, "model", "?"),
                    "rubric_hash": rubric_hash,
                    "duration_s": round(time.monotonic() - t0, 4),
                },
            )
        duration = time.monotonic() - t0
        score = max(0.0, min(1.0, float(verdict.score)))
        reasons = [] if verdict.passed else [f"judge: {verdict.rationale or 'failed'}"]
        return GradeResult(
            passed=bool(verdict.passed),
            score=score,
            reasons=reasons,
            grader=self.name,
            evidence={
                "judge_provider": getattr(self._adapter, "provider", "?"),
                "judge_model": getattr(self._adapter, "model", "?"),
                "rubric_hash": rubric_hash,
                "duration_s": round(duration, 4),
                "rationale": verdict.rationale,
                "judge_evidence": list(verdict.evidence),
            },
        )


class CompositeGrader:
    """Run several graders; determinism-first.

    A case passes only if every grader passes. Because deterministic graders are
    included, any deterministic failure fails the case regardless of the judge.
    The composite score is the mean of component scores, but is forced to the
    deterministic score whenever a deterministic grader fails, so a passing
    judge can never mask a hard assertion failure.
    """

    name = "composite"

    # Graders whose failure is authoritative (score cannot be rescued).
    _DETERMINISTIC = frozenset({"deterministic", "trajectory"})

    def __init__(self, graders: list[Grader]) -> None:
        if not graders:
            raise ValueError("CompositeGrader requires at least one grader")
        self._graders = graders

    def grade(self, case: EvalCase, traj: Trajectory) -> GradeResult:
        parts: list[GradeResult] = [g.grade(case, traj) for g in self._graders]
        reasons: list[str] = []
        det_failed = False
        det_scores: list[float] = []
        for p in parts:
            if p.reasons:
                reasons.extend(f"[{p.grader}] {r}" for r in p.reasons)
            if p.grader in self._DETERMINISTIC:
                det_scores.append(p.score)
                if not p.passed:
                    det_failed = True

        passed = all(p.passed for p in parts)
        mean_score = sum(p.score for p in parts) / len(parts)
        # Determinism wins: a hard failure pins the score to the deterministic mean.
        score = (
            (sum(det_scores) / len(det_scores)) if det_failed and det_scores else mean_score
        )
        return GradeResult(
            passed=passed,
            score=round(score, 4),
            reasons=reasons,
            grader=self.name,
            evidence={p.grader: p.evidence for p in parts if p.evidence},
        )
