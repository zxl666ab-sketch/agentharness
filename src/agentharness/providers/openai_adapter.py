"""OpenAI provider adapter: Responses API + Chat Completions (OpenAI-compatible).

Official OpenAI defaults to the Responses API. Custom ``base_url`` gateways
(ModelScope, vLLM, OneAPI, etc.) usually only expose Chat Completions and are
selected automatically. Override with ``OPENAI_API_MODE=chat|responses``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, Literal

from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
)

ApiMode = Literal["auto", "chat", "responses"]


def resolve_openai_api_mode(
    base_url: str | None,
    *,
    explicit: str | None = None,
) -> Literal["chat", "responses"]:
    """Pick chat vs responses. Custom base_url → chat; official OpenAI → responses."""
    raw = (explicit or os.environ.get("OPENAI_API_MODE") or "auto").strip().lower()
    if raw in {"chat", "completions", "chat_completions", "chat.completions"}:
        return "chat"
    if raw in {"responses", "response"}:
        return "responses"
    # auto
    if not base_url:
        return "responses"
    host = base_url.lower()
    if "api.openai.com" in host:
        return "responses"
    return "chat"


def _classify_error(exc: BaseException) -> tuple[str, str]:
    msg = str(exc)
    kind = "provider"
    low = msg.lower()
    status = getattr(exc, "status_code", None)
    if status is None:
        # openai.APIStatusError exposes .status on some versions
        status = getattr(exc, "status", None)
    if "rate" in low or status == 429:
        kind = "rate_limit"
    elif "timeout" in low or type(exc).__name__ == "TimeoutError":
        kind = "timeout"
    elif "cancel" in low:
        kind = "cancelled"
    return msg, kind


def _is_not_found(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status == 404:
        return True
    low = str(exc).lower()
    return "404" in low or "not found" in low


class OpenAIResponsesAdapter:
    """OpenAI-compatible model adapter (name kept for import stability)."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        api_mode: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        # Prefer explicit arg, then env, then hard default (arg must be None to allow env)
        self.default_model = (
            default_model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        )
        self.api_mode: Literal["chat", "responses"] = resolve_openai_api_mode(
            self.base_url, explicit=api_mode
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        if self.api_mode == "chat":
            async for item in self._stream_chat(request):
                yield item
            return

        # Responses first; on create() 404 fall back to chat (OAI-compatible gateways).
        try:
            async for item in self._stream_responses(request):
                yield item
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                self.api_mode = "chat"
                async for item in self._stream_chat(request):
                    yield item
                return
            msg, kind = _classify_error(exc)
            yield ModelStreamItem(type=StreamItemType.error, error=msg, error_kind=kind)

    # ------------------------------------------------------------------
    # Responses API (official OpenAI)
    # ------------------------------------------------------------------

    async def _stream_responses(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelStreamItem]:
        client = self._get_client()
        model = request.model or self.default_model
        input_items = self._to_input(request)
        tools = self._to_tools_responses(request)
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": input_items,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools
            if request.max_tokens:
                kwargs["max_output_tokens"] = request.max_tokens
            stream = await client.responses.create(**kwargs)
            tc_buf: dict[str, dict[str, Any]] = {}
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield ModelStreamItem(type=StreamItemType.text_delta, text=delta)
                elif etype == "response.function_call_arguments.delta":
                    item_id = getattr(event, "item_id", None) or getattr(
                        event, "output_index", "0"
                    )
                    key = str(item_id)
                    if key not in tc_buf:
                        tc_buf[key] = {
                            "id": key,
                            "name": getattr(event, "name", None) or "",
                            "arguments": "",
                        }
                    delta = getattr(event, "delta", "") or ""
                    tc_buf[key]["arguments"] += delta
                    yield ModelStreamItem(
                        type=StreamItemType.tool_call_delta,
                        tool_call_id=key,
                        tool_name=tc_buf[key]["name"] or None,
                        arguments_delta=delta,
                    )
                elif etype == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", "") == "function_call":
                        tc_id = (
                            getattr(item, "call_id", None)
                            or getattr(item, "id", "")
                            or ""
                        )
                        name = getattr(item, "name", "") or ""
                        tc_buf[str(tc_id)] = {"id": tc_id, "name": name, "arguments": ""}
                        yield ModelStreamItem(
                            type=StreamItemType.tool_call_start,
                            tool_call_id=str(tc_id),
                            tool_name=name,
                        )
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", "") == "function_call":
                        tc_id = (
                            getattr(item, "call_id", None)
                            or getattr(item, "id", "")
                            or ""
                        )
                        name = getattr(item, "name", "") or ""
                        raw = getattr(item, "arguments", "") or ""
                        try:
                            args = json.loads(raw) if raw else {}
                        except json.JSONDecodeError:
                            args = {"_raw": raw}
                        yield ModelStreamItem(
                            type=StreamItemType.tool_call_end,
                            tool_call_id=str(tc_id),
                            tool_name=name,
                            arguments=args if isinstance(args, dict) else {"_raw": raw},
                        )
                elif etype == "response.completed":
                    resp = getattr(event, "response", None)
                    usage = getattr(resp, "usage", None) if resp else None
                    if usage:
                        inp = int(getattr(usage, "input_tokens", 0) or 0)
                        out = int(getattr(usage, "output_tokens", 0) or 0)
                        yield ModelStreamItem(
                            type=StreamItemType.usage,
                            usage=Usage(
                                input_tokens=inp,
                                output_tokens=out,
                                total_tokens=inp + out,
                            ),
                        )
                elif etype == "error" or etype.endswith(".failed"):
                    msg = str(
                        getattr(event, "message", None) or getattr(event, "error", event)
                    )
                    kind = "provider"
                    low = msg.lower()
                    if "rate" in low and "limit" in low:
                        kind = "rate_limit"
                    elif "timeout" in low:
                        kind = "timeout"
                    yield ModelStreamItem(
                        type=StreamItemType.error, error=msg, error_kind=kind
                    )
                    return
            yield ModelStreamItem(type=StreamItemType.done)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                # Let outer stream() fall back to chat when nothing was emitted.
                raise
            msg, kind = _classify_error(exc)
            yield ModelStreamItem(type=StreamItemType.error, error=msg, error_kind=kind)

    # ------------------------------------------------------------------
    # Chat Completions (OpenAI-compatible gateways)
    # ------------------------------------------------------------------

    async def _stream_chat(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelStreamItem]:
        client = self._get_client()
        model = request.model or self.default_model
        messages = self._to_chat_messages(request)
        tools = self._to_tools_chat(request)
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools
            if request.max_tokens:
                kwargs["max_tokens"] = request.max_tokens
            # Best-effort usage on stream; many gateways ignore unknown fields.
            kwargs["stream_options"] = {"include_usage": True}
            try:
                stream = await client.chat.completions.create(**kwargs)
            except Exception:
                kwargs.pop("stream_options", None)
                stream = await client.chat.completions.create(**kwargs)

            # index → {id, name, arguments, started}
            tc_buf: dict[int, dict[str, Any]] = {}
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    inp = int(
                        getattr(usage, "prompt_tokens", 0)
                        or getattr(usage, "input_tokens", 0)
                        or 0
                    )
                    out = int(
                        getattr(usage, "completion_tokens", 0)
                        or getattr(usage, "output_tokens", 0)
                        or 0
                    )
                    if inp or out:
                        yield ModelStreamItem(
                            type=StreamItemType.usage,
                            usage=Usage(
                                input_tokens=inp,
                                output_tokens=out,
                                total_tokens=inp + out,
                            ),
                        )
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    yield ModelStreamItem(type=StreamItemType.text_delta, text=content)
                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    idx = int(getattr(tc, "index", 0) or 0)
                    row = tc_buf.get(idx)
                    if row is None:
                        row = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                            "started": False,
                        }
                        tc_buf[idx] = row
                    tc_id = getattr(tc, "id", None)
                    if tc_id:
                        row["id"] = str(tc_id)
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        name = getattr(fn, "name", None)
                        if name:
                            row["name"] = str(name)
                        args_delta = getattr(fn, "arguments", None) or ""
                        if args_delta:
                            row["arguments"] += str(args_delta)
                            if row["started"]:
                                yield ModelStreamItem(
                                    type=StreamItemType.tool_call_delta,
                                    tool_call_id=row["id"] or f"call_{idx}",
                                    tool_name=row["name"] or None,
                                    arguments_delta=str(args_delta),
                                )
                    if not row["started"] and (row["name"] or row["id"]):
                        if not row["id"]:
                            row["id"] = f"call_{idx}"
                        row["started"] = True
                        yield ModelStreamItem(
                            type=StreamItemType.tool_call_start,
                            tool_call_id=row["id"],
                            tool_name=row["name"] or None,
                        )
                        # If arguments already arrived with the start chunk, emit delta.
                        if row["arguments"]:
                            yield ModelStreamItem(
                                type=StreamItemType.tool_call_delta,
                                tool_call_id=row["id"],
                                tool_name=row["name"] or None,
                                arguments_delta=row["arguments"],
                            )

            for idx in sorted(tc_buf):
                row = tc_buf[idx]
                raw = row["arguments"]
                try:
                    args = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw}
                if not row["started"]:
                    yield ModelStreamItem(
                        type=StreamItemType.tool_call_start,
                        tool_call_id=row["id"] or f"call_{idx}",
                        tool_name=row["name"] or None,
                    )
                yield ModelStreamItem(
                    type=StreamItemType.tool_call_end,
                    tool_call_id=row["id"] or f"call_{idx}",
                    tool_name=row["name"] or None,
                    arguments=args if isinstance(args, dict) else {"_raw": raw},
                )
            yield ModelStreamItem(type=StreamItemType.done)
        except Exception as exc:  # noqa: BLE001
            msg, kind = _classify_error(exc)
            yield ModelStreamItem(type=StreamItemType.error, error=msg, error_kind=kind)

    # ------------------------------------------------------------------
    # Message / tool conversion
    # ------------------------------------------------------------------

    def _to_input(self, request: ModelRequest) -> list[dict[str, Any]]:
        """Responses API input items."""
        items: list[dict[str, Any]] = []
        if request.system:
            items.append({"role": "system", "content": request.system})
        for m in request.messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id or m.id,
                        "output": m.content,
                    }
                )
            elif role == "assistant" and m.tool_calls:
                for tc in m.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        }
                    )
                if m.content:
                    items.append({"role": "assistant", "content": m.content})
            else:
                items.append({"role": role, "content": m.content})
        return items

    def _to_chat_messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        """Chat Completions messages array."""
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or m.id,
                        "content": m.content or "",
                    }
                )
            elif role == "assistant" and m.tool_calls:
                tool_calls = []
                for tc in m.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        }
                    )
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": tool_calls,
                }
                messages.append(entry)
            else:
                messages.append({"role": role, "content": m.content})
        return messages

    def _to_tools_responses(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                    or {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": True,
                    },
                }
            )
        return out

    def _to_tools_chat(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters
                        or {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    },
                }
            )
        return out

    # Back-compat alias used by older tests / callers
    def _to_tools(self, request: ModelRequest) -> list[dict[str, Any]]:
        if self.api_mode == "chat":
            return self._to_tools_chat(request)
        return self._to_tools_responses(request)
