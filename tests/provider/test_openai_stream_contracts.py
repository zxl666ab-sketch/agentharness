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

        # Force responses mode so errors come from responses.create, not chat fallback.
        ad = OpenAIResponsesAdapter(api_key="test-key", api_mode="responses")
        client = MagicMock()
        client.responses.create = AsyncMock(side_effect=_boom())
        with patch.object(ad, "_get_client", return_value=client):
            items = await _collect(ad, _req())
        errs = [i for i in items if i.type == StreamItemType.error]
        assert errs, kind
        assert errs[0].error_kind == kind


def test_resolve_api_mode_prefers_chat_for_custom_base_url():
    from agentharness.providers.openai_adapter import resolve_openai_api_mode

    assert resolve_openai_api_mode(None) == "responses"
    assert resolve_openai_api_mode("https://api.openai.com/v1") == "responses"
    assert (
        resolve_openai_api_mode("https://api-inference.modelscope.cn/v1") == "chat"
    )
    assert resolve_openai_api_mode(None, explicit="chat") == "chat"
    assert resolve_openai_api_mode(
        "https://api-inference.modelscope.cn/v1", explicit="responses"
    ) == "responses"


@pytest.mark.asyncio
async def test_chat_mode_streams_text_and_tools():
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hi", tool_calls=None),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(
                                    name="read_file", arguments='{"p'
                                ),
                            )
                        ],
                    )
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None, arguments='ath":"x"}'
                                ),
                            )
                        ],
                    )
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
            choices=[],
        ),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_AsyncIter(chunks))
    ad = OpenAIResponsesAdapter(
        api_key="test-key",
        base_url="https://api-inference.modelscope.cn/v1",
        api_mode="chat",
    )
    assert ad.api_mode == "chat"
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req(tools=True))
    text = "".join(i.text or "" for i in items if i.type == StreamItemType.text_delta)
    assert text == "Hi"
    assert any(i.type == StreamItemType.tool_call_start for i in items)
    ends = [i for i in items if i.type == StreamItemType.tool_call_end]
    assert len(ends) == 1
    assert ends[0].arguments == {"path": "x"}
    assert any(i.type == StreamItemType.usage for i in items)
    assert any(i.type == StreamItemType.done for i in items)
    # Request went to chat.completions, not responses
    client.chat.completions.create.assert_awaited()
    kwargs = client.chat.completions.create.await_args.kwargs
    assert "messages" in kwargs
    assert kwargs["stream"] is True


@pytest.mark.asyncio
async def test_responses_404_falls_back_to_chat():
    client = MagicMock()
    err = Exception("Error code: 404 - {'error': None}")
    err.status_code = 404  # type: ignore[attr-defined]
    client.responses.create = AsyncMock(side_effect=err)
    client.chat.completions.create = AsyncMock(
        return_value=_AsyncIter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="fallback-ok", tool_calls=None)
                        )
                    ],
                )
            ]
        )
    )
    ad = OpenAIResponsesAdapter(api_key="test-key", api_mode="responses")
    with patch.object(ad, "_get_client", return_value=client):
        items = await _collect(ad, _req())
    text = "".join(i.text or "" for i in items if i.type == StreamItemType.text_delta)
    assert text == "fallback-ok"
    assert ad.api_mode == "chat"


def test_chat_message_conversion_includes_tool_results():
    ad = OpenAIResponsesAdapter(
        api_key="test",
        base_url="https://example.com/v1",
    )
    assert ad.api_mode == "chat"
    req = ModelRequest(
        messages=[
            Message(role=MessageRole.user, content="hi"),
            Message(
                role=MessageRole.assistant,
                content="",
                tool_calls=[],
            ),
            Message(role=MessageRole.tool, content="result", tool_call_id="c1"),
        ],
        system="sys",
        tools=[ToolSpec(name="read_file", description="r")],
    )
    # Empty tool_calls list is falsy — still user/tool path
    msgs = ad._to_chat_messages(req)
    assert msgs[0]["role"] == "system"
    assert any(m.get("role") == "tool" for m in msgs)
    tools = ad._to_tools_chat(req)
    assert tools[0]["type"] == "function"
    assert "function" in tools[0]
