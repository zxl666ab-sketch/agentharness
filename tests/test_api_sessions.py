"""API: session detail + transcript GET, redaction, writes still 405."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.harness import Harness


@pytest.mark.asyncio
async def test_session_and_transcript_endpoints(data_dir, workspace):
    h = Harness(data_dir=data_dir)

    async def auto(req):
        from agentharness.contracts import ApprovalDecision

        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)
    r1 = await h.run(
        RunRequest(
            message="[fake:text]Hello secret sk-abcdefghijklmnopqrstuvwxyz",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    await h.run(
        RunRequest(
            message="[fake:error:provider]",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List endpoint must enrich latest_status for the left column (not only detail)
        listed = await client.get("/api/sessions")
        assert listed.status_code == 200
        rows = listed.json()
        assert any(r.get("id") == r1.session_id for r in rows)
        match = next(r for r in rows if r["id"] == r1.session_id)
        assert match.get("latest_status") in ("failed", "completed")
        assert match.get("title")
        assert match.get("updated_at")

        s = await client.get(f"/api/sessions/{r1.session_id}")
        assert s.status_code == 200
        body = s.json()
        assert body["id"] == r1.session_id
        assert "latest_status" in body
        assert body["latest_status"] == match["latest_status"]

        t = await client.get(f"/api/sessions/{r1.session_id}/transcript")
        assert t.status_code == 200
        turns = t.json()
        assert len(turns) == 2
        assert turns[0]["run_id"] == r1.run_id
        assert turns[1]["status"] == "failed"
        # redaction of OpenAI-style keys on the API surface
        blob = str(turns)
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob
        assert "REDACTED" in blob or "Hello secret" in blob

        missing = await client.get("/api/sessions/does-not-exist")
        assert missing.status_code == 404

        for method in ("post", "put", "patch", "delete"):
            r = await getattr(client, method)(f"/api/sessions/{r1.session_id}/transcript")
            assert r.status_code == 405
            r2 = await getattr(client, method)(f"/api/sessions/{r1.session_id}")
            assert r2.status_code == 405
    h.close()


@pytest.mark.asyncio
async def test_write_methods_still_405(data_dir):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/api/runs",
            "/api/sessions",
            "/api/health",
            "/api/sessions/x",
            "/api/sessions/x/transcript",
        ):
            for method in ("post", "put", "patch", "delete"):
                r = await getattr(client, method)(path)
                assert r.status_code == 405, f"{method} {path}"
    h.close()
