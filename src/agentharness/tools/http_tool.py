"""HTTP request tool (network effect) — SSRF-guarded by the shared EgressPolicy."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from urllib.parse import urljoin

import httpx

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec
from agentharness.security.egress import EgressError, EgressPolicy, default_policy


class HttpTool:
    def __init__(self, policy: EgressPolicy | None = None) -> None:
        self.policy = policy or default_policy()

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
        timeout = min(
            float(arguments.get("timeout_s") or self.policy.timeout_s),
            self.policy.timeout_s,
        )
        max_bytes = self.policy.max_response_bytes
        if ctx.cancel_event and ctx.cancel_event.is_set():
            return self._err("cancelled")

        try:
            return await self._request_with_redirects(
                ctx, url, method, headers, body, timeout, max_bytes
            )
        except EgressError as exc:
            return self._err(f"blocked by egress policy: {exc}")
        except Exception as exc:  # noqa: BLE001
            return self._err(f"HTTP error: {exc}")

    async def _request_with_redirects(
        self,
        ctx: ToolContext,
        url: str,
        method: str,
        headers: dict[str, Any],
        body: Any,
        timeout: float,
        max_bytes: int,
    ) -> ToolResult:
        current = url
        redirects = 0
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            while True:
                # Re-validate every hop: scheme + resolved IPs (fails closed).
                target = self.policy.validate(current)
                resp = await client.send(
                    client.build_request(
                        method, current, headers=headers, content=body
                    ),
                    stream=True,
                )
                try:
                    # Defense-in-depth: the client resolved DNS itself; verify the
                    # peer we actually connected to is still allowed (anti-rebinding).
                    self._verify_peer(resp, target.host)
                    status_code = resp.status_code
                    if (
                        status_code in (301, 302, 303, 307, 308)
                        and "location" in resp.headers
                    ):
                        redirects += 1
                        if redirects > self.policy.max_redirects:
                            return self._err(
                                f"too many redirects (>{self.policy.max_redirects})"
                            )
                        current = urljoin(current, resp.headers["location"])
                        # 303 (and legacy 301/302 on POST) collapse to GET.
                        if status_code == 303 or (
                            status_code in (301, 302) and method not in ("GET", "HEAD")
                        ):
                            method = "GET"
                            body = None
                        continue

                    response_body = bytearray()
                    truncated = False
                    cancelled = False
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        if ctx.cancel_event and ctx.cancel_event.is_set():
                            cancelled = True
                            break
                        remaining = max_bytes + 1 - len(response_body)
                        response_body.extend(chunk[:remaining])
                        if len(response_body) > max_bytes:
                            truncated = True
                            break
                    if cancelled:
                        return self._err("cancelled")
                    encoding = resp.encoding or "utf-8"
                    text = bytes(response_body[:max_bytes]).decode(
                        encoding, errors="replace"
                    )
                    if truncated:
                        text += "\n...[truncated]"
                    return ToolResult(
                        tool_call_id="",
                        name="http_request",
                        content=f"status={status_code}\n{text}",
                        is_error=status_code >= 400,
                    )
                finally:
                    # Close tolerantly: we stopped reading early on purpose, so a
                    # transport error while tearing down a half-consumed body (the
                    # server is still pushing) must not mask a successful read.
                    with suppress(Exception):
                        await resp.aclose()

    def _verify_peer(self, resp: httpx.Response, host: str) -> None:
        """Abort if the connected peer IP is not allowed (DNS rebinding guard)."""
        network_stream = resp.extensions.get("network_stream")
        if network_stream is None:
            return
        try:
            peer = network_stream.get_extra_info("server_addr")
        except Exception:  # noqa: BLE001
            return
        if not peer:
            return
        peer_ip = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
        if not self.policy.peer_ip_allowed(str(peer_ip), host):
            raise EgressError(f"connected peer not allowed: {peer_ip}")

    @staticmethod
    def _err(message: str) -> ToolResult:
        return ToolResult(
            tool_call_id="", name="http_request", content=message, is_error=True
        )
