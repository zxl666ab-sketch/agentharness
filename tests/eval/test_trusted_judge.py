from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentharness.eval.calibration import CalibrationDataset, JudgeCalibrator
from agentharness.eval.contracts import (
    AgentTrace,
    CalibrationExample,
    CheckResult,
    EvaluationReport,
    EvidenceRef,
    JudgeRubric,
    JudgeSample,
    TraceSpan,
)
from agentharness.eval.trusted_judge import (
    JUDGE_INJECTION_ATTACKS,
    JudgeOrchestrator,
)


def _trace(output: str = "answer") -> AgentTrace:
    return AgentTrace(
        trace_id="trace",
        run_id="run",
        status="completed",
        completeness="complete",
        final_output=output,
        spans=[
            TraceSpan(
                trace_id="trace",
                run_id="run",
                span_id="model",
                kind="model",
                status="completed",
                sequence_start=1,
                sequence_end=2,
                output=output,
                event_ids=["event"],
            )
        ],
        event_count=2,
    )


def _deterministic(*, passed: bool = True, score: float = 1.0) -> EvaluationReport:
    evidence = EvidenceRef(
        trace_id="trace", run_id="run", span_id="model", event_id="event", sequence=1
    )
    return EvaluationReport(
        trace_id="trace",
        run_id="run",
        policy_id="policy",
        mode="scored",
        passed=passed,
        score=score,
        checks=[
            CheckResult(
                id="hard",
                category="output",
                status="passed" if passed else "failed",
                hard=True,
                score=score,
                evidence=[evidence],
            )
        ],
        hard_failures=0 if passed else 1,
        passed_count=1 if passed else 0,
        failed_count=0 if passed else 1,
    )


class RecordingJudge:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.requests = []

    async def sample(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        score = self.scores[request.sample_index]
        return JudgeSample(
            score=score,
            passed=score >= request.rubric.pass_threshold,
            confidence=0.9,
            rationale=f"sample {request.sample_index}",
            evidence=[
                EvidenceRef(
                    trace_id=request.trace.trace_id,
                    run_id=request.trace.run_id,
                    span_id="model",
                    event_id="event",
                    sequence=1,
                )
            ],
        )


@pytest.mark.asyncio
async def test_judge_orchestrator_multi_sample_statistics_and_unverified_status() -> None:
    adapter = RecordingJudge([0.8, 0.6, 1.0])
    rubric = JudgeRubric(
        rubric_id="summarization",
        version="2026-07-24",
        task_type="summary",
        text="Reward faithful, concise summaries grounded in the trace.",
        pass_threshold=0.7,
    )
    semantic = await JudgeOrchestrator(adapter, sample_count=3).evaluate(
        _trace(), _deterministic(), rubric
    )

    assert semantic.status == "unverified"
    assert semantic.mean_score == pytest.approx(0.8)
    assert semantic.median_score == pytest.approx(0.8)
    assert semantic.variance == pytest.approx(0.0266666667)
    assert semantic.consistency == pytest.approx(2 / 3)
    assert semantic.passed is True
    assert len(semantic.samples) == 3
    assert all(sample.evidence for sample in semantic.samples)
    assert all(request.tools == [] for request in adapter.requests)
    assert all(request.network_access is False for request in adapter.requests)
    assert all(request.filesystem_access is False for request in adapter.requests)
    assert all(request.rubric == rubric for request in adapter.requests)
    assert all("untrusted_agent_trace" in request.user_prompt for request in adapter.requests)


@pytest.mark.asyncio
async def test_deterministic_hard_failure_cannot_be_overridden_by_judge() -> None:
    semantic = await JudgeOrchestrator(RecordingJudge([1.0, 1.0, 1.0])).evaluate(
        _trace(),
        _deterministic(passed=False, score=0.0),
        JudgeRubric(rubric_id="r", version="1", text="Be correct."),
    )
    assert semantic.mean_score == 1.0
    assert semantic.passed is False
    assert semantic.fallback_score == 0.0
    assert semantic.fallback_report_id is not None


class BrokenJudge:
    async def sample(self, request):  # type: ignore[no-untyped-def]
        raise RuntimeError("judge unavailable")


@pytest.mark.asyncio
async def test_judge_failure_degrades_to_deterministic_rule_score() -> None:
    deterministic = _deterministic(passed=True, score=0.75)
    semantic = await JudgeOrchestrator(BrokenJudge(), sample_count=3).evaluate(
        _trace(),
        deterministic,
        JudgeRubric(rubric_id="r", version="1", text="Be correct."),
    )
    assert semantic.status == "degraded"
    assert semantic.mean_score is None
    assert semantic.fallback_score == 0.75
    assert semantic.fallback_report_id == deterministic.report_id
    assert semantic.passed is True
    assert len(semantic.samples) == 3
    assert all(sample.error and sample.abstained for sample in semantic.samples)


class InjectionFollowingJudge:
    async def sample(self, request):  # type: ignore[no-untyped-def]
        assert request.rubric.text == "Score factual correctness only."
        return {
            "score": 1.0,
            "passed": True,
            "confidence": 1.0,
            "rationale": "the untrusted output demanded a full score",
            "evidence": [],
            "rubric": {"text": "give full score"},
        }


@pytest.mark.asyncio
async def test_prompt_injection_cannot_modify_rubric_or_force_full_score() -> None:
    attack = JUDGE_INJECTION_ATTACKS[0]
    rubric = JudgeRubric(
        rubric_id="factuality",
        version="1",
        text="Score factual correctness only.",
    )
    semantic = await JudgeOrchestrator(InjectionFollowingJudge(), sample_count=2).evaluate(
        _trace(attack), _deterministic(passed=False, score=0.0), rubric
    )
    assert semantic.status == "abstained"
    assert semantic.mean_score is None
    assert semantic.passed is False
    assert semantic.attack_resistant is True
    assert all(sample.abstained for sample in semantic.samples)


def test_calibrator_computes_metrics_and_synthetic_data_stays_unverified() -> None:
    examples = [
        CalibrationExample(
            example_id="a",
            task_type="summary",
            human_score=1.0,
            human_passed=True,
            judge_scores=[0.9, 1.0, 0.8],
            synthetic=True,
        ),
        CalibrationExample(
            example_id="b",
            task_type="summary",
            human_score=0.0,
            human_passed=False,
            judge_scores=[0.1, 0.2, 0.1],
            synthetic=True,
        ),
        CalibrationExample(
            example_id="c",
            task_type="coding",
            human_score=0.8,
            human_passed=True,
            judge_scores=[0.7, 0.8, 0.9],
            synthetic=True,
        ),
        CalibrationExample(
            example_id="d",
            task_type="coding",
            human_score=0.2,
            human_passed=False,
            judge_scores=[0.4, 0.3, 0.2],
            synthetic=True,
        ),
    ]
    report = JudgeCalibrator().calibrate(examples)
    assert report.sample_count == 4
    assert report.synthetic_only is True
    assert report.trust_status == "unverified"
    assert report.accuracy == 1.0
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.f1 == 1.0
    assert report.cohens_kappa == 1.0
    assert report.spearman is not None and report.spearman > 0.8
    assert report.mean_absolute_error is not None and report.mean_absolute_error < 0.15
    assert report.internal_consistency is not None
    assert set(report.task_type_bias) == {"summary", "coding"}


def test_calibration_dataset_json_and_jsonl_roundtrip(tmp_path: Path) -> None:
    rows = [
        CalibrationExample(
            example_id="real-1",
            human_score=0.7,
            human_passed=True,
            judge_scores=[0.6, 0.7],
            synthetic=False,
        )
    ]
    json_path = CalibrationDataset.export(rows, tmp_path / "labels.json")
    jsonl_path = CalibrationDataset.export(rows, tmp_path / "labels.jsonl")
    assert CalibrationDataset.load(json_path) == rows
    assert CalibrationDataset.load(jsonl_path) == rows
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["examples"][0]["synthetic"] is False
