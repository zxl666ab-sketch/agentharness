from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    EffectKind,
    ModelRequest,
    ModelStreamItem,
    PricingConfig,
    RunRequest,
    RunStatus,
    StreamItemType,
    ToolContext,
    ToolResult,
    ToolSpec,
    Usage,
)
from agentharness.tools.shell import kill_process_tree
from tests.fake_provider import create_test_harness


class _SlowProvider:
    name = "slow"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        await asyncio.sleep(0.2)
        yield ModelStreamItem(type=StreamItemType.done)


class _TokenProvider:
    name = "tokens"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.requests.append(request)
        yield ModelStreamItem(type=StreamItemType.text_delta, text="ok")
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=3, output_tokens=3, total_tokens=6),
        )
        yield ModelStreamItem(type=StreamItemType.done)


class _ToolProvider:
    name = "tool-provider"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        yield ModelStreamItem(
            type=StreamItemType.tool_call_start,
            tool_call_id="slow-call",
            tool_name="slow_tool",
        )
        yield ModelStreamItem(
            type=StreamItemType.tool_call_end,
            tool_call_id="slow-call",
            tool_name="slow_tool",
            arguments={},
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )
        yield ModelStreamItem(type=StreamItemType.done)


class _ManyDeltaProvider:
    name = "many-deltas"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        for _ in range(20_000):
            yield ModelStreamItem(type=StreamItemType.text_delta, text="x")
        yield ModelStreamItem(type=StreamItemType.done)


class _SlowTool:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="slow_tool", description="slow", effect=EffectKind.pure)

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        self.started.set()
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ToolResult(tool_call_id="", name="slow_tool", content="done")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_tool_calls_per_turn", 17),
        ("max_tool_calls", 129),
        ("max_concurrent_tools", 5),
        ("max_tool_argument_bytes", 262_145),
        ("max_tool_result_bytes", 1_048_577),
        ("max_inline_tool_result_bytes", 4_097),
    ],
)
def test_tool_governance_caps_cannot_be_raised(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        BudgetConfig(**{field: value})


@pytest.mark.asyncio
async def test_wall_time_interrupts_single_slow_provider_stream(
    data_dir: Path, workspace: Path
):
    harness = create_test_harness(data_dir=data_dir, providers={"slow": _SlowProvider()})
    started = time.monotonic()
    try:
        result = await harness.run(
            RunRequest(
                message="slow",
                provider="slow",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_wall_time_s=0.01),
            )
        )
    finally:
        harness.close()

    assert time.monotonic() - started < 0.15
    assert result.status == RunStatus.failed
    assert result.error == "max_wall_time exceeded"


@pytest.mark.asyncio
async def test_wall_time_cancels_slow_tool_batch(data_dir: Path, workspace: Path):
    tool = _SlowTool()
    harness = create_test_harness(
        data_dir=data_dir,
        providers={"tool-provider": _ToolProvider()},
        tools={"slow_tool": tool},
    )
    started = time.monotonic()
    try:
        result = await harness.run(
            RunRequest(
                message="use tool",
                provider="tool-provider",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                # Leave enough time for SQLite/event setup so this specifically
                # exercises cancellation after the tool has started.
                budget=BudgetConfig(max_wall_time_s=0.1),
            )
        )
    finally:
        harness.close()

    assert time.monotonic() - started < 0.25
    assert result.status == RunStatus.failed
    assert result.error == "max_wall_time exceeded"
    assert tool.started.is_set()
    assert tool.cancelled.is_set()


@pytest.mark.asyncio
async def test_wall_time_kills_real_shell_process(data_dir: Path, workspace: Path):
    harness = create_test_harness(data_dir=data_dir)

    async def approve(_request):
        from agentharness.contracts import ApprovalDecision

        return ApprovalDecision.allow_once

    harness.set_approval_callback(approve)
    python = sys.executable.replace("\\", "/")
    command = f'"{python}" -c "import time; time.sleep(30)"'
    request = RunRequest(
        message=f"[fake:tools]shell\n{json.dumps({'command': command})}",
        provider="fake",
        approval=ApprovalMode.auto,
        cwd=str(workspace),
        budget=BudgetConfig(max_wall_time_s=0.2),
    )
    task = asyncio.create_task(harness.run(request))
    process = None
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and process is None:
            for processes in harness.engine._active_processes.values():
                if processes:
                    process = processes[0]
                    break
            await asyncio.sleep(0.01)

        assert process is not None, "shell process did not start before the wall-time deadline"
        result = await asyncio.wait_for(task, timeout=5.0)
        assert result.status == RunStatus.failed
        assert result.error == "max_wall_time exceeded"
        assert process.returncode is not None
    finally:
        if process is not None and process.returncode is None:
            await kill_process_tree(process)
        if not task.done():
            task.cancel()
        harness.close()


@pytest.mark.asyncio
async def test_output_length_cannot_complete_over_limit(data_dir: Path, workspace: Path):
    harness = create_test_harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:text]TOO_LONG",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_output_length=5),
            )
        )
    finally:
        harness.close()

    assert result.status == RunStatus.failed
    assert result.error == "max_output_length exceeded"
    assert len(result.output) <= 5


@pytest.mark.asyncio
async def test_token_limit_is_passed_to_provider_and_enforced(
    data_dir: Path, workspace: Path
):
    provider = _TokenProvider()
    harness = create_test_harness(data_dir=data_dir, providers={"tokens": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="tokens",
                provider="tokens",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_tokens=5),
            )
        )
    finally:
        harness.close()

    assert provider.requests[0].max_tokens == 5
    # Providers may ignore max_tokens, but an over-budget result is never accepted.
    assert result.status == RunStatus.failed
    assert result.error == "max_tokens exceeded"
    assert result.output == "ok"
    assert result.usage is not None
    assert result.usage.total_tokens == 6


@pytest.mark.asyncio
async def test_strict_cost_budget_rejects_unknown_pricing_before_provider_call(
    data_dir: Path, workspace: Path
) -> None:
    provider = _TokenProvider()
    harness = create_test_harness(data_dir=data_dir, providers={"tokens": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="unknown price",
                provider="tokens",
                cwd=str(workspace),
                budget=BudgetConfig(max_cost_usd=1.0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert "requires known" in (result.error or "")
    assert provider.requests == []
    assert result.usage.cost_status == "unknown"


@pytest.mark.asyncio
async def test_known_pricing_records_estimated_cost(
    data_dir: Path, workspace: Path
) -> None:
    provider = _TokenProvider()
    harness = create_test_harness(data_dir=data_dir, providers={"tokens": provider})
    pricing = PricingConfig(
        input_per_million_usd=1.0,
        output_per_million_usd=2.0,
    )
    try:
        result = await harness.run(
            RunRequest(
                message="known price",
                provider="tokens",
                cwd=str(workspace),
                pricing=pricing,
                budget=BudgetConfig(max_cost_usd=1.0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert result.usage.cost_status == "estimated"
    expected = (
        result.usage.input_tokens * 1.0 + result.usage.output_tokens * 2.0
    ) / 1_000_000
    assert result.usage.estimated_cost_usd == pytest.approx(expected)
    assert result.usage.provider_attempts[-1].estimated_cost_usd is not None


@pytest.mark.asyncio
async def test_many_small_stream_deltas_remain_linear_time(
    data_dir: Path, workspace: Path
):
    harness = create_test_harness(
        data_dir=data_dir,
        providers={"many-deltas": _ManyDeltaProvider()},
    )
    started = time.monotonic()
    try:
        result = await harness.run(
            RunRequest(
                message="many deltas",
                provider="many-deltas",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_output_length=25_000),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert len(result.output) == 20_000
    assert time.monotonic() - started < 3
