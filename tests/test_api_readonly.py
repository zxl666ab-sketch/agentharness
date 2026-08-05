from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.compatibility import API_CAPABILITIES, API_SCHEMA_VERSION
from agentharness.api.server import create_app
from agentharness.contracts import RunStatus
from agentharness.harness import Harness


@pytest.mark.asyncio
async def test_only_explicit_control_plane_writes_are_allowed(data_dir):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Generic runtime execution is deliberately absent from the procurement
        # control plane; only token-protected internal commands may mutate it.
        assert (await client.post("/api/runs", json={})).status_code == 405
        for path in ("/api/sessions", "/api/health"):
            assert (await client.post(path)).status_code == 405
        for path in ("/api/runs", "/api/sessions", "/api/health"):
            for method in ("put", "patch", "delete"):
                r = await getattr(client, method)(path)
                assert r.status_code == 405, f"{method} {path}"
    await h.aclose()


@pytest.mark.asyncio
async def test_health_identifies_service_and_normalizes_data_dir(data_dir):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "agentharness"
    assert body["api_schema_version"] == API_SCHEMA_VERSION
    assert body["api_capabilities"] == list(API_CAPABILITIES)
    assert body["backend_version"]
    assert body["server_started_at"]
    assert response.headers["cache-control"] == "no-store"
    assert Path(body["data_dir"]) == data_dir.expanduser().resolve()
    h.close()


@pytest.mark.asyncio
async def test_health_locks_web_build_identity_at_server_start(data_dir, tmp_path):
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (web_dist / "build-meta.json").write_text(
        '{"web_build_id":"build-before-start","api_schema_version":5}',
        encoding="utf-8",
    )
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=web_dist)

    # Simulate an in-place frontend rebuild after the Python routes were loaded.
    (web_dist / "build-meta.json").write_text(
        '{"web_build_id":"build-after-start","api_schema_version":5}',
        encoding="utf-8",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        index = await client.get("/")

    assert health.json()["web_build_id"] == "build-before-start"
    assert index.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    await h.aclose()


@pytest.mark.asyncio
async def test_app_lifespan_closes_only_internally_owned_harness(data_dir, tmp_path):
    owned_app = create_app(data_dir=data_dir)
    owned = owned_app.state.harness
    assert owned._closed is False
    async with owned_app.router.lifespan_context(owned_app):
        assert owned._closed is False
    assert owned._closed is True

    external = Harness(data_dir=tmp_path / "external-data")
    external_app = create_app(harness=external)
    async with external_app.router.lifespan_context(external_app):
        assert external._closed is False
    assert external._closed is False
    await external.aclose()


@pytest.mark.asyncio
async def test_existing_run_without_checkpoint_is_optional_null(data_dir):
    h = Harness(data_dir=data_dir)
    session_id = h.storage.create_session(title="external run")
    h.storage.create_run(
        run_id="external-running",
        session_id=session_id,
        root_run_id="external-running",
        status=RunStatus.running,
    )
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        existing = await client.get("/api/runs/external-running/checkpoint")
        missing = await client.get("/api/runs/missing/checkpoint")

    assert existing.status_code == 200
    assert existing.json() is None
    assert missing.status_code == 404
    await h.aclose()
