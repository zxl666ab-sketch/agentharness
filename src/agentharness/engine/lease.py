"""Run lease acquisition, heartbeat renewal, and release."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agentharness.contracts import new_id
from agentharness.storage.sqlite import Storage


class LeaseManager:
    """Owns single-writer run leases: acquire on start, renew by heartbeat,
    release on teardown. A lease that cannot be renewed (another process took
    it over, or the row was reset) triggers ``on_lost`` so the engine can
    interrupt the run instead of double-writing.
    """

    def __init__(
        self,
        storage: Storage,
        *,
        owner_id: str | None = None,
        ttl_s: float = 60.0,
        heartbeat_s: float = 10.0,
    ) -> None:
        self.storage = storage
        self.owner_id = owner_id or new_id()
        self.ttl_s = max(5.0, ttl_s)
        self.heartbeat_s = max(0.5, min(heartbeat_s, self.ttl_s / 2))

    def acquire(self, run_id: str) -> None:
        acquired = self.storage.acquire_run_lease(
            run_id, self.owner_id, ttl_s=self.ttl_s
        )
        if not acquired:
            raise RuntimeError(f"run {run_id} is leased by another active process")

    def start_heartbeat(
        self, run_id: str, *, on_lost: Callable[[str], None]
    ) -> asyncio.Task[None]:
        return asyncio.create_task(
            self._heartbeat_loop(run_id, on_lost),
            name=f"agentharness-lease-{run_id[:12]}",
        )

    async def _heartbeat_loop(
        self, run_id: str, on_lost: Callable[[str], None]
    ) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            renewed = self.storage.heartbeat_run_lease(
                run_id, self.owner_id, ttl_s=self.ttl_s
            )
            if not renewed:
                on_lost(run_id)
                return

    def release(self, run_id: str) -> None:
        self.storage.release_run_lease(run_id, self.owner_id)
