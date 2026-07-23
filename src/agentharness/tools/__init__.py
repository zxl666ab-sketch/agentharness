"""Built-in tools registry factory."""

from __future__ import annotations

from typing import Any

from agentharness.tools.browser import BrowserTool
from agentharness.tools.delegate import DelegateTool
from agentharness.tools.fs import ReadFileTool, SearchFilesTool, WriteFileTool
from agentharness.tools.http_tool import HttpTool
from agentharness.tools.mcp_tool import MCPBridge, MCPTool
from agentharness.tools.memory import MemorySearchTool, MemoryStoreTool
from agentharness.tools.shell import ShellTool
from agentharness.tools.skills import ListSkillsTool


def create_default_tools(
    *,
    process_registry: dict[str, list[Any]] | None = None,
    mcp_bridge: MCPBridge | None = None,
) -> dict[str, Any]:
    tools = [
        ReadFileTool(),
        WriteFileTool(),
        SearchFilesTool(),
        ShellTool(process_registry=process_registry),
        HttpTool(),
        BrowserTool(),
        MCPTool(bridge=mcp_bridge),
        MemoryStoreTool(),
        MemorySearchTool(),
        ListSkillsTool(),
        DelegateTool(),
    ]
    return {t.spec.name: t for t in tools}


__all__ = ["create_default_tools", "MCPBridge"]
