"""OpenAI Responses adapter stream contracts via mocked SDK events (no live keys)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentharness.contracts import (
    ApprovalMode,
    Message,
    MessageRole,
    ModelRequest,
    RunRequest,
    RunStatus,
    StreamItemType,
    ToolSpec,
)
from agentharness.harness import Harness
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter


class _AsyncIter:
    def __init__(self, items: list[Any], *, error: BaseException | None = None) -> None:
        self._items = iter(items)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            if self._error is not None:
                error = self._error
                self._error = None
                raise error from None
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


class _BlockingAsyncIter:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.entered.set()
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


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
            output_index=0,
            item=SimpleNamespace(
                type="function_call", call_id="c1", name="read_file", id="fc1"
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc1",
            output_index=0,
            delta='{"pa',
            name="read_file",
        ),
        SimpleNamespace(
            type="response.output_item.added",
            output_index=1,
            item=SimpleNamespace(
                type="function_call", call_id="c2", name="write_file", id="fc2"
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc2",
            output_index=1,
            delta='{"path":"b","content":"x"}',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc1",
            output_index=0,
            delta='th":"a"}',
            name="read_file",
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc2",
            output_index=1,
            name="write_file",
            arguments='{"path":"b","content":"x"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=1,
            item=SimpleNamespace(
                type="function_call",
                call_id="c2",
                name="write_file",
                arguments='{"path":"b","content":"x"}',
                id="fc2",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc1",
            output_index=0,
            name="read_file",
            arguments='{"path":"a"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                call_id="c1",
                name="read_file",
                arguments='{"path":"a"}',
                id="fc1",
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=11, output_tokens=7)
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
        ("server_error", "Internal server error", 500),
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
                    finish_reason=None,
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
                    ),
                    finish_reason=None,
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
                    ),
                    finish_reason=None,
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="tool_calls",
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
                            delta=SimpleNamespace(content="fallback-ok", tool_calls=None),
                            finish_reason=None,
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=None, tool_calls=None),
                            finish_reason="stop",
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


@pytest.mark.asyncio
async def test_responses_late_call_id_keeps_one_stable_tool_identity():
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                type="function_call", id="fc_late", call_id=None, name="read_file"
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc_late",
            output_index=0,
            delta='{"pa',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="fc_late",
            output_index=0,
            delta='th":"a"}',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc_late",
            output_index=0,
            name="read_file",
            arguments='{"path":"a"}',
        ),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                id="fc_late",
                call_id="call_real",
                name="read_file",
                arguments='{"path":"a"}',
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=5, output_tokens=3)
            ),
        ),
    ]
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=_AsyncIter(events))
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="responses")

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req(tools=True))

    calls = [item for item in items if item.tool_call_id]
    assert {item.tool_call_id for item in calls} == {"call_real"}
    assert sum(item.type == StreamItemType.tool_call_start for item in calls) == 1
    assert sum(item.type == StreamItemType.tool_call_end for item in calls) == 1
    assert "".join(
        item.arguments_delta or ""
        for item in calls
        if item.type == StreamItemType.tool_call_delta
    ) == '{"path":"a"}'


@pytest.mark.asyncio
async def test_chat_late_call_id_flushes_buffered_fragments_once():
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name="read_file", arguments='{"pa'
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_real",
                                function=SimpleNamespace(
                                    name=None, arguments='th":"a"}'
                                ),
                            )
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(content=None, tool_calls=None),
                )
            ],
        ),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_AsyncIter(chunks))
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="chat")

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req(tools=True))

    calls = [item for item in items if item.tool_call_id]
    assert {item.tool_call_id for item in calls} == {"call_real"}
    assert sum(item.type == StreamItemType.tool_call_start for item in calls) == 1
    assert sum(item.type == StreamItemType.tool_call_end for item in calls) == 1
    assert "".join(
        item.arguments_delta or ""
        for item in calls
        if item.type == StreamItemType.tool_call_delta
    ) == '{"path":"a"}'


@pytest.mark.asyncio
async def test_chat_interleaved_multi_tool_calls_remain_distinct():
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_read",
                                function=SimpleNamespace(
                                    name="read_file", arguments='{"pa'
                                ),
                            ),
                            SimpleNamespace(
                                index=1,
                                id="call_write",
                                function=SimpleNamespace(
                                    name="write_file", arguments='{"path":"b",'
                                ),
                            ),
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=1,
                                id=None,
                                function=SimpleNamespace(
                                    name=None, arguments='"content":"x"}'
                                ),
                            ),
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None, arguments='th":"a"}'
                                ),
                            ),
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(content=None, tool_calls=None),
                )
            ],
        ),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_AsyncIter(chunks))
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="chat")

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req(tools=True))

    ends = [item for item in items if item.type == StreamItemType.tool_call_end]
    assert [(item.tool_call_id, item.arguments) for item in ends] == [
        ("call_read", {"path": "a"}),
        ("call_write", {"path": "b", "content": "x"}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["responses", "chat"])
async def test_invalid_tool_arguments_fail_protocol_without_tool_end(api_mode: str):
    client = MagicMock()
    if api_mode == "responses":
        stream = _AsyncIter(
            [
                SimpleNamespace(
                    type="response.output_item.added",
                    output_index=0,
                    item=SimpleNamespace(
                        type="function_call",
                        id="fc_bad",
                        call_id="call_bad",
                        name="read_file",
                    ),
                ),
                SimpleNamespace(
                    type="response.function_call_arguments.done",
                    item_id="fc_bad",
                    output_index=0,
                    name="read_file",
                    arguments='{"path":',
                ),
            ]
        )
        client.responses.create = AsyncMock(return_value=stream)
    else:
        stream = _AsyncIter(
            [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call_bad",
                                        function=SimpleNamespace(
                                            name="read_file", arguments='{"path":'
                                        ),
                                    )
                                ],
                            ),
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason="tool_calls",
                            delta=SimpleNamespace(content=None, tool_calls=None),
                        )
                    ],
                ),
            ]
        )
        client.chat.completions.create = AsyncMock(return_value=stream)
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode=api_mode)

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req(tools=True))

    errors = [item for item in items if item.type == StreamItemType.error]
    assert len(errors) == 1
    assert errors[0].error_kind == "provider_protocol"
    assert not any(item.type == StreamItemType.tool_call_end for item in items)
    assert not any(item.type == StreamItemType.done for item in items)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["responses", "chat"])
async def test_stream_eof_without_terminal_event_is_an_interruption(api_mode: str):
    client = MagicMock()
    stream = _AsyncIter(
        [SimpleNamespace(type="response.output_text.delta", delta="partial")]
        if api_mode == "responses"
        else [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="partial", tool_calls=None),
                    )
                ],
            )
        ]
    )
    if api_mode == "responses":
        client.responses.create = AsyncMock(return_value=stream)
    else:
        client.chat.completions.create = AsyncMock(return_value=stream)
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode=api_mode)

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req())

    assert [item.text for item in items if item.type == StreamItemType.text_delta] == [
        "partial"
    ]
    errors = [item for item in items if item.type == StreamItemType.error]
    assert len(errors) == 1
    assert errors[0].error_kind == "connection"
    assert not any(item.type == StreamItemType.done for item in items)
    assert stream.closed is True


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_provider_stream():
    stream = _BlockingAsyncIter()
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=stream)
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="responses")

    with patch.object(adapter, "_get_client", return_value=client):
        task = asyncio.create_task(_collect(adapter, _req()))
        await asyncio.wait_for(stream.entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.closed is True


@pytest.mark.asyncio
async def test_chat_usage_is_emitted_once_from_latest_usage_chunk():
    chunks = [
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            choices=[],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(content="ok", tool_calls=None),
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4),
            choices=[],
        ),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_AsyncIter(chunks))
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="chat")

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req())

    usage = [item.usage for item in items if item.type == StreamItemType.usage]
    assert len(usage) == 1
    assert usage[0] is not None
    assert usage[0].model_dump()["total_tokens"] == 9


@pytest.mark.asyncio
async def test_midstream_404_does_not_replay_through_chat():
    stream = _AsyncIter(
        [SimpleNamespace(type="response.output_text.delta", delta="partial")],
        error=type("NotFound", (Exception,), {"status_code": 404})("not found"),
    )
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=stream)
    client.chat.completions.create = AsyncMock()
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="responses")

    with patch.object(adapter, "_get_client", return_value=client):
        items = await _collect(adapter, _req())

    assert any(item.type == StreamItemType.error for item in items)
    client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_openai_arguments_fail_run_without_tool_execution(
    data_dir, workspace
):
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(
                type="function_call",
                id="fc_bad",
                call_id="call_bad",
                name="read_file",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="fc_bad",
            output_index=0,
            name="read_file",
            arguments='{"path":',
        ),
    ]
    client = MagicMock()
    client.responses.create = AsyncMock(return_value=_AsyncIter(events))
    adapter = OpenAIResponsesAdapter(api_key="test", api_mode="responses")
    harness = Harness(data_dir=data_dir, providers={"openai": adapter})

    try:
        with patch.object(adapter, "_get_client", return_value=client):
            result = await harness.run(
                RunRequest(
                    message="read a.txt",
                    provider="openai",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                    tools=["read_file"],
                )
            )
        assert result.status == RunStatus.failed
        assert "invalid JSON" in (result.error or "")
        assert harness.list_tool_invocations(result.run_id) == []
    finally:
        await harness.aclose()


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
