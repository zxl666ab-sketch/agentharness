"""Readonly run-observability API used by the inspector."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    Message,
    MessageRole,
    RunRequest,
    RunStatus,
)
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor


@pytest.mark.asyncio
async def test_runs_list_includes_summary_depth_and_child_count_without_n_plus_one(
    data_dir, workspace
):
    harness = Harness(data_dir=data_dir)
    session_id = harness.storage.create_session(title="fixture")
    harness.storage.create_run(
        run_id="parent-run",
        session_id=session_id,
        root_run_id="parent-run",
        status=RunStatus.completed,
        provider="fake",
        approval="auto",
        cwd=str(workspace),
    )
    harness.storage.save_message(
        "parent-run",
        session_id,
        Message(role=MessageRole.user, content="Inspect the release trace"),
        1,
    )
    harness.storage.create_run(
        run_id="child-run",
        session_id=session_id,
        root_run_id="parent-run",
        parent_run_id="parent-run",
        status=RunStatus.failed,
        provider="fake",
        approval="auto",
        cwd=str(workspace),
        delegate_depth=1,
    )
    harness.storage.save_message(
        "child-run",
        session_id,
        Message(role=MessageRole.user, content="Check the child step"),
        1,
    )
    try:
        app = create_app(harness=harness)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/runs?limit=1&offset=0")
            second_page = await client.get("/api/runs?limit=1&offset=1")
    finally:
        harness.close()

    assert response.status_code == 200
    assert second_page.status_code == 200
    rows = response.json() + second_page.json()
    by_id = {row["id"]: row for row in rows}
    assert by_id["parent-run"]["user_summary"] == "Inspect the release trace"
    assert by_id["parent-run"]["depth"] == 0
    assert by_id["parent-run"]["child_count"] == 1
    assert by_id["child-run"]["user_summary"] == "Check the child step"
    assert by_id["child-run"]["depth"] == 1
    assert by_id["child-run"]["child_count"] == 0


@pytest.mark.asyncio
async def test_sse_defaults_to_new_events_and_last_event_id_advances_query_cursor(
    data_dir, workspace
):
    harness = Harness(data_dir=data_dir)
    result = await harness.run(
        RunRequest(
            message="[fake:text]history",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    maximum = harness.storage.max_global_seq()
    try:
        app = create_app(harness=harness)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            fresh = await client.get(
                "/api/stream",
                headers={"x-test-short-stream": "1"},
                timeout=5.0,
            )
            resumed = await client.get(
                "/api/stream?after=0",
                headers={
                    "Last-Event-ID": str(maximum - 1),
                    "x-test-short-stream": "1",
                },
                timeout=5.0,
            )
            replay = await client.get(
                "/api/stream?after=0",
                headers={"x-test-short-stream": "1"},
                timeout=5.0,
            )
    finally:
        harness.close()

    assert result.run_id
    assert "data:" not in fresh.text
    assert f"id: {maximum}\n" in resumed.text
    assert "id: 1\n" not in resumed.text
    assert "id: 1\n" in replay.text


@pytest.mark.asyncio
async def test_tool_result_args_summary_matches_shared_summarizer(data_dir, workspace):
    """Goal 3: the arguments_summary in tool_result events is produced by the shared
    summarizer, so it matches byte-for-byte what the web summarizeArgs() twin renders."""
    from agentharness.tools.summary import summarize_tool_arguments

    harness = Harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(
                message=(
                    "[fake:tools]read_file\n" + json.dumps({"path": "notes.md"})
                ),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        app = create_app(harness=harness)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            events = (
                await client.get(f"/api/runs/{result.run_id}/events?limit=5000")
            ).json()
            messages = (
                await client.get(f"/api/runs/{result.run_id}/messages")
            ).json()
    finally:
        harness.close()

    # Locate the tool_call_end event carrying the summary the frontend renders.
    tool_ends = [e for e in events if e["type"] == "tool_call_end"]
    assert tool_ends, "expected a tool_call_end event"
    payload = tool_ends[0]["payload"]
    assert "arguments_summary" in payload

    # The same arguments recorded on the assistant message must reproduce that summary
    # through the shared Python summarizer (identical algorithm to web summarizeArgs).
    assistant = next(m for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
    args = assistant["tool_calls"][0]["arguments"]
    expected = summarize_tool_arguments(args)
    assert payload["arguments_summary"] == expected
    assert "path=notes.md" in expected


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
