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

    async def new_page(self) -> _FakePage:
        return self.pages[0]


class _FakeChromium:
    def __init__(self) -> None:
        self.launches: list[str] = []

    async def launch_persistent_context(self, *, user_data_dir: str, **_: Any):
        self.launches.append(user_data_dir)
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
