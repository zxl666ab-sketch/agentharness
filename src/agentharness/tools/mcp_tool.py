"""MCP client tools — stdio + streamable HTTP via official SDK; fault-isolated.

Effects are computed *from the arguments*, not fixed on the spec: listing tools is
``pure``, an HTTP connect is ``network``, and stdio connect / tool call are
``destructive`` (they spawn processes or run arbitrary server-side code). The engine
reads the dynamic effect via ``effect_for`` so approval gating and the scheduler use
the real effect of each action rather than a lowest-common-denominator label.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from contextlib import suppress
from typing import Any

import httpx

from agentharness.contracts import (
    EffectKind,
    ReplayPolicy,
    ToolContentPart,
    ToolContext,
    ToolResult,
    ToolSpec,
)
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


def _mcp_http_client_factory(
    *,
    headers: dict[str, Any] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Create an MCP client that never follows redirects automatically.

    The MCP SDK defaults to ``follow_redirects=True``.  Redirect targets are not
    revalidated by the harness egress policy, so a redirect could move a request
    from a validated public endpoint to a private network address.  MCP endpoints
    are expected to be stable URLs; fail closed on any redirect instead.
    """

    kwargs: dict[str, Any] = {"follow_redirects": False}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


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

    @staticmethod
    def _tool_descriptor(tool: Any) -> dict[str, Any]:
        schema = (
            getattr(tool, "inputSchema", None)
            or getattr(tool, "input_schema", None)
            or {"type": "object", "properties": {}}
        )
        annotations = getattr(tool, "annotations", None)
        if annotations is None:
            annotation_data: dict[str, Any] = {}
        elif hasattr(annotations, "model_dump"):
            annotation_data = annotations.model_dump(by_alias=True, exclude_none=True)
        elif isinstance(annotations, dict):
            annotation_data = dict(annotations)
        else:
            annotation_data = {}
        return {
            "name": str(getattr(tool, "name", "")),
            "description": str(getattr(tool, "description", "") or ""),
            "input_schema": schema,
            "annotations": annotation_data,
        }

    async def _refresh_tools(self, name: str, session: Any) -> int:
        result = await session.list_tools()
        self._tools_cache[name] = [self._tool_descriptor(tool) for tool in result.tools]
        return len(self._tools_cache[name])

    @staticmethod
    async def _close_resources(session: Any | None, cm: Any | None) -> None:
        if session is not None:
            with suppress(Exception):
                await session.__aexit__(None, None, None)
        if cm is not None:
            with suppress(Exception):
                await cm.__aexit__(None, None, None)

    async def _disconnect(self, name: str) -> None:
        entry = self._sessions.pop(name, None)
        self._tools_cache.pop(name, None)
        if entry is not None:
            await self._close_resources(entry.get("session"), entry.get("cm"))

    async def connect_stdio(self, name: str, command: str, args: list[str] | None = None) -> str:
        await self._disconnect(name)
        cm: Any | None = None
        session: Any | None = None
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
            count = await self._refresh_tools(name, session)
            return f"Connected MCP stdio server '{name}' tools={count}"
        except Exception as exc:  # noqa: BLE001
            self._sessions.pop(name, None)
            self._tools_cache.pop(name, None)
            await self._close_resources(session, cm)
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
        await self._disconnect(name)
        cm: Any | None = None
        session: Any | None = None
        try:
            from mcp import ClientSession

            # streamable HTTP transport
            try:
                from mcp.client.streamable_http import streamablehttp_client
            except ImportError:
                from mcp.client.sse import sse_client

                cm = sse_client(url, httpx_client_factory=_mcp_http_client_factory)
                read, write = await cm.__aenter__()
            else:
                cm = streamablehttp_client(
                    url,
                    httpx_client_factory=_mcp_http_client_factory,
                )
                read, write, _ = await cm.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            self._sessions[name] = {"session": session, "cm": cm, "kind": "http"}
            count = await self._refresh_tools(name, session)
            return f"Connected MCP HTTP server '{name}' tools={count}"
        except Exception as exc:  # noqa: BLE001
            self._sessions.pop(name, None)
            self._tools_cache.pop(name, None)
            await self._close_resources(session, cm)
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

    async def call_tool_result(
        self,
        ctx: ToolContext,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        entry = self._sessions.get(server)
        if not entry:
            return ToolResult(
                tool_call_id="",
                name=tool,
                content=f"MCP server '{server}' not connected",
                is_error=True,
                error_code="mcp_not_connected",
                error_category="mcp",
            )
        try:
            raw = await self._call_with_cancel(
                entry["session"], tool, arguments, ctx.cancel_event
            )
            if raw is None:
                return ToolResult(
                    tool_call_id="",
                    name=tool,
                    content="MCP tool cancelled",
                    is_error=True,
                    error_code="cancelled",
                    error_category="cancellation",
                    retryable=False,
                )
            parts: list[ToolContentPart] = []
            rendered: list[str] = []
            raw_budget = (ctx.metadata or {}).get("budget") or {}
            budget = raw_budget if isinstance(raw_budget, dict) else {}
            artifact_limit = int(budget.get("max_tool_result_bytes") or 1_048_576)
            artifact_bytes = 0
            raw_contents = list(getattr(raw, "content", []) or [])
            prepared_artifacts: dict[int, bytes] = {}
            for content_index, content in enumerate(raw_contents):
                kind = str(getattr(content, "type", "text") or "text")
                payload: bytes | None = None
                if kind == "image" and isinstance(getattr(content, "data", None), str):
                    try:
                        payload = base64.b64decode(content.data, validate=True)
                    except (ValueError, TypeError):
                        payload = None
                resource = getattr(content, "resource", None)
                if resource is not None or kind in {"resource", "resource_link"}:
                    value = resource if resource is not None else content
                    resource_text = getattr(value, "text", None)
                    resource_blob = getattr(value, "blob", None)
                    if resource_text is not None:
                        payload = str(resource_text).encode("utf-8")
                    elif isinstance(resource_blob, str):
                        try:
                            payload = base64.b64decode(resource_blob, validate=True)
                        except (ValueError, TypeError):
                            payload = None
                if payload is not None:
                    artifact_bytes += len(payload)
                    if artifact_bytes > artifact_limit:
                        return ToolResult(
                            tool_call_id="",
                            name=tool,
                            content="MCP artifact result exceeds the governed byte limit",
                            is_error=True,
                            error_code="result_too_large",
                            error_category="budget",
                            retryable=False,
                        )
                    prepared_artifacts[content_index] = payload

            for content_index, content in enumerate(raw_contents):
                kind = str(getattr(content, "type", "text") or "text")
                text = getattr(content, "text", None)
                if text is not None:
                    parts.append(ToolContentPart(type="text", text=str(text)))
                    rendered.append(str(text))
                    continue
                data = getattr(content, "data", None)
                mime = getattr(content, "mimeType", None) or getattr(content, "mime_type", None)
                if kind == "image" and isinstance(data, str) and ctx.harness is not None:
                    payload = prepared_artifacts.get(content_index)
                    if payload is not None:
                        meta = ctx.harness.storage.artifacts.put(
                            payload, content_type=str(mime or "application/octet-stream")
                        )
                        artifact_id = ctx.harness.storage.register_artifact(meta)
                        parts.append(
                            ToolContentPart(
                                type="image",
                                mime_type=str(mime or "application/octet-stream"),
                                artifact_id=artifact_id,
                            )
                        )
                        rendered.append(f"[image artifact:{artifact_id}]")
                        continue
                resource = getattr(content, "resource", None)
                if resource is not None or kind in {"resource", "resource_link"}:
                    value = resource if resource is not None else content
                    uri = getattr(value, "uri", None)
                    resource_mime = getattr(value, "mimeType", None) or getattr(
                        value, "mime_type", None
                    )
                    resource_text = getattr(value, "text", None)
                    artifact_id: str | None = None
                    artifact_payload = prepared_artifacts.get(content_index)
                    if artifact_payload is not None and ctx.harness is not None:
                        meta = ctx.harness.storage.artifacts.put(
                            artifact_payload,
                            content_type=str(resource_mime or "application/octet-stream"),
                        )
                        artifact_id = ctx.harness.storage.register_artifact(meta)
                    resource_data = {
                        "uri": str(uri) if uri is not None else None,
                        "text": str(resource_text) if resource_text is not None else None,
                    }
                    parts.append(
                        ToolContentPart(
                            type="resource",
                            data=resource_data,
                            mime_type=(str(resource_mime) if resource_mime else None),
                            artifact_id=artifact_id,
                        )
                    )
                    rendered.append(
                        f"[resource {resource_data['uri'] or '(embedded)'}"
                        f"{f' artifact:{artifact_id}' if artifact_id else ''}]"
                    )
                    continue
                if kind == "json" and data is not None:
                    parts.append(ToolContentPart(type="json", data=data))
                    rendered.append(json.dumps(data, ensure_ascii=False, default=str))
                    continue
                rendered.append(str(content))
            structured = getattr(raw, "structuredContent", None)
            if structured is None:
                structured = getattr(raw, "structured_content", None)
            if structured is not None:
                parts.append(ToolContentPart(type="json", data=structured))
                rendered.append(json.dumps(structured, ensure_ascii=False, default=str))
            is_error = bool(getattr(raw, "isError", False) or getattr(raw, "is_error", False))
            return ToolResult(
                tool_call_id="",
                name=tool,
                content="\n".join(rendered) or "(empty)",
                parts=parts,
                is_error=is_error,
                error_code="mcp_tool_error" if is_error else None,
                error_category="mcp" if is_error else None,
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            safe_error = self.redactor.redact_text(str(exc))
            return ToolResult(
                tool_call_id="",
                name=tool,
                content=f"MCP tool error (isolated): {safe_error}",
                is_error=True,
                error_code="mcp_transport_error",
                error_category="mcp",
                retryable=False,
                recovery_hint="Inspect the MCP tool policy before deciding whether to retry.",
            )

    def proxy_tools(self) -> dict[str, MCPProxyTool]:
        proxies: dict[str, MCPProxyTool] = {}
        for server, tools in self._tools_cache.items():
            for descriptor in tools:
                proxy = MCPProxyTool(self, server, descriptor)
                proxies[proxy.spec.name] = proxy
        return proxies

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
        for name in list(self._sessions):
            await self._disconnect(name)


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
                    "server": {"type": "string", "minLength": 1, "maxLength": 128},
                    "command": {"type": "string", "minLength": 1, "maxLength": 32_768},
                    "args": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 8_192},
                        "maxItems": 256,
                    },
                    "url": {"type": "string", "minLength": 1, "maxLength": 8_192},
                    "tool": {"type": "string", "minLength": 1, "maxLength": 128},
                    "arguments": {"type": "object", "maxProperties": 1_024},
                },
                "required": ["action"],
                "allOf": [
                    {
                        "if": {"properties": {"action": {"const": "connect_stdio"}}},
                        "then": {"required": ["command"]},
                    },
                    {
                        "if": {"properties": {"action": {"const": "connect_http"}}},
                        "then": {"required": ["url"]},
                    },
                    {
                        "if": {"properties": {"action": {"const": "call_tool"}}},
                        "then": {"required": ["server", "tool"]},
                    },
                ],
                "additionalProperties": False,
            },
            # Static spec is the maximum (destructive); the engine narrows per-call via
            # effect_for so a bare list_tools does not demand destructive approval.
            effect=EffectKind.destructive,
        )

    def effect_for(self, arguments: dict[str, Any]) -> EffectKind:
        return mcp_effect_for(arguments)

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = arguments.get("action") or ""
        try:
            if action == "connect_stdio":
                msg = await self.bridge.connect_stdio(
                    arguments.get("server") or "default",
                    arguments.get("command") or "",
                    arguments.get("args"),
                )
                is_err = "connect failed" in msg
                return ToolResult(
                    tool_call_id="",
                    name="mcp",
                    content=msg,
                    is_error=is_err,
                    error_code="mcp_connect_failed" if is_err else None,
                    error_category="mcp" if is_err else None,
                    retryable=False,
                )
            if action == "connect_http":
                msg = await self.bridge.connect_http(
                    arguments.get("server") or "default",
                    arguments.get("url") or "",
                )
                is_err = "blocked by egress" in msg or "connect failed" in msg
                return ToolResult(
                    tool_call_id="",
                    name="mcp",
                    content=msg,
                    is_error=is_err,
                    error_code=(
                        "egress_blocked" if "blocked by egress" in msg else "mcp_connect_failed"
                    )
                    if is_err
                    else None,
                    error_category=("security" if "blocked by egress" in msg else "mcp")
                    if is_err
                    else None,
                    retryable=False,
                )
            if action == "list_tools":
                tools = await self.bridge.list_tools(arguments.get("server"))
                return ToolResult(
                    tool_call_id="",
                    name="mcp",
                    content=str(tools) if tools else "No tools",
                    parts=[ToolContentPart(type="json", data=tools)],
                )
            if action == "call_tool":
                result = await self.bridge.call_tool_result(
                    ctx,
                    arguments.get("server") or "default",
                    arguments.get("tool") or "",
                    arguments.get("arguments") or {},
                )
                update: dict[str, Any] = {"name": "mcp"}
                if result.error_code in {"cancelled", "mcp_transport_error"}:
                    update.update(
                        {
                            "error_code": "outcome_indeterminate",
                            "error_category": "recovery",
                            "retryable": False,
                            "recovery_hint": (
                                "Inspect the MCP target before repeating the unknown operation."
                            ),
                        }
                    )
                return result.model_copy(update=update)
            return ToolResult(
                tool_call_id="",
                name="mcp",
                content=f"Unknown action {action}",
                is_error=True,
                error_code="invalid_action",
                error_category="validation",
            )
        except Exception as exc:  # noqa: BLE001
            # Never crash the main run
            return ToolResult(
                tool_call_id="",
                name="mcp",
                content=f"MCP fault isolated: {exc}",
                is_error=True,
                error_code="mcp_fault",
                error_category="mcp",
            )


def _proxy_name(server: str, tool: str) -> str:
    clean_server = re.sub(r"[^A-Za-z0-9_-]", "_", server).strip("_-") or "default"
    clean_tool = re.sub(r"[^A-Za-z0-9_-]", "_", tool).strip("_-") or "tool"
    base = f"mcp__{clean_server}__{clean_tool}"
    changed = clean_server != server or clean_tool != tool
    if len(base) <= 64 and not changed:
        return base
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:12]
    suffix = f"__{digest}"
    prefix = base[: 64 - len(suffix)].rstrip("_-") or "mcp"
    return f"{prefix}{suffix}"


class MCPProxyTool:
    mcp_proxy = True

    def __init__(self, bridge: MCPBridge, server: str, descriptor: dict[str, Any]) -> None:
        self.bridge = bridge
        self.server = server
        self.remote_name = str(descriptor.get("name") or "")
        annotations = descriptor.get("annotations") or {}
        read_only = bool(
            annotations.get("readOnlyHint") or annotations.get("read_only_hint")
        )
        idempotent = bool(
            annotations.get("idempotentHint") or annotations.get("idempotent_hint")
        )
        self._spec = ToolSpec(
            name=_proxy_name(server, self.remote_name),
            description=str(descriptor.get("description") or self.remote_name),
            parameters=descriptor.get("input_schema")
            or {"type": "object", "properties": {}},
            effect=EffectKind.network if read_only else EffectKind.destructive,
            replay_policy=(
                ReplayPolicy.safe
                if read_only or idempotent
                else ReplayPolicy.never
            ),
            parallel_safe=read_only,
            max_attempts=2 if read_only or idempotent else 1,
            timeout_s=60,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        result = await self.bridge.call_tool_result(
            ctx, self.server, self.remote_name, arguments
        )
        if (
            result.is_error
            and result.error_code in {"cancelled", "mcp_transport_error"}
        ):
            if self.spec.replay_policy == ReplayPolicy.safe:
                return result.model_copy(
                    update={
                        "retryable": True,
                        "recovery_hint": "Retry after the MCP transport recovers.",
                    }
                )
            return result.model_copy(
                update={
                    "error_code": "outcome_indeterminate",
                    "error_category": "recovery",
                    "retryable": False,
                    "recovery_hint": (
                        "Inspect the MCP target before repeating this operation."
                    ),
                }
            )
        return result
