"""Tool base utilities."""

from __future__ import annotations

from typing import Any

from agentharness.contracts import EffectKind, ToolResult, ToolSpec


class FunctionTool:
    """Simple function-backed tool."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        effect: EffectKind,
        handler: Any,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            effect=effect,
            requires_approval=effect
            not in (EffectKind.pure, EffectKind.workspace_read),
        )
        self._handler = handler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, ctx: Any, arguments: dict[str, Any]) -> ToolResult:
        return await self._handler(ctx, arguments)
