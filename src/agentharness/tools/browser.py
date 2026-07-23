"""Playwright browser tool — isolated profile, never user real browser state."""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec
from agentharness.security.egress import EgressError, EgressPolicy, default_policy

_browser_lock_note = "browser ops serialize per context_id via scheduler"


def _safe_segment(value: str) -> bool:
    """True if value is a single relative path segment (no traversal / absolute)."""
    return not (
        not value
        or value in {".", ".."}
        or ".." in value
        or "/" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
        or bool(PureWindowsPath(value).root)
    )


def _profile_dir(data_dir: str, run_id: str, context_id: str) -> Path:
    """Resolve a per-run browser profile dir without allowing path semantics.

    Profiles are isolated per (run_id, context_id) so concurrent runs never share
    an on-disk profile (which would deadlock Playwright's profile lock) or bleed
    cookies/storage across runs.
    """
    if not _safe_segment(context_id):
        raise ValueError("context_id must be a single relative profile name")
    if not _safe_segment(run_id):
        raise ValueError("run_id must be a single relative profile name")

    profiles_root = (Path(data_dir) / "browser_profiles").resolve()
    profile = (profiles_root / run_id / context_id).resolve()
    try:
        profile.relative_to(profiles_root)
    except ValueError as exc:
        raise ValueError("context_id escapes the browser profile directory") from exc
    return profile


class BrowserTool:
    """Minimal browser tool using Playwright with an isolated profile under data_dir."""

    # Engine uses this so scheduler serializes even when context_id is omitted
    browser_bound = True

    def __init__(self, policy: EgressPolicy | None = None) -> None:
        self._playwright = None
        # Keyed by (run_id, context_id) for per-run isolation.
        self._browsers: dict[tuple[str, str], Any] = {}
        self.policy = policy or default_policy()

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
            profile = _profile_dir(ctx.data_dir, ctx.run_id, context_id)
            # Browsers are keyed by (run_id, context_id) so one run never touches
            # another run's live browser even if they share a context_id.
            key = (ctx.run_id, context_id)
            if action == "launch":
                return await self._launch(
                    key,
                    context_id,
                    profile,
                    bool(arguments.get("headless", True)),
                )
            if action == "goto":
                return await self._goto(
                    key,
                    arguments.get("url") or "",
                    _timeout_ms(arguments.get("timeout_s"), 30),
                )
            if action == "content":
                return await self._content(key)
            if action == "click":
                return await self._click(
                    key,
                    arguments.get("selector") or "",
                    _timeout_ms(arguments.get("timeout_s"), 10),
                )
            if action == "type":
                return await self._type(
                    key,
                    arguments.get("selector") or "",
                    arguments.get("text") or "",
                    _timeout_ms(arguments.get("timeout_s"), 10),
                )
            if action == "screenshot":
                return await self._screenshot(key, profile)
            if action == "close":
                return await self._close(key)
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
        self,
        key: tuple[str, str],
        context_id: str,
        profile: Path,
        headless: bool,
    ) -> ToolResult:
        if key in self._browsers:
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
        self._browsers[key] = {
            "context": browser,
            "page": page,
            "run_id": key[0],
        }
        return ToolResult(
            tool_call_id="",
            name="browser",
            content=f"Launched isolated browser context={context_id} profile={profile}",
        )

    async def _get_page(self, key: tuple[str, str]) -> Any:
        entry = self._browsers.get(key)
        if not entry:
            raise RuntimeError(
                f"No browser context '{key[1]}'. Call action=launch first."
            )
        return entry["page"]

    async def _goto(self, key: tuple[str, str], url: str, timeout_ms: int) -> ToolResult:
        # Same SSRF policy as http_request: scheme allowlist + resolved-IP validation.
        try:
            self.policy.validate(url)
        except EgressError as exc:
            return ToolResult(
                tool_call_id="",
                name="browser",
                content=f"blocked by egress policy: {exc}",
                is_error=True,
            )
        page = await self._get_page(key)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return ToolResult(
            tool_call_id="", name="browser", content=f"Navigated to {page.url}"
        )

    async def _content(self, key: tuple[str, str]) -> ToolResult:
        page = await self._get_page(key)
        title = await page.title()
        text = await page.inner_text("body")
        if len(text) > 30_000:
            text = text[:30_000] + "\n...[truncated]"
        return ToolResult(
            tool_call_id="",
            name="browser",
            content=f"title={title}\nurl={page.url}\n{text}",
        )

    async def _click(self, key: tuple[str, str], selector: str, timeout_ms: int) -> ToolResult:
        page = await self._get_page(key)
        await page.click(selector, timeout=timeout_ms)
        return ToolResult(tool_call_id="", name="browser", content=f"Clicked {selector}")

    async def _type(
        self, key: tuple[str, str], selector: str, text: str, timeout_ms: int
    ) -> ToolResult:
        page = await self._get_page(key)
        await page.fill(selector, text, timeout=timeout_ms)
        return ToolResult(tool_call_id="", name="browser", content=f"Typed into {selector}")

    async def _screenshot(self, key: tuple[str, str], profile: Path) -> ToolResult:
        page = await self._get_page(key)
        out = profile / "shot.png"
        await page.screenshot(path=str(out))
        return ToolResult(
            tool_call_id="", name="browser", content=f"Screenshot saved to {out}"
        )

    async def _close(self, key: tuple[str, str]) -> ToolResult:
        entry = self._browsers.pop(key, None)
        if entry:
            await entry["context"].close()
        return ToolResult(
            tool_call_id="", name="browser", content=f"Closed context={key[1]}"
        )

    async def cancel_run(self, run_id: str) -> None:
        """Close browser contexts owned by a cancelled or timed-out run."""
        keys = [key for key in self._browsers if key[0] == run_id]
        for key in keys:
            await self._close(key)

    def release_run(self, run_id: str) -> None:
        # Per-run keying means a completed run's entries are its own; nothing to
        # unshare here. Live contexts are torn down by cancel_run / close_all.
        return None

    async def close_all(self) -> None:
        for key in list(self._browsers):
            try:
                await self._close(key)
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
