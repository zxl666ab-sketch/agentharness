from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    Message,
    MessageRole,
    RunRequest,
    RunStatus,
    ToolResult,
    VerificationCandidate,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.engine.verification import VerificationLoop
from agentharness.providers.fake import FakeModelAdapter
from agentharness.security.redaction import Redactor


def _candidate(tmp_path: Path, output: str) -> VerificationCandidate:
    return VerificationCandidate(
        run_id="verify-run",
        goal="produce DONE",
        output=output,
        cwd=str(tmp_path),
        messages=[
            Message(role=MessageRole.user, content="produce DONE"),
            Message(role=MessageRole.assistant, content=output),
        ],
    )


@pytest.mark.asyncio
async def test_eval_assert_failure_returns_structured_retry_feedback(tmp_path: Path) -> None:
    loop = VerificationLoop(redactor=Redactor())
    policy = VerificationPolicy(
        validators=[
            VerificationCheck(kind="eval_assert", assertions={"contains": ["DONE"]})
        ],
        max_retries=2,
    )

    failed = await loop.evaluate(_candidate(tmp_path, "not ready"), policy, attempt=0)

    assert failed.action == "retry"
    assert failed.failures[0].validator == "eval_assert"
    assert failed.failures[0].retryable
    assert failed.feedback_message is not None
    assert failed.feedback_message.role == MessageRole.user
    assert "produce DONE" in failed.feedback_message.content
    assert "DONE" in failed.feedback_message.content

    passed = await loop.evaluate(_candidate(tmp_path, "DONE"), policy, attempt=1)
    assert passed.action == "pass"
    assert not passed.failures


@pytest.mark.asyncio
async def test_file_checks_are_sandboxed_and_composable(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("verified content", encoding="utf-8")
    loop = VerificationLoop(redactor=Redactor())
    policy = VerificationPolicy(
        validators=[
            VerificationCheck(kind="file", path="result.txt", contains=["verified"]),
            VerificationCheck(kind="file", path="missing.txt", exists=False),
        ]
    )

    decision = await loop.evaluate(_candidate(tmp_path, "candidate"), policy, attempt=0)
    assert decision.action == "pass"

    escape = VerificationPolicy(
        validators=[VerificationCheck(kind="file", path="../outside.txt")]
    )
    denied = await loop.evaluate(_candidate(tmp_path, "candidate"), escape, attempt=0)
    assert denied.action == "retry"
    assert denied.failures[0].error_code == "workspace_violation"


@pytest.mark.asyncio
async def test_verification_honors_cancellation_before_running_validators(
    tmp_path: Path,
) -> None:
    cancel = asyncio.Event()
    cancel.set()
    candidate = _candidate(tmp_path, "candidate").model_copy(
        update={"cancel_event": cancel}
    )
    decision = await VerificationLoop(redactor=Redactor()).evaluate(
        candidate,
        VerificationPolicy(
            validators=[VerificationCheck(kind="file", path="should-not-be-read.txt")]
        ),
        attempt=0,
    )

    assert decision.action == "stop"
    assert decision.failures[0].error_code == "cancelled"


@pytest.mark.asyncio
async def test_command_validator_uses_injected_governed_runner(tmp_path: Path) -> None:
    commands: list[str] = []

    async def governed(candidate: VerificationCandidate, command: str) -> ToolResult:
        commands.append(command)
        return ToolResult(
            tool_call_id="verify-call",
            name="shell",
            content="Approval denied",
            is_error=True,
            error_code="approval_denied",
            error_category="approval",
            retryable=False,
            recovery_hint="Ask a human to approve the verification command.",
        )

    loop = VerificationLoop(redactor=Redactor(), command_runner=governed)
    policy = VerificationPolicy(
        validators=[VerificationCheck(kind="command", command="pytest -q")],
        max_retries=3,
    )

    decision = await loop.evaluate(_candidate(tmp_path, "candidate"), policy, attempt=0)
    assert commands == ["pytest -q"]
    assert decision.action == "require_human"
    assert decision.failures[0].error_code == "approval_denied"
    assert decision.failures[0].recovery_hint


@pytest.mark.asyncio
async def test_retryable_command_failure_drives_correction(tmp_path: Path) -> None:
    async def governed(candidate: VerificationCandidate, command: str) -> ToolResult:
        return ToolResult(
            tool_call_id="verify-call",
            name="shell",
            content="exit=1\ntest failed",
            is_error=True,
            error_code="command_failed",
            error_category="process",
            retryable=True,
            recovery_hint="Fix the failing test.",
        )

    decision = await VerificationLoop(
        redactor=Redactor(), command_runner=governed
    ).evaluate(
        _candidate(tmp_path, "candidate"),
        VerificationPolicy(
            validators=[VerificationCheck(kind="command", command="pytest -q")],
            max_retries=2,
        ),
        attempt=0,
    )
    assert decision.action == "retry"
    assert decision.feedback_message is not None
    assert "Fix the failing test" in decision.feedback_message.content


@pytest.mark.asyncio
async def test_retry_exhaustion_obeys_policy(tmp_path: Path) -> None:
    policy = VerificationPolicy(
        validators=[VerificationCheck(kind="file", path="missing.txt")],
        max_retries=1,
        on_exhausted="require_human",
    )

    decision = await VerificationLoop(redactor=Redactor()).evaluate(
        _candidate(tmp_path, "candidate"), policy, attempt=1
    )
    assert decision.action == "require_human"


def _passing_judge_json() -> str:
    dimensions = {
        name: {"score": 1.0, "reason": "observable pass", "applicable": True}
        for name in (
            "task_completion",
            "correctness",
            "completeness",
            "planning_recovery",
            "tool_use",
            "execution_verification",
            "efficiency",
            "safety_control",
            "user_experience",
        )
    }
    return json.dumps(
        {
            "dimensions": dimensions,
            "hard_safety_violation": False,
            "confidence": 1.0,
            "failure_category": "none",
            "evidence": ["candidate output"],
            "improvements": [],
        }
    )


@pytest.mark.asyncio
async def test_ai_evaluator_must_be_independent_from_executor(tmp_path: Path) -> None:
    shared = FakeModelAdapter(script=[{"kind": "text", "text": _passing_judge_json()}])
    candidate = _candidate(tmp_path, "DONE").model_copy(
        update={"executor_provider": "executor"}
    )
    policy = VerificationPolicy(
        validators=[VerificationCheck(kind="ai", min_score=0.8)],
        evaluator_provider="executor",
    )
    loop = VerificationLoop(
        redactor=Redactor(), evaluator_resolver=lambda _name: shared
    )

    rejected = await loop.evaluate(candidate, policy, attempt=0)
    assert rejected.action == "require_human"
    assert rejected.failures[0].error_code == "evaluator_not_independent"
    assert not shared.calls

    evaluator = FakeModelAdapter(script=[{"kind": "text", "text": _passing_judge_json()}])
    independent_policy = policy.model_copy(update={"evaluator_provider": "judge"})
    independent = VerificationLoop(
        redactor=Redactor(), evaluator_resolver=lambda name: evaluator if name == "judge" else None
    )
    passed = await independent.evaluate(candidate, independent_policy, attempt=0)
    assert passed.action == "pass"
    assert evaluator.calls
    assert evaluator.calls[0].tools == []


@pytest.mark.asyncio
async def test_run_engine_retries_failed_candidate_then_completes(harness, workspace) -> None:
    provider = FakeModelAdapter(
        script=[
            {"kind": "text", "text": "candidate missing marker"},
            {"kind": "text", "text": "corrected DONE"},
        ]
    )
    harness.register_provider("verify-script", provider)
    result = await harness.run(
        RunRequest(
            message="produce DONE",
            provider="verify-script",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(kind="eval_assert", assertions={"contains": ["DONE"]})
                ],
                max_retries=2,
            ),
        )
    )

    assert result.status == RunStatus.completed
    assert result.output == "corrected DONE"
    assert len(provider.calls) == 2
    assert "verification_feedback" in provider.calls[1].messages[-1].content
    events = harness.get_events(run_id=result.run_id, limit=1000)
    event_types = [event.type.value if hasattr(event.type, "value") else str(event.type) for event in events]
    assert event_types.count("verification_started") == 2
    assert event_types.count("verification_result") == 2
    assert event_types.count("verification_feedback") == 1
    assert event_types.index("run_completed") > max(
        index for index, value in enumerate(event_types) if value == "verification_result"
    )


@pytest.mark.asyncio
async def test_run_engine_stops_when_verification_retries_are_exhausted(harness, workspace) -> None:
    provider = FakeModelAdapter(
        script=[
            {"kind": "text", "text": "wrong one"},
            {"kind": "text", "text": "wrong two"},
        ]
    )
    harness.register_provider("verify-fail", provider)
    result = await harness.run(
        RunRequest(
            message="produce DONE",
            provider="verify-fail",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(kind="eval_assert", assertions={"contains": ["DONE"]})
                ],
                max_retries=1,
                on_exhausted="failed",
            ),
        )
    )

    assert result.status == RunStatus.failed
    assert "verification failed" in (result.error or "")
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_command_verifier_flows_through_shell_approval_and_events(
    ask_harness, workspace
) -> None:
    command = f'"{sys.executable}" -c "print(\'verified-command\')"'
    result = await ask_harness.run(
        RunRequest(
            message="[fake:text]candidate",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(
                        kind="command", command=command, contains=["verified-command"]
                    )
                ]
            ),
        )
    )

    assert result.status == RunStatus.completed
    approvals = ask_harness.list_approvals(result.run_id)
    assert len(approvals) == 1
    assert approvals[0]["tool_name"] == "shell"
    assert approvals[0]["effect"] == "destructive"
    events = ask_harness.get_events(run_id=result.run_id, limit=1000)
    types = {event.type.value if hasattr(event.type, "value") else str(event.type) for event in events}
    assert {"verification_started", "approval_requested", "tool_result", "verification_result"} <= types


@pytest.mark.asyncio
async def test_nonretryable_verification_failure_pauses_with_checkpoint(harness, workspace) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]candidate",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            verification=VerificationPolicy(
                validators=[VerificationCheck(kind="command", command="echo verify")]
            ),
        )
    )

    assert result.status == RunStatus.require_human
    checkpoint = harness.get_checkpoint(result.run_id)
    assert checkpoint is not None
    assert checkpoint.status == RunStatus.require_human
    assert checkpoint.metadata.get("verification_attempt") == 0


@pytest.mark.asyncio
async def test_verification_retry_remains_bounded_by_step_budget(harness, workspace) -> None:
    provider = FakeModelAdapter(script=[{"kind": "text", "text": "always wrong"}])
    harness.register_provider("verify-budget", provider)
    result = await harness.run(
        RunRequest(
            message="produce DONE",
            provider="verify-budget",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            budget=BudgetConfig(max_steps=1),
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(kind="eval_assert", assertions={"contains": ["DONE"]})
                ],
                max_retries=10,
            ),
        )
    )
    assert result.status == RunStatus.failed
    assert result.error == "max_steps exceeded"
    assert len(provider.calls) == 1
