"""Phase-2 reliability tests: length/zero-output retry with widened budget,
budget-aware safe-boundary stop, and few-shot prompt behavior
(docs/agent-upgrade-2026-08-05.md section 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    EffectKind,
    ModelRequest,
    ModelStreamItem,
    ProviderRetryConfig,
    RunRequest,
    RunStatus,
    StreamItemType,
    ToolContext,
    ToolResult,
    ToolSpec,
    Usage,
    new_id,
)
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    ProcurementAgent,
    _fake_run_profile,
)
from agentharness.procurement.service import ProcurementService


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
