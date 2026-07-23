"""Anthropic Messages adapter stream contracts via mocked SDK events (no live keys)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentharness.contracts import Message, MessageRole, ModelRequest, StreamItemType, ToolSpec
from agentharness.providers.anthropic_adapter import AnthropicMessagesAdapter


class _StreamCM:
    """Async context manager yielding a fake message stream."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


async def _collect(ad: AnthropicMessagesAdapter, req: ModelRequest):
    return [item async for item in ad.stream(req)]


def _req() -> ModelRequest:
    return ModelRequest(
        messages=[Message(role=MessageRole.user, content="hi")],
        tools=[ToolSpec(name="read_file", description="r")],
    )


@pytest.mark.asyncio
async def test_anthropic_stream_text_usage_done():
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10)),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hi "),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="there"),
        ),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(output_tokens=5),
        ),
    ]
    client = MagicMock()
    client.messages.stream = MagicMock(return_value=_StreamCM(events))
    ad = AnthropicMessagesAdapter(api_key="test-key")
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req())
    text = "".join(i.text or "" for i in items if i.type == StreamItemType.text_delta)
    assert "Hi there" in text
    assert any(i.type == StreamItemType.usage for i in items)
    assert any(i.type == StreamItemType.done for i in items)


@pytest.mark.asyncio
async def test_anthropic_fragmented_tool_args_multi():
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="t1", name="read_file"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"pa'),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json='th":"x"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="t2", name="shell"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(
                type="input_json_delta", partial_json='{"command":"echo 1"}'
            ),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
    ]
    client = MagicMock()
    client.messages.stream = MagicMock(return_value=_StreamCM(events))
    ad = AnthropicMessagesAdapter(api_key="test-key")
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req())
    deltas = [i for i in items if i.type == StreamItemType.tool_call_delta]
    assert len(deltas) >= 2
    ends = [i for i in items if i.type == StreamItemType.tool_call_end]
    assert len(ends) == 2
    assert {e.tool_name for e in ends} == {"read_file", "shell"}


@pytest.mark.asyncio
async def test_anthropic_errors():
    for kind, msg in (
        ("rate_limit", "Error code: 429 - rate limit"),
        ("timeout", "Request timeout"),
        ("cancelled", "cancelled by client"),
        ("provider", "server error"),
    ):
        ad = AnthropicMessagesAdapter(api_key="test-key")
        client = MagicMock()
        client.messages.stream = MagicMock(side_effect=Exception(msg))
        with patch.object(ad, "_get_client", return_value=client):
            items = await _collect(ad, _req())
        errs = [i for i in items if i.type == StreamItemType.error]
        assert errs, kind
        assert errs[0].error_kind == kind
