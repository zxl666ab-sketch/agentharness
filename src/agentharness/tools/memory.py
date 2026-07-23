"""Explicit memory tools — long-term via SQLite FTS5."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec


class MemoryStoreTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_store",
            description="Store a fact into long-term memory (explicit only; never auto-promoted).",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "scope": {"type": "string", "default": "global"},
                    "source": {"type": "string", "default": "agent"},
                },
                "required": ["content"],
            },
            effect=EffectKind.workspace_write,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        content = arguments.get("content") or ""
        scope = arguments.get("scope") or "global"
        source = arguments.get("source") or "agent"
        if not content.strip():
            return ToolResult(
                tool_call_id="", name="memory_store", content="empty content", is_error=True
            )
        # harness storage via metadata path
        storage = None
        if ctx.harness is not None:
            storage = getattr(ctx.harness, "storage", None)
        if storage is None:
            return ToolResult(
                tool_call_id="",
                name="memory_store",
                content="storage unavailable",
                is_error=True,
            )
        mid = storage.add_memory(content, source=source, scope=scope)
        return ToolResult(
            tool_call_id="",
            name="memory_store",
            content=f"Stored memory id={mid}",
        )


class MemorySearchTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_search",
            description="Search long-term memory by keyword/FTS query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            effect=EffectKind.pure,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query") or ""
        limit = int(arguments.get("limit") or 5)
        storage = None
        if ctx.harness is not None:
            storage = getattr(ctx.harness, "storage", None)
        if storage is None:
            return ToolResult(
                tool_call_id="",
                name="memory_search",
                content="storage unavailable",
                is_error=True,
            )
        rows = storage.search_memories(query, limit=limit)
        if not rows:
            return ToolResult(tool_call_id="", name="memory_search", content="No memories found")
        lines = [f"- [{r.get('scope')}] {r['content']}" for r in rows]
        return ToolResult(tool_call_id="", name="memory_search", content="\n".join(lines))
