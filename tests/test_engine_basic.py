import asyncio

import pytest

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    BudgetConfig,
    EffectKind,
    RunRequest,
    RunStatus,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from agentharness.harness import Harness
from agentharness.storage.sqlite import Storage
from tests.fake_provider import FakeModelAdapter


@pytest.mark.asyncio
async def test_simple_echo_run(harness, workspace):
    result = await harness.run(
        RunRequest(
            message="[fake:text]Hello from agent",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert "Hello from agent" in result.output
    run = harness.get_run(result.run_id)
    assert run is not None
    assert run["status"] == "completed"
    events = harness.get_events(run_id=result.run_id)
    types = [e.type for e in events]
    assert "run_started" in types
    assert "run_completed" in types


@pytest.mark.asyncio
async def test_external_stop_interrupts_blocked_provider_stream(
    harness, workspace, data_dir
):
    harness.register_provider(
        "fake", FakeModelAdapter(script=[{"kind": "sleep", "seconds": 30.0}])
    )
    task = asyncio.create_task(
        harness.run(
            RunRequest(
                message="provider blocks until externally cancelled",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    )

    run_id = None
    for _ in range(100):
        rows = harness.list_runs()
        if rows and rows[0]["status"] == RunStatus.running.value:
            run_id = rows[0]["id"]
            break
        await asyncio.sleep(0.01)
    assert run_id is not None

    external = Storage(data_dir)
    try:
        external.request_stop(run_id, "cancel")
    finally:
        external.close()

    result = await asyncio.wait_for(task, timeout=2.0)
    assert result.status == RunStatus.cancelled
    assert harness.storage.get_stop_request(run_id) is None

class _WriteTool:
    name = "write_file"
    spec = ToolSpec(
        name="write_file",
        description="write a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        effect=EffectKind.destructive,
    )

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        return ToolResult(tool_call_id="", name=self.name, content="written")


@pytest.mark.asyncio
async def test_wall_clock_pauses_while_waiting_for_human_approval(
    data_dir, workspace
) -> None:
    """Time spent waiting at the human approval gate must not consume the run's
    max_wall_time budget: a slow buyer must not turn a waiting_approval run into
    a failed 'max_wall_time exceeded' run."""

    async def delayed_approval(req):
        await asyncio.sleep(0.6)
        return ApprovalDecision.allow_once

    harness = Harness(data_dir=data_dir)
    harness.register_tool(_WriteTool())
    harness.set_approval_callback(delayed_approval)
    harness.register_provider(
        "fake",
        FakeModelAdapter(
            script=[
                {
                    "kind": "tools",
                    "tools": [{"name": "write_file", "arguments": {"path": "a.txt"}}],
                }
            ]
        ),
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]write_file",
                provider="fake",
                approval=ApprovalMode.ask,
                cwd=str(workspace),
                budget=BudgetConfig(max_wall_time_s=0.3),
            )
        )
    finally:
        await harness.aclose()

    # The approval wait alone (0.6s) exceeds the 0.3s budget; only active work
    # is charged, so the run must complete instead of failing on wall time.
    assert result.status == RunStatus.completed
    assert result.error is None

