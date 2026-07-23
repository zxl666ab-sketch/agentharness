"""Anthropic Messages API streaming adapter."""

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


class AnthropicMessagesAdapter:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self.default_model = (
            default_model
            or os.environ.get("ANTHROPIC_MODEL")
            or "claude-sonnet-4-20250514"
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        client = self._get_client()
        model = request.model or self.default_model
        system, messages = self._to_messages(request)
        tools = self._to_tools(request)
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": request.max_tokens or 4096,
                "stream": True,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools
            # Track tool use blocks by index
            tool_blocks: dict[int, dict[str, Any]] = {}
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    etype = getattr(event, "type", "") or ""
                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        idx = int(getattr(event, "index", 0) or 0)
                        if block is not None and getattr(block, "type", "") == "tool_use":
                            tc_id = getattr(block, "id", "") or f"tool_{idx}"
                            name = getattr(block, "name", "") or ""
                            tool_blocks[idx] = {
                                "id": tc_id,
                                "name": name,
                                "arguments": "",
                            }
                            yield ModelStreamItem(
                                type=StreamItemType.tool_call_start,
                                tool_call_id=tc_id,
                                tool_name=name,
                            )
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        idx = int(getattr(event, "index", 0) or 0)
                        if delta is None:
                            continue
                        dtype = getattr(delta, "type", "") or ""
                        if dtype == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield ModelStreamItem(
                                    type=StreamItemType.text_delta, text=text
                                )
                        elif dtype == "input_json_delta":
                            partial = getattr(delta, "partial_json", "") or ""
                            if idx in tool_blocks:
                                tool_blocks[idx]["arguments"] += partial
                                yield ModelStreamItem(
                                    type=StreamItemType.tool_call_delta,
                                    tool_call_id=tool_blocks[idx]["id"],
                                    tool_name=tool_blocks[idx]["name"],
                                    arguments_delta=partial,
                                )
                    elif etype == "content_block_stop":
                        idx = int(getattr(event, "index", 0) or 0)
                        if idx in tool_blocks:
                            tb = tool_blocks[idx]
                            raw = tb["arguments"]
                            try:
                                args = json.loads(raw) if raw else {}
                            except json.JSONDecodeError:
                                args = {"_raw": raw}
                            yield ModelStreamItem(
                                type=StreamItemType.tool_call_end,
                                tool_call_id=tb["id"],
                                tool_name=tb["name"],
                                arguments=args if isinstance(args, dict) else {"_raw": raw},
                            )
                    elif etype == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage:
                            out = int(getattr(usage, "output_tokens", 0) or 0)
                            yield ModelStreamItem(
                                type=StreamItemType.usage,
                                usage=Usage(output_tokens=out, total_tokens=out),
                            )
                    elif etype == "message_start":
                        msg = getattr(event, "message", None)
                        usage = getattr(msg, "usage", None) if msg else None
                        if usage:
                            inp = int(getattr(usage, "input_tokens", 0) or 0)
                            yield ModelStreamItem(
                                type=StreamItemType.usage,
                                usage=Usage(input_tokens=inp, total_tokens=inp),
                            )
            yield ModelStreamItem(type=StreamItemType.done)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            kind = "provider"
            low = msg.lower()
            if "rate" in low or getattr(exc, "status_code", None) == 429:
                kind = "rate_limit"
            elif "timeout" in low:
                kind = "timeout"
            elif "cancel" in low:
                kind = "cancelled"
            yield ModelStreamItem(type=StreamItemType.error, error=msg, error_kind=kind)

    def _to_messages(
        self, request: ModelRequest
    ) -> tuple[str | None, list[dict[str, Any]]]:
        system = request.system
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "system":
                system = (system or "") + ("\n" + m.content if system else m.content)
                continue
            if role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or m.id,
                                "content": m.content,
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.tool_calls:
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": "user" if role == "user" else role, "content": m.content})
        # Anthropic requires alternating roles; merge consecutive same roles simply
        if not messages:
            messages = [{"role": "user", "content": ""}]
        return system, messages

    def _to_tools(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in request.tools:
            out.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters
                    or {"type": "object", "properties": {}, "additionalProperties": True},
                }
            )
        return out
