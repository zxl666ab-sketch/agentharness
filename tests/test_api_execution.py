import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import (
    ApprovalMode,
    EffectKind,
    ReplayPolicy,
    RunStatus,
    ToolInvocationRecord,
    ToolInvocationStatus,
)
from agentharness.engine.tool_execution import arguments_sha256
from agentharness.harness import Harness
from tests.fake_provider import FakeModelAdapter


def _web_harness(data_dir, adapter: FakeModelAdapter | None = None) -> Harness:
    return Harness(
        data_dir=data_dir,
        providers={"openai": adapter or FakeModelAdapter()},
    )


async def _wait_for_status(
    client: AsyncClient,
    run_id: str,
    expected: set[str],
    *,
    timeout: float = 5.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/runs/{run_id}")
        if response.status_code == 200:
            last = response.json()
            if last.get("status") in expected:
                return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected}; last={last}")


@pytest.mark.asyncio
async def test_web_starts_background_run_and_returns_persisted_identity(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        runtime = await client.get("/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["execution_enabled"] is True
        assert runtime.json()["default_provider"] == "openai"
        assert runtime.json()["providers"] == [
            {"name": "openai", "configured": True, "default_model": None}
        ]
        assert runtime.json()["workspaces"] == [{"id": "default", "name": workspace.name}]

        response = await client.post(
            "/api/runs",
            json={
                "message": "[fake:text]created from web",
                "provider": "openai",
            },
        )
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["run_id"]
        assert accepted["session_id"]
        assert accepted["run"]["metadata_json"]

        row = await _wait_for_status(client, accepted["run_id"], {"completed"})
        assert row["provider"] == "openai"
        metadata = json.loads(row["metadata_json"])
        assert metadata["source"] == "web"
        messages = (await client.get(f"/api/runs/{accepted['run_id']}/messages")).json()
        assert any(message["content"] == "created from web" for message in messages)

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_approval_unblocks_governed_write(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": (
                    '[fake:tools]write_file\n'
                    '{"path":"from-web.txt","content":"approved"}'
                ),
                "approval": "ask",
                "allow_write": True,
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"waiting_approval"})

        approvals = (await client.get(f"/api/runs/{run_id}/approvals")).json()
        pending = next(item for item in approvals if item["decision"] is None)
        assert pending["requires_confirmation"] is False
        mismatched = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_once",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": "0" * 64,
            },
        )
        assert mismatched.status_code == 409
        decision = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_once",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": pending["arguments_sha256"],
            },
        )
        assert decision.status_code == 200
        assert decision.json()["run_id"] == run_id

        await _wait_for_status(client, run_id, {"completed"})
        assert (workspace / "from-web.txt").read_text(encoding="utf-8") == "approved"
        duplicate = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_once",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": pending["arguments_sha256"],
            },
        )
        assert duplicate.status_code == 409

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_exposes_and_enforces_one_time_confirmation(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": (
                    "[fake:tools]memory_store\n"
                    '{"content":"confirmed through the Web"}'
                ),
                "approval": "ask",
                "allow_write": True,
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"waiting_approval"})

        approvals = (await client.get(f"/api/runs/{run_id}/approvals")).json()
        pending = next(item for item in approvals if item["decision"] is None)
        assert pending["tool_name"] == "memory_store"
        assert pending["requires_confirmation"] is True

        run_wide = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_run",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": pending["arguments_sha256"],
            },
        )
        assert run_wide.status_code == 400

        allow_once = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_once",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": pending["arguments_sha256"],
            },
        )
        assert allow_once.status_code == 200
        await _wait_for_status(client, run_id, {"completed"})

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_run_cannot_escape_configured_workspace(data_dir, workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        traversal = await client.post(
            "/api/runs",
            json={"message": "no", "cwd": "../outside"},
        )
        absolute = await client.post(
            "/api/runs",
            json={"message": "no", "cwd": str(outside)},
        )
        provider_override = await client.post(
            "/api/runs",
            json={"message": "no", "provider": "missing"},
        )

        assert traversal.status_code == 400
        assert absolute.status_code == 400
        assert provider_override.status_code == 422
        assert harness.list_runs() == []

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_can_cancel_and_resume_run(data_dir, workspace):
    harness = _web_harness(
        data_dir,
        FakeModelAdapter(script=[{"kind": "sleep", "seconds": 10}]),
    )
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={"message": "wait"},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"running"})

        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        await _wait_for_status(client, run_id, {"cancelled"})

        resumed = await client.post(
            f"/api/runs/{run_id}/resume",
            json={"input": "[fake:text]resumed from web"},
        )
        assert resumed.status_code == 202
        completed = await _wait_for_status(client, run_id, {"completed"})
        assert completed["error"] is None
        assert completed["finished_at"] is not None

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_resolves_indeterminate_tool_with_hash_bound_cas(data_dir, workspace):
    harness = _web_harness(data_dir)
    run_id = "web-tool-recovery"
    session_id = harness.storage.create_session()
    argument_hash = arguments_sha256({"command": "external-write"})
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.require_human,
        provider="openai",
        approval=ApprovalMode.auto.value,
        cwd=str(workspace),
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id="web-indeterminate-invocation",
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id="provider-call",
            tool_name="shell",
            status=ToolInvocationStatus.indeterminate,
            effect=EffectKind.destructive,
            replay_policy=ReplayPolicy.never,
            arguments={"command": "external-write"},
            arguments_sha256=argument_hash,
            error_code="outcome_indeterminate",
            error_category="recovery",
        )
    )
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        stale = await client.post(
            "/api/tool-invocations/web-indeterminate-invocation/resolution",
            json={"decision": "skip", "arguments_sha256": "0" * 64},
        )
        assert stale.status_code == 409

        resolved = await client.post(
            "/api/tool-invocations/web-indeterminate-invocation/resolution",
            json={"decision": "skip", "arguments_sha256": argument_hash},
        )
        assert resolved.status_code == 200
        assert resolved.json()["decision"] == "skip"

        duplicate = await client.post(
            "/api/tool-invocations/web-indeterminate-invocation/resolution",
            json={"decision": "retry", "arguments_sha256": argument_hash},
        )
        assert duplicate.status_code == 409

    await app.state.run_supervisor.aclose()
    await harness.aclose()
