"""Structural tests for the OpenAI adapter; no live key required."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agentharness.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    StreamItemType,
)
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter


def test_openai_adapter_message_conversion():
    ad = OpenAIResponsesAdapter(api_key="test", api_mode="responses")
    req = ModelRequest(
        messages=[
            Message(role=MessageRole.user, content="hi"),
            Message(role=MessageRole.assistant, content="yo"),
        ],
        system="sys",
    )
    items = ad._to_input(req)
    assert items[0]["role"] == "system"
    assert any(i.get("role") == "user" for i in items)


def test_openai_custom_base_url_defaults_to_chat_mode():
    ad = OpenAIResponsesAdapter(
        api_key="test", base_url="https://api-inference.modelscope.cn/v1"
    )
    assert ad.api_mode == "chat"
    req = ModelRequest(
        messages=[Message(role=MessageRole.user, content="hi")],
        system="sys",
    )
    msgs = ad._to_chat_messages(req)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1]["role"] == "user"


def test_openai_root_base_url_is_normalized_to_v1():
    adapter = OpenAIResponsesAdapter(
        api_key="test", base_url="https://api.muzeai.top", use_env=False
    )

    assert adapter.base_url == "https://api.muzeai.top/v1"


def test_openai_client_uses_provider_neutral_user_agent():
    fake_client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=fake_client) as constructor:
        adapter = OpenAIResponsesAdapter(api_key="test-key")
        assert adapter._get_client() is fake_client

    headers = constructor.call_args.kwargs["default_headers"]
    assert headers["User-Agent"] == "agentharness"


def test_openai_chat_stream_without_finish_reason_is_accepted_when_stream_closes():
    class _Stream:
        def __aiter__(self):
            return self._events()

        async def _events(self):
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="完整输出", tool_calls=[]),
                    )
                ],
                usage=None,
            )

        async def aclose(self):
            return None

    class _Completions:
        async def create(self, **_kwargs):
            return _Stream()

    class _Client:
        chat = SimpleNamespace(completions=_Completions())

    async def collect():
        adapter = OpenAIResponsesAdapter(
            api_key="test",
            base_url="https://gateway.example/v1",
            api_mode="chat",
            use_env=False,
        )
        adapter._client = _Client()
        request = ModelRequest(messages=[Message(role=MessageRole.user, content="hi")])
        return [item async for item in adapter._stream_chat(request)]

    items = asyncio.run(collect())

    assert [item.type for item in items] == [StreamItemType.text_delta, StreamItemType.done]
