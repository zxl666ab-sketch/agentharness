"""The inspector process must keep reclaiming runs whose owner died.

Harness recovers expired leases once, at construction. The API server is
long-lived, so a crash after startup would otherwise leave a run stuck at
"running" — not resumable, and displayed as live.
"""

from __future__ import annotations

import asyncio

import pytest

from agentharness.api.server import _sweep_expired_leases, create_app
from agentharness.contracts import RunStatus
from agentharness.harness import Harness


def _create_running_run(harness: Harness, run_id: str) -> None:
    session_id = harness.storage.create_session(f"session-{run_id}")
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.running,
        provider="fake",
        model="fake",
    )


@pytest.mark.asyncio
async def test_sweeper_recovers_a_run_whose_owner_vanished(data_dir):
    harness = Harness(data_dir=data_dir)
    try:
        _create_running_run(harness, "ghost-run")
        assert harness.storage.acquire_run_lease("ghost-run", "ghost-owner", ttl_s=-1.0)
        assert harness.storage.get_run("ghost-run")["status"] == "running"

        task = asyncio.create_task(_sweep_expired_leases(harness, interval_s=0.01))
        try:
            for _ in range(200):
                await asyncio.sleep(0.01)
                if (
                    harness.storage.get_run("ghost-run")["status"] != "running"
                    and "ghost-run" in harness.recovered_run_ids
                ):
                    break
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        row = harness.storage.get_run("ghost-run")
        assert row["status"] == RunStatus.interrupted.value
        assert "ghost-run" in harness.recovered_run_ids
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_sweeper_leaves_a_live_lease_alone(data_dir):
    harness = Harness(data_dir=data_dir)
    try:
        _create_running_run(harness, "live-run")
        assert harness.storage.acquire_run_lease("live-run", "live-owner", ttl_s=600)

        task = asyncio.create_task(_sweep_expired_leases(harness, interval_s=0.01))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert harness.storage.get_run("live-run")["status"] == "running"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_sweeper_survives_storage_errors(data_dir, monkeypatch):
    harness = Harness(data_dir=data_dir)
    calls: list[int] = []

    def boom() -> list[str]:
        calls.append(1)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(harness.storage, "recover_expired_run_leases", boom)
    try:
        task = asyncio.create_task(_sweep_expired_leases(harness, interval_s=0.01))
        await asyncio.sleep(0.15)
        assert not task.done(), "a storage error must not end the sweep"
        assert len(calls) > 1, "the sweep must keep retrying"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_app_lifespan_starts_and_stops_the_sweeper(data_dir):
    """Guards against the sweeper being created but never cancelled, or vice versa."""
    from httpx import ASGITransport, AsyncClient

    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness)
    before = len(asyncio.all_tasks())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # ASGITransport does not run lifespan; call the context manager directly.
        async with app.router.lifespan_context(app):
            during = len(asyncio.all_tasks())
            assert during > before
            response = await client.get("/api/health")
            assert response.status_code == 200
        await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) <= during
    harness.close()
