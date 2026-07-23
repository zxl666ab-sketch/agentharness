"""MCP client tools — stdio + streamable HTTP via official SDK; fault-isolated.

Effects are computed *from the arguments*, not fixed on the spec: listing tools is
``pure``, an HTTP connect is ``network``, and stdio connect / tool call are
``destructive`` (they spawn processes or run arbitrary server-side code). The engine
reads the dynamic effect via ``effect_for`` so approval gating and the scheduler use
the real effect of each action rather than a lowest-common-denominator label.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec
from agentharness.security.egress import EgressError, EgressPolicy, default_policy
from agentharness.security.redaction import Redactor, default_redactor

logger = logging.getLogger(__name__)

# Environment variables an MCP stdio child may inherit. Everything else is dropped so
# API keys / secrets in the harness environment never leak into a spawned MCP server.
_MCP_ENV_WHITELIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "TERM",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "USERNAME",
    "USER",
    "SHELL",
    "PWD",
}


def mcp_env_whitelist() -> dict[str, str]:
    """Explicit safe environment for MCP stdio children (no implicit SDK default)."""
    return {k: v for k, v in os.environ.items() if k.upper() in _MCP_ENV_WHITELIST}


class MCPBridge:
    """Manages MCP server connections. Failures are isolated from the main run."""

    def __init__(
        self,
        redactor: Redactor | None = None,
        policy: EgressPolicy | None = None,
    ) -> None:
        self._sessions: dict[str, Any] = {}
        self._tools_cache: dict[str, list[dict[str, Any]]] = {}
        self.redactor = redactor or default_redactor
        self.policy = policy or default_policy()

    async def connect_stdio(self, name: str, command: str, args: list[str] | None = None) -> str:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # Explicit env whitelist — do NOT rely on the SDK's implicit default,
            # which may forward the full parent environment (including secrets).
            params = StdioServerParameters(
                command=command,
                args=args or [],
                env=mcp_env_whitelist(),
            )
            # Keep references so connection stays open
            cm = stdio_client(params)
            read, write = await cm.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            self._sessions[name] = {"session": session, "cm": cm, "kind": "stdio"}
            tools = await session.list_tools()
            self._tools_cache[name] = [
                {"name": t.name, "description": t.description or ""} for t in tools.tools
            ]
            return f"Connected MCP stdio server '{name}' tools={len(self._tools_cache[name])}"
        except Exception as exc:  # noqa: BLE001
            safe_name = self.redactor.redact_text(name)
            safe_error = self.redactor.redact_text(str(exc))
            logger.warning("MCP stdio connect failed (%s): %s", safe_name, safe_error)
            return f"MCP connect failed (isolated): {safe_error}"

    async def connect_http(self, name: str, url: str) -> str:
        # SSRF guard: same egress policy as http_request / browser.
        try:
            self.policy.validate(url)
        except EgressError as exc:
            safe_error = self.redactor.redact_text(str(exc))
            return f"MCP connect blocked by egress policy: {safe_error}"
        try:
            from mcp import ClientSession

            # streamable HTTP transport
            try:
                from mcp.client.streamable_http import streamablehttp_client

                cm = streamablehttp_client(url)
                read, write, _ = await cm.__aenter__()
            except ImportError:
                from mcp.client.sse import sse_client

                cm = sse_client(url)
                read, write = await cm.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            self._sessions[name] = {"session": session, "cm": cm, "kind": "http"}
            tools = await session.list_tools()
            self._tools_cache[name] = [
                {"name": t.name, "description": t.description or ""} for t in tools.tools
            ]
            return f"Connected MCP HTTP server '{name}' tools={len(self._tools_cache[name])}"
        except Exception as exc:  # noqa: BLE001
            safe_name = self.redactor.redact_text(name)
            safe_error = self.redactor.redact_text(str(exc))
            logger.warning("MCP HTTP connect failed (%s): %s", safe_name, safe_error)
            return f"MCP connect failed (isolated): {safe_error}"

    async def list_tools(self, name: str | None = None) -> list[dict[str, Any]]:
        if name:
            return self._tools_cache.get(name, [])
        out: list[dict[str, Any]] = []
        for sname, tools in self._tools_cache.items():
            for t in tools:
                out.append({**t, "server": sname})
        return out

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        entry = self._sessions.get(server)
        if not entry:
            return f"MCP server '{server}' not connected"
        try:
            session = entry["session"]
            result = await self._call_with_cancel(session, tool, arguments, cancel_event)
            if result is None:
                return "MCP tool cancelled"
            parts: list[str] = []
            for c in getattr(result, "content", []) or []:
                text = getattr(c, "text", None)
                if text:
                    parts.append(text)
                else:
                    parts.append(str(c))
            return "\n".join(parts) or "(empty)"
        except Exception as exc:  # noqa: BLE001
            safe_server = self.redactor.redact_text(server)
            safe_tool = self.redactor.redact_text(tool)
            safe_error = self.redactor.redact_text(str(exc))
            logger.warning("MCP call failed %s/%s: %s", safe_server, safe_tool, safe_error)
            return f"MCP tool error (isolated): {safe_error}"

    async def _call_with_cancel(
        self,
        session: Any,
        tool: str,
        arguments: dict[str, Any],
        cancel_event: asyncio.Event | None,
    ) -> Any:
        """Await the tool call while watching cancel_event; clean up the loser task."""
        call_task = asyncio.ensure_future(session.call_tool(tool, arguments))
        if cancel_event is None:
            return await call_task
        cancel_task = asyncio.ensure_future(cancel_event.wait())
        try:
            done, pending = await asyncio.wait(
                {call_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if call_task in done:
                return call_task.result()
            # Cancelled first — abort the in-flight call and drain it.
            call_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await call_task
            return None
        finally:
            for task in (call_task, cancel_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task

    async def close_all(self) -> None:
        for name, entry in list(self._sessions.items()):
            try:
                await entry["session"].__aexit__(None, None, None)
                await entry["cm"].__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._sessions.pop(name, None)


def mcp_effect_for(arguments: dict[str, Any]) -> EffectKind:
    """Dynamic effect from the requested MCP action (the real blast radius).

    - list_tools: read-only catalog lookup → pure
    - connect_http: opens a network connection → network
    - connect_stdio: spawns a local process → destructive
    - call_tool: runs arbitrary server-side code → destructive
    """
    action = (arguments or {}).get("action") or ""
    if action == "list_tools":
        return EffectKind.pure
    if action == "connect_http":
        return EffectKind.network
    # connect_stdio, call_tool, and any unknown/mutating action fail safe as destructive.
    return EffectKind.destructive


class MCPTool:
    def __init__(self, bridge: MCPBridge | None = None) -> None:
        self.bridge = bridge or MCPBridge()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mcp",
            description=(
                "Interact with MCP servers. Actions: connect_stdio, connect_http, "
                "list_tools, call_tool. Server faults are isolated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["connect_stdio", "connect_http", "list_tools", "call_tool"],
                    },
                    "server": {"type": "string"},
                    "command": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "url": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["action"],
            },
            # Static spec is the maximum (destructive); the engine narrows per-call via
            # effect_for so a bare list_tools does not demand destructive approval.
            effect=EffectKind.destructive,
        )

    def effect_for(self, arguments: dict[str, Any]) -> EffectKind:
        return mcp_effect_for(arguments)

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = arguments.get("action") or ""
        cancel_event = ctx.cancel_event
        try:
            if action == "connect_stdio":
                msg = await self.bridge.connect_stdio(
                    arguments.get("server") or "default",
                    arguments.get("command") or "",
                    arguments.get("args"),
                )
                return ToolResult(tool_call_id="", name="mcp", content=msg)
            if action == "connect_http":
                msg = await self.bridge.connect_http(
                    arguments.get("server") or "default",
                    arguments.get("url") or "",
                )
                is_err = "blocked by egress" in msg or "connect failed" in msg
                return ToolResult(tool_call_id="", name="mcp", content=msg, is_error=is_err)
            if action == "list_tools":
                tools = await self.bridge.list_tools(arguments.get("server"))
                return ToolResult(
                    tool_call_id="",
                    name="mcp",
                    content=str(tools) if tools else "No tools",
                )
            if action == "call_tool":
                out = await self.bridge.call_tool(
                    arguments.get("server") or "default",
                    arguments.get("tool") or "",
                    arguments.get("arguments") or {},
                    cancel_event=cancel_event,
                )
                is_err = (
                    out.startswith("MCP tool error")
                    or "not connected" in out
                    or out == "MCP tool cancelled"
                )
                return ToolResult(
                    tool_call_id="", name="mcp", content=out, is_error=is_err
                )
            return ToolResult(
                tool_call_id="", name="mcp", content=f"Unknown action {action}", is_error=True
            )
        except Exception as exc:  # noqa: BLE001
            # Never crash the main run
            return ToolResult(
                tool_call_id="",
                name="mcp",
                content=f"MCP fault isolated: {exc}",
                is_error=True,
            )
