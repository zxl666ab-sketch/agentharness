"""Explicit memory tools — long-term via SQLite FTS5."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import EffectKind, ReplayPolicy, ToolContext, ToolResult, ToolSpec
from agentharness.memory_scope import (
    resolve_memory_scope,
    session_memory_scope,
    workspace_memory_scope,
)


class MemoryStoreTool:
    requires_confirmation = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_store",
            description="Store a fact into long-term memory (explicit only; never auto-promoted).",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 200_000},
                    "scope": {"type": "string", "minLength": 1, "maxLength": 512, "default": "workspace"},
                    "source": {"type": "string", "minLength": 1, "maxLength": 512, "default": "agent"},
                    "expires_at": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_write,
            replay_policy=ReplayPolicy.reconcile,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        content = arguments.get("content") or ""
        scope = resolve_memory_scope(
            arguments.get("scope"), cwd=ctx.cwd, session_id=ctx.session_id
        )
        source = arguments.get("source") or "agent"
        expires_at = arguments.get("expires_at")
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
        mid = storage.add_memory(
            content, source=source, scope=scope, expires_at=expires_at
        )
        memory = storage.get_memory(mid) or {}
        return ToolResult(
            tool_call_id="",
            name="memory_store",
            content=(
                f"Stored memory id={mid} scope={scope} "
                f"content_hash=sha256:{memory.get('content_hash', '')}"
            ),
        )

    async def reconcile(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult:
        # add_memory is content-hash deduplicated inside one scope, so invoking the
        # normal path safely identifies an already-completed interrupted insert.
        return await self.run(ctx, arguments)


class MemorySearchTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_search",
            description="Search long-term memory by keyword/FTS query.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 4_096},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
                    "scope": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 512,
                        "description": (
                            "workspace (default, then global), session, global, or an "
                            "explicit named scope"
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            parallel_safe=True,
            max_attempts=2,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query") or ""
        limit = int(arguments.get("limit") or 5)
        requested_scope = arguments.get("scope")
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
        if requested_scope:
            scopes = [
                resolve_memory_scope(
                    str(requested_scope), cwd=ctx.cwd, session_id=ctx.session_id
                )
            ]
        else:
            scopes = [
                scope
                for scope in (
                    workspace_memory_scope(ctx.cwd),
                    session_memory_scope(ctx.session_id),
                    "global",
                )
                if scope
            ]
        rows = storage.search_memories(query, limit=limit, scopes=scopes)
        if not rows:
            return ToolResult(tool_call_id="", name="memory_search", content="No memories found")
        lines = [f"- [{r.get('scope')}] {r['content']}" for r in rows]
        return ToolResult(tool_call_id="", name="memory_search", content="\n".join(lines))


class MemoryUpdateTool:
    requires_confirmation = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_update",
            description="Update one confirmed long-term memory by id.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "content": {"type": "string", "minLength": 1, "maxLength": 200_000},
                    "scope": {"type": "string", "minLength": 1, "maxLength": 512},
                    "source": {"type": "string", "minLength": 1, "maxLength": 512},
                    "expires_at": {"type": "string", "minLength": 1, "maxLength": 128},
                    "expected_hash": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_write,
            replay_policy=ReplayPolicy.reconcile,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        storage = getattr(ctx.harness, "storage", None) if ctx.harness is not None else None
        if storage is None:
            return ToolResult(
                tool_call_id="", name="memory_update", content="storage unavailable", is_error=True
            )
        scope = arguments.get("scope")
        if scope is not None:
            scope = resolve_memory_scope(str(scope), cwd=ctx.cwd, session_id=ctx.session_id)
        try:
            row = storage.update_memory(
                str(arguments.get("id") or ""),
                content=arguments.get("content"),
                source=arguments.get("source"),
                scope=scope,
                expires_at=arguments.get("expires_at"),
                expected_hash=arguments.get("expected_hash"),
            )
        except (KeyError, ValueError) as exc:
            return ToolResult(
                tool_call_id="",
                name="memory_update",
                content=str(exc),
                is_error=True,
                error_code="memory_update_conflict",
                error_category="memory",
                retryable=isinstance(exc, ValueError),
            )
        return ToolResult(
            tool_call_id="",
            name="memory_update",
            content=(
                f"Updated memory id={row['id']} scope={row['scope']} "
                f"content_hash=sha256:{row['content_hash']}"
            ),
        )

    async def reconcile(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult | None:
        storage = getattr(ctx.harness, "storage", None) if ctx.harness is not None else None
        row = storage.get_memory(str(arguments.get("id") or "")) if storage else None
        if row is None:
            return _indeterminate_memory_result("Updated memory no longer exists")
        expected_hash = str(arguments.get("expected_hash") or "").removeprefix("sha256:")
        if expected_hash and row.get("content_hash") == expected_hash:
            return None
        desired_content = arguments.get("content")
        if desired_content is not None:
            normalized = " ".join(str(desired_content).split())
            current = " ".join(str(row.get("content") or "").split())
            if normalized == current:
                return ToolResult(
                    tool_call_id="",
                    name="memory_update",
                    content=f"Reconciled updated memory id={row['id']}",
                )
        return _indeterminate_memory_result("Memory state differs after interrupted update")


class MemoryDeleteTool:
    requires_confirmation = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="memory_delete",
            description="Delete one confirmed long-term memory by id.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "expected_hash": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_write,
            replay_policy=ReplayPolicy.reconcile,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        storage = getattr(ctx.harness, "storage", None) if ctx.harness is not None else None
        if storage is None:
            return ToolResult(
                tool_call_id="", name="memory_delete", content="storage unavailable", is_error=True
            )
        try:
            deleted = storage.delete_memory(
                str(arguments.get("id") or ""),
                expected_hash=arguments.get("expected_hash"),
            )
        except ValueError as exc:
            return ToolResult(
                tool_call_id="",
                name="memory_delete",
                content=str(exc),
                is_error=True,
                error_code="memory_delete_conflict",
                error_category="memory",
                retryable=True,
            )
        return ToolResult(
            tool_call_id="",
            name="memory_delete",
            content=("Deleted memory" if deleted else "Memory not found"),
            is_error=not deleted,
        )

    async def reconcile(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult | None:
        storage = getattr(ctx.harness, "storage", None) if ctx.harness is not None else None
        row = storage.get_memory(str(arguments.get("id") or "")) if storage else None
        if row is None:
            return ToolResult(
                tool_call_id="", name="memory_delete", content="Reconciled deleted memory"
            )
        expected_hash = str(arguments.get("expected_hash") or "").removeprefix("sha256:")
        if expected_hash and row.get("content_hash") == expected_hash:
            return None
        return _indeterminate_memory_result("Memory still exists with an unexpected version")


def _indeterminate_memory_result(message: str) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        name="memory",
        content=message,
        is_error=True,
        error_code="outcome_indeterminate",
        error_category="recovery",
        retryable=False,
        recovery_hint="Inspect long-term memory before retrying the mutation.",
    )
