"""Delegate tool — creates a real child run."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    EffectKind,
    RunRequest,
    ToolContext,
    ToolResult,
    ToolSpec,
)


class DelegateTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="delegate",
            description=(
                "Delegate a subtask to a child agent. Child is readonly by default. "
                "Parent must summarize child results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Subtask for the child agent"},
                    "allow_write": {
                        "type": "boolean",
                        "description": "Explicitly grant write permission",
                        "default": False,
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tool allow-list for child",
                    },
                },
                "required": ["task"],
            },
            effect=EffectKind.pure,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        task = arguments.get("task") or ""
        allow_write = bool(arguments.get("allow_write", False))
        tools = arguments.get("tools")
        if not task:
            return ToolResult(
                tool_call_id="", name="delegate", content="empty task", is_error=True
            )
        harness = ctx.harness
        if harness is None:
            return ToolResult(
                tool_call_id="", name="delegate", content="harness unavailable", is_error=True
            )

        depth = int((ctx.metadata or {}).get("delegate_depth") or 0)
        budget_raw = (ctx.metadata or {}).get("budget") or {}
        budget = BudgetConfig.model_validate(budget_raw) if budget_raw else BudgetConfig()

        if depth >= budget.max_delegate_depth:
            return ToolResult(
                tool_call_id="",
                name="delegate",
                content=f"Max delegate depth {budget.max_delegate_depth} reached",
                is_error=True,
            )

        # Concurrent children limit checked via active children
        engine = getattr(harness, "engine", None)
        if engine is not None:
            children = engine.child_run_ids(ctx.run_id)
            # count still running
            running = 0
            for cid in children:
                row = harness.storage.get_run(cid)
                if row and row["status"] in ("running", "pending", "waiting_approval"):
                    running += 1
            if running >= budget.max_concurrent_children:
                return ToolResult(
                    tool_call_id="",
                    name="delegate",
                    content=f"Max concurrent children {budget.max_concurrent_children} reached",
                    is_error=True,
                )

        child_req = RunRequest(
            message=task,
            session_id=ctx.session_id,
            provider=(ctx.metadata or {}).get("provider") or "fake",
            model=(ctx.metadata or {}).get("model"),
            approval=ctx.approval_mode
            if isinstance(ctx.approval_mode, ApprovalMode)
            else ApprovalMode(str(ctx.approval_mode)),
            budget=budget,
            cwd=ctx.cwd,
            extra_dirs=ctx.extra_dirs,
            skills_dirs=(ctx.metadata or {}).get("skills_dirs") or [],
            parent_run_id=ctx.run_id,
            root_run_id=(ctx.metadata or {}).get("root_run_id") or ctx.run_id,
            delegate_depth=depth + 1,
            allow_write=allow_write and ctx.allow_write,
            tools=tools,
            metadata={
                "delegated_from": ctx.run_id,
                "parent_tool_call_id": (ctx.metadata or {}).get("tool_call_id"),
                "actor": "delegate",
            },
        )

        try:
            result = await harness.run(child_req)
            # Emit child linkage is handled by engine.create_run
            summary = (
                f"child_run_id={result.run_id} status={result.status.value}\n"
                f"output:\n{result.output[:3000]}"
            )
            if result.error:
                summary += f"\nerror: {result.error}"
            return ToolResult(
                tool_call_id="",
                name="delegate",
                content=summary,
                is_error=result.status.value in ("failed", "cancelled"),
            )
        except Exception as exc:  # noqa: BLE001
            # Child failure must not crash parent — isolate
            return ToolResult(
                tool_call_id="",
                name="delegate",
                content=f"Child agent failed (isolated): {exc}",
                is_error=True,
            )
