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
        # Goal 2: all per-run state lives in one RunContext, dropped atomically on finish.
        assert result.run_id not in engine._runs
        assert engine._runs == {}
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_many_completed_runs_leave_no_lingering_engine_state(
    data_dir: Path, workspace: Path
):
    """Goal 2: after N runs complete, the RunContext registry is empty (no leak)."""
    harness = Harness(data_dir=data_dir)
    try:
        for i in range(100):
            await harness.run(
                RunRequest(
                    message=f"[fake:text]run {i}",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                )
            )
        assert harness.engine._runs == {}, harness.engine._runs
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_cleanup_survives_tool_release_exception(data_dir: Path, workspace: Path):
    """Goal 2: a tool whose release_run raises must not leak the run's RunContext."""
    harness = Harness(data_dir=data_dir)

    class _ExplodingTool:
        name = "boom"

        def release_run(self, _run_id: str) -> None:
            raise RuntimeError("release failed")

    # Register a tool that always fails its per-run release.
    harness.engine.tools["boom"] = _ExplodingTool()
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:text]cleanup",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        # ExitStack guarantees the RunContext is dropped even though release_run threw.
        assert result.run_id not in harness.engine._runs
        assert harness.engine._runs == {}
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
