"""Phase-5.3 demo one-click create/clean tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.harness import Harness


@pytest.mark.asyncio
async def test_demo_create_and_clean(data_dir, workspace) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/procurement/demo")
            assert created.status_code == 202, created.text
            payload = created.json()
            assert payload["status"] == "accepted"
            request_id = payload["purchase_request_id"]
            run_id = payload["run_id"]

            # Wait until the run reaches a comparison or a safe terminal state.
            for _ in range(200):
                detail = (
                    await client.get(f"/api/procurement/requests/{request_id}")
                ).json()
                if detail.get("comparison") is not None:
                    break
                run = (await client.get(f"/api/runs/{run_id}")).json()
                if run["status"] in {"failed", "cancelled", "interrupted"}:
                    break
                await asyncio.sleep(0.02)

            report = (
                await client.get(f"/api/procurement/requests/{request_id}/report")
            ).json()
            assert any(
                event["type"] == "demo_request" for event in report["audit_events"]
            )

            cleaned = await client.post("/api/procurement/demo/clean")
            assert cleaned.status_code == 200
            assert cleaned.json()["removed"] >= 1
            requests = (await client.get("/api/procurement/requests?limit=200")).json()
            assert all(item["id"] != request_id for item in requests)
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()



def test_clean_demo_requests_skips_demo_without_visible_run(data_dir, monkeypatch) -> None:
    """P3: a demo request whose analysis run is not (yet) visible must be kept,
    never deleted by the cleanup sweep."""
    from agentharness.harness import Harness
    from agentharness.procurement.service import ProcurementService

    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    try:
        request = service.create_demo_request()
        request_id = str(request["id"])
        monkeypatch.setattr(service.harness, "get_run", lambda run_id: None)
        result = service.clean_demo_requests()
        assert result["removed"] == 0
        assert result["skipped"] >= 1
        assert service.get_request(request_id)["id"] == request_id
    finally:
        harness.close()
