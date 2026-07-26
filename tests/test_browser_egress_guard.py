"""The browser must enforce the egress policy on every request, not just goto.

Validating only the explicit goto URL left redirects, click-navigations and
subresource loads unchecked — any of which can reach a private address.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentharness.contracts import ToolContext
from agentharness.security.egress import default_policy
from agentharness.tools.browser import BrowserTool


class _FakeRoute:
    def __init__(self) -> None:
        self.aborted_with: str | None = None
        self.continued = False

    async def abort(self, error_code: str = "failed") -> None:
        self.aborted_with = error_code

    async def continue_(self) -> None:
        self.continued = True


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _RecordingContext:
    """Captures the route handler the tool installs so tests can drive it."""

    def __init__(self) -> None:
        self.pages: list[Any] = [_FakePage()]
        self.handler: Any = None
        self.pattern: str | None = None

    async def new_page(self) -> Any:
        return self.pages[0]

    async def route(self, pattern: str, handler: Any) -> None:
        self.pattern = pattern
        self.handler = handler

    async def close(self) -> None:
        return None


class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.goto_calls: list[str] = []
        self.clicked: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_calls.append(url)
        self.url = url

    async def click(self, selector: str, **_: Any) -> None:
        self.clicked.append(selector)

    async def title(self) -> str:
        return "fake title"

    async def inner_text(self, _selector: str) -> str:
        return "SENSITIVE-INTERNAL-BODY"


class _RecordingChromium:
    def __init__(self) -> None:
        self.context = _RecordingContext()

    async def launch_persistent_context(self, *, user_data_dir: str, **_: Any):  # noqa: ARG002
        return self.context


class _RecordingPlaywright:
    def __init__(self) -> None:
        self.chromium = _RecordingChromium()

    async def stop(self) -> None:
        return None


def _ctx(data_dir, workspace) -> ToolContext:
    return ToolContext(
        run_id="r", session_id="s", cwd=str(workspace), data_dir=str(data_dir)
    )


async def _launched(data_dir, workspace) -> tuple[BrowserTool, _RecordingContext]:
    tool = BrowserTool(policy=default_policy())
    pw = _RecordingPlaywright()
    tool._playwright = pw
    await tool.run(_ctx(data_dir, workspace), {"action": "launch"})
    return tool, pw.chromium.context


@pytest.mark.asyncio
async def test_launch_installs_a_catch_all_route_guard(data_dir, workspace):
    _tool, context = await _launched(data_dir, workspace)
    assert context.pattern == "**/*"
    assert context.handler is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://169.254.169.254/latest/meta-data/", id="imds"),
        pytest.param("http://127.0.0.1:8741/api/runs", id="loopback-api"),
        pytest.param("http://10.0.0.5/internal", id="private-range"),
        pytest.param("file:///etc/passwd", id="file-scheme"),
    ],
)
async def test_route_guard_aborts_private_and_non_http_requests(
    url: str, data_dir, workspace
):
    _tool, context = await _launched(data_dir, workspace)
    route = _FakeRoute()
    await context.handler(route, _FakeRequest(url))
    assert route.aborted_with is not None
    assert route.continued is False


@pytest.mark.asyncio
async def test_route_guard_allows_public_requests(data_dir, workspace, monkeypatch):
    tool = BrowserTool(policy=default_policy())
    monkeypatch.setattr(tool.policy, "validate", lambda url: url)
    pw = _RecordingPlaywright()
    tool._playwright = pw
    await tool.run(_ctx(data_dir, workspace), {"action": "launch"})

    route = _FakeRoute()
    await pw.chromium.context.handler(route, _FakeRequest("https://example.com/page"))
    assert route.continued is True
    assert route.aborted_with is None


@pytest.mark.asyncio
async def test_goto_rechecks_url_after_redirect(data_dir, workspace, monkeypatch):
    """A public URL that redirects to a private one must not be reported as OK."""
    tool = BrowserTool(policy=default_policy())
    pw = _RecordingPlaywright()
    tool._playwright = pw
    await tool.run(_ctx(data_dir, workspace), {"action": "launch"})
    page = pw.chromium.context.pages[0]

    real_validate = tool.policy.validate
    calls: list[str] = []

    def validate(url: str):
        calls.append(url)
        if url.startswith("https://public.example"):
            return url
        return real_validate(url)

    monkeypatch.setattr(tool.policy, "validate", validate)

    async def redirecting_goto(url: str, **_: Any) -> None:
        page.goto_calls.append(url)
        page.url = "http://127.0.0.1:8741/api/runs" if "public" in url else url

    monkeypatch.setattr(page, "goto", redirecting_goto)

    result = await tool.run(
        _ctx(data_dir, workspace),
        {"action": "goto", "url": "https://public.example/redirect-me"},
    )

    assert result.is_error
    assert "blocked by egress policy" in result.content
    assert "127.0.0.1" in " ".join(calls)


@pytest.mark.asyncio
async def test_content_refuses_to_read_a_blocked_page(data_dir, workspace):
    tool = BrowserTool(policy=default_policy())
    pw = _RecordingPlaywright()
    tool._playwright = pw
    await tool.run(_ctx(data_dir, workspace), {"action": "launch"})
    # Simulate having landed on an internal page by any means.
    pw.chromium.context.pages[0].url = "http://127.0.0.1:8741/api/runs"

    result = await tool.run(_ctx(data_dir, workspace), {"action": "content"})

    assert result.is_error
    assert "blocked by egress policy" in result.content
    assert "SENSITIVE-INTERNAL-BODY" not in result.content


@pytest.mark.asyncio
async def test_click_that_navigates_to_private_url_is_reported(data_dir, workspace):
    tool = BrowserTool(policy=default_policy())
    pw = _RecordingPlaywright()
    tool._playwright = pw
    await tool.run(_ctx(data_dir, workspace), {"action": "launch"})
    page = pw.chromium.context.pages[0]

    async def navigating_click(selector: str, **_: Any) -> None:
        page.clicked.append(selector)
        page.url = "http://169.254.169.254/latest/meta-data/"

    page.click = navigating_click  # type: ignore[assignment]

    result = await tool.run(
        _ctx(data_dir, workspace), {"action": "click", "selector": "#go"}
    )

    assert result.is_error
    assert "blocked by egress policy" in result.content
