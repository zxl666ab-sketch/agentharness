import asyncio
import json
import sys

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
        report = (await client.get(f"/api/runs/{accepted['run_id']}/report")).json()
        assert report["conclusion"] == {
            "status": "unverified",
            "label": "运行结束",
            "verified": False,
            "reason": "该历史运行没有配置验收规则，因此不标记为已完成。",
        }
        assert report["verification"]["configured"] is False

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_maps_acceptance_rules_and_reports_persisted_evidence(
    data_dir, workspace
):
    (workspace / "proof.txt").write_text("file proof", encoding="utf-8")
    command = f'"{sys.executable}" -c "print(\'command-proof\')"'
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": "[fake:text]accepted output",
                "allow_write": True,
                "verification": {
                    "output": {
                        "contains": ["accepted"],
                        "not_contains": ["rejected"],
                    },
                    "files": [
                        {"path": "proof.txt", "exists": True, "contains": ["file proof"]}
                    ],
                    "commands": [
                        {"command": command, "contains": ["command-proof"]}
                    ],
                    "max_retries": 0,
                    "on_failure": "failed",
                },
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"waiting_approval"})
        approvals = (await client.get(f"/api/runs/{run_id}/approvals")).json()
        pending = next(item for item in approvals if item["status"] == "pending")
        assert pending["tool_name"] == "shell"
        decision = await client.post(
            f"/api/approvals/{pending['id']}/decision",
            json={
                "decision": "allow_once",
                "invocation_id": pending["invocation_id"],
                "arguments_sha256": pending["arguments_sha256"],
            },
        )
        assert decision.status_code == 200
        await _wait_for_status(client, run_id, {"completed"})

        report_response = await client.get(f"/api/runs/{run_id}/report")
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["conclusion"]["status"] == "passed"
        assert report["conclusion"]["verified"] is True
        assert [item["kind"] for item in report["verification"]["policy"]["validators"]] == [
            "output",
            "file",
            "command",
        ]
        assert report["verification"]["attempts"][0]["passed"] is True
        assert set(report["verification"]["attempts"][0]["evidence"]) == {
            "0:output",
            "1:file",
            "2:command",
        }
        assert len(report["tools"]) == 1
        assert report["tools"][0]["tool_name"] == "shell"
        assert len(report["approvals"]) == 1
        assert report["approvals"][0]["decision"] == "allow_once"
        assert report["usage"]["total_tokens"] > 0
        assert len(report["evidence_sha256"]) == 64

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_failed_acceptance_report_survives_runtime_restart(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": "[fake:text]wrong output",
                "verification": {
                    "output": {"contains": ["required marker"]},
                    "max_retries": 0,
                    "on_failure": "failed",
                },
            },
        )
        run_id = response.json()["run_id"]
        failed = await _wait_for_status(client, run_id, {"failed"})
        assert "verification failed" in failed["error"]
        before = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert before["conclusion"]["status"] == "failed"
        assert before["verification"]["attempts"][0]["passed"] is False
        assert before["verification"]["failure_reasons"] == [
            "output is missing required text: ['required marker']"
        ]

    await app.state.run_supervisor.aclose()
    await harness.aclose()

    restored_harness = _web_harness(data_dir)
    restored_app = create_app(harness=restored_harness, workspace_roots=[workspace])
    restored_transport = ASGITransport(app=restored_app)
    async with AsyncClient(
        transport=restored_transport, base_url="http://test"
    ) as restored_client:
        restored_run = (await restored_client.get(f"/api/runs/{run_id}")).json()
        after = (await restored_client.get(f"/api/runs/{run_id}/report")).json()
        assert restored_run["status"] == "failed"
        assert after["conclusion"] == before["conclusion"]
        assert after["verification"] == before["verification"]
        assert after["evidence_sha256"] == before["evidence_sha256"]

    await restored_app.state.run_supervisor.aclose()
    await restored_harness.aclose()


@pytest.mark.asyncio
async def test_web_can_route_exhausted_acceptance_to_human_review(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": "[fake:text]not accepted",
                "verification": {
                    "output": {"contains": ["required"]},
                    "max_retries": 0,
                    "on_failure": "require_human",
                },
            },
        )
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"require_human"})
        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert report["conclusion"]["status"] == "needs_review"
        assert report["conclusion"]["label"] == "需要人工处理"
        assert report["verification"]["attempts"][0]["action"] == "require_human"

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_run_report_links_persisted_tool_artifacts(data_dir, workspace):
    (workspace / "large.txt").write_text("artifact evidence\n" * 1_000, encoding="utf-8")
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs",
            json={
                "message": '[fake:tools]read_file\n{"path":"large.txt"}',
            },
        )
        run_id = response.json()["run_id"]
        await _wait_for_status(client, run_id, {"completed"})
        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        artifact_id = report["tools"][0]["result"]["artifact_id"]
        assert artifact_id
        linked = next(item for item in report["artifacts"] if item["id"] == artifact_id)
        assert linked["sha256"]
        artifact = await client.get(f"/api/artifacts/{artifact_id}")
        assert artifact.status_code == 200
        assert "artifact evidence" in artifact.json()["content"]

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_web_rejects_unsafe_or_ungoverned_acceptance_rules(data_dir, workspace):
    harness = _web_harness(data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.post(
            "/api/runs",
            json={"message": "no", "verification": {"output": {}}},
        )
        traversal = await client.post(
            "/api/runs",
            json={
                "message": "no",
                "verification": {"files": [{"path": "../outside.txt"}]},
            },
        )
        command_without_write = await client.post(
            "/api/runs",
            json={
                "message": "no",
                "verification": {"commands": [{"command": "echo proof"}]},
            },
        )

        assert empty.status_code == 422
        assert traversal.status_code == 422
        assert command_without_write.status_code == 400
        assert harness.list_runs() == []

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
        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert report["workspace_changes"][0]["path"] == "from-web.txt"
        assert report["workspace_changes"][0]["changed"] is True
        assert len(report["workspace_changes"][0]["resulting_version"]) == 64
        assert report["tools"][0]["tool_name"] == "write_file"
        assert report["approvals"][0]["decision"] == "allow_once"
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
