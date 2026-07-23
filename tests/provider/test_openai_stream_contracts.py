"""OpenAI Responses adapter stream contracts via mocked SDK events (no live keys)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentharness.contracts import Message, MessageRole, ModelRequest, StreamItemType, ToolSpec
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter


class _AsyncIter:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


async def _collect(adapter: OpenAIResponsesAdapter, req: ModelRequest):
    return [item async for item in adapter.stream(req)]


def _req(text: str = "hi", tools: bool = False) -> ModelRequest:
    return ModelRequest(
        messages=[Message(role=MessageRole.user, content=text)],
        tools=[ToolSpec(name="read_file", description="r")] if tools else [],
    )


@pytest.mark.asyncio
async def test_openai_stream_text_usage_done():
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="Hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=3, output_tokens=2)
            ),
        ),
    ]
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=_AsyncIter(events))
    ad = OpenAIResponsesAdapter(api_key="test-key")
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req())
    text = "".join(i.text or "" for i in items if i.type == StreamItemType.text_delta)
    assert text == "Hello"
    assert any(i.type == StreamItemType.usage for i in items)
    assert any(i.type == StreamItemType.done for i in items)
    for i in items:
        assert "choices" not in i.model_dump()


@pytest.mark.asyncio
async def test_openai_fragmented_tool_args_and_multi_tool():
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c1", name="read_file", id="c1"),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="c1",
            delta='{"pa',
            name="read_file",
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="c1",
            delta='th":"a"}',
            name="read_file",
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c1",
                name="read_file",
                arguments='{"path":"a"}',
                id="c1",
            ),
        ),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="function_call", call_id="c2", name="write_file", id="c2"),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="c2",
                name="write_file",
                arguments='{"path":"b","content":"x"}',
                id="c2",
            ),
        ),
    ]
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=_AsyncIter(events))
    ad = OpenAIResponsesAdapter(api_key="test-key")
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req(tools=True))
    deltas = [i for i in items if i.type == StreamItemType.tool_call_delta]
    assert len(deltas) >= 2
    ends = [i for i in items if i.type == StreamItemType.tool_call_end]
    assert len(ends) == 2
    names = {e.tool_name for e in ends}
    assert "read_file" in names and "write_file" in names


@pytest.mark.asyncio
async def test_openai_error_rate_limit_timeout_cancel():
    cases = (
        ("rate_limit", "Rate limit exceeded", 429),
        ("timeout", "Request timeout", None),
        ("cancelled", "Request cancelled by user", None),
        ("provider", "Internal server error", 500),
    )
    for kind, msg, status_code in cases:

        def _boom(m: str = msg, sc: int | None = status_code) -> Exception:
            err = Exception(m)
            if sc is not None:
                err.status_code = sc  # type: ignore[attr-defined]
            return err

        ad = OpenAIResponsesAdapter(api_key="test-key")
        client = MagicMock()
        client.responses.create = AsyncMock(side_effect=_boom())
        with patch.object(ad, "_get_client", return_value=client):
            items = await _collect(ad, _req())
        errs = [i for i in items if i.type == StreamItemType.error]
        assert errs, kind
        assert errs[0].error_kind == kind
