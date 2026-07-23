from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalMode,
    ModelRequest,
    ModelStreamItem,
    RunRequest,
    StreamItemType,
)
from agentharness.harness import Harness
from agentharness.storage.migrations import SCHEMA_VERSION


def test_harness_expands_tilde_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    expected = Path("~/.agentharness").expanduser()

    harness = Harness(data_dir="~/.agentharness")
    try:
        assert harness.data_dir == expected
        assert harness.data_dir.is_dir()
    finally:
        harness.close()


class _CloseableProvider:
    name = "closeable"

    def __init__(self) -> None:
        self.closed_on_loop: int | None = None

    async def stream(self, request: ModelRequest):
        yield ModelStreamItem(type=StreamItemType.done)

    async def aclose(self) -> None:
        import asyncio

        self.closed_on_loop = id(asyncio.get_running_loop())


@pytest.mark.asyncio
async def test_harness_aclose_closes_provider_on_owning_loop(data_dir: Path):
    import asyncio

    provider = _CloseableProvider()
    harness = Harness(data_dir=data_dir, providers={"closeable": provider}, tools={})
    loop_id = id(asyncio.get_running_loop())

    await harness.aclose()

    assert provider.closed_on_loop == loop_id


@pytest.mark.asyncio
async def test_completed_run_releases_transient_engine_state(data_dir: Path, workspace: Path):
    harness = Harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:text]cleanup",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        engine = harness.engine
        mappings = [
            engine._cancel_events,
            engine._run_allow_effects,
            engine._active_processes,
            engine._delta_buf,
            engine._delta_buf_size,
            engine._delta_last_flush,
            engine._completed_tool_ids,
            engine._pending_tool_calls,
            engine._stop_mode,
            engine._run_messages,
            engine._child_runs,
        ]
        assert all(result.run_id not in mapping for mapping in mappings)
    finally:
        await harness.aclose()


def test_doctor_reports_storage_web_and_browser_readiness(data_dir: Path):
    harness = Harness(data_dir=data_dir)
    try:
        info = harness.doctor()
    finally:
        harness.close()

    assert info["sqlite_integrity"] == "ok"
    assert info["schema_version"] == SCHEMA_VERSION
    assert info["web_build"] in {"ready", "missing"}
    assert info["browser_runtime"] in {"ready", "missing"}
