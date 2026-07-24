from agentharness.contracts import (
    EffectKind,
    EventEnvelope,
    Message,
    ModelStreamItem,
    RunRequest,
    RunResult,
    StreamItemType,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)


def test_core_models_roundtrip():
    req = RunRequest(message="hi", provider="fake")
    assert req.approval.value == "ask"
    assert req.budget.max_delegate_depth == 3
    assert req.budget.max_concurrent_children == 4
    assert req.budget.max_context_tokens == 100_000
    assert req.verification is None

    result = RunResult(run_id="r", session_id="s", status="completed")
    assert result.status.value == "completed"

    tc = ToolCall(name="read_file", arguments={"path": "x"})
    tr = ToolResult(tool_call_id=tc.id, name="read_file", content="ok")
    assert tr.is_error is False
    assert tr.error_code is None
    assert tr.error_category is None
    assert tr.retryable is False
    assert tr.recovery_hint is None

    usage = Usage(input_tokens=1, output_tokens=2, total_tokens=3)
    assert usage.total_tokens == 3

    msg = Message(role="user", content="x")
    assert msg.role.value == "user"

    item = ModelStreamItem(type=StreamItemType.text_delta, text="a")
    assert item.type == StreamItemType.text_delta

    spec = ToolSpec(name="t", description="d", effect=EffectKind.pure)
    assert spec.effect == EffectKind.pure

    for effect in EffectKind:
        ToolSpec(name="x", description="d", effect=effect)

    ev = EventEnvelope(
        session_id="s",
        root_run_id="r",
        run_id="r",
        type="run_started",
        payload={},
    )
    assert ev.schema_version == 1
    assert ev.event_id
    data = ev.model_dump()
    for key in (
        "schema_version",
        "event_id",
        "global_seq",
        "run_seq",
        "session_id",
        "root_run_id",
        "run_id",
        "parent_run_id",
        "span_id",
        "parent_span_id",
        "type",
        "timestamp",
        "payload",
    ):
        assert key in data
