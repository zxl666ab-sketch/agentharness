"""Provider contract tests against the shipped FakeModelAdapter."""

from __future__ import annotations

import asyncio

import pytest

from agentharness.contracts import Message, MessageRole, ModelRequest, StreamItemType, ToolSpec
from agentharness.providers.fake import FakeModelAdapter


async def collect(adapter, request):
    items = []
    async for it in adapter.stream(request):
        items.append(it)
    return items


@pytest.mark.asyncio
async def test_stream_text():
    fake = FakeModelAdapter()
    req = ModelRequest(
        messages=[Message(role=MessageRole.user, content="[fake:text]Hello world stream")]
    )
    items = await collect(fake, req)
    text = "".join(i.text or "" for i in items if i.type == StreamItemType.text_delta)
    assert "Hello world stream" in text
    assert any(i.type == StreamItemType.done for i in items)
    assert any(i.type == StreamItemType.usage for i in items)
    # No raw provider JSON fields
    for i in items:
        assert not hasattr(i, "raw")
        dumped = i.model_dump()
        assert "choices" not in dumped
        assert "content_block" not in dumped


@pytest.mark.asyncio
async def test_fragmented_tool_arguments():
    fake = FakeModelAdapter()
    req = ModelRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content='[fake:tools]read_file\n{"path": "README.md"}',
            )
        ],
        tools=[ToolSpec(name="read_file", description="r")],
    )
    items = await collect(fake, req)
    deltas = [i for i in items if i.type == StreamItemType.tool_call_delta]
    assert len(deltas) >= 2  # fragmented
    ends = [i for i in items if i.type == StreamItemType.tool_call_end]
    assert len(ends) == 1
    assert ends[0].arguments is not None
    assert ends[0].arguments.get("path") == "README.md"


@pytest.mark.asyncio
async def test_multi_tool_call():
    fake = FakeModelAdapter()
    req = ModelRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content='[fake:tools]read_file|write_file\n[{"path":"a"},{"path":"b","content":"x"}]',
            )
        ],
        tools=[
            ToolSpec(name="read_file", description="r"),
            ToolSpec(name="write_file", description="w"),
        ],
    )
    items = await collect(fake, req)
    starts = [i for i in items if i.type == StreamItemType.tool_call_start]
    ends = [i for i in items if i.type == StreamItemType.tool_call_end]
    assert len(starts) == 2
    assert len(ends) == 2
    names = {e.tool_name for e in ends}
    assert names == {"read_file", "write_file"}


@pytest.mark.asyncio
async def test_usage_error_rate_limit_timeout():
    fake = FakeModelAdapter()
    for kind in ("rate_limit", "timeout", "provider"):
        items = await collect(
            fake,
            ModelRequest(
                messages=[
                    Message(role=MessageRole.user, content=f"[fake:error:{kind}]")
                ]
            ),
        )
        errs = [i for i in items if i.type == StreamItemType.error]
        assert errs, kind
        assert errs[0].error_kind == kind


@pytest.mark.asyncio
async def test_cancel_via_event():
    cancel = asyncio.Event()
    fake = FakeModelAdapter(cancel_event=cancel)
    cancel.set()
    items = await collect(
        fake,
        ModelRequest(messages=[Message(role=MessageRole.user, content="hello")]),
    )
    assert any(i.error_kind == "cancelled" for i in items)


@pytest.mark.asyncio
async def test_historical_tool_results_do_not_override_latest_user_directive():
    fake = FakeModelAdapter()
    request = ModelRequest(
        messages=[
            Message(role=MessageRole.user, content="first request"),
            Message(
                role=MessageRole.tool,
                name="read_file",
                tool_call_id="old-call",
                content="historical tool result",
            ),
            Message(role=MessageRole.assistant, content="historical answer"),
            Message(role=MessageRole.user, content="[fake:text]SECOND_OK"),
        ]
    )

    items = await collect(fake, request)
    text = "".join(item.text or "" for item in items)

    assert text == "SECOND_OK"
