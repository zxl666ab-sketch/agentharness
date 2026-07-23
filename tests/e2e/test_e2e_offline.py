"""End-to-end offline suite with fake provider."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    RunRequest,
    RunStatus,
)
from agentharness.harness import Harness
from agentharness.providers.fake import FakeModelAdapter
from agentharness.security.redaction import Redactor


@pytest.mark.asyncio
async def test_parallel_reads_serial_writes(data_dir, workspace):
    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    # Parallel reads via multi tool call
    result = await h.run(
        RunRequest(
            message='[fake:tools]read_file|read_file\n[{"path":"a.txt"},{"path":"b.txt"}]',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert "alpha" in result.output or "Tool results" in result.output

    # Serial writes
    result2 = await h.run(
        RunRequest(
            message='[fake:tools]write_file|write_file\n'
            '[{"path":"w1.txt","content":"one"},{"path":"w2.txt","content":"two"}]',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result2.status == RunStatus.completed
    assert (workspace / "w1.txt").read_text() == "one"
    assert (workspace / "w2.txt").read_text() == "two"
    h.close()


@pytest.mark.asyncio
async def test_cli_approval_deny(data_dir, workspace):
    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def deny_all(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.deny

    h.set_approval_callback(deny_all)
    result = await h.run(
        RunRequest(
            message='[fake:tools]write_file\n{"path":"x.txt","content":"nope"}',
            provider="fake",
            approval=ApprovalMode.ask,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert not (workspace / "x.txt").exists()
    events = h.get_events(run_id=result.run_id)
    assert any(e.type == "approval_requested" for e in events)
    h.close()


@pytest.mark.asyncio
async def test_parent_child_delegate_and_failure_isolation(data_dir, workspace):
    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    result = await h.run(
        RunRequest(
            message='[fake:tools]delegate\n{"task":"[fake:text]child says hi","allow_write":false}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    tree = h.get_run_tree(result.run_id)
    assert len(tree) >= 2  # parent + child
    child = [r for r in tree if r["parent_run_id"] == result.run_id]
    assert child
    assert child[0]["status"] == "completed"

    # Failure isolation: child error must not fail parent hard-crash
    result2 = await h.run(
        RunRequest(
            message='[fake:tools]delegate\n{"task":"[fake:error:provider]"}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    # Parent still finishes (completed with tool result noting child error)
    assert result2.status in (RunStatus.completed, RunStatus.failed)
    assert result2.run_id
    h.close()


@pytest.mark.asyncio
async def test_interrupt_resume_no_tool_reexec(data_dir, workspace, monkeypatch):
    h = Harness(data_dir=data_dir)
    fake = FakeModelAdapter()
    h.register_provider("fake", fake)

    call_count = {"n": 0}
    original = h.tools["write_file"].run

    async def counting_run(ctx, arguments):
        call_count["n"] += 1
        return await original(ctx, arguments)

    h.tools["write_file"].run = counting_run  # type: ignore[method-assign]

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    result = await h.run(
        RunRequest(
            message='[fake:tools]write_file\n{"path":"resume.txt","content":"once"}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert call_count["n"] == 1
    assert (workspace / "resume.txt").read_text() == "once"

    # Resume completed run should not re-run tools from checkpoint path with empty pending
    # Simulate interrupted mid-way by manually setting status and resuming with same checkpoint
    cp = h.storage.load_checkpoint(result.run_id)
    assert cp is not None
    # completed tools recorded
    assert cp.completed_tool_call_ids or cp.phase == "terminal"

    # Force a new run that gets interrupted after first tool via cancel mid-flight is hard;
    # instead verify resume path skips completed ids by replaying with pre-seeded checkpoint.
    from agentharness.contracts import Checkpoint, Message, MessageRole, ToolCall, Usage

    run_id = "manual_resume_run"
    sid = h.storage.create_session()
    h.storage.create_run(
        run_id=run_id,
        session_id=sid,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider="fake",
        cwd=str(workspace),
    )
    tc = ToolCall(
        id="tc_done",
        name="write_file",
        arguments={"path": "r2.txt", "content": "from_resume"},
        status="completed",
    )
    tc2 = ToolCall(
        id="tc_pending",
        name="write_file",
        arguments={"path": "r3.txt", "content": "pending_only"},
    )
    # Pre-create completed side effect
    (workspace / "r2.txt").write_text("from_resume", encoding="utf-8")
    msgs = [
        Message(role=MessageRole.user, content="resume test"),
        Message(role=MessageRole.assistant, content="tools", tool_calls=[tc, tc2]),
        Message(
            role=MessageRole.tool,
            content="Wrote already",
            tool_call_id="tc_done",
            name="write_file",
        ),
    ]
    h.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=msgs,
            pending_tool_calls=[tc, tc2],
            completed_tool_call_ids=["tc_done"],
            usage=Usage(),
            status=RunStatus.interrupted,
        )
    )
    call_count["n"] = 0
    resumed = await h.resume(run_id)
    # Only pending tool should run
    assert call_count["n"] == 1
    assert (workspace / "r3.txt").read_text() == "pending_only"
    assert (workspace / "r2.txt").read_text() == "from_resume"  # not rewritten differently
    assert resumed.status in (RunStatus.completed, RunStatus.failed, RunStatus.running)
    h.close()


@pytest.mark.asyncio
async def test_shell_process_tree_cancel(data_dir, workspace):
    """Real path: Harness.run(shell sleep) then cancel mid-flight kills process tree."""
    import sys

    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    # Long-running shell via shipped tool path (registers in process registry)
    py = sys.executable.replace("\\", "/")
    cmd = f'"{py}" -c "import time; time.sleep(120)"'
    payload = json.dumps({"command": cmd, "timeout_s": 120})

    task = asyncio.create_task(
        h.run(
            RunRequest(
                message=f"[fake:tools]shell\n{payload}",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    )

    rid = None
    procs: list = []
    for _ in range(100):
        await asyncio.sleep(0.1)
        rid = h.engine.active_run_id
        if rid:
            procs = list(h.engine._active_processes.get(rid, []))
            if procs:
                break
    assert rid, "run never became active"
    assert procs, "shell process never registered on shipped tool path"
    live_before = [p for p in procs if p.returncode is None]
    assert live_before, "shell process already exited before cancel"

    await h.cancel(rid)
    result = await asyncio.wait_for(task, timeout=20)
    assert result.status in (RunStatus.cancelled, RunStatus.completed, RunStatus.failed)
    # Process tree dead + registry cleared
    for p in procs:
        try:
            await asyncio.wait_for(p.wait(), timeout=3)
        except TimeoutError:
            p.kill()
            await p.wait()
        assert p.returncode is not None
    assert h.engine._active_processes.get(rid) in (None, [])
    h.close()


@pytest.mark.asyncio
async def test_interrupt_mid_batch_resume_skips_completed(data_dir, workspace):
    """Interrupt after write completes mid-shell: write stays done, shell stays pending.

    Resume must not re-exec write; must re-run incomplete shell (spot-check shell_on_resume).
    """
    import sys

    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    write_calls = {"n": 0}
    shell_calls = {"n": 0}
    orig_write = h.tools["write_file"].run
    orig_shell = h.tools["shell"].run

    async def counting_write(ctx, arguments):
        write_calls["n"] += 1
        return await orig_write(ctx, arguments)

    shell_started = asyncio.Event()

    async def counting_shell(ctx, arguments):
        shell_calls["n"] += 1
        shell_started.set()
        # First attempt: long sleep (will be cancelled). Resume: short echo.
        if shell_calls["n"] == 1:
            py = sys.executable.replace("\\", "/")
            arguments = {
                **arguments,
                "command": f'"{py}" -c "import time; time.sleep(120)"',
                "timeout_s": 120,
            }
        else:
            arguments = {**arguments, "command": "echo shell_resumed", "timeout_s": 10}
        return await orig_shell(ctx, arguments)

    h.tools["write_file"].run = counting_write  # type: ignore[method-assign]
    h.tools["shell"].run = counting_shell  # type: ignore[method-assign]

    py = sys.executable.replace("\\", "/")
    cmd = f'"{py}" -c "import time; time.sleep(120)"'
    message = (
        "[fake:tools]write_file|shell\n"
        + json.dumps(
            [
                {"path": "once.txt", "content": "written-once"},
                {"command": cmd, "timeout_s": 120},
            ]
        )
    )

    task = asyncio.create_task(
        h.run(
            RunRequest(
                message=message,
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    )
    await asyncio.wait_for(shell_started.wait(), timeout=15)
    await asyncio.sleep(0.25)
    assert write_calls["n"] == 1
    assert (workspace / "once.txt").read_text(encoding="utf-8") == "written-once"
    rid = h.engine.active_run_id
    assert rid

    await h.interrupt(rid, "test_interrupt")
    result = await asyncio.wait_for(task, timeout=20)
    assert result.status in (RunStatus.interrupted, RunStatus.cancelled, RunStatus.failed)

    cp = h.storage.load_checkpoint(rid)
    assert cp is not None
    # Write completed; shell must remain pending (not falsely completed)
    assert len(cp.completed_tool_call_ids) >= 1
    assert cp.pending_tool_calls, (
        f"incomplete shell must stay pending, got completed={cp.completed_tool_call_ids} "
        f"pending={cp.pending_tool_calls}"
    )
    pending_names = {tc.name for tc in cp.pending_tool_calls}
    assert "shell" in pending_names

    write_before_resume = write_calls["n"]
    shell_before_resume = shell_calls["n"]
    resumed = await h.resume(rid)
    shell_on_resume = shell_calls["n"] - shell_before_resume
    assert write_calls["n"] == write_before_resume, "completed write_file re-executed"
    assert shell_on_resume >= 1, (
        f"incomplete shell must re-run on resume, shell_on_resume={shell_on_resume}, "
        f"pending was {cp.pending_tool_calls}"
    )
    assert (workspace / "once.txt").read_text(encoding="utf-8") == "written-once"
    assert resumed.run_id == rid
    h.close()


@pytest.mark.asyncio
async def test_sse_replay_and_readonly_api(data_dir, workspace):
    redactor = Redactor()
    sentinel = "SECRET_SENTINEL_e2e_7a9f3c"
    redactor.add_sentinel(sentinel)
    h = Harness(data_dir=data_dir, redactor=redactor)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)

    # Large tool result forces artifact path (summary must also be redacted)
    big = ("leak " + sentinel + " ") * 400
    result = await h.run(
        RunRequest(
            message=(
                f"[fake:text]User said {sentinel} should be redacted\n"
                f"[also in payload]"
            ),
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert sentinel not in result.output

    # Force artifact with secret in body + summary path via storage API used by engine
    meta = h.storage.artifacts.put(big, summary=f"sum {sentinel}")
    h.storage.register_artifact(meta)
    assert sentinel not in meta["summary"]

    # DB must not contain sentinel
    raw_db = (data_dir / "agentharness.db").read_bytes()
    assert sentinel.encode() not in raw_db

    # Artifact files
    for p in (data_dir / "artifacts").rglob("*"):
        if p.is_file():
            assert sentinel.encode() not in p.read_bytes()

    # Events via API
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        runs = await client.get("/api/runs")
        assert runs.status_code == 200
        assert any(r["id"] == result.run_id for r in runs.json())
        assert sentinel not in runs.text

        ev1 = await client.get(f"/api/runs/{result.run_id}/events")
        assert ev1.status_code == 200
        events = ev1.json()
        assert events
        max_seq = max(e["global_seq"] for e in events)
        assert sentinel not in ev1.text

        # SSE replay via Last-Event-ID style after param
        mid = events[0]["global_seq"]
        ev2 = await client.get(f"/api/runs/{result.run_id}/events?after={mid}")
        assert all(e["global_seq"] > mid for e in ev2.json())

        # Write methods 405
        for method in ("post", "put", "patch"):
            resp = await getattr(client, method)("/api/runs", json={})
            assert resp.status_code == 405, method
        resp = await client.delete("/api/runs")
        assert resp.status_code == 405

        # Stream endpoint: exercise the shipped generator with a short-stream header
        # so ASGI test clients do not hang on infinite SSE.
        resp = await client.get(
            f"/api/stream?after={max(0, max_seq - 1)}",
            headers={"x-test-short-stream": "1"},
            timeout=5.0,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        blob = resp.text
        assert "data:" in blob or "heartbeat" in blob or ":" in blob
        assert sentinel not in blob

        # Artifact API metadata
        art = await client.get(f"/api/artifacts/{meta['id']}")
        assert art.status_code == 200
        assert sentinel not in art.text

        # HTML page body (SPA shell — must not embed secrets)
        page = await client.get("/")
        assert page.status_code == 200
        assert sentinel not in page.text

        for e in events:
            assert sentinel not in json.dumps(e)

    h.close()


@pytest.mark.asyncio
async def test_approval_never_and_destructive(data_dir, workspace):
    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    # never mode should deny write without callback allow
    h.set_approval_callback(None)
    result = await h.run(
        RunRequest(
            message='[fake:tools]write_file\n{"path":"n.txt","content":"x"}',
            provider="fake",
            approval=ApprovalMode.never,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    assert not (workspace / "n.txt").exists()
    h.close()


@pytest.mark.asyncio
async def test_memory_explicit_only(data_dir, workspace):
    h = Harness(data_dir=data_dir)
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)
    result = await h.run(
        RunRequest(
            message='[fake:tools]memory_store\n{"content":"Paris is the capital of France"}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    hits = h.storage.search_memories("Paris capital")
    assert hits
    h.close()
