"""Structural tests for OpenAI/Anthropic adapters — no live keys required."""

from agentharness.contracts import Message, MessageRole, ModelRequest
from agentharness.providers.anthropic_adapter import AnthropicMessagesAdapter
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter


def test_openai_adapter_message_conversion():
    ad = OpenAIResponsesAdapter(api_key="test")
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


def test_anthropic_adapter_message_conversion():
    ad = AnthropicMessagesAdapter(api_key="test")
    req = ModelRequest(
        messages=[Message(role=MessageRole.user, content="hi")],
        system="sys",
    )
    system, messages = ad._to_messages(req)
    assert system == "sys"
    assert messages[0]["role"] == "user"
