"""Unit tests for the pure helper functions in tool execution."""

from __future__ import annotations

import pytest

from agentharness.contracts import (
    EffectKind,
    Message,
    MessageRole,
    ReplayPolicy,
    RunRequest,
    ToolCall,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
    ToolSpec,
)
from agentharness.engine.tool_execution import (
    _fit_serialized_tool_result,
    _json_string_payload_bytes,
    _serialized_tool_result,
    _tool_result_messages_for_call,
    _truncate_json_string_payload,
    _truncate_utf8,
    approval_scope,
    arguments_sha256,
    canonical_arguments,
    enabled_tool_names,
    invalid_arguments_result,
    resolved_parallel_safe,
    resolved_replay_policy,
    tool_call_completed,
    tool_result_model_content,
    validate_tool_arguments,
    validate_tool_spec,
)


def test_canonical_arguments_and_sha256() -> None:
    left = {"b": 2, "a": 1, "note": None}
    right = {"note": None, "a": 1, "b": 2}
    assert canonical_arguments(left) == canonical_arguments(right)
    assert arguments_sha256(left) == arguments_sha256(right)
    assert len(arguments_sha256(left)) == 64


def test_tool_call_completed() -> None:
    call = ToolCall(name="x", id="call-1", invocation_id="inv-1")
    assert tool_call_completed(call, {"inv-1"}) is True
    assert tool_call_completed(call, {"call-1"}) is True
    assert tool_call_completed(call, {"other"}) is False


def test_validate_tool_spec_valid_and_invalid() -> None:
    valid = ToolSpec(name="procurement_read_request", description="read", parameters={"type": "object"})
    validate_tool_spec(valid)

    with pytest.raises(ValueError, match="invalid tool name"):
        validate_tool_spec(ToolSpec(name="bad name!", description="x"))
    with pytest.raises(ValueError, match="must have a description"):
        validate_tool_spec(ToolSpec(name="tool_x", description="   "))
    with pytest.raises(ValueError, match="invalid JSON schema"):
        validate_tool_spec(
            ToolSpec(name="tool_x", description="x", parameters={"type": 42})
        )
    with pytest.raises(ValueError, match="must describe an object"):
        validate_tool_spec(
            ToolSpec(name="tool_x", description="x", parameters={"type": "array"})
        )


def test_validate_tool_arguments() -> None:
    spec = ToolSpec(
        name="tool_x",
        description="x",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    assert validate_tool_arguments(spec, {"path": "a.txt"}) == []
    errors = validate_tool_arguments(spec, {"path": 1, "extra": True})
    assert any("path" in error for error in errors)
    assert any("extra" in error for error in errors)


def test_resolved_replay_policy_and_parallel_safe() -> None:
    pure = ToolSpec(name="t", description="x", effect=EffectKind.pure)
    write = ToolSpec(name="t", description="x", effect=EffectKind.workspace_write)
    custom = ToolSpec(
        name="t",
        description="x",
        effect=EffectKind.workspace_write,
        replay_policy=ReplayPolicy.safe,
        parallel_safe=True,
    )
    assert resolved_replay_policy(pure, pure.effect) == ReplayPolicy.safe
    assert resolved_replay_policy(write, write.effect) == ReplayPolicy.never
    assert resolved_replay_policy(custom, custom.effect) == ReplayPolicy.safe
    assert resolved_parallel_safe(pure, pure.effect) is True
    assert resolved_parallel_safe(write, write.effect) is False
    assert resolved_parallel_safe(custom, custom.effect) is True


def test_approval_scope() -> None:
    assert approval_scope("fs_write", EffectKind.workspace_write, {"path": "/tmp/x"}) == (
        "fs_write:workspace_write:path=/tmp/x"
    )
    assert approval_scope("http", EffectKind.workspace_write, {"url": "https://example.com"}) == (
        "http:workspace_write:url=https://example.com"
    )
    assert approval_scope("tool", EffectKind.destructive, {"path": ""}) == "tool:destructive"


def test_invalid_arguments_result() -> None:
    result = invalid_arguments_result(
        tool_call_id="c1",
        invocation_id="i1",
        tool_name="tool_x",
        errors=["a: bad", "b: worse", "c: third", "d: fourth", "e: fifth", "f: sixth", "g: seventh", "h: eighth", "i: ninth"],
    )
    assert result.is_error is True
    assert result.error_code == "invalid_arguments"
    assert "a: bad" in result.content
    assert "i: ninth" not in result.content  # capped at 8


def test_tool_result_model_content() -> None:
    ok = ToolResult(tool_call_id="c", name="t", content="hello")
    assert tool_result_model_content(ok) == "hello"
    err = ToolResult(
        tool_call_id="c",
        name="t",
        content="boom",
        is_error=True,
        error_code="e1",
        error_category="budget",
        retryable=True,
        recovery_hint="fix it",
    )
    payload = tool_result_model_content(err)
    assert '"ok": false' in payload
    assert "boom" in payload
    assert "fix it" in payload


def test_tool_result_messages_for_call() -> None:
    call = ToolCall(name="t", id="call-1", invocation_id="inv-1")
    other = ToolCall(name="t", id="call-2", invocation_id="inv-2")
    messages = [
        Message(role=MessageRole.user, content="go"),
        Message(role=MessageRole.assistant, content="", tool_calls=[call, other]),
        Message(
            role=MessageRole.tool,
            tool_call_id="call-1",
            tool_result=ToolResult(tool_call_id="call-1", name="t", content="legacy hit"),
        ),
        Message(
            role=MessageRole.tool,
            tool_call_id="call-9",
            tool_result=ToolResult(
                tool_call_id="call-9",
                name="t",
                content="modern hit",
                invocation_id="inv-1",
            ),
        ),
        Message(role=MessageRole.tool, tool_call_id="call-2", content="no result"),
    ]
    matches = _tool_result_messages_for_call(messages, call)
    assert [m.tool_result.content for m in matches] == ["legacy hit", "modern hit"]


def test_truncate_utf8() -> None:
    assert _truncate_utf8("hello", 10) == "hello"
    assert _truncate_utf8("hello", 3, suffix="...") == "..."
    text = "中文内容" * 20
    truncated = _truncate_utf8(text, 20, suffix="…")
    assert len(truncated.encode("utf-8")) <= 20 + len("…".encode())
    assert truncated.endswith("…")
    assert _truncate_utf8(text, 0) == ""


def test_json_string_payload_bytes_and_truncate() -> None:
    assert _json_string_payload_bytes("abc") == 3
    assert _json_string_payload_bytes("") == 0
    text = "x" * 100
    assert _truncate_json_string_payload(text, 10, "…") == "x" * 7 + "…"
    assert _truncate_json_string_payload("short", 100, "…") == "short"
    # Suffix larger than budget is dropped entirely.
    assert _truncate_json_string_payload(text, 0, "…") == ""


def test_fit_serialized_tool_result() -> None:
    base = ToolResult(
        tool_call_id="c",
        name="t",
        content="x" * 500,
        final_output="y" * 500,
        parts=[],
    )
    fitted = _fit_serialized_tool_result(base, 400)
    assert len(_serialized_tool_result(fitted)) <= 400
    assert fitted.final_output.endswith("...[final output limit reached]")

    content_only = ToolResult(tool_call_id="c", name="t", content="x" * 500)
    fitted_content = _fit_serialized_tool_result(content_only, 400)
    assert len(_serialized_tool_result(fitted_content)) <= 400
    assert fitted_content.content.endswith("...[structured tool result limit reached]")
    assert fitted_content.final_output is None

    small = ToolResult(tool_call_id="c", name="t", content="ok")
    assert _fit_serialized_tool_result(small, 10_000) is small


def test_enabled_tool_names_with_prerequisites() -> None:
    request = RunRequest(
        message="x",
        tools=["a", "b", "c"],
        metadata={
            "tool_prerequisites": {
                "c": ["a"],
                "b": "not-a-list",
            }
        },
    )
    invocations = [
        ToolInvocationRecord(
            id="i1",
            run_id="r",
            session_id="s",
            provider_call_id="p1",
            tool_name="a",
            step=0,
            ordinal=0,
            status=ToolInvocationStatus.succeeded,
        )
    ]
    enabled = enabled_tool_names(request, invocations, {"a", "b", "c", "d"})
    assert "a" in enabled
    assert "c" in enabled  # prerequisite a succeeded
    assert "b" not in enabled  # malformed prerequisite list
    assert "d" not in enabled  # not in request allowlist

    no_prereq = RunRequest(message="x", tools=["a", "b"])
    assert enabled_tool_names(no_prereq, [], {"a", "b", "c"}) == {"a", "b"}
    no_tools = RunRequest(message="x")
    assert enabled_tool_names(no_tools, [], {"a"}) == {"a"}

    unmet = RunRequest(
        message="x",
        tools=["c"],
        metadata={"tool_prerequisites": {"c": ["a"]}},
    )
    assert enabled_tool_names(unmet, [], {"c"}) == set()
