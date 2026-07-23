"""MCP client tools — stdio + streamable HTTP via official SDK; fault-isolated."""

from __future__ import annotations

import logging
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec
from agentharness.security.redaction import Redactor, default_redactor

logger = logging.getLogger(__name__)


class MCPBridge:
    """Manages MCP server connections. Failures are isolated from the main run."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        self._sessions: dict[str, Any] = {}
        self._tools_cache: dict[str, list[dict[str, Any]]] = {}
        self.redactor = redactor or default_redactor

    async def connect_stdio(self, name: str, command: str, args: list[str] | None = None) -> str:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=command, args=args or [])
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

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        entry = self._sessions.get(server)
        if not entry:
            return f"MCP server '{server}' not connected"
        try:
            session = entry["session"]
            result = await session.call_tool(tool, arguments)
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

    async def close_all(self) -> None:
        for name, entry in list(self._sessions.items()):
            try:
                await entry["session"].__aexit__(None, None, None)
                await entry["cm"].__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._sessions.pop(name, None)


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
            effect=EffectKind.network,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = arguments.get("action") or ""
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
                return ToolResult(tool_call_id="", name="mcp", content=msg)
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
                )
                is_err = out.startswith("MCP tool error") or "not connected" in out
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
