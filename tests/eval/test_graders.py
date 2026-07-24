"""Graders: deterministic, trajectory, composite, and fake LLM judge."""

from __future__ import annotations

from typing import Any

import pytest

from agentharness.eval.dataset import AssertionSpec, EvalCase
from agentharness.eval.graders import (
    CompositeGrader,
    DeterministicGrader,
    JudgeVerdict,
    LLMJudgeGrader,
    Trajectory,
    TrajectoryGrader,
)
from agentharness.security.redaction import Redactor


def _case(**assert_kwargs: Any) -> EvalCase:
    return EvalCase(id="c1", prompt="hi", assertions=AssertionSpec(**assert_kwargs))


def _traj(**kwargs: Any) -> Trajectory:
    base = dict(
        status="completed",
        output="hello world",
        total_tokens=10,
        steps=1,
        latency_s=0.1,
        tools_ordered=[],
        messages=[],
    )
    base.update(kwargs)
    return Trajectory(**base)


# --- DeterministicGrader ---


def test_deterministic_status_pass_and_fail() -> None:
    g = DeterministicGrader()
    case = _case(status="completed")
    assert g.grade(case, _traj(status="completed")).passed
    fail = g.grade(case, _traj(status="failed"))
    assert not fail.passed
    assert any("status=" in r for r in fail.reasons)


def test_deterministic_contains_pass_and_fail() -> None:
    g = DeterministicGrader()
    case = _case(contains=["hello", "world"])
    assert g.grade(case, _traj(output="hello world")).passed
    fail = g.grade(case, _traj(output="hello"))
    assert not fail.passed
    assert any("missing substring" in r for r in fail.reasons)


def test_deterministic_contains_any_pass_and_fail() -> None:
    g = DeterministicGrader()
    case = _case(contains_any=["alpha", "beta"])
    assert g.grade(case, _traj(output="xx beta yy")).passed
    fail = g.grade(case, _traj(output="gamma"))
    assert not fail.passed
    assert any("contains_any" in r for r in fail.reasons)


def test_deterministic_regex_pass_and_fail() -> None:
    g = DeterministicGrader()
    case = _case(regex=r"he..o")
    assert g.grade(case, _traj(output="hello")).passed
    fail = g.grade(case, _traj(output="hi"))
    assert not fail.passed
    assert any("regex" in r for r in fail.reasons)


def test_deterministic_tools_used_pass_and_fail() -> None:
    g = DeterministicGrader()
    case = _case(tools_used=["read_file", "shell"])
    ok = g.grade(case, _traj(tools_ordered=["shell", "read_file"]))
    assert ok.passed
    fail = g.grade(case, _traj(tools_ordered=["shell"]))
    assert not fail.passed
    assert any("tool not used: read_file" in r for r in fail.reasons)


def test_deterministic_max_tokens_steps_latency() -> None:
    g = DeterministicGrader()
    case = _case(max_tokens=5, max_steps=2, max_latency_s=1.0)
    assert g.grade(
        case, _traj(total_tokens=5, steps=2, latency_s=0.5)
    ).passed
    fail = g.grade(case, _traj(total_tokens=6, steps=3, latency_s=2.0))
    assert not fail.passed
    joined = " ".join(fail.reasons)
    assert "tokens" in joined and "steps" in joined and "latency" in joined


# --- TrajectoryGrader ---


def test_tools_order_subsequence_pass() -> None:
    g = TrajectoryGrader()
    case = _case(tools_order=["a", "c"])
    ok = g.grade(case, _traj(tools_ordered=["a", "b", "c", "d"]))
    assert ok.passed
    assert ok.score == 1.0


def test_tools_order_subsequence_fail() -> None:
    g = TrajectoryGrader()
    case = _case(tools_order=["a", "c"])
    fail = g.grade(case, _traj(tools_ordered=["c", "a"]))
    assert not fail.passed
    assert any("subsequence" in r for r in fail.reasons)


def test_tools_order_empty_is_pass() -> None:
    g = TrajectoryGrader()
    case = _case()
    assert g.grade(case, _traj(tools_ordered=["x"])).passed


# --- CompositeGrader ---


def test_composite_determinism_beats_judge() -> None:
    class AlwaysPassJudge:
        provider = "fake-judge"
        model = "m"

        def judge(self, rubric: str, trajectory: dict[str, Any]) -> JudgeVerdict:
            return JudgeVerdict(score=1.0, passed=True, rationale="looks good")

    composite = CompositeGrader(
        [
            DeterministicGrader(),
            TrajectoryGrader(),
            LLMJudgeGrader(AlwaysPassJudge()),
        ]
    )
    case = _case(status="completed", contains=["need-this"], rubric="be nice")
    traj = _traj(status="completed", output="nope")
    result = composite.grade(case, traj)
    assert not result.passed
    # Determinism pins score to the deterministic mean (det=0, traj=1 -> 0.5),
    # not the overall mean that would include the passing judge (would be ~0.67).
    assert result.score == 0.5
    assert result.score < 1.0
    assert any("missing substring" in r for r in result.reasons)


def test_composite_all_pass() -> None:
    composite = CompositeGrader([DeterministicGrader(), TrajectoryGrader()])
    case = _case(status="completed", contains=["hello"])
    result = composite.grade(case, _traj())
    assert result.passed
    assert result.score == 1.0


# --- LLMJudgeGrader ---


class RecordingJudge:
    provider = "mem"
    model = "judge-1"

    def __init__(self) -> None:
        self.last_traj: dict[str, Any] | None = None
        self.last_rubric: str | None = None

    def judge(self, rubric: str, trajectory: dict[str, Any]) -> JudgeVerdict:
        self.last_rubric = rubric
        self.last_traj = trajectory
        return JudgeVerdict(score=0.9, passed=True, rationale="ok", evidence=["e1"])


def test_llm_judge_records_metadata_and_no_network() -> None:
    adapter = RecordingJudge()
    grader = LLMJudgeGrader(adapter)
    case = _case(rubric="quality above 0.5")
    result = grader.grade(case, _traj())
    assert result.passed
    assert result.score == pytest.approx(0.9)
    assert result.evidence["judge_provider"] == "mem"
    assert result.evidence["judge_model"] == "judge-1"
    assert result.evidence["rubric_hash"]
    assert "duration_s" in result.evidence
    assert adapter.last_rubric == "quality above 0.5"


def test_llm_judge_redacts_sentinel_secrets() -> None:
    secret = "sk-" + ("A" * 24)
    adapter = RecordingJudge()
    redactor = Redactor()
    grader = LLMJudgeGrader(adapter, redactor=redactor)
    case = _case(rubric="check")
    traj = _traj(
        output=f"token is {secret}",
        messages=[{"role": "assistant", "content": f"use {secret} please"}],
    )
    grader.grade(case, traj)
    assert adapter.last_traj is not None
    blob = str(adapter.last_traj)
    assert secret not in blob
    assert "REDACTED" in blob
