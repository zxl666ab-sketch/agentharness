import asyncio

import pytest

from agentharness.contracts import ApprovalMode, RunRequest, RunStatus
from agentharness.providers.fake import FakeModelAdapter
from agentharness.storage.sqlite import Storage


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
