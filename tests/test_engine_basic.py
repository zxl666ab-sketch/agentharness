import asyncio

import pytest

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    Checkpoint,
    EffectKind,
    RunRequest,
    RunStatus,
    ToolResult,
    ToolSpec,
    Usage,
)
from agentharness.engine.context import ContextBudgetError
from agentharness.storage.sqlite import Storage
from tests.fake_provider import FakeModelAdapter, create_test_harness


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
async def test_tool_read_file(harness, workspace):
    result = await harness.run(
        RunRequest(
            message='[fake:tools]read_file\n{"path": "README.md"}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert "hello workspace" in result.output or "Tool results" in result.output


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


class _GatedTool:
    """Pure-effect tool whose only governance signal is `ToolSpec.requires_approval`."""

    def __init__(self, *, requires_approval: bool) -> None:
        self.spec = ToolSpec(
            name="gated",
            description="A pure tool that still demands a human decision.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            requires_approval=requires_approval,
        )
        self.calls = 0

    async def run(self, ctx, arguments):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ToolResult(
            tool_call_id=str(ctx.metadata.get("tool_call_id") or ""),
            name=self.spec.name,
            content="gated tool ran",
        )


def _gated_harness(data_dir, workspace, tool, approvals):  # type: ignore[no-untyped-def]
    async def approve(request):  # type: ignore[no-untyped-def]
        approvals.append(request)
        return ApprovalDecision.allow_once

    harness = create_test_harness(
        data_dir=data_dir,
        tools={"gated": tool},
        providers={
            "fake": FakeModelAdapter(
                script=[
                    {"kind": "tools", "tools": [{"name": "gated", "arguments": {}}]},
                    {"kind": "text", "text": "finished"},
                ]
            )
        },
    )
    harness.set_approval_callback(approve)
    return harness


@pytest.mark.asyncio
async def test_spec_requires_approval_overrides_auto_mode(data_dir, workspace):
    """P-M8: `requires_approval` was a decorative field — auto mode ignored it."""
    approvals: list = []
    tool = _GatedTool(requires_approval=True)
    harness = _gated_harness(data_dir, workspace, tool, approvals)
    try:
        result = await harness.run(
            RunRequest(
                message="run the gated tool",
                provider="fake",
                approval=ApprovalMode.auto,  # ← auto 也必须问人
                cwd=str(workspace),
                tools=["gated"],
            )
        )
        assert result.status == RunStatus.completed
        assert tool.calls == 1
        assert [request.tool_name for request in approvals] == ["gated"]
        assert harness.list_approvals(result.run_id)[0]["decision"] == "allow_once"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_unflagged_pure_tool_stays_auto_approved(data_dir, workspace):
    """The gate must stay opt-in: an unflagged pure tool is not escalated."""
    approvals: list = []
    tool = _GatedTool(requires_approval=False)
    harness = _gated_harness(data_dir, workspace, tool, approvals)
    try:
        result = await harness.run(
            RunRequest(
                message="run the plain tool",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                tools=["gated"],
            )
        )
        assert result.status == RunStatus.completed
        assert tool.calls == 1
        assert approvals == []
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_resume_unexpected_failure_marks_run_failed(data_dir, workspace, monkeypatch):
    """P-H2: anything `resume()` raised left the run stuck in `running` forever."""
    harness = create_test_harness(data_dir=data_dir)
    try:
        session_id = harness.storage.create_session("resumable")
        harness.storage.create_run(
            run_id="resume-run",
            session_id=session_id,
            root_run_id="resume-run",
            status=RunStatus.interrupted,
            provider="fake",
            approval=ApprovalMode.ask.value,
            cwd=str(workspace),
        )
        harness.storage.save_checkpoint(
            Checkpoint(
                run_id="resume-run",
                phase="model_turn",
                step=0,
                messages=[],
                status=RunStatus.interrupted,
                usage=Usage(),
            )
        )

        async def exploding_loop(**_kwargs):  # type: ignore[no-untyped-def]
            raise ContextBudgetError("上下文预算耗尽")

        monkeypatch.setattr(harness.engine, "_loop", exploding_loop)
        result = await harness.resume("resume-run")

        assert result.status == RunStatus.failed
        assert "上下文预算耗尽" in (result.error or "")
        run = harness.storage.get_run("resume-run")
        assert run is not None
        assert run["status"] == RunStatus.failed.value  # 绝不允许停在 running
        assert run["error"] == "上下文预算耗尽"
        assert harness.engine._runs == {}
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_workspace_roots_bound_explicit_run_cwd(data_dir, workspace):
    """P-M7: the launcher's authorized roots are enforced, not just printed."""
    harness = create_test_harness(data_dir=data_dir)
    harness.workspace_roots = [workspace]
    try:
        with pytest.raises(ValueError, match="outside the authorized roots"):
            await harness.run(
                RunRequest(
                    message="[fake:text]escaped",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(data_dir),
                )
            )
        assert harness.list_runs() == []  # 拒绝发生在建形之前

        inside = await harness.run(
            RunRequest(
                message="[fake:text]inside",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        assert inside.status == RunStatus.completed
    finally:
        await harness.aclose()
