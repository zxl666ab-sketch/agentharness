"""HTTP request tool (network effect) — SSRF-guarded by the shared EgressPolicy."""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from urllib.parse import urljoin

import httpx

from agentharness.contracts import EffectKind, ReplayPolicy, ToolContext, ToolResult, ToolSpec
from agentharness.security.egress import (
    EgressError,
    EgressPolicy,
    ValidatedTarget,
    default_policy,
)

_CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "x-auth-token",
        "x-amz-security-token",
        "metadata-flavor",
    }
)


def _strip_credential_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Drop credential-bearing headers supplied by the model.

    Header values here come straight from tool arguments, so a prompt-injected
    model could attach a stolen or guessed credential to any allowed origin, or set
    the metadata-service headers that cloud IMDS endpoints gate on. Credentials
    must be injected by Harness configuration, never chosen by the model.
    """
    if not isinstance(headers, dict):
        return {}
    return {
        key: value
        for key, value in headers.items()
        if str(key).strip().lower() not in _CREDENTIAL_HEADERS
    }


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
                    "url": {"type": "string", "minLength": 1, "maxLength": 8_192},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "maxProperties": 64,
                        "additionalProperties": {"type": "string", "maxLength": 8_192},
                    },
                    "body": {"type": "string", "maxLength": 262_144},
                    "timeout_s": {"type": "number", "minimum": 0.01, "maximum": 60},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            effect=EffectKind.network,
            max_attempts=2,
            timeout_s=60,
        )

    def effect_for(self, arguments: dict[str, Any]) -> EffectKind:
        method = str(arguments.get("method") or "GET").upper()
        return EffectKind.network if method in {"GET", "HEAD"} else EffectKind.destructive

    def replay_policy_for(self, arguments: dict[str, Any]) -> ReplayPolicy:
        method = str(arguments.get("method") or "GET").upper()
        return ReplayPolicy.safe if method in {"GET", "HEAD"} else ReplayPolicy.never

    def parallel_safe_for(self, arguments: dict[str, Any]) -> bool:
        method = str(arguments.get("method") or "GET").upper()
        return method in {"GET", "HEAD"}

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        url = arguments.get("url") or ""
        method = (arguments.get("method") or "GET").upper()
        headers = _strip_credential_headers(arguments.get("headers") or {})
        body = arguments.get("body")
        replay_safe = method in {"GET", "HEAD"}
        timeout = min(
            float(arguments.get("timeout_s") or self.policy.timeout_s),
            self.policy.timeout_s,
        )
        max_bytes = self.policy.max_response_bytes
        if ctx.cancel_event and ctx.cancel_event.is_set():
            return self._err(
                "cancelled",
                code="cancelled",
                category="cancellation",
                retryable=replay_safe,
                recovery_hint=(
                    "Resume the run to retry this safe request."
                    if replay_safe
                    else "Inspect the remote outcome before submitting the write again."
                ),
            )

        try:
            return await self._request_with_redirects(
                ctx, url, method, headers, body, timeout, max_bytes
            )
        except EgressError as exc:
            return self._err(
                f"blocked by egress policy: {exc}",
                code="egress_blocked",
                category="security",
                recovery_hint="Use an origin allowed by the configured egress policy.",
            )
        except httpx.TimeoutException as exc:
            return self._err(
                f"HTTP error: request timed out: {exc}",
                code="http_timeout" if replay_safe else "outcome_indeterminate",
                category="timeout" if replay_safe else "recovery",
                retryable=replay_safe,
                recovery_hint=(
                    "Retry the safe request or increase its governed timeout."
                    if replay_safe
                    else "Inspect the remote outcome before submitting the write again."
                ),
            )
        except httpx.TransportError as exc:
            return self._err(
                f"HTTP transport error: {exc}",
                code=(
                    "http_transport_error" if replay_safe else "outcome_indeterminate"
                ),
                category="network" if replay_safe else "recovery",
                retryable=replay_safe,
                recovery_hint=(
                    "Check network reachability and retry the safe request."
                    if replay_safe
                    else "Inspect the remote outcome before submitting the write again."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(
                f"HTTP error: {exc}",
                code="http_error",
                category="network",
            )

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
        replay_safe = method in {"GET", "HEAD"}
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
                    self._verify_peer(resp, target)
                    status_code = resp.status_code
                    if (
                        status_code in (301, 302, 303, 307, 308)
                        and "location" in resp.headers
                    ):
                        redirects += 1
                        if redirects > self.policy.max_redirects:
                            return self._err(
                                f"too many redirects (>{self.policy.max_redirects})",
                                code="too_many_redirects",
                                category="network",
                                recovery_hint="Use the final trusted URL directly.",
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
                        return self._err(
                            "cancelled",
                            code="cancelled",
                            category="cancellation",
                            retryable=replay_safe,
                            recovery_hint=(
                                "Resume the run to retry this safe request."
                                if replay_safe
                                else "Inspect the remote outcome before submitting the write again."
                            ),
                        )
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
                        error_code="http_status" if status_code >= 400 else None,
                        error_category="http" if status_code >= 400 else None,
                        retryable=replay_safe
                        and (status_code in {408, 425, 429} or status_code >= 500),
                        recovery_hint=(
                            "Retry the safe request after the remote service recovers."
                            if replay_safe
                            and (status_code in {408, 425, 429} or status_code >= 500)
                            else "Inspect the remote outcome before submitting the write again."
                            if not replay_safe and status_code >= 500
                            else "Correct the request before retrying."
                            if status_code >= 400
                            else None
                        ),
                    )
                finally:
                    # Close tolerantly: we stopped reading early on purpose, so a
                    # transport error while tearing down a half-consumed body (the
                    # server is still pushing) must not mask a successful read.
                    with suppress(Exception):
                        await resp.aclose()

    def _verify_peer(self, resp: httpx.Response, target: ValidatedTarget) -> None:
        """Abort if the connected peer IP is not allowed (DNS rebinding guard).

        Fails closed: if the peer address cannot be determined we cannot rule out
        that DNS re-resolved to a private target between validation and connect,
        so the response is rejected rather than returned unverified.
        """
        network_stream = resp.extensions.get("network_stream")
        if network_stream is None:
            raise EgressError(
                "cannot verify connected peer address; refusing unverified response"
            )
        try:
            peer = network_stream.get_extra_info("server_addr")
        except Exception as exc:  # noqa: BLE001
            raise EgressError(
                f"cannot verify connected peer address: {exc}"
            ) from exc
        if not peer:
            raise EgressError(
                "connected peer address unavailable; refusing unverified response"
            )
        peer_ip = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
        if not self.policy.peer_ip_allowed(
            str(peer_ip), target.host, scheme=target.scheme, port=target.port
        ):
            raise EgressError(f"connected peer not allowed: {peer_ip}")

    @staticmethod
    def _err(
        message: str,
        *,
        code: str,
        category: str,
        retryable: bool = False,
        recovery_hint: str | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            name="http_request",
            content=message,
            is_error=True,
            error_code=code,
            error_category=category,
            retryable=retryable,
            recovery_hint=recovery_hint,
        )
