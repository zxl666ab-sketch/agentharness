"""Effect-aware tool scheduler — concurrency rules for pure/read/write/process/browser."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

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
        items: list[tuple[Any, ...]],
        *,
        max_concurrency: int = 4,
    ) -> list[T]:
        """Run parallel-safe segments concurrently and preserve effectful barriers.

        Three-item tuples retain the historical effect-derived behavior. A fourth
        boolean lets the runtime apply the tool's explicit ``parallel_safe`` policy.
        """
        results: list[T | None] = [None] * len(items)
        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def _one(
            idx: int,
            effect: EffectKind,
            fn: Callable[[], Awaitable[T]],
            browser_id: str | None,
        ) -> None:
            async with semaphore:
                results[idx] = await self.run(effect, fn, browser_context_id=browser_id)

        segment: list[tuple[int, EffectKind, Callable[[], Awaitable[T]], str | None]] = []

        async def flush_segment() -> None:
            if not segment:
                return
            await asyncio.gather(*[_one(i, e, f, b) for i, e, f, b in segment])
            segment.clear()

        for index, item in enumerate(items):
            effect, fn, browser_id = item[:3]
            parallel_safe = (
                bool(item[3])
                if len(item) >= 4
                else effect in (EffectKind.pure, EffectKind.workspace_read, EffectKind.network)
            )
            if parallel_safe:
                segment.append((index, effect, fn, browser_id))
                continue
            await flush_segment()
            await _one(index, effect, fn, browser_id)
        await flush_segment()

        # Preserve position + count: every slot was assigned by _one. Filtering None
        # here would drop legitimate None returns and misalign results with inputs.
        return results  # type: ignore[return-value]
