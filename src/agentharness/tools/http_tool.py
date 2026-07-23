"""HTTP request tool (network effect)."""

from __future__ import annotations

from typing import Any

import httpx

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec

_MAX_RESPONSE_BYTES = 50_000


class HttpTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description="Perform an HTTP request. Returns status and truncated body.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "default": "GET"},
                    "headers": {"type": "object"},
                    "body": {"type": "string"},
                    "timeout_s": {"type": "number"},
                },
                "required": ["url"],
            },
            effect=EffectKind.network,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        url = arguments.get("url") or ""
        method = (arguments.get("method") or "GET").upper()
        headers = arguments.get("headers") or {}
        body = arguments.get("body")
        timeout = float(arguments.get("timeout_s") or 30)
        if ctx.cancel_event and ctx.cancel_event.is_set():
            return ToolResult(
                tool_call_id="", name="http_request", content="cancelled", is_error=True
            )
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream(
                    method, url, headers=headers, content=body
                ) as resp:
                    response_body = bytearray()
                    truncated = False
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        if ctx.cancel_event and ctx.cancel_event.is_set():
                            return ToolResult(
                                tool_call_id="",
                                name="http_request",
                                content="cancelled",
                                is_error=True,
                            )
                        remaining = _MAX_RESPONSE_BYTES + 1 - len(response_body)
                        response_body.extend(chunk[:remaining])
                        if len(response_body) > _MAX_RESPONSE_BYTES:
                            truncated = True
                            break
                    encoding = resp.encoding or "utf-8"
                    text = bytes(response_body[:_MAX_RESPONSE_BYTES]).decode(
                        encoding, errors="replace"
                    )
                    status_code = resp.status_code
            if truncated:
                text += "\n...[truncated]"
            return ToolResult(
                tool_call_id="",
                name="http_request",
                content=f"status={status_code}\n{text}",
                is_error=status_code >= 400,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                name="http_request",
                content=f"HTTP error: {exc}",
                is_error=True,
            )
