"""Effect-aware scheduler for the procurement tool batch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from agentharness.contracts import EffectKind

T = TypeVar("T")


class EffectScheduler:
    """Serialize side-effecting procurement tools and parallelize safe work."""

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()

    async def run(self, effect: EffectKind, fn: Callable[[], Awaitable[T]]) -> T:
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
        """Run safe segments concurrently while preserving effectful barriers.

        Items are ``(effect, callable, parallel_safe)`` tuples. The historical
        four-item shape ``(effect, callable, _context, parallel_safe)`` is also
        accepted while the unused context slot is ignored.
        """
        results: list[T | None] = [None] * len(items)
        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def _one(
            idx: int,
            effect: EffectKind,
            fn: Callable[[], Awaitable[T]],
        ) -> None:
            async with semaphore:
                results[idx] = await self.run(effect, fn)

        segment: list[tuple[int, EffectKind, Callable[[], Awaitable[T]]]] = []

        async def flush_segment() -> None:
            if not segment:
                return
            await asyncio.gather(*[_one(i, effect, fn) for i, effect, fn in segment])
            segment.clear()

        for index, item in enumerate(items):
            effect, fn = item[:2]
            if len(item) >= 4:
                parallel_safe = bool(item[3])
            elif len(item) >= 3:
                parallel_safe = bool(item[2])
            else:
                parallel_safe = effect in (
                    EffectKind.pure,
                    EffectKind.workspace_read,
                    EffectKind.network,
                )
            if parallel_safe:
                segment.append((index, effect, fn))
                continue
            await flush_segment()
            await _one(index, effect, fn)
        await flush_segment()
        return results  # type: ignore[return-value]
