"""Playwright browser tool — isolated profile, never user real browser state."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec

_browser_lock_note = "browser ops serialize per context_id via scheduler"


def _profile_dir(data_dir: str, context_id: str) -> Path:
    """Resolve one browser profile name without allowing path semantics."""
    if (
        not context_id
        or context_id in {".", ".."}
        or ".." in context_id
        or "/" in context_id
        or "\\" in context_id
        or PurePosixPath(context_id).is_absolute()
        or PureWindowsPath(context_id).is_absolute()
        or bool(PureWindowsPath(context_id).drive)
        or bool(PureWindowsPath(context_id).root)
    ):
        raise ValueError("context_id must be a single relative profile name")

    profiles_root = (Path(data_dir) / "browser_profiles").resolve()
    profile = (profiles_root / context_id).resolve()
    try:
        profile.relative_to(profiles_root)
    except ValueError as exc:
        raise ValueError("context_id escapes the browser profile directory") from exc
    return profile


class BrowserTool:
    """Minimal browser tool using Playwright with an isolated profile under data_dir."""

    # Engine uses this so scheduler serializes even when context_id is omitted
    browser_bound = True

    def __init__(self) -> None:
        self._playwright = None
        self._browsers: dict[str, Any] = {}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="browser",
            description=(
                "Control an isolated Playwright browser (never uses the user's real profile). "
                "Actions: launch, goto, content, click, type, close."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["launch", "goto", "content", "click", "type", "close", "screenshot"],
                    },
                    "context_id": {
                        "type": "string",
                        "description": "Browser context id (default: 'default')",
                    },
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "headless": {"type": "boolean", "default": True},
                    "timeout_s": {
                        "type": "number",
                        "description": "Action timeout in seconds (default: goto 30, click/type 10)",
                    },
                },
                "required": ["action"],
            },
            effect=EffectKind.network,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = arguments.get("action") or "content"
        context_id = str(arguments.get("context_id") or "default")
        try:
            profile = _profile_dir(ctx.data_dir, context_id)
            existing = self._browsers.get(context_id)
            if existing is not None:
                existing.setdefault("run_ids", set()).add(ctx.run_id)
            if action == "launch":
                return await self._launch(
                    ctx.run_id,
                    context_id,
                    profile,
                    bool(arguments.get("headless", True)),
                )
            if action == "goto":
                return await self._goto(
                    context_id,
                    arguments.get("url") or "",
                    _timeout_ms(arguments.get("timeout_s"), 30),
                )
            if action == "content":
                return await self._content(context_id)
            if action == "click":
                return await self._click(
                    context_id,
                    arguments.get("selector") or "",
                    _timeout_ms(arguments.get("timeout_s"), 10),
                )
            if action == "type":
                return await self._type(
                    context_id,
                    arguments.get("selector") or "",
                    arguments.get("text") or "",
                    _timeout_ms(arguments.get("timeout_s"), 10),
                )
            if action == "screenshot":
                return await self._screenshot(context_id, profile)
            if action == "close":
                return await self._close(context_id)
            return ToolResult(
                tool_call_id="",
                name="browser",
                content=f"Unknown action: {action}",
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                name="browser",
                content=f"Browser error: {exc}",
                is_error=True,
            )

    async def _ensure_pw(self) -> Any:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        return self._playwright

    async def _launch(
        self, run_id: str, context_id: str, profile: Path, headless: bool
    ) -> ToolResult:
        if context_id in self._browsers:
            return ToolResult(
                tool_call_id="",
                name="browser",
                content=f"Already launched context={context_id}",
            )
        pw = await self._ensure_pw()
        profile.mkdir(parents=True, exist_ok=True)
        # Isolated user data dir — never the real user Chrome profile
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
            viewport={"width": 1280, "height": 720},
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        self._browsers[context_id] = {
            "context": browser,
            "page": page,
            "run_ids": {run_id},
        }
        return ToolResult(
            tool_call_id="",
            name="browser",
            content=f"Launched isolated browser context={context_id} profile={profile}",
        )

    async def _get_page(self, context_id: str) -> Any:
        entry = self._browsers.get(context_id)
        if not entry:
            raise RuntimeError(f"No browser context '{context_id}'. Call action=launch first.")
        return entry["page"]

    async def _goto(self, context_id: str, url: str, timeout_ms: int) -> ToolResult:
        page = await self._get_page(context_id)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return ToolResult(
            tool_call_id="", name="browser", content=f"Navigated to {page.url}"
        )

    async def _content(self, context_id: str) -> ToolResult:
        page = await self._get_page(context_id)
        title = await page.title()
        text = await page.inner_text("body")
        if len(text) > 30_000:
            text = text[:30_000] + "\n...[truncated]"
        return ToolResult(
            tool_call_id="",
            name="browser",
            content=f"title={title}\nurl={page.url}\n{text}",
        )

    async def _click(self, context_id: str, selector: str, timeout_ms: int) -> ToolResult:
        page = await self._get_page(context_id)
        await page.click(selector, timeout=timeout_ms)
        return ToolResult(tool_call_id="", name="browser", content=f"Clicked {selector}")

    async def _type(
        self, context_id: str, selector: str, text: str, timeout_ms: int
    ) -> ToolResult:
        page = await self._get_page(context_id)
        await page.fill(selector, text, timeout=timeout_ms)
        return ToolResult(tool_call_id="", name="browser", content=f"Typed into {selector}")

    async def _screenshot(self, context_id: str, profile: Path) -> ToolResult:
        page = await self._get_page(context_id)
        out = profile / "shot.png"
        await page.screenshot(path=str(out))
        return ToolResult(
            tool_call_id="", name="browser", content=f"Screenshot saved to {out}"
        )

    async def _close(self, context_id: str) -> ToolResult:
        entry = self._browsers.pop(context_id, None)
        if entry:
            await entry["context"].close()
        return ToolResult(
            tool_call_id="", name="browser", content=f"Closed context={context_id}"
        )

    async def cancel_run(self, run_id: str) -> None:
        """Close browser contexts touched by a cancelled or timed-out run."""
        context_ids = [
            context_id
            for context_id, entry in self._browsers.items()
            if run_id in entry.get("run_ids", set())
        ]
        for context_id in context_ids:
            await self._close(context_id)

    def release_run(self, run_id: str) -> None:
        for entry in self._browsers.values():
            entry.get("run_ids", set()).discard(run_id)

    async def close_all(self) -> None:
        for context_id in list(self._browsers):
            try:
                await self._close(context_id)
            except Exception:  # noqa: BLE001
                pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None


def _timeout_ms(value: Any, default_s: float) -> int:
    try:
        seconds = float(value) if value is not None else default_s
    except (TypeError, ValueError):
        seconds = default_s
    if not math.isfinite(seconds):
        seconds = default_s
    return max(1, int(min(120.0, max(0.001, seconds)) * 1000))
