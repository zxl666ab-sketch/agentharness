"""Phase-2 reliability tests: length/zero-output retry with widened budget,
budget-aware safe-boundary stop, and few-shot prompt behavior
(docs/agent-upgrade-2026-08-05.md section 2)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    Checkpoint,
    EffectKind,
    Message,
    MessageRole,
    ModelRequest,
    ModelStreamItem,
    ProviderRetryConfig,
    ReplayPolicy,
    RunRequest,
    RunStatus,
    StreamItemType,
    ToolCall,
    ToolContext,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
    ToolSpec,
    Usage,
    VerificationCandidate,
    VerificationCheck,
    VerificationDecision,
    VerificationPolicy,
    new_id,
)
from agentharness.engine.runtime import RunEngine
from agentharness.engine.verification import VerificationLoop
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    ProcurementAgent,
    _fake_run_profile,
)
from agentharness.procurement.service import ProcurementService
from agentharness.security.redaction import Redactor
from tests.fake_provider import FakeModelAdapter


class _PermissiveRedactor(Redactor):
    """No-op redactor; see test_context_compaction for rationale."""

    def redact_text(self, text: str) -> str:
        return text or ""

    def redact_public_text(self, text: str) -> str:
        return text or ""

    def redact_obj(self, obj):
        return obj

    def redact_public_obj(self, obj):
        return obj


class _SequenceProvider:
    def __init__(self, name: str, attempts: list[list[ModelStreamItem]]) -> None:
        self.name = name
        self.attempts = attempts
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        index = min(self.calls, len(self.attempts) - 1)
        self.calls += 1
        for item in self.attempts[index]:
            yield item


def _length_error() -> list[ModelStreamItem]:
    return [
        ModelStreamItem(
            type=StreamItemType.error,
            error="OpenAI Chat completion ended with length",
            error_kind="length",
        )
    ]


def _success(text: str) -> list[ModelStreamItem]:
    return [
        ModelStreamItem(type=StreamItemType.text_delta, text=text),
        ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=2, output_tokens=1, total_tokens=3),
        ),
        ModelStreamItem(type=StreamItemType.done),
    ]


@pytest.mark.asyncio
async def test_length_zero_output_retries_once_with_widened_budget(
    data_dir, workspace
) -> None:
    provider = _SequenceProvider("truncated", [_length_error(), _success("OK")])
    harness = Harness(data_dir=data_dir, providers={"truncated": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="retry length",
                provider="truncated",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
            )
        )
        events = harness.get_events(run_id=result.run_id, limit=1000)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert result.output == "OK"
    assert provider.calls == 2
    assert [attempt.status for attempt in result.usage.provider_attempts] == [
        "error",
        "completed",
    ]
    retry = next(event for event in events if str(event.type) == "provider_retry")
    assert retry.payload["error_kind"] == "length"
    assert retry.payload["next_attempt"] == 2
    assert retry.payload["output_budget_relaxed_to"] > 0


@pytest.mark.asyncio
async def test_persistent_length_failure_gives_chinese_actionable_message(
    data_dir, workspace
) -> None:
    provider = _SequenceProvider(
        "truncated-twice", [_length_error(), _length_error()]
    )
    harness = Harness(data_dir=data_dir, providers={"truncated-twice": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="retry length twice",
                provider="truncated-twice",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert provider.calls == 2
    assert result.error is not None
    assert "模型输出被截断" in result.error
    assert "调高输出预算" in result.error


class _LoopTool:
    name = "read_status"
    spec = ToolSpec(
        name="read_status",
        description="read status",
        parameters={"type": "object", "properties": {}},
        effect=EffectKind.pure,
    )

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        return ToolResult(tool_call_id="", name=self.name, content='{"status":"ok"}')


class _LoopToolProvider:
    name = "loop"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.calls += 1
        call_id = new_id()
        yield ModelStreamItem(
            type=StreamItemType.tool_call_start,
            tool_call_id=call_id,
            tool_name="read_status",
        )
        yield ModelStreamItem(
            type=StreamItemType.tool_call_delta,
            tool_call_id=call_id,
            tool_name="read_status",
            arguments_delta="{}",
        )
        yield ModelStreamItem(
            type=StreamItemType.tool_call_end,
            tool_call_id=call_id,
            tool_name="read_status",
            arguments={},
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=40, output_tokens=10, total_tokens=50),
        )
        yield ModelStreamItem(type=StreamItemType.done)


@pytest.mark.asyncio
async def test_token_budget_exhaustion_stops_at_safe_boundary(
    data_dir, workspace
) -> None:
    harness = Harness(data_dir=data_dir, providers={"loop": _LoopToolProvider()})
    harness.register_tool(_LoopTool())
    try:
        result = await harness.run(
            RunRequest(
                message="budget loop",
                provider="loop",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
                budget=BudgetConfig(
                    max_tokens=80,
                    max_context_tokens=16_000,
                    max_steps=20,
                    max_output_length=10_000,
                ),
            )
        )
        events = harness.get_events(run_id=result.run_id, limit=1000)
    finally:
        await harness.aclose()

    # Safe boundary stop, not a red-screen failure.
    assert result.status == RunStatus.budget_stopped
    assert result.error is not None
    assert "安全边界" in result.error
    assert "预算" in result.error
    warnings = [
        event for event in events if str(event.type) == "budget_warning"
    ]
    assert warnings, "budget degradation should emit a budget_warning event"
    assert warnings[0].payload["context_shrunk_to"] == 8_000


@pytest.mark.asyncio
async def test_step_budget_exhaustion_stops_at_safe_boundary(
    data_dir, workspace
) -> None:
    harness = Harness(data_dir=data_dir, providers={"loop": _LoopToolProvider()})
    harness.register_tool(_LoopTool())
    try:
        result = await harness.run(
            RunRequest(
                message="step budget loop",
                provider="loop",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
                budget=BudgetConfig(
                    max_steps=2,
                    max_tokens=200_000,
                    max_context_tokens=100_000,
                ),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.budget_stopped
    assert result.error is not None
    assert "回合数预算已用尽" in result.error

@pytest.mark.asyncio
async def test_few_shot_examples_in_procurement_system_prompt(data_dir) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        req = agent._run_request(
            request_id="a" * 32,
            session_id="b" * 32,
            message="采购测试",
            source="procurement_conversation",
        )
        assert req.system is not None
        assert "理想工具序列（few-shot）" in req.system
        assert "procurement_capture_requirement" in req.system
        assert "procurement_execute_analysis" in req.system
        assert "procurement_approve_supplier" in req.system
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------------------
# Independent evaluator: usage accounting and stream timeout
# ---------------------------------------------------------------------------


class _UsageEvaluator:
    name = "eval"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        del request
        self.calls += 1
        yield ModelStreamItem(
            type=StreamItemType.text_delta,
            text=json.dumps(
                {
                    "dimensions": {
                        name: {"score": 0.9}
                        for name in ("task_completion", "correctness", "completeness")
                    },
                    "confidence": 0.9,
                    "hard_safety_violation": False,
                    "failure_category": None,
                    "evidence": [],
                    "improvements": [],
                }
            ),
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cached_input_tokens=5,
            ),
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(
                input_tokens=50,
                output_tokens=10,
                total_tokens=60,
                cached_input_tokens=3,
            ),
        )
        yield ModelStreamItem(type=StreamItemType.done)


@pytest.mark.asyncio
async def test_ai_check_records_usage_in_evidence_and_charges_budget() -> None:
    """P2 regression: evaluator usage must reach the run budget ledger."""
    evaluator = _UsageEvaluator()
    candidate = VerificationCandidate(
        executor_provider="main",
        executor_adapter=object(),
        goal="build report",
        output="done",
    )
    policy = VerificationPolicy(
        validators=[VerificationCheck(kind="ai")],
        evaluator_provider="eval",
        evaluator_model="reviewer",
    )
    loop = VerificationLoop(evaluator_resolver=lambda name: evaluator)
    failure, evidence = await loop._ai_check(candidate, policy, policy.validators[0])
    assert failure is None
    usage = evidence["usage"]
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 180
    assert usage["cached_input_tokens"] == 8

    charged = Usage()
    RunEngine._charge_verification_usage(
        charged,
        VerificationDecision(action="pass", evidence={"0:ai": evidence}),
    )
    assert charged.input_tokens == 150
    assert charged.output_tokens == 30
    assert charged.total_tokens == 180
    assert charged.cached_input_tokens == 8


class _HangingEvaluator:
    name = "eval"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        del request
        await asyncio.sleep(3600)
        yield ModelStreamItem(type=StreamItemType.done)  # pragma: no cover


@pytest.mark.asyncio
async def test_ai_check_timeout_returns_evaluator_failed() -> None:
    """P2 regression: a hung evaluator stream must not block the run forever."""
    evaluator = _HangingEvaluator()
    candidate = VerificationCandidate(
        executor_provider="main",
        executor_adapter=object(),
        goal="build report",
        output="done",
    )
    policy = VerificationPolicy(
        validators=[VerificationCheck(kind="ai")],
        evaluator_provider="eval",
        evaluator_model="reviewer",
    )
    loop = VerificationLoop(evaluator_resolver=lambda name: evaluator)
    failure, evidence = await loop._ai_check(
        candidate, policy, policy.validators[0], timeout_s=0.05
    )
    assert failure is not None
    assert failure.error_code == "evaluator_failed"
    assert failure.retryable is False
    assert "timed out" in failure.message
    assert "usage" in evidence


# ---------------------------------------------------------------------------
# Tool retry budget is a hard cap across resumes
# ---------------------------------------------------------------------------


class _FlakyTool:
    name = "flaky"
    spec = ToolSpec(
        name="flaky",
        description="always fails retryably",
        parameters={"type": "object", "properties": {}},
        effect=EffectKind.pure,
        max_attempts=3,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        del ctx, arguments
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            name=self.name,
            content="flaky failure",
            is_error=True,
            error_code="tool_error",
            error_category="tool",
            retryable=True,
            recovery_hint="retry",
        )


@pytest.mark.asyncio
async def test_tool_retry_budget_is_hard_cap_across_resume(
    data_dir, workspace
) -> None:
    """P2 regression: a resume must not grant a fresh full retry window."""
    tool = _FlakyTool()
    harness = Harness(data_dir=data_dir, redactor=_PermissiveRedactor())
    harness.register_tool(tool)
    harness.register_provider("fake", FakeModelAdapter())
    session_id = harness.storage.create_session()
    run_id = new_id()
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider="fake",
        approval="auto",
        cwd=str(workspace),
    )
    user = Message(role=MessageRole.user, content="retry")
    harness.storage.save_message(run_id, session_id, user, seq=0)
    call = ToolCall(id="call-1", invocation_id="inv-1", name="flaky", arguments={})
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=[user],
            pending_tool_calls=[call],
            completed_tool_call_ids=[],
            usage=Usage(),
            status=RunStatus.interrupted,
        )
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id="inv-1",
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id="call-1",
            tool_name="flaky",
            status=ToolInvocationStatus.received,
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            arguments={},
            attempt_count=1,
        )
    )
    try:
        result = await harness.resume(run_id)
        invocation = harness.storage.get_tool_invocation("inv-1")
        attempts = harness.storage.list_tool_attempts("inv-1")
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert invocation is not None
    # max_attempts=3 with attempt_count=1 leaves exactly 2 attempts on resume.
    assert invocation.attempt_count == 3
    assert len(attempts) == 2
    assert tool.calls == 2
