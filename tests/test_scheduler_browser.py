"""Browser context ops must serialize even when effect is network."""

from __future__ import annotations

import asyncio

import pytest

from agentharness.contracts import EffectKind
from agentharness.engine.scheduler import EffectScheduler


@pytest.mark.asyncio
async def test_same_browser_context_serializes():
    sched = EffectScheduler()
    log: list[str] = []

    async def op(name: str, delay: float = 0.05) -> str:
        log.append(f"start:{name}")
        await asyncio.sleep(delay)
        log.append(f"end:{name}")
        return name

    items = [
        (EffectKind.network, lambda: op("a"), "ctx1"),
        (EffectKind.network, lambda: op("b"), "ctx1"),
    ]
    results = await sched.run_batch(items)
    assert set(results) == {"a", "b"}
    # Same context: no interleaving of starts before first end
    assert log == ["start:a", "end:a", "start:b", "end:b"] or log == [
        "start:b",
        "end:b",
        "start:a",
        "end:a",
    ]
    # Never start both before either ends
    first_ends = min(i for i, x in enumerate(log) if x.startswith("end:"))
    starts_before_first_end = [x for x in log[:first_ends] if x.startswith("start:")]
    assert len(starts_before_first_end) == 1


@pytest.mark.asyncio
async def test_different_browser_contexts_can_overlap():
    sched = EffectScheduler()
    log: list[str] = []
    barrier = asyncio.Event()

    async def op_a() -> str:
        log.append("start:a")
        barrier.set()
        await asyncio.sleep(0.08)
        log.append("end:a")
        return "a"

    async def op_b() -> str:
        await barrier.wait()
        log.append("start:b")
        await asyncio.sleep(0.02)
        log.append("end:b")
        return "b"

    items = [
        (EffectKind.network, op_a, "ctxA"),
        (EffectKind.network, op_b, "ctxB"),
    ]
    await sched.run_batch(items)
    # b starts while a still running
    assert log.index("start:b") < log.index("end:a")
