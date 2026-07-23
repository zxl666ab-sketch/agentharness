"""OpenAI Responses API streaming adapter."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
)


class OpenAIResponsesAdapter:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        # Prefer explicit arg, then env, then hard default (arg must be None to allow env)
        self.default_model = (
            default_model
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o-mini"
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
        client = self._get_client()
        model = request.model or self.default_model
        input_items = self._to_input(request)
        tools = self._to_tools(request)
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
            # Accumulate function call fragments
            tc_buf: dict[str, dict[str, Any]] = {}
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        yield ModelStreamItem(type=StreamItemType.text_delta, text=delta)
                elif etype == "response.function_call_arguments.delta":
                    item_id = getattr(event, "item_id", None) or getattr(event, "output_index", "0")
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
                        tc_id = getattr(item, "call_id", None) or getattr(item, "id", "") or ""
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
                        tc_id = getattr(item, "call_id", None) or getattr(item, "id", "") or ""
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
                    msg = str(getattr(event, "message", None) or getattr(event, "error", event))
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
            msg = str(exc)
            kind = "provider"
            low = msg.lower()
            if "rate" in low or getattr(exc, "status_code", None) == 429:
                kind = "rate_limit"
            elif "timeout" in low or type(exc).__name__ == "TimeoutError":
                kind = "timeout"
            elif "cancel" in low:
                kind = "cancelled"
            yield ModelStreamItem(type=StreamItemType.error, error=msg, error_kind=kind)

    def _to_input(self, request: ModelRequest) -> list[dict[str, Any]]:
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

    def _to_tools(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                    or {"type": "object", "properties": {}, "additionalProperties": True},
                }
            )
        return out
