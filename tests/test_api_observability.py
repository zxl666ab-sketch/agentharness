"""Readonly run-observability API used by the inspector."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import ApprovalDecision, ApprovalMode, RunRequest
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor


@pytest.mark.asyncio
async def test_run_observability_endpoints_expose_redacted_execution_details(
    data_dir, workspace
):
    secret = "SECRET_OBSERVABILITY_SENTINEL_11223"
    harness = Harness(
        data_dir=data_dir,
        redactor=Redactor(extra_sentinels=[secret]),
    )

    async def approve(_request):
        return ApprovalDecision.allow_once

    harness.set_approval_callback(approve)
    try:
        result = await harness.run(
            RunRequest(
                message=(
                    "[fake:tools]write_file\n"
                    + json.dumps(
                        {"path": "observed.txt", "content": f"visible {secret}"}
                    )
                ),
                provider="fake",
                approval=ApprovalMode.ask,
                cwd=str(workspace),
            )
        )
        app = create_app(harness=harness)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            messages = await client.get(f"/api/runs/{result.run_id}/messages")
            approvals = await client.get(f"/api/runs/{result.run_id}/approvals")
            checkpoint = await client.get(f"/api/runs/{result.run_id}/checkpoint")
            missing = await client.get("/api/runs/missing/checkpoint")
            writes = [
                await client.post(f"/api/runs/{result.run_id}/messages"),
                await client.put(f"/api/runs/{result.run_id}/approvals"),
                await client.delete(f"/api/runs/{result.run_id}/checkpoint"),
            ]
    finally:
        harness.close()

    assert messages.status_code == 200
    message_rows = messages.json()
    assistant = next(row for row in message_rows if row["role"] == "assistant")
    tool_call = assistant["tool_calls"][0]
    assert tool_call["name"] == "write_file"
    assert tool_call["arguments"]["path"] == "observed.txt"
    tool_result = next(row for row in message_rows if row["role"] == "tool")
    assert "Wrote" in tool_result["content"]

    assert approvals.status_code == 200
    approval_rows = approvals.json()
    assert approval_rows[0]["tool_name"] == "write_file"
    assert approval_rows[0]["decision"] == "allow_once"

    assert checkpoint.status_code == 200
    checkpoint_body = checkpoint.json()
    assert checkpoint_body["phase"] == "terminal"
    assert checkpoint_body["status"] == "completed"
    assert "usage" in checkpoint_body
    assert "completed_tool_call_ids" in checkpoint_body
    assert missing.status_code == 404
    assert all(response.status_code == 405 for response in writes)

    combined = messages.text + approvals.text + checkpoint.text
    assert secret not in combined
    assert "REDACTED" in combined
