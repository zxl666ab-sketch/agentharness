"""Seed a disposable observer database and serve it for Playwright."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentharness.api.server import serve
from agentharness.contracts import (
    ApprovalMode,
    EventEnvelope,
    EventType,
    RunRequest,
    RunStatus,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.eval.contracts import (
    CalibrationReport,
    DiagnosisReport,
    EvaluationReport,
    GateDecision,
    JudgeSample,
    RegressionReport,
    RerunStatistics,
    SemanticEvaluation,
)
from agentharness.harness import Harness
from agentharness.providers.fake import FakeModelAdapter


async def seed(data_dir: Path, workspace: Path) -> None:
    harness = Harness(data_dir=data_dir)
    try:
        await harness.run(
            RunRequest(
                message='[fake:tools]read_file\n{"path":"README.md"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        evaluation_provider = FakeModelAdapter(
            script=[
                {
                    "kind": "tools",
                    "tools": [
                        {"name": "read_file", "arguments": {"path": "README.md"}}
                    ],
                },
                {"kind": "text", "text": "已读取 README.md，并完成发布流程检查。"},
            ]
        )
        harness.register_provider("evaluation-demo", evaluation_provider)
        evaluation_run = await harness.run(
            RunRequest(
                message="请读取 README.md，并按发布流程验证结果。",
                provider="evaluation-demo",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                metadata={
                    "eval_assert": {
                        "status": "completed",
                        "tools_order": ["write_file"],
                    }
                },
            )
        )
        evaluation_row = harness.get_run(evaluation_run.run_id)
        assert evaluation_row is not None
        evaluation_metadata = json.loads(evaluation_row["metadata_json"])
        evaluation_report = EvaluationReport.model_validate(
            evaluation_metadata["evaluation"]["report"]
        )
        diagnosis = DiagnosisReport.model_validate(
            evaluation_metadata["evaluation"]["diagnosis"]
        )
        evidence = [evaluation_report.first_divergence] if evaluation_report.first_divergence else []
        semantic = SemanticEvaluation(
            rubric_id="e2e-web-rubric",
            rubric_version="1",
            status="unverified",
            samples=[
                JudgeSample(
                    score=0.38,
                    passed=False,
                    confidence=0.82,
                    rationale="The required write trajectory was not followed.",
                    evidence=evidence,
                ),
                JudgeSample(
                    score=0.42,
                    passed=False,
                    confidence=0.79,
                    rationale="Observed read_file where write_file was required.",
                    evidence=evidence,
                ),
                JudgeSample(
                    score=0.4,
                    passed=False,
                    confidence=0.8,
                    rationale="The answer cannot satisfy the trajectory rubric.",
                    evidence=evidence,
                ),
            ],
            mean_score=0.4,
            median_score=0.4,
            variance=0.00027,
            consistency=0.98,
            passed=False,
            fallback_score=evaluation_report.score,
            fallback_report_id=evaluation_report.report_id,
            attack_resistant=True,
        )
        calibration = CalibrationReport(
            sample_count=4,
            synthetic_only=True,
            trust_status="unverified",
            accuracy=0.75,
            internal_consistency=0.98,
        )
        harness.retain_run_evaluation(
            evaluation_run.run_id,
            report=evaluation_report,
            diagnosis=diagnosis,
            semantic=semantic,
            calibration=calibration,
            legacy_eval=evaluation_metadata["eval"],
            source="e2e",
        )
        rerun = RerunStatistics(
            sample_count=5,
            success_rate=0.4,
            wilson_low=0.1176,
            wilson_high=0.7693,
            mean_score=0.42,
            score_variance=0.012,
            p50_latency_ms=118,
            p95_latency_ms=164,
        )
        regression = RegressionReport(
            baseline_id="golden-web",
            candidate_id="candidate-web",
            new_failures=["evaluation-web-fixture"],
            score_drops=[{"case_id": "evaluation-web-fixture", "delta": -0.58}],
            summary={"baseline_pass_rate": 1.0, "current_pass_rate": 0.4},
            case_metrics={
                "baseline": {"case_count": 5, "pass_rate": 1.0, "mean_score": 1.0},
                "candidate": {"case_count": 5, "pass_rate": 0.4, "mean_score": 0.42},
            },
            first_divergence_distribution={"1": 3, "2": 1},
            rerun_statistics=rerun,
        )
        gate = GateDecision(
            passed=False,
            reason="new failure and score regression detected",
            regression=regression,
            failed_case_ids=["evaluation-web-fixture"],
            exit_code=1,
        )
        harness.retain_run_regression(
            evaluation_run.run_id,
            regression=regression,
            gate_decision=gate,
            baseline_diff={
                "baseline_passed": True,
                "candidate_passed": False,
                "score_delta": -0.58,
            },
            rerun_statistics=rerun,
        )
        verification_provider = FakeModelAdapter(
            script=[
                {"kind": "text", "text": "candidate missing marker"},
                {"kind": "text", "text": "corrected PLAYWRIGHT_VERIFIED"},
            ]
        )
        harness.register_provider("playwright-verifier", verification_provider)
        await harness.run(
            RunRequest(
                message="playwright verification fixture",
                provider="playwright-verifier",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                verification=VerificationPolicy(
                    validators=[
                        VerificationCheck(
                            kind="eval_assert",
                            assertions={"contains": ["PLAYWRIGHT_VERIFIED"]},
                        )
                    ]
                ),
            )
        )
        await harness.run(
            RunRequest(
                message=(
                    '[fake:tools]delegate\n'
                    '{"task":"[fake:text]child e2e output","allow_write":false}'
                ),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        await harness.run(
            RunRequest(
                message="[fake:error:provider]",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        long_run = await harness.run(
            RunRequest(
                message="[fake:text]long trace fixture",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        long_row = harness.get_run(long_run.run_id)
        assert long_row is not None
        for start in range(0, 1200, 200):
            harness.storage.append_events(
                [
                    EventEnvelope(
                        session_id=long_run.session_id,
                        root_run_id=long_run.run_id,
                        run_id=long_run.run_id,
                        type=EventType.budget_warning,
                        payload={"message": f"fixture row {index}"},
                    )
                    for index in range(start, start + 200)
                ]
            )

        stale_session = harness.storage.create_session(title="stale fixture")
        harness.storage.create_run(
            run_id="stale-e2e-run",
            session_id=stale_session,
            root_run_id="stale-e2e-run",
            status=RunStatus.running,
            provider="fake",
            approval="auto",
            cwd=str(workspace),
        )
        stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with harness.storage._lock:
            harness.storage._conn.execute(  # noqa: SLF001 - deterministic test fixture
                "UPDATE runs SET created_at = ?, updated_at = ? WHERE id = ?",
                (stale_time, stale_time, "stale-e2e-run"),
            )
    finally:
        harness.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    data_dir = Path(tempfile.mkdtemp(prefix="agentharness-web-e2e-"))
    workspace = data_dir / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("临时端到端工作区", encoding="utf-8")
    (workspace / "AGENTS.md").write_text(
        "Playwright fixture workspace rule", encoding="utf-8"
    )
    atexit.register(shutil.rmtree, data_dir, True)
    asyncio.run(seed(data_dir, workspace))
    serve(data_dir=data_dir, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
