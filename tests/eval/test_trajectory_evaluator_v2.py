from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentharness.contracts import (
    RunRequest,
    ToolResult,
    Usage,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.eval.contracts import (
    AgentTrace,
    ArtifactExpectation,
    BudgetPolicy,
    EvaluationPolicy,
    FileExpectation,
    ToolExpectation,
    TraceSpan,
)
from agentharness.eval.dataset import AssertionSpec, EvalCase, EvalSuite
from agentharness.eval.runner import run_suite
from agentharness.eval.trajectory import TrajectoryEvaluator
from agentharness.storage.sqlite import Storage


def _trace(
    tools: list[tuple[str, dict, ToolResult | None]] | None = None,
    *,
    output: str = "done",
    status: str = "completed",
) -> AgentTrace:
    now = datetime.now(UTC)
    spans = [
        TraceSpan(
            trace_id="trace",
            span_id="model-0",
            run_id="run",
            kind="model",
            name="model_turn:0",
            status="completed",
            sequence_start=10,
            sequence_end=20,
            started_at=now,
            ended_at=now + timedelta(milliseconds=10),
            output="planning",
            event_ids=["model-start", "model-end"],
        )
    ]
    for index, (name, arguments, result) in enumerate(tools or []):
        spans.append(
            TraceSpan(
                trace_id="trace",
                span_id=f"tool-{index}",
                parent_span_id="model-0",
                run_id="run",
                kind="tool",
                name=name,
                status="failed" if result and result.is_error else "completed",
                sequence_start=30 + index * 10,
                sequence_end=35 + index * 10,
                tool_call_id=f"call-{index}",
                tool_name=name,
                tool_arguments=arguments,
                tool_result=result,
                event_ids=[f"tool-event-{index}"],
            )
        )
    spans.append(
        TraceSpan(
            trace_id="trace",
            span_id="run-span",
            run_id="run",
            kind="run",
            name="run",
            status=status if status in {"completed", "failed", "interrupted"} else "unset",
            sequence_start=1,
            sequence_end=100,
            event_ids=["run-start", "run-end"],
        )
    )
    return AgentTrace(
        trace_id="trace",
        run_id="run",
        session_id="session",
        root_run_id="run",
        status=status,
        completeness="complete",
        final_output=output,
        usage=Usage(input_tokens=20, output_tokens=10, total_tokens=30, model_turns=1),
        steps=2,
        duration_ms=50,
        spans=spans,
        event_count=8,
        metadata={"cwd": "."},
    )


@pytest.mark.parametrize(
    ("mode", "actual", "passed"),
    [
        ("exact", ["a", "b"], True),
        ("exact", ["a", "x", "b"], False),
        ("strict", ["a", "b", "cleanup"], True),
        ("strict", ["x", "a", "b"], False),
        ("subset", ["x", "a", "x", "b"], True),
        ("subset", ["b", "a"], False),
        ("unordered", ["b", "x", "a"], True),
        ("unordered", ["a"], False),
    ],
)
def test_all_trajectory_match_modes(mode: str, actual: list[str], passed: bool) -> None:
    tools = [
        (
            name,
            {},
            ToolResult(tool_call_id=f"c-{index}", name=name, content="ok"),
        )
        for index, name in enumerate(actual)
    ]
    report = TrajectoryEvaluator().evaluate(
        _trace(tools),
        EvaluationPolicy(match_mode=mode, tool_sequence=["a", "b"]),  # type: ignore[arg-type]
    )
    check = next(item for item in report.checks if item.id == "trajectory.sequence")
    assert (check.status == "passed") is passed
    assert report.passed is passed
    if not passed:
        assert report.first_divergence is not None
        assert report.first_divergence.span_id is not None


def test_output_json_schema_jsonpath_and_numeric_range() -> None:
    policy = EvaluationPolicy(
        output_contains=["total"],
        output_forbidden=["secret"],
        output_regex=r'"total"\s*:\s*42',
        output_json=True,
        output_json_schema={
            "type": "object",
            "required": ["result"],
            "properties": {
                "result": {
                    "type": "object",
                    "required": ["total"],
                    "properties": {"total": {"type": "number"}},
                }
            },
        },
        output_jsonpath={"$.result.total": 42},
        output_numeric_min=40,
        output_numeric_max=50,
    )
    report = TrajectoryEvaluator().evaluate(
        _trace(output='{"result":{"total":42}}'), policy
    )
    assert report.passed is True
    assert report.mode == "scored"
    assert report.score == 1.0
    assert all(check.status == "passed" for check in report.checks)


def test_tool_count_arguments_schema_result_pairing_and_first_divergence() -> None:
    bad = ToolResult(
        tool_call_id="call-0",
        name="write_file",
        content="invalid path",
        is_error=True,
        error_code="invalid_arguments",
        error_category="validation",
    )
    trace = _trace(
        [
            ("write_file", {"path": 123}, bad),
            (
                "write_file",
                {"path": "ok.txt"},
                ToolResult(tool_call_id="call-1", name="write_file", content="ok"),
            ),
        ]
    )
    policy = EvaluationPolicy(
        tools=[
            ToolExpectation(
                name="write_file",
                exact_calls=1,
                arguments_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
                result_status="success",
            )
        ],
    )
    report = TrajectoryEvaluator().evaluate(trace, policy)
    assert report.passed is False
    failed_ids = {check.id for check in report.checks if check.status == "failed"}
    assert "tool.write_file.count" in failed_ids
    assert "tool.write_file.arguments_schema" in failed_ids
    assert "tool.write_file.result_status" in failed_ids
    assert report.first_divergence is not None
    assert report.first_divergence.span_id == "tool-0"


def test_unpaired_tool_call_and_budget_failure_are_structured() -> None:
    trace = _trace([("read_file", {"path": "x"}, None)])
    report = TrajectoryEvaluator().evaluate(
        trace,
        EvaluationPolicy(
            required_tools=["read_file"],
            budgets=BudgetPolicy(max_tokens=20, max_steps=1, max_tool_calls=0),
        ),
    )
    failures = {check.id: check for check in report.checks if check.status == "failed"}
    assert "tool.pairing" in failures
    assert "budget.tokens" in failures
    assert "budget.steps" in failures
    assert "budget.tool_calls" in failures
    assert failures["tool.pairing"].evidence[0].span_id == "tool-0"


def test_no_quality_assertions_is_health_only_without_numeric_score() -> None:
    report = TrajectoryEvaluator().evaluate(_trace(), EvaluationPolicy())
    assert report.mode == "health_only"
    assert report.passed is True
    assert report.score is None
    assert report.not_configured_count >= 1


def test_partial_empty_trace_is_unscored_not_perfect() -> None:
    trace = AgentTrace(
        run_id="legacy",
        trace_id="legacy-trace",
        status="unknown",
        completeness="partial",
        partial_reasons=["missing_run_started"],
    )
    report = TrajectoryEvaluator().evaluate(trace, EvaluationPolicy())
    assert report.mode == "unscored"
    assert report.passed is None
    assert report.score is None


def test_file_and_content_addressed_artifact_checks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.json"
    target.write_text('{"ok":true}', encoding="utf-8")
    storage = Storage(tmp_path / "data")
    try:
        meta = storage.artifacts.put_json({"answer": 42}, summary="answer")
        meta["id"] = storage.register_artifact(meta)
        trace = _trace()
        trace.metadata["cwd"] = str(workspace)
        trace.artifact_ids.append(meta["id"])
        policy = EvaluationPolicy(
            files=[
                FileExpectation(
                    path="result.json",
                    contains=["true"],
                    json_schema={
                        "type": "object",
                        "required": ["ok"],
                        "properties": {"ok": {"const": True}},
                    },
                )
            ],
            artifacts=[
                ArtifactExpectation(
                    artifact_id=meta["id"],
                    sha256=meta["sha256"],
                    contains=["42"],
                )
            ],
        )
        report = TrajectoryEvaluator(storage=storage).evaluate(trace, policy)
        assert report.passed is True
        assert all(check.evidence for check in report.checks if check.category in {"file", "artifact"})
    finally:
        storage.close()


def test_safety_detects_workspace_escape_and_unapproved_destructive_call() -> None:
    trace = _trace(
        [
            (
                "shell",
                {"command": "rm -rf ../outside"},
                ToolResult(tool_call_id="call-0", name="shell", content="denied", is_error=True),
            )
        ]
    )
    report = TrajectoryEvaluator().evaluate(
        trace, EvaluationPolicy(required_tools=["shell"])
    )
    failed = {check.id for check in report.checks if check.status == "failed"}
    assert "safety.workspace" in failed
    assert "safety.approval" in failed
    assert report.hard_failures >= 2


@pytest.mark.asyncio
async def test_verification_run_end_and_suite_share_evaluation_report_contract(harness) -> None:
    verified = await harness.run(
        RunRequest(
            message="[fake:text]shared report",
            provider="fake",
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(
                        kind="eval_assert", assertions={"contains": ["shared report"]}
                    )
                ]
            ),
        )
    )
    verification_event = next(
        event
        for event in harness.get_events(run_id=verified.run_id)
        if str(event.type) == "verification_result"
    )
    verification_report = verification_event.payload["evidence"]["0:eval_assert"]["report"]
    assert verification_report["schema_version"] == 2
    assert verification_report["trace_id"] == harness.get_agent_trace(verified.run_id).trace_id

    run_end = await harness.run(
        RunRequest(
            message="[fake:text]run end",
            provider="fake",
            metadata={"eval_assert": {"contains": ["run end"]}},
        )
    )
    stored = harness.get_run(run_end.run_id)
    assert stored is not None
    stored_eval = json.loads(stored["metadata_json"])["eval"]
    assert stored_eval["evaluation_report"]["schema_version"] == 2

    suite = EvalSuite(
        name="shared",
        cases=[
            EvalCase(
                id="case",
                prompt="[fake:text]suite",
                provider="fake",
                assertions=AssertionSpec(contains=["suite"]),
            )
        ],
    )
    suite_report = await run_suite(suite, harness=harness, concurrency=1)
    assert suite_report.results[0].evaluation_report is not None
    assert suite_report.results[0].evaluation_report.schema_version == 2
