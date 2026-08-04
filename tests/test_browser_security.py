from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentharness.contracts import ToolContext
from agentharness.tools.browser import BrowserTool


class _FakePage:
    async def screenshot(self, *, path: str) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake screenshot")


class _FakeBrowserContext:
    def __init__(self) -> None:
        self.pages = [_FakePage()]
        self.routes: list[str] = []

    async def new_page(self) -> _FakePage:
        return self.pages[0]

    async def route(self, pattern: str, handler: Any) -> None:  # noqa: ARG002
        self.routes.append(pattern)


class _FakeChromium:
    def __init__(self) -> None:
        self.launches: list[str] = []
        self.launch_options: list[dict[str, Any]] = []

    async def launch_persistent_context(self, *, user_data_dir: str, **kwargs: Any):
        self.launches.append(user_data_dir)
        self.launch_options.append(kwargs)
        return _FakeBrowserContext()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


def _context(data_dir: Path, workspace: Path) -> ToolContext:
    return ToolContext(
        run_id="run",
        session_id="session",
        cwd=str(workspace),
        data_dir=str(data_dir),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_id",
    [
        pytest.param("..", id="parent"),
        pytest.param("nested/profile", id="forward-slash"),
        pytest.param("nested\\profile", id="backslash"),
    ],
)
async def test_browser_rejects_context_ids_with_path_components(
    context_id: str, data_dir: Path, workspace: Path
):
    tool = BrowserTool()
    fake = _FakePlaywright()
    tool._playwright = fake

    result = await tool.run(
        _context(data_dir, workspace),
        {"action": "launch", "context_id": context_id},
    )

    assert result.is_error
    assert fake.chromium.launches == []


@pytest.mark.asyncio
async def test_browser_rejects_absolute_context_id_without_creating_profile(
    tmp_path: Path, data_dir: Path, workspace: Path
):
    outside = tmp_path / "outside-profile"
    tool = BrowserTool()
    fake = _FakePlaywright()
    tool._playwright = fake

    result = await tool.run(
        _context(data_dir, workspace),
        {"action": "launch", "context_id": str(outside.resolve())},
    )

    assert result.is_error
    assert fake.chromium.launches == []
    assert not outside.exists()


@pytest.mark.asyncio
async def test_browser_forces_headless_mode_even_for_legacy_false_argument(
    data_dir: Path, workspace: Path
):
    tool = BrowserTool()
    fake = _FakePlaywright()
    tool._playwright = fake

    result = await tool.run(
        _context(data_dir, workspace),
        {"action": "launch", "headless": False},
    )

    assert not result.is_error
    assert fake.chromium.launch_options == [{"headless": True, "viewport": {"width": 1280, "height": 720}}]


class _ClosableContext(_FakeBrowserContext):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _ClosableChromium:
    async def launch_persistent_context(self, *, user_data_dir: str, **_: Any):  # noqa: ARG002
        return _ClosableContext()


class _ClosablePlaywright:
    def __init__(self) -> None:
        self.chromium = _ClosableChromium()

    async def stop(self) -> None:
        return None


def _context_for(run_id: str, data_dir: Path, workspace: Path) -> ToolContext:
    return ToolContext(
        run_id=run_id,
        session_id="session",
        cwd=str(workspace),
        data_dir=str(data_dir),
    )


@pytest.mark.asyncio
async def test_two_runs_same_context_id_are_isolated(data_dir: Path, workspace: Path):
    """Cross-run isolation: same context_id in two runs → separate browsers.

    Cancelling run A must not close run B's live browser.
    """
    tool = BrowserTool()
    tool._playwright = _ClosablePlaywright()

    await tool.run(_context_for("runA", data_dir, workspace), {"action": "launch"})
    await tool.run(_context_for("runB", data_dir, workspace), {"action": "launch"})

    # Two distinct entries keyed by (run_id, "default").
    assert set(tool._browsers) == {("runA", "default"), ("runB", "default")}
    ctx_a = tool._browsers[("runA", "default")]["context"]
    ctx_b = tool._browsers[("runB", "default")]["context"]
    assert ctx_a is not ctx_b

    # Cancelling run A closes only A's browser; B stays live.
    await tool.cancel_run("runA")
    assert ("runA", "default") not in tool._browsers
    assert ("runB", "default") in tool._browsers
    assert ctx_a.closed is True
    assert ctx_b.closed is False

    await tool.close_all()
    assert ctx_b.closed is True
    assert tool._browsers == {}


@pytest.mark.asyncio
async def test_browser_screenshot_revalidates_context_path(
    tmp_path: Path, data_dir: Path, workspace: Path
):
    outside = (tmp_path / "outside-screenshot").resolve()
    context_id = str(outside)
    tool = BrowserTool()
    tool._browsers[context_id] = {
        "context": _FakeBrowserContext(),
        "page": _FakePage(),
    }

    result = await tool.run(
        _context(data_dir, workspace),
        {"action": "screenshot", "context_id": context_id},
    )

    assert result.is_error
    assert not (outside / "shot.png").exists()


@pytest.mark.asyncio
async def test_browser_interaction_failure_is_indeterminate(
    monkeypatch, data_dir: Path, workspace: Path
):
    tool = BrowserTool()

    async def fail_click(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("navigation started before timeout")

    monkeypatch.setattr(tool, "_click", fail_click)
    result = await tool.run(
        _context(data_dir, workspace),
        {"action": "click", "selector": "#submit"},
    )

    assert result.is_error is True
    assert result.error_code == "outcome_indeterminate"
    assert result.error_category == "recovery"
    assert result.retryable is False
