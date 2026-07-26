"""Browser tools without explicit context_id must still share scheduler lock (default)."""

from __future__ import annotations

import asyncio

import pytest

from agentharness.contracts import EffectKind, ToolCall
from agentharness.engine.runtime import RunEngine
from agentharness.engine.scheduler import EffectScheduler
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage
from agentharness.tools.browser import BrowserTool
from tests.fake_provider import FakeModelAdapter


def test_resolve_browser_context_defaults_to_default(data_dir):
    storage = Storage(data_dir)
    tools = {"browser": BrowserTool()}
    engine = RunEngine(storage, {"fake": FakeModelAdapter()}, tools, redactor=Redactor())

    tc_omit = ToolCall(name="browser", arguments={"action": "content"})
    tc_explicit = ToolCall(
        name="browser", arguments={"action": "content", "context_id": "custom"}
    )
    tc_other = ToolCall(name="read_file", arguments={"path": "x"})

    assert engine.tool_executor._resolve_browser_context_id(tc_omit) == "default"
    assert engine.tool_executor._resolve_browser_context_id(tc_explicit) == "custom"
    assert engine.tool_executor._resolve_browser_context_id(tc_other) is None
    storage.close()


@pytest.mark.asyncio
async def test_two_browser_tools_without_context_id_serialize():
    """Spot-check: omit context_id → both map to 'default' → no concurrent start."""
    sched = EffectScheduler()
    log: list[str] = []

    async def op(name: str) -> str:
        log.append(f"start:{name}")
        await asyncio.sleep(0.05)
        log.append(f"end:{name}")
        return name

    # Engine would pass browser_id="default" for both when context_id omitted
    items = [
        (EffectKind.network, lambda: op("1"), "default"),
        (EffectKind.network, lambda: op("2"), "default"),
    ]
    await sched.run_batch(items)
    # Must not interleave starts
    assert log in (
        ["start:1", "end:1", "start:2", "end:2"],
        ["start:2", "end:2", "start:1", "end:1"],
    )
