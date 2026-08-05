"""Deterministic ModelAdapter used only by offline tests."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
    new_id,
)


class FakeModelAdapter:
    """Scriptable fake provider.

    Behaviors controlled by the last user message content:
    - `[fake:text]...`  → stream that text
    - `[fake:tools]NAME1|NAME2` with optional JSON after newline
    - `[fake:error:rate_limit|timeout|provider]`
    - `[fake:slow:N]`   → delay N ms between deltas
    - default: echo the user message with a short reply and optional tool calls
      if the message mentions known tool names.
    """

    name = "fake"

    def __init__(
        self,
        *,
        script: list[dict[str, Any]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.script = list(script or [])
        self._script_idx = 0
        self.cancel_event = cancel_event
        self.calls: list[ModelRequest] = []

    def reset(self) -> None:
        self._script_idx = 0
        self.calls.clear()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.calls.append(request)
        if self.cancel_event and self.cancel_event.is_set():
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="cancelled",
                error_kind="cancelled",
            )
            return

        if self.script and self._script_idx < len(self.script):
            step = self.script[self._script_idx]
            self._script_idx += 1
            async for item in self._emit_step(step):
                yield item
            return

        user_text = ""
        last_user_index = -1
        for index in range(len(request.messages) - 1, -1, -1):
            m = request.messages[index]
            if m.role.value == "user" or m.role == "user":
                user_text = m.content
                last_user_index = index
                break

        # Only tool results from the current user turn should trigger a summary.
        current_turn = request.messages[last_user_index + 1 :]
        parts = [
            m.content
            for m in current_turn
            if (m.role.value == "tool" if hasattr(m.role, "value") else m.role == "tool")
        ]
        if parts:
            summary = "Tool results received. " + " | ".join(parts)[:500]
            async for item in self._stream_text(summary):
                yield item
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=50, output_tokens=20, total_tokens=70),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        if user_text.startswith("[verification_feedback]"):
            required = re.search(r"missing substring: ['\"]([^'\"]+)['\"]", user_text)
            reply = required.group(1) if required else "verification feedback received"
            async for item in self._stream_text(reply):
                yield item
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=20, output_tokens=5, total_tokens=25),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        # Directive parsing
        if "[fake:error:rate_limit]" in user_text:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="rate limited",
                error_kind="rate_limit",
            )
            return
        if "[fake:error:timeout]" in user_text:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="timeout",
                error_kind="timeout",
            )
            return
        if "[fake:error:provider]" in user_text:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="provider error",
                error_kind="provider",
            )
            return
        if "[fake:cancel]" in user_text:
            await asyncio.sleep(0.05)
            if self.cancel_event:
                self.cancel_event.set()
            yield ModelStreamItem(
                type=StreamItemType.error,
                error="cancelled",
                error_kind="cancelled",
            )
            return

        # Prefer tool directives over nested [fake:text] inside tool JSON args
        m_tools = re.search(r"\[fake:tools\]([^\n]+)", user_text)
        if m_tools:
            names = [n.strip() for n in m_tools.group(1).split("|") if n.strip()]
            # Optional JSON args after first newline block
            args_list: list[dict[str, Any]] = []
            rest = user_text.split("\n", 1)
            if len(rest) > 1:
                try:
                    parsed = json.loads(rest[1].strip())
                    if isinstance(parsed, list):
                        args_list = parsed
                    elif isinstance(parsed, dict):
                        args_list = [parsed]
                except json.JSONDecodeError:
                    args_list = []
            async for item in self._stream_text("Calling tools..."):
                yield item
            for i, name in enumerate(names):
                args = args_list[i] if i < len(args_list) else {}
                async for item in self._stream_tool(name, args, fragment=True):
                    yield item
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=30, output_tokens=15, total_tokens=45),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        m_text = re.search(r"\[fake:text\](.*?)(?:\[fake:|$)", user_text, re.S)
        if m_text:
            async for item in self._stream_text(m_text.group(1).strip()):
                yield item
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        # Heuristic: mention of tools in available tools
        tool_names = {t.name for t in request.tools}
        invoked: list[tuple[str, dict[str, Any]]] = []
        lower = user_text.lower()
        if "read_file" in tool_names and ("read" in lower or "cat " in lower or "打开" in user_text):
            path = "README.md"
            m_path = re.search(r"['\"]([^'\"]+)['\"]", user_text)
            if m_path:
                path = m_path.group(1)
            invoked.append(("read_file", {"path": path}))
        if "write_file" in tool_names and ("write" in lower or "写入" in user_text):
            invoked.append(
                ("write_file", {"path": "out.txt", "content": "hello from fake agent"})
            )
        if invoked:
            async for item in self._stream_text("I will use tools to help."):
                yield item
            for name, args in invoked:
                async for item in self._stream_tool(name, args, fragment=True):
                    yield item
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=40, output_tokens=25, total_tokens=65),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        # Default echo
        reply = f"Echo: {user_text[:500]}"
        async for item in self._stream_text(reply):
            yield item
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=20, output_tokens=10, total_tokens=30, estimated=False),
        )
        yield ModelStreamItem(type=StreamItemType.done)


    async def _stream_text(self, text: str, chunk: int = 8) -> AsyncIterator[ModelStreamItem]:
        for i in range(0, len(text), chunk):
            if self.cancel_event and self.cancel_event.is_set():
                yield ModelStreamItem(
                    type=StreamItemType.error,
                    error="cancelled",
                    error_kind="cancelled",
                )
                return
            yield ModelStreamItem(type=StreamItemType.text_delta, text=text[i : i + chunk])
            await asyncio.sleep(0)  # yield control

    async def _stream_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        fragment: bool = False,
    ) -> AsyncIterator[ModelStreamItem]:
        tc_id = new_id()
        yield ModelStreamItem(
            type=StreamItemType.tool_call_start,
            tool_call_id=tc_id,
            tool_name=name,
        )
        raw = json.dumps(args, ensure_ascii=False)
        if fragment:
            # Emit fragmented argument deltas (provider contract)
            mid = max(1, len(raw) // 3)
            parts = [raw[:mid], raw[mid : mid * 2], raw[mid * 2 :]]
            for p in parts:
                if p:
                    yield ModelStreamItem(
                        type=StreamItemType.tool_call_delta,
                        tool_call_id=tc_id,
                        tool_name=name,
                        arguments_delta=p,
                    )
                    await asyncio.sleep(0)
        else:
            yield ModelStreamItem(
                type=StreamItemType.tool_call_delta,
                tool_call_id=tc_id,
                tool_name=name,
                arguments_delta=raw,
            )
        yield ModelStreamItem(
            type=StreamItemType.tool_call_end,
            tool_call_id=tc_id,
            tool_name=name,
            arguments=args,
        )

    async def _emit_step(self, step: dict[str, Any]) -> AsyncIterator[ModelStreamItem]:
        kind = step.get("kind", "text")
        if kind == "text":
            async for item in self._stream_text(step.get("text", "")):
                yield item
        elif kind == "tools":
            for t in step.get("tools", []):
                async for item in self._stream_tool(
                    t["name"], t.get("arguments", {}), fragment=t.get("fragment", True)
                ):
                    yield item
        elif kind == "error":
            yield ModelStreamItem(
                type=StreamItemType.error,
                error=step.get("error", "error"),
                error_kind=step.get("error_kind", "provider"),
            )
            return
        elif kind == "sleep":
            await asyncio.sleep(step.get("seconds", 0.1))
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(
                input_tokens=step.get("input_tokens", 10),
                output_tokens=step.get("output_tokens", 5),
                total_tokens=step.get("total_tokens", 15),
                cached_input_tokens=step.get("cached_input_tokens", 0),
            ),
        )
        yield ModelStreamItem(type=StreamItemType.done)


def create_test_harness(*args: Any, providers: dict[str, Any] | None = None, **kwargs: Any):
    """Build a Harness with an explicit test Provider when none is supplied."""
    from agentharness.harness import Harness

    resolved = providers if providers is not None else {"fake": FakeModelAdapter()}
    return Harness(*args, providers=resolved, **kwargs)
