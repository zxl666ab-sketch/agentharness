"""The SPA catch-all must never serve a file from outside the build directory.

``FileResponse`` bypasses the redaction layer, so an escape here is an unredacted
arbitrary host-file read — and ``agentharness web --host 0.0.0.0`` makes it remote.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.harness import Harness

SECRET = "TOP-SECRET-OUTSIDE-DIST"


@pytest.fixture
def dist_with_secret_sibling(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return dist


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/%2e%2e/secret.txt", id="encoded-dotdot"),
        pytest.param("/%2e%2e%2fsecret.txt", id="encoded-dotdot-slash"),
        pytest.param("/assets/%2e%2e/%2e%2e/secret.txt", id="nested-encoded"),
        pytest.param("/..%2fsecret.txt", id="mixed-encoding"),
    ],
)
async def test_encoded_traversal_never_leaks_file_outside_dist(
    path: str, data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    # Either rejected outright or served the SPA shell — never the sibling file.
    assert SECRET not in response.text
    h.close()


@pytest.mark.asyncio
async def test_absolute_path_is_not_served(data_dir, dist_with_secret_sibling: Path):
    secret = dist_with_secret_sibling.parent / "secret.txt"
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/{secret.as_posix()}")

    assert SECRET not in response.text
    h.close()


@pytest.mark.asyncio
async def test_real_asset_inside_dist_is_still_served(
    data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text
    h.close()


@pytest.mark.asyncio
async def test_unknown_ui_route_falls_back_to_spa(
    data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/runs/abc123")

    assert response.status_code == 200
    assert "spa" in response.text
    h.close()


# ---------------------------------------------------------------------------
# API/security fixes: bind guard, pagination, write gating, body limit, SSE
# ---------------------------------------------------------------------------


def test_serve_rejects_non_loopback_without_explicit_opt_in():
    from agentharness.api import server as server_module
    from agentharness.api.server import serve

    assert server_module._is_loopback("127.0.0.1")
    assert server_module._is_loopback("localhost")
    assert not server_module._is_loopback("0.0.0.0")

    with pytest.raises(SystemExit) as excinfo:
        serve(host="0.0.0.0")
    assert "allow-remote-execution" in str(excinfo.value)


@pytest.mark.asyncio
async def test_runs_pagination_reports_true_total_and_boundary(data_dir):
    from agentharness.api.server import create_app
    from agentharness.contracts import RunStatus
    from agentharness.harness import Harness

    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    try:
        session_id = h.storage.create_session("pagination-session")
        for i in range(5):
            h.storage.create_run(
                run_id=f"run-{i}",
                session_id=session_id,
                root_run_id=f"run-{i}",
                status=RunStatus.completed,
                provider="fake",
                model="fake",
            )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            page1 = (await client.get("/api/runs?limit=2&offset=0")).json()
            assert page1["total"] == 5
            assert len(page1["items"]) == 2
            assert page1["has_more"] is True

            page2 = (await client.get("/api/runs?limit=2&offset=2")).json()
            assert page2["total"] == 5
            assert len(page2["items"]) == 2
            assert page2["has_more"] is True

            page3 = (await client.get("/api/runs?limit=2&offset=4")).json()
            assert page3["total"] == 5
            assert len(page3["items"]) == 1
            assert page3["has_more"] is False

            filtered = (await client.get("/api/runs?limit=10&status=completed")).json()
            assert filtered["total"] == 5
            assert filtered["has_more"] is False
    finally:
        h.close()


@pytest.mark.asyncio
async def test_run_events_offset_has_upper_bound(data_dir):
    from agentharness.api.server import create_app
    from agentharness.contracts import RunStatus
    from agentharness.harness import Harness

    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    try:
        session_id = h.storage.create_session("events-session")
        h.storage.create_run(
            run_id="events-run",
            session_id=session_id,
            root_run_id="events-run",
            status=RunStatus.completed,
            provider="fake",
            model="fake",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/runs/events-run/events?offset=2000000")
            assert response.status_code == 422
    finally:
        h.close()


@pytest.mark.asyncio
async def test_procurement_config_post_rejected_when_execution_disabled(data_dir):
    from agentharness.api.server import create_app
    from agentharness.harness import Harness

    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, execution_enabled=False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            read = await client.get("/api/procurement/config")
            assert read.status_code == 200
            write = await client.post(
                "/api/procurement/config", json={"model": "blocked-model"}
            )
            assert write.status_code == 403
            assert "execution" in write.json()["detail"]
    finally:
        h.close()


@pytest.mark.asyncio
async def test_oversized_content_length_rejected_with_413(data_dir):
    from agentharness.api.server import create_app
    from agentharness.harness import Harness

    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/procurement/config",
                headers={"content-length": str(33 * 1024 * 1024)},
                content=b"{}",
            )
            assert response.status_code == 413
            # Normal-sized traffic still passes the middleware.
            health = await client.get("/api/health")
            assert health.status_code == 200
    finally:
        h.close()


async def _collect_stream_lines(response):
    return [line async for line in response.aiter_lines()]


@pytest.mark.asyncio
async def test_stream_idle_timeout_closes_without_test_switch(data_dir, monkeypatch):
    from agentharness.api import server as server_module
    from agentharness.api.server import create_app
    from agentharness.harness import Harness

    monkeypatch.setattr(server_module, "STREAM_IDLE_TIMEOUT_S", 0.35)
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", "/api/stream") as response:
                assert response.status_code == 200
                lines = await asyncio.wait_for(_collect_stream_lines(response), timeout=5)
        assert any("heartbeat" in line for line in lines)
    finally:
        h.close()


@pytest.mark.asyncio
async def test_stream_after_validation_and_clamp(data_dir):
    from agentharness.api.server import create_app
    from agentharness.contracts import EventEnvelope, EventType, RunStatus
    from agentharness.harness import Harness

    h = Harness(data_dir=data_dir)
    app = create_app(harness=h)
    try:
        session_id = h.storage.create_session("stream-session")
        h.storage.create_run(
            run_id="stream-run",
            session_id=session_id,
            root_run_id="stream-run",
            status=RunStatus.completed,
            provider="fake",
            model="fake",
        )
        h.storage.append_events(
            [
                EventEnvelope(
                    session_id=session_id,
                    root_run_id="stream-run",
                    run_id="stream-run",
                    type=EventType.run_started,
                    payload={},
                )
            ]
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.get("/api/stream?after=-1")
            assert bad.status_code == 422
            async with client.stream(
                "GET",
                "/api/stream?after=999999999",
                headers={"x-test-short-stream": "1"},
            ) as response:
                assert response.status_code == 200
                lines = await asyncio.wait_for(_collect_stream_lines(response), timeout=5)
        # The cursor is clamped to max_global_seq, so the existing event is
        # never replayed to a client that claims to be far ahead.
        assert not any(line.startswith("event:") for line in lines)
    finally:
        h.close()
