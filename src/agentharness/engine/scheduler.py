"""Effect-aware tool scheduler — concurrency rules for pure/read/write/process/browser."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agentharness.contracts import EffectKind

T = TypeVar("T")


class EffectScheduler:
    """Serialize writes and per-context browser ops; allow concurrent pure/read/network."""

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()
        self._browser_locks: dict[str, asyncio.Lock] = {}
        self._browser_guard = asyncio.Lock()

    async def _browser_lock(self, context_id: str) -> asyncio.Lock:
        async with self._browser_guard:
            if context_id not in self._browser_locks:
                self._browser_locks[context_id] = asyncio.Lock()
            return self._browser_locks[context_id]

    async def run(
        self,
        effect: EffectKind,
        fn: Callable[[], Awaitable[T]],
        *,
        browser_context_id: str | None = None,
    ) -> T:
        # Same browser context always serializes (even if effect is network).
        if browser_context_id:
            lock = await self._browser_lock(browser_context_id)
            async with lock:
                return await fn()

        if effect in (EffectKind.pure, EffectKind.workspace_read, EffectKind.network):
            return await fn()

        if effect in (
            EffectKind.workspace_write,
            EffectKind.destructive,
            EffectKind.process,
        ):
            async with self._write_lock:
                return await fn()

        return await fn()

    async def run_batch(
        self,
        items: list[tuple[EffectKind, Callable[[], Awaitable[T]], str | None]],
    ) -> list[T]:
        """Run a batch: concurrent pure/read/network (browser ctx still serial via lock);
        serial write/process/destructive.
        """
        results: list[T | None] = [None] * len(items)
        concurrent: list[tuple[int, EffectKind, Callable[[], Awaitable[T]], str | None]] = []
        serial: list[tuple[int, EffectKind, Callable[[], Awaitable[T]], str | None]] = []

        for i, (effect, fn, browser_id) in enumerate(items):
            # Browser-bound work still goes through concurrent gather but serializes on lock.
            if effect in (EffectKind.pure, EffectKind.workspace_read, EffectKind.network):
                concurrent.append((i, effect, fn, browser_id))
            else:
                serial.append((i, effect, fn, browser_id))

        async def _one(
            idx: int,
            effect: EffectKind,
            fn: Callable[[], Awaitable[T]],
            browser_id: str | None,
        ) -> None:
            results[idx] = await self.run(effect, fn, browser_context_id=browser_id)

        if concurrent:
            await asyncio.gather(*[_one(i, e, f, b) for i, e, f, b in concurrent])
        for i, e, f, b in serial:
            await _one(i, e, f, b)

        # Preserve position + count: every slot was assigned by _one. Filtering None
        # here would drop legitimate None returns and misalign results with inputs.
        return results  # type: ignore[return-value]
