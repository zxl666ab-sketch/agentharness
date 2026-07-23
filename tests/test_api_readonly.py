from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.harness import Harness


@pytest.mark.asyncio
async def test_write_methods_405(data_dir):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/runs", "/api/sessions", "/api/health"):
            for method in ("post", "put", "patch", "delete"):
                r = await getattr(client, method)(path)
                assert r.status_code == 405, f"{method} {path}"
    h.close()


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
    assert Path(body["data_dir"]) == data_dir.expanduser().resolve()
    h.close()


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
