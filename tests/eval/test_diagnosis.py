from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.eval.contracts import EvaluationPolicy, ToolExpectation
from agentharness.eval.diagnosis import DiagnosisEngine
from agentharness.eval.trajectory import TrajectoryEvaluator
from agentharness.providers.fake import FakeModelAdapter


@pytest.mark.asyncio
async def test_real_failure_trace_diagnoses_invalid_arguments_and_probes_evidence(
    harness, workspace: Path
) -> None:
    (workspace / "AGENTS.md").write_text("Only read files inside this workspace.", encoding="utf-8")
    provider = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [
                    {"name": "read_file", "arguments": {"path": "missing.txt"}}
                ],
            },
            {
                "kind": "tools",
                "tools": [
                    {"name": "read_file", "arguments": {"path": "missing.txt"}}
                ],
            },
            {"kind": "text", "text": "done without evidence"},
        ]
    )
    harness.register_provider("diagnosis-fake", provider)
    result = await harness.run(
        RunRequest(
            message="Read a.txt once, then report its contents.",
            provider="diagnosis-fake",
            cwd=str(workspace),
            approval=ApprovalMode.auto,
        )
    )
    trace = harness.get_agent_trace(result.run_id)
    evaluation = TrajectoryEvaluator(storage=harness.storage).evaluate(
        trace,
        EvaluationPolicy(
            policy_id="diagnosis",
            version="1",
            match_mode="exact",
            output_contains=["alpha"],
            tool_sequence=["read_file"],
            tools=[
                ToolExpectation(
                    name="read_file",
                    exact_calls=1,
                    arguments={"path": "a.txt"},
                    argument_match="exact",
                    result_status="success",
                )
            ],
        ),
    )
    diagnosis = DiagnosisEngine(storage=harness.storage).diagnose(trace, evaluation)

    assert evaluation.passed is False
    assert evaluation.first_divergence is not None
    assert evaluation.first_divergence.span_id is not None
    assert diagnosis.root_cause in {"invalid_tool_arguments", "retry_loop"}
    assert diagnosis.confidence >= 0.8
    assert diagnosis.first_divergence == evaluation.first_divergence
    assert diagnosis.evidence
    assert all(ref.span_id or ref.artifact_id or ref.path for ref in diagnosis.evidence)
    assert diagnosis.read_only is True
    assert diagnosis.recommendations

    probes = {finding.probe: finding for finding in diagnosis.probes}
    assert "ToolSpecProbe" in probes
    assert "PromptProbe" in probes
    assert "ContextManifestProbe" in probes
    assert "WorkspaceRuleProbe" in probes
    assert "RuntimeConfigProbe" in probes
    assert "VersionProbe" in probes
    assert probes["ToolSpecProbe"].evidence[0].span_id == evaluation.first_divergence.span_id
    assert any("read_file" in item for item in probes["ToolSpecProbe"].affected_configuration)
    assert any("AGENTS.md" in (ref.path or "") for ref in probes["WorkspaceRuleProbe"].evidence)


@pytest.mark.parametrize(
    ("failure_category", "root_cause"),
    [
        ("wrong_tool_selection", "wrong_tool_selection"),
        ("invalid_tool_arguments", "invalid_tool_arguments"),
        ("duplicate_tool_call", "duplicate_tool_call"),
        ("retry_loop", "retry_loop"),
        ("missing_required_step", "missing_required_step"),
        ("tool_result_missing", "tool_result_ignored"),
        ("premature_completion", "premature_completion"),
        ("verification_missing", "verification_missing"),
        ("approval_deadlock", "approval_deadlock"),
        ("context_drift", "context_drift"),
        ("budget_exhaustion", "budget_exhaustion"),
        ("provider_failure", "provider_failure"),
        ("environment_failure", "environment_failure"),
    ],
)
def test_deterministic_failure_taxonomy(
    failure_category: str, root_cause: str
) -> None:
    from agentharness.eval.contracts import (
        AgentTrace,
        CheckResult,
        EvaluationReport,
        EvidenceRef,
        TraceSpan,
    )

    evidence = EvidenceRef(trace_id="t", run_id="r", span_id="s", sequence=1)
    trace = AgentTrace(
        trace_id="t",
        run_id="r",
        status="failed",
        completeness="complete",
        spans=[
            TraceSpan(
                trace_id="t",
                run_id="r",
                span_id="s",
                kind="control",
                status="failed",
                sequence_start=1,
                sequence_end=1,
                event_ids=["e"],
            )
        ],
        event_count=1,
    )
    report = EvaluationReport(
        trace_id="t",
        run_id="r",
        policy_id="p",
        mode="scored",
        passed=False,
        score=0,
        first_divergence=evidence,
        hard_failures=1,
        failed_count=1,
        checks=[
            CheckResult(
                id="failed",
                category="test",
                status="failed",
                failure_category=failure_category,
                evidence=[evidence],
            )
        ],
    )
    assert DiagnosisEngine().diagnose(trace, report).root_cause == root_cause


def test_diagnosis_does_not_write_prompt_tool_skill_or_config(
    tmp_path: Path,
) -> None:
    from agentharness.eval.contracts import AgentTrace, EvaluationReport

    config = tmp_path / "config.json"
    config.write_text('{"enabled":true}', encoding="utf-8")
    before = config.read_bytes()
    trace = AgentTrace(
        trace_id="t",
        run_id="r",
        status="completed",
        completeness="complete",
        event_count=1,
        metadata={"runtime_config_path": str(config)},
    )
    report = EvaluationReport(
        trace_id="t", run_id="r", policy_id="p", mode="health_only", passed=True
    )
    diagnosis = DiagnosisEngine().diagnose(trace, report)
    assert diagnosis.root_cause == "none"
    assert config.read_bytes() == before
