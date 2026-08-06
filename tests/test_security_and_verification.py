"""Unit coverage for security sandbox/approval policies and the verification loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    EffectKind,
    MessageRole,
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    ToolSpec,
    VerificationCandidate,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.engine.verification import VerificationLoop
from agentharness.security.approval import auto_decision, effect_needs_approval, should_gate
from agentharness.security.sandbox import (
    SandboxError,
    assert_in_workspace,
    normalize_path,
    safe_join,
)

# ---------------------------------------------------------------------------
# Workspace sandbox
# ---------------------------------------------------------------------------


def test_normalize_path_relative_and_absolute(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    assert normalize_path("sub/file.txt", cwd=cwd) == (cwd / "sub/file.txt").resolve()
    assert normalize_path(cwd / "x.txt", cwd=cwd) == (cwd / "x.txt").resolve()


def test_assert_in_workspace_allows_inside_and_missing(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    (cwd / "a.txt").write_text("alpha", encoding="utf-8")
    inside = assert_in_workspace("a.txt", cwd=cwd)
    assert inside == (cwd / "a.txt").resolve()
    # Deep missing path walks the parent chain and stays inside the workspace.
    deep = assert_in_workspace("a/b/c/d.txt", cwd=cwd)
    assert deep == (cwd / "a/b/c/d.txt").resolve()
    with pytest.raises(FileNotFoundError):
        assert_in_workspace("missing.txt", cwd=cwd, must_exist=True)
    assert assert_in_workspace("missing.txt", cwd=cwd, must_exist=False) == (
        cwd / "missing.txt"
    ).resolve()


def test_assert_in_workspace_rejects_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(SandboxError):
        assert_in_workspace("../secret.txt", cwd=cwd)
    with pytest.raises(SandboxError):
        assert_in_workspace(str(tmp_path / "secret.txt"), cwd=cwd)


def test_assert_in_workspace_extra_dirs(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "data.csv").write_text("x", encoding="utf-8")
    assert assert_in_workspace("../extra/data.csv", cwd=cwd, extra_dirs=[extra]) == (
        extra / "data.csv"
    ).resolve()
    assert assert_in_workspace("../extra/data.csv", cwd=cwd, extra_dirs=str(extra)) == (
        extra / "data.csv"
    ).resolve()
    with pytest.raises(SandboxError):
        assert_in_workspace("../extra/data.csv", cwd=cwd)
    with pytest.raises(SandboxError):
        assert_in_workspace(str(extra / "data.csv"), cwd=cwd)


def test_safe_join(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    assert safe_join(cwd, "sub", "file.txt") == (cwd / "sub/file.txt").resolve()
    with pytest.raises(SandboxError):
        safe_join(cwd, "..", "escape.txt")


# ---------------------------------------------------------------------------
# Approval policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("effect", "mode", "expected"),
    [
        (EffectKind.pure, ApprovalMode.ask, False),
        (EffectKind.workspace_read, ApprovalMode.never, False),
        (EffectKind.workspace_write, ApprovalMode.auto, False),
        (EffectKind.workspace_write, ApprovalMode.ask, True),
        (EffectKind.workspace_write, ApprovalMode.never, True),
        (EffectKind.destructive, ApprovalMode.auto, True),
        (EffectKind.destructive, ApprovalMode.ask, True),
    ],
)
def test_effect_needs_approval(
    effect: EffectKind, mode: ApprovalMode, expected: bool
) -> None:
    assert effect_needs_approval(effect, mode) is expected


def test_auto_decision() -> None:
    assert auto_decision(EffectKind.pure, ApprovalMode.ask) == ApprovalDecision.allow_once
    assert (
        auto_decision(EffectKind.workspace_read, ApprovalMode.never)
        == ApprovalDecision.allow_once
    )
    assert (
        auto_decision(EffectKind.workspace_write, ApprovalMode.auto)
        == ApprovalDecision.allow_run
    )
    assert (
        auto_decision(EffectKind.workspace_write, ApprovalMode.never)
        == ApprovalDecision.deny
    )
    assert auto_decision(EffectKind.workspace_write, ApprovalMode.ask) is None
    assert (
        auto_decision(EffectKind.destructive, ApprovalMode.never)
        == ApprovalDecision.deny
    )
    assert auto_decision(EffectKind.destructive, ApprovalMode.auto) is None
    assert auto_decision(EffectKind.destructive, ApprovalMode.ask) is None


def test_should_gate() -> None:
    pure = ToolSpec(name="read", description="read", effect=EffectKind.pure)
    write = ToolSpec(name="write", description="write", effect=EffectKind.workspace_write)
    assert should_gate(pure, ApprovalMode.ask) is False
    assert should_gate(write, ApprovalMode.ask) is True


# ---------------------------------------------------------------------------
# Verification loop
# ---------------------------------------------------------------------------


class _StubEvaluator:
    name = "stub_evaluator"

    def __init__(
        self,
        *,
        verdict: dict[str, Any] | None = None,
        error: str | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.verdict = verdict
        self.error = error
        self.gate = gate
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.requests.append(request)
        if self.error is not None:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error=self.error,
                error_kind="provider",
            )
            return
        yield ModelStreamItem(
            type=StreamItemType.text_delta,
            text=json.dumps(self.verdict or {}, ensure_ascii=False),
        )
        if self.gate is not None:
            await self.gate.wait()
        yield ModelStreamItem(type=StreamItemType.done)


def _candidate(**overrides: Any) -> VerificationCandidate:
    values: dict[str, Any] = {
        "goal": "write a file",
        "output": "done",
        "cwd": ".",
        "steps": 1,
        "tools_ordered": ["write"],
        "tools_succeeded": ["write"],
    }
    values.update(overrides)
    return VerificationCandidate(**values)


def _policy(*checks: VerificationCheck, **overrides: Any) -> VerificationPolicy:
    values: dict[str, Any] = {"validators": list(checks)}
    values.update(overrides)
    return VerificationPolicy(**values)


async def _evaluate(
    policy: VerificationPolicy,
    *,
    candidate: VerificationCandidate | None = None,
    attempt: int = 0,
    cancel: asyncio.Event | None = None,
    resolver: Any = None,
) -> Any:
    loop = VerificationLoop(evaluator_resolver=resolver)
    return await loop.evaluate(
        candidate or _candidate(cancel_event=cancel),
        policy,
        attempt=attempt,
    )


@pytest.mark.asyncio
async def test_verification_output_check_pass_and_fail() -> None:
    passed = await _evaluate(
        _policy(VerificationCheck(kind="output", assertions={"contains": ["done"]}))
    )
    assert passed.action == "pass"

    failed = await _evaluate(
        _policy(
            VerificationCheck(
                kind="output",
                assertions={
                    "contains": ["missing text"],
                    "not_contains": ["done"],
                    "tools_ordered": ["read"],
                    "tools_succeeded": ["nonexistent"],
                    "max_steps": 0,
                },
            )
        ),
        attempt=0,
    )
    assert failed.action == "retry"
    assert failed.failures[0].error_code == "assertion_failed"
    assert failed.feedback_message is not None
    assert failed.feedback_message.role == MessageRole.user
    assert "[verification_feedback]" in failed.feedback


@pytest.mark.asyncio
async def test_verification_output_check_invalid_assertions() -> None:
    bad_list = await _evaluate(
        _policy(VerificationCheck(kind="output", assertions={"contains": "not-a-list"}))
    )
    assert bad_list.failures[0].error_code == "invalid_assertion"

    bad_steps = await _evaluate(
        _policy(VerificationCheck(kind="output", assertions={"max_steps": "many"}))
    )
    assert bad_steps.failures[0].error_code == "invalid_assertion"

@pytest.mark.asyncio
async def test_verification_file_check(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("alpha beta", encoding="utf-8")

    missing_path = await _evaluate(
        _policy(VerificationCheck(kind="file")),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert missing_path.failures[0].error_code == "invalid_file_check"

    ok = await _evaluate(
        _policy(VerificationCheck(kind="file", path="report.txt", contains=["alpha"])),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert ok.action == "pass"

    missing_content = await _evaluate(
        _policy(VerificationCheck(kind="file", path="report.txt", contains=["omega"])),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert missing_content.failures[0].error_code == "file_content_failed"

    absent_expected = await _evaluate(
        _policy(VerificationCheck(kind="file", path="report.txt", exists=False)),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert absent_expected.failures[0].error_code == "file_condition_failed"

    absent_ok = await _evaluate(
        _policy(VerificationCheck(kind="file", path="missing.txt", exists=False)),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert absent_ok.action == "pass"

    escape = await _evaluate(
        _policy(VerificationCheck(kind="file", path="../outside.txt", exists=True)),
        candidate=_candidate(cwd=str(tmp_path)),
    )
    assert escape.failures[0].error_code == "workspace_violation"


@pytest.mark.asyncio
async def test_verification_ai_check_configuration_failures() -> None:
    not_configured = await _evaluate(
        _policy(VerificationCheck(kind="ai")),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
    )
    assert not_configured.failures[0].error_code == "evaluator_unavailable"

    same_provider = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="main",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
        resolver=lambda name: _StubEvaluator(),
    )
    assert same_provider.failures[0].error_code == "evaluator_not_independent"

    adapter = _StubEvaluator()
    same_adapter = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="other",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=adapter),
        resolver=lambda name: adapter,
    )
    assert same_adapter.failures[0].error_code == "evaluator_not_independent"

    unavailable = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="missing",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
        resolver=lambda name: None,
    )
    assert unavailable.failures[0].error_code == "evaluator_unavailable"


@pytest.mark.asyncio
async def test_verification_ai_check_pass_low_score_and_hard_safety() -> None:
    def verdict(score: float, hard: bool = False) -> dict[str, Any]:
        return {
            "dimensions": {
                name: {"score": score} for name in ("task_completion", "correctness", "completeness")
            },
            "confidence": 0.9,
            "hard_safety_violation": hard,
            "failure_category": "none",
            "evidence": ["e1"],
            "improvements": ["do better"],
        }

    evaluator = _StubEvaluator(verdict=verdict(0.95))
    passed = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="other",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
        resolver=lambda name: evaluator,
    )
    assert passed.action == "pass"
    assert evaluator.requests

    low = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.9),
            evaluator_provider="other",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
        resolver=lambda name: _StubEvaluator(verdict=verdict(0.2)),
    )
    assert low.failures[0].error_code == "ai_score_below_threshold"

    unsafe = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.9),
            evaluator_provider="other",
        ),
        candidate=_candidate(executor_provider="main", executor_adapter=object()),
        resolver=lambda name: _StubEvaluator(verdict=verdict(0.95, hard=True)),
    )
    assert unsafe.failures[0].error_code == "hard_safety_violation"
    assert unsafe.failures[0].retryable is False


@pytest.mark.asyncio
async def test_verification_ai_check_malformed_verdicts() -> None:
    base = {"executor_provider": "main", "executor_adapter": object()}

    bad_json = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="other",
        ),
        candidate=_candidate(**base),
        resolver=lambda name: _StubEvaluator(verdict={"no_dimensions": True}),
    )
    assert bad_json.failures[0].error_code == "evaluator_failed"

    out_of_range = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="other",
        ),
        candidate=_candidate(**base),
        resolver=lambda name: _StubEvaluator(
            verdict={
                "dimensions": {
                    name: {"score": 1.5}
                    for name in ("task_completion", "correctness", "completeness")
                }
            }
        ),
    )
    assert out_of_range.failures[0].error_code == "evaluator_failed"

    stream_error = await _evaluate(
        _policy(
            VerificationCheck(kind="ai", min_score=0.5),
            evaluator_provider="other",
        ),
        candidate=_candidate(**base),
        resolver=lambda name: _StubEvaluator(error="boom"),
    )
    assert stream_error.failures[0].error_code == "evaluator_failed"


@pytest.mark.asyncio
async def test_verification_cancel_and_action_collapse() -> None:
    cancel = asyncio.Event()
    cancel.set()
    cancelled = await _evaluate(
        _policy(VerificationCheck(kind="output", assertions={"contains": ["done"]})),
        candidate=_candidate(cancel_event=cancel),
    )
    assert cancelled.action == "stop"
    assert cancelled.failures[0].error_code == "cancelled"

    await _evaluate(
        _policy(VerificationCheck(kind="file", path="x.txt", contains=["y"])),
        candidate=_candidate(cwd="."),
    )
    # A valid file check on a missing file is retryable, so exhaustion drives it.
    stopped = await _evaluate(
        _policy(
            VerificationCheck(kind="output", assertions={"contains": ["never"]}),
            max_retries=0,
            on_exhausted="failed",
        ),
        attempt=0,
    )
    assert stopped.action == "stop"

    human = await _evaluate(
        _policy(
            VerificationCheck(kind="output", assertions={"contains": ["never"]}),
            max_retries=0,
            on_exhausted="require_human",
        ),
        attempt=0,
    )
    assert human.action == "require_human"
    assert human.feedback is not None

    # Mid-loop cancellation produces a cancelled failure: the first check is an
    # AI check that suspends, then the loop notices the cancel before check two.
    cancel = asyncio.Event()
    gate = asyncio.Event()
    evaluator = _StubEvaluator(
        verdict={
            "dimensions": {
                name: {"score": 0.9}
                for name in ("task_completion", "correctness", "completeness")
            }
        },
        gate=gate,
    )
    loop = VerificationLoop(evaluator_resolver=lambda name: evaluator)
    candidate = _candidate(
        executor_provider="main",
        executor_adapter=object(),
        cancel_event=cancel,
    )
    task = asyncio.create_task(
        loop.evaluate(
            candidate,
            _policy(
                VerificationCheck(kind="ai", min_score=0.5),
                VerificationCheck(kind="output", assertions={"contains": ["never"]}),
                evaluator_provider="other",
            ),
            attempt=0,
        )
    )
    for _ in range(100):
        if evaluator.requests:
            break
        await asyncio.sleep(0)
    assert evaluator.requests, "evaluator never started"
    cancel.set()
    gate.set()
    decision = await asyncio.wait_for(task, timeout=5)
    assert decision.failures[-1].error_code == "cancelled"
