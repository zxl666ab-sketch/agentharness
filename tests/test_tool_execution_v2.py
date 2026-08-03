from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    BudgetConfig,
    Checkpoint,
    EffectKind,
    Message,
    MessageRole,
    ModelStreamItem,
    ReplayPolicy,
    RunRequest,
    RunStatus,
    StreamItemType,
    ToolCall,
    ToolContentPart,
    ToolContext,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolRecoveryDecision,
    ToolResult,
    ToolSpec,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.engine.tool_execution import arguments_sha256, validate_tool_spec
from agentharness.tools.http_tool import HttpTool
from agentharness.tools.mcp_tool import MCPBridge, MCPTool
from tests.fake_provider import create_test_harness


class _DuplicateCallProvider:
    name = "duplicate"

    async def stream(self, _request):  # type: ignore[no-untyped-def]
        for _ in range(2):
            yield ModelStreamItem(
                type=StreamItemType.tool_call_start,
                tool_call_id="same-id",
                tool_name="read_file",
            )
        yield ModelStreamItem(type=StreamItemType.done)


class _CrossRoundDuplicateCallProvider:
    name = "cross-round-duplicate"

    def __init__(self) -> None:
        self.turn = 0

    async def stream(self, _request):  # type: ignore[no-untyped-def]
        self.turn += 1
        if self.turn <= 2:
            yield ModelStreamItem(
                type=StreamItemType.tool_call_start,
                tool_call_id="reused-provider-id",
                tool_name="read_file",
            )
            yield ModelStreamItem(
                type=StreamItemType.tool_call_end,
                tool_call_id="reused-provider-id",
                tool_name="read_file",
                arguments={"path": "a.txt"},
            )
        else:
            yield ModelStreamItem(type=StreamItemType.text_delta, text="done")
        yield ModelStreamItem(type=StreamItemType.done)


class _CaptureProvider:
    name = "capture"

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def stream(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        yield ModelStreamItem(type=StreamItemType.text_delta, text="done")
        yield ModelStreamItem(type=StreamItemType.done)


class _RetryTool:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="retry_tool",
            description="Fail once, then succeed.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            parallel_safe=True,
            max_attempts=2,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(
                tool_call_id="",
                name="retry_tool",
                content="temporary",
                is_error=True,
                error_code="temporary",
                error_category="tool",
                retryable=True,
            )
        return ToolResult(tool_call_id="", name="retry_tool", content="ok")


class _TerminalOutputTool:
    def __init__(self, final_output: str = "DONE: deterministic work completed") -> None:
        self.final_output = final_output

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="terminal_output_tool",
            description="Return a trusted final response after deterministic work.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            name="terminal_output_tool",
            content="deterministic work completed",
            final_output=self.final_output,
        )


class _ExceptionTool(_RetryTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="exception_tool",
            description="Raise a non-transient tool exception.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            max_attempts=2,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        raise ValueError("invalid local state")


class _ReconcileTool:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="reconcile_tool",
            description="Recovery test tool.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.reconcile,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_call_id="", name="reconcile_tool", content="ran")


class _SafeRecoveryTool(_ReconcileTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="safe_recovery_tool",
            description="Safely retry after process loss.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            max_attempts=1,
        )


class _CancellableSafeTool(_SafeRecoveryTool):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cancellable_safe_tool",
            description="Cooperatively cancelled safe recovery tool.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            max_attempts=1,
        )

    async def run(self, ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            assert ctx.cancel_event is not None
            await ctx.cancel_event.wait()
            return ToolResult(
                tool_call_id="",
                name=self.spec.name,
                content="cancelled",
                is_error=True,
            )
        return ToolResult(tool_call_id="", name=self.spec.name, content="recovered")


class _RaisingReconcileTool(_ReconcileTool):
    async def reconcile(
        self, _ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        raise RuntimeError("reconciler unavailable")


class _HangingReconcileTool(_ReconcileTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="reconcile_tool",
            description="Recovery timeout test tool.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.reconcile,
            timeout_s=0.01,
        )

    async def reconcile(
        self, _ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(tool_call_id="", name="reconcile_tool", content="late")


class _TimeoutReconcileTool(_ReconcileTool):
    def __init__(self, *, reconcile_fails: bool = False) -> None:
        super().__init__()
        self.reconcile_fails = reconcile_fails

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="timeout_reconcile_tool",
            description="Reconcile a timed out write.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.workspace_write,
            replay_policy=ReplayPolicy.reconcile,
            max_attempts=2,
            timeout_s=0.05,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        await asyncio.sleep(1)
        return ToolResult(tool_call_id="", name="timeout_reconcile_tool", content="late")

    async def reconcile(
        self, _ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        if self.reconcile_fails:
            raise RuntimeError("target unavailable")
        return ToolResult(
            tool_call_id="",
            name="timeout_reconcile_tool",
            content="write already completed",
        )


class _LargeResultTool:
    def __init__(self, *, structured: bool = False) -> None:
        self.structured = structured

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="large_result",
            description="Return a governed large result.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            effect=EffectKind.pure,
        )

    async def run(self, _ctx: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        if self.structured:
            return ToolResult(
                tool_call_id="",
                name="large_result",
                content="structured",
                parts=[ToolContentPart(type="json", data={"value": "x" * 2_000_000})],
            )
        return ToolResult(
            tool_call_id="", name="large_result", content="\U0001f600" * 2_000
        )


@pytest.mark.asyncio
async def test_invalid_arguments_never_request_approval_or_execute(harness, workspace):
    calls = 0
    approvals = 0
    original = harness.tools["write_file"].run

    async def counting(ctx, arguments):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await original(ctx, arguments)

    async def approve(_request: ApprovalRequest) -> ApprovalDecision:
        nonlocal approvals
        approvals += 1
        return ApprovalDecision.allow_once

    harness.tools["write_file"].run = counting
    harness.set_approval_callback(approve)
    result = await harness.run(
        RunRequest(
            message='[fake:tools]write_file\n{"path":"never.txt"}',
            provider="fake",
            approval=ApprovalMode.ask,
            allow_write=True,
            cwd=str(workspace),
        )
    )

    assert calls == 0
    assert approvals == 0
    assert not (workspace / "never.txt").exists()
    invocation = harness.list_tool_invocations(result.run_id)[0]
    assert invocation.status.value == "failed"
    assert invocation.result is not None
    assert invocation.result.error_code == "invalid_arguments"
    assert invocation.attempt_count == 0


@pytest.mark.asyncio
async def test_duplicate_provider_call_id_fails_before_tool_execution(data_dir, workspace):
    harness = create_test_harness(
        data_dir=data_dir, providers={"duplicate": _DuplicateCallProvider()}
    )
    try:
        result = await harness.run(
            RunRequest(message="duplicate", provider="duplicate", cwd=str(workspace))
        )
        assert result.status.value == "failed"
        assert "duplicate tool call id" in (result.error or "")
        assert harness.list_tool_invocations(result.run_id) == []
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_provider_call_id_may_be_reused_in_later_model_turns(data_dir, workspace):
    provider = _CrossRoundDuplicateCallProvider()
    harness = create_test_harness(data_dir=data_dir, providers={provider.name: provider})
    try:
        result = await harness.run(
            RunRequest(message="read twice", provider=provider.name, cwd=str(workspace))
        )
        invocations = harness.list_tool_invocations(result.run_id)
        assert result.status == RunStatus.completed
        assert len(invocations) == 2
        assert {item.provider_call_id for item in invocations} == {"reused-provider-id"}
        assert len({item.id for item in invocations}) == 2
        assert all(item.status == ToolInvocationStatus.succeeded for item in invocations)
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_recovered_terminal_result_deduplicates_by_invocation_id(
    data_dir, workspace
):
    provider = _CaptureProvider()
    harness = create_test_harness(data_dir=data_dir, providers={provider.name: provider})
    run_id = "terminal-result-recovery"
    session_id = harness.storage.create_session()
    second_call = ToolCall(
        id="reused-provider-id",
        invocation_id="second-invocation",
        name="read_file",
        arguments={"path": "a.txt"},
    )
    first_call = ToolCall(
        id="reused-provider-id",
        invocation_id="first-invocation",
        name="read_file",
        arguments={"path": "a.txt"},
    )
    first_result = ToolResult(
        tool_call_id="reused-provider-id",
        invocation_id="first-invocation",
        name="read_file",
        content="first",
    )
    second_result = ToolResult(
        tool_call_id="reused-provider-id",
        invocation_id=second_call.invocation_id,
        name="read_file",
        content="second",
    )
    messages = [
        Message(role=MessageRole.user, content="recover"),
        Message(role=MessageRole.assistant, tool_calls=[first_call]),
        Message(
            role=MessageRole.tool,
            tool_call_id="reused-provider-id",
            name="read_file",
            content="first",
            tool_result=first_result,
        ),
        Message(role=MessageRole.assistant, tool_calls=[second_call]),
    ]
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider=provider.name,
        approval=ApprovalMode.auto.value,
        cwd=str(workspace),
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id=second_call.invocation_id,
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id=second_call.id,
            tool_name=second_call.name,
            status=ToolInvocationStatus.succeeded,
            effect=EffectKind.workspace_read,
            replay_policy=ReplayPolicy.safe,
            arguments=second_call.arguments,
            result=second_result,
        )
    )
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=messages,
            pending_tool_calls=[second_call],
            status=RunStatus.interrupted,
        )
    )
    try:
        result = await harness.resume(run_id)
        invocation_ids = [
            message.tool_result.invocation_id
            for message in provider.requests[0].messages
            if message.role == MessageRole.tool and message.tool_result is not None
        ]
        assert result.status == RunStatus.completed
        assert invocation_ids == ["first-invocation", "second-invocation"]
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_safe_retry_is_bounded_and_audited(harness, workspace):
    tool = _RetryTool()
    harness.register_tool(tool)
    result = await harness.run(
        RunRequest(
            message="[fake:tools]retry_tool\n{}",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )

    invocation = harness.list_tool_invocations(result.run_id)[0]
    assert tool.calls == 2
    assert invocation.status.value == "succeeded"
    assert invocation.attempt_count == 2
    assert invocation.result is not None and invocation.result.attempts == 2
    assert any(event.type == "tool_retry" for event in harness.get_events(result.run_id))


@pytest.mark.asyncio
async def test_unclassified_tool_exception_is_not_automatically_retried(
    data_dir, workspace
):
    tool = _ExceptionTool()
    harness = create_test_harness(data_dir=data_dir, tools={"exception_tool": tool})
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]exception_tool\n{}",
                provider="fake",
                cwd=str(workspace),
            )
        )
        invocation = harness.list_tool_invocations(result.run_id)[0]
        assert tool.calls == 1
        assert invocation.attempt_count == 1
        assert invocation.result is not None
        assert invocation.result.error_code == "tool_exception"
        assert invocation.result.retryable is False
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_successful_tool_final_output_ends_run_without_another_model_turn(
    harness,
    workspace,
) -> None:
    tool = _TerminalOutputTool()
    harness.register_tool(tool)

    result = await harness.run(
        RunRequest(
            message="[fake:tools]terminal_output_tool\n{}",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            tools=[tool.spec.name],
            verification=VerificationPolicy(
                validators=[
                    VerificationCheck(
                        kind="output",
                        assertions={
                            "contains": ["DONE: deterministic work completed"],
                            "tools_succeeded": [tool.spec.name],
                        },
                    )
                ],
                max_retries=0,
                on_exhausted="failed",
            ),
        )
    )

    assert result.status == RunStatus.completed
    assert result.output.endswith("DONE: deterministic work completed")
    assert result.usage.model_turns == 1
    assert len(harness.providers["fake"].calls) == 1


@pytest.mark.asyncio
async def test_tool_final_output_does_not_hide_another_tool_failure(
    harness,
    workspace,
) -> None:
    tool = _TerminalOutputTool()
    harness.register_tool(tool)

    result = await harness.run(
        RunRequest(
            message="[fake:tools]terminal_output_tool|missing_tool\n[{},{}]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            tools=[tool.spec.name],
        )
    )

    assert result.status == RunStatus.completed
    assert result.usage.model_turns == 2
    assert len(harness.providers["fake"].calls) == 2
    assert "Unknown tool: missing_tool" in result.output


@pytest.mark.asyncio
async def test_tool_final_output_respects_total_result_budget(harness, workspace) -> None:
    tool = _TerminalOutputTool("x" * 5_000)
    harness.register_tool(tool)

    result = await harness.run(
        RunRequest(
            message="[fake:tools]terminal_output_tool\n{}",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            tools=[tool.spec.name],
            budget=BudgetConfig(
                max_tool_result_bytes=1_024,
                max_inline_tool_result_bytes=256,
                max_output_length=5_000,
            ),
        )
    )
    invocation = harness.list_tool_invocations(result.run_id)[0]
    assert invocation.result is not None
    encoded = json.dumps(
        invocation.result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert result.status == RunStatus.completed
    assert len(encoded) <= 1_024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reconcile_fails", "expected_status", "expected_invocation"),
    [
        (False, RunStatus.completed, ToolInvocationStatus.succeeded),
        (True, RunStatus.require_human, ToolInvocationStatus.indeterminate),
    ],
)
async def test_timed_out_reconcilable_write_is_never_blindly_retried(
    data_dir,
    workspace,
    reconcile_fails,
    expected_status,
    expected_invocation,
):
    tool = _TimeoutReconcileTool(reconcile_fails=reconcile_fails)
    harness = create_test_harness(
        data_dir=data_dir, tools={"timeout_reconcile_tool": tool}
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]timeout_reconcile_tool\n{}",
                provider="fake",
                approval=ApprovalMode.auto,
                allow_write=True,
                cwd=str(workspace),
            )
        )
        invocation = harness.list_tool_invocations(result.run_id)[0]
        assert result.status == expected_status
        assert tool.calls == 1
        assert invocation.attempt_count == 1
        assert invocation.status == expected_invocation
        if reconcile_fails:
            assert invocation.error_code == "outcome_indeterminate"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_http_connect_error_is_retried_once_for_safe_request(
    data_dir, workspace, monkeypatch
):
    tool = HttpTool()
    calls = 0

    async def flaky_request(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused")
        return ToolResult(tool_call_id="", name="http_request", content="status=200\nok")

    monkeypatch.setattr(tool, "_request_with_redirects", flaky_request)
    harness = create_test_harness(data_dir=data_dir, tools={"http_request": tool})
    try:
        result = await harness.run(
            RunRequest(
                message='[fake:tools]http_request\n{"url":"https://example.com"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        invocation = harness.list_tool_invocations(result.run_id)[0]
        assert calls == 2
        assert invocation.status == ToolInvocationStatus.succeeded
        assert invocation.attempt_count == 2
        assert len(harness.list_tool_attempts(invocation.id)) == 2
    finally:
        await harness.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_type",
    [_ReconcileTool, _RaisingReconcileTool, _HangingReconcileTool],
)
async def test_interrupted_reconcile_fails_closed_without_reexecution(
    data_dir, workspace, tool_type
):
    tool = tool_type()
    harness = create_test_harness(data_dir=data_dir, tools={"reconcile_tool": tool})
    run_id = f"reconcile-{tool_type.__name__}"
    session_id = harness.storage.create_session()
    call = ToolCall(
        id="provider-call",
        invocation_id="invocation",
        name="reconcile_tool",
        arguments={},
    )
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider="fake",
        approval=ApprovalMode.auto.value,
        cwd=str(workspace),
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id=call.invocation_id,
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id=call.id,
            tool_name=call.name,
            status=ToolInvocationStatus.running,
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.reconcile,
        )
    )
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=[
                Message(role=MessageRole.user, content="recover"),
                Message(role=MessageRole.assistant, tool_calls=[call]),
            ],
            pending_tool_calls=[call],
            status=RunStatus.interrupted,
        )
    )
    try:
        result = await harness.resume(run_id)
        invocation = harness.get_tool_invocation(call.invocation_id)
        assert result.status == RunStatus.require_human
        assert tool.calls == 0
        assert invocation is not None
        assert invocation.status == ToolInvocationStatus.indeterminate
        assert invocation.error_code == "outcome_indeterminate"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_status", "is_error", "expected_calls"),
    [
        (
            ToolRecoveryDecision.mark_succeeded,
            ToolInvocationStatus.succeeded,
            False,
            0,
        ),
        (ToolRecoveryDecision.skip, ToolInvocationStatus.failed, True, 0),
        (ToolRecoveryDecision.retry, ToolInvocationStatus.succeeded, False, 1),
    ],
)
async def test_human_can_resolve_indeterminate_tool_and_resume(
    data_dir,
    workspace,
    decision,
    expected_status,
    is_error,
    expected_calls,
):
    tool = _ReconcileTool()
    harness = create_test_harness(data_dir=data_dir, tools={"reconcile_tool": tool})
    run_id = f"human-resolution-{decision.value}"
    session_id = harness.storage.create_session()
    argument_hash = arguments_sha256({})
    call = ToolCall(
        id="provider-call",
        invocation_id="indeterminate-invocation",
        name="reconcile_tool",
        arguments={},
    )
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.require_human,
        provider="fake",
        approval=ApprovalMode.auto.value,
        cwd=str(workspace),
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id=call.invocation_id,
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id=call.id,
            tool_name=call.name,
            status=ToolInvocationStatus.indeterminate,
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.never,
            arguments={},
            arguments_sha256=argument_hash,
            attempt_count=1,
            error_code="outcome_indeterminate",
            error_category="recovery",
        )
    )
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=[
                Message(role=MessageRole.user, content="recover"),
                Message(role=MessageRole.assistant, tool_calls=[call]),
            ],
            pending_tool_calls=[call],
            status=RunStatus.require_human,
        )
    )
    try:
        with pytest.raises(RuntimeError, match="parameters do not match"):
            harness.resolve_indeterminate_tool(
                call.invocation_id,
                decision,
                arguments_sha256="0" * 64,
            )
        harness.resolve_indeterminate_tool(
            call.invocation_id,
            decision,
            arguments_sha256=argument_hash,
        )
        with pytest.raises(RuntimeError, match="not indeterminate"):
            harness.resolve_indeterminate_tool(
                call.invocation_id,
                decision,
                arguments_sha256=argument_hash,
            )

        capture = _CaptureProvider()
        harness.register_provider("fake", capture)
        resumed = await harness.resume(run_id)
        invocation = harness.get_tool_invocation(call.invocation_id)

        assert resumed.status == RunStatus.completed
        assert invocation is not None
        assert invocation.status == expected_status
        assert invocation.result is not None
        assert invocation.result.is_error is is_error
        assert tool.calls == expected_calls
        assert invocation.attempt_count == (2 if decision == ToolRecoveryDecision.retry else 1)
        tool_messages = [
            message for message in capture.requests[0].messages if message.role == MessageRole.tool
        ]
        assert len(tool_messages) == 1
        assert tool_messages[0].tool_result == invocation.result
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_interrupted_safe_tool_continues_with_next_audit_attempt(
    data_dir, workspace
):
    tool = _SafeRecoveryTool()
    harness = create_test_harness(data_dir=data_dir, tools={"safe_recovery_tool": tool})
    run_id = "safe-recovery"
    session_id = harness.storage.create_session()
    call = ToolCall(
        id="provider-call",
        invocation_id="safe-invocation",
        name="safe_recovery_tool",
        arguments={},
    )
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.interrupted,
        provider="fake",
        approval=ApprovalMode.auto.value,
        cwd=str(workspace),
    )
    harness.storage.save_tool_invocation(
        ToolInvocationRecord(
            id=call.invocation_id,
            run_id=run_id,
            session_id=session_id,
            step=0,
            ordinal=0,
            provider_call_id=call.id,
            tool_name=call.name,
            status=ToolInvocationStatus.running,
            effect=EffectKind.pure,
            replay_policy=ReplayPolicy.safe,
            attempt_count=1,
        )
    )
    harness.storage.start_tool_attempt(call.invocation_id, 1)
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="tool_batch",
            step=0,
            messages=[Message(role=MessageRole.user, content="recover")],
            pending_tool_calls=[call],
            status=RunStatus.interrupted,
        )
    )
    try:
        result = await harness.resume(run_id)
        invocation = harness.get_tool_invocation(call.invocation_id)
        attempts = harness.list_tool_attempts(call.invocation_id)
        assert result.status == RunStatus.completed
        assert tool.calls == 1
        assert invocation is not None
        assert invocation.status == ToolInvocationStatus.succeeded
        assert invocation.attempt_count == 2
        assert [(item["attempt"], item["status"]) for item in attempts] == [
            (1, "interrupted"),
            (2, "succeeded"),
        ]
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_live_cancelled_safe_tool_resumes_without_duplicate_attempt_or_result(
    data_dir, workspace
):
    tool = _CancellableSafeTool()
    harness = create_test_harness(data_dir=data_dir, tools={tool.spec.name: tool})
    try:
        task = asyncio.create_task(
            harness.run(
                RunRequest(
                    message=f"[fake:tools]{tool.spec.name}\n{{}}",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                )
            )
        )
        await asyncio.wait_for(tool.started.wait(), timeout=5)
        run_id = harness.engine.active_run_id
        assert run_id is not None
        await harness.interrupt(run_id, "test interruption")
        interrupted = await asyncio.wait_for(task, timeout=5)
        assert interrupted.status == RunStatus.interrupted

        capture = _CaptureProvider()
        harness.register_provider("fake", capture)
        resumed = await asyncio.wait_for(harness.resume(run_id), timeout=5)

        assert resumed.status == RunStatus.completed
        assert tool.calls == 2
        invocation = harness.list_tool_invocations(run_id)[0]
        assert invocation.attempt_count == 2
        attempts = harness.list_tool_attempts(invocation.id)
        assert [item["attempt"] for item in attempts] == [1, 2]
        tool_messages = [
            message for message in capture.requests[0].messages if message.role == MessageRole.tool
        ]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "recovered"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_inline_result_limit_counts_utf8_bytes(data_dir, workspace):
    harness = create_test_harness(
        data_dir=data_dir, tools={"large_result": _LargeResultTool()}
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]large_result\n{}",
                provider="fake",
                cwd=str(workspace),
            )
        )
        invocation = harness.list_tool_invocations(result.run_id)[0]
        assert invocation.result is not None
        assert len(invocation.result.content.encode("utf-8")) <= 4_096
        assert invocation.result.artifact_id is not None
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_structured_result_cannot_bypass_total_byte_limit(data_dir, workspace):
    harness = create_test_harness(
        data_dir=data_dir,
        tools={"large_result": _LargeResultTool(structured=True)},
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]large_result\n{}",
                provider="fake",
                cwd=str(workspace),
            )
        )
        invocation = harness.list_tool_invocations(result.run_id)[0]
        stored = harness.storage._reader().execute(  # noqa: SLF001 - persisted bound evidence
            "SELECT result_json FROM tool_invocations WHERE id = ?", (invocation.id,)
        ).fetchone()[0]
        assert len(stored.encode("utf-8")) <= 1_048_576
        assert invocation.result is not None
        assert invocation.result.parts == []
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_run_scoped_approval_is_bound_to_tool_target(harness, workspace):
    approvals: list[ApprovalRequest] = []

    async def allow_run(request: ApprovalRequest) -> ApprovalDecision:
        approvals.append(request)
        return ApprovalDecision.allow_run

    harness.set_approval_callback(allow_run)
    await harness.run(
        RunRequest(
            message=(
                "[fake:tools]write_file|write_file\n"
                '[{"path":"a.txt","content":"a"},{"path":"b.txt","content":"b"}]'
            ),
            provider="fake",
            approval=ApprovalMode.ask,
            allow_write=True,
            cwd=str(workspace),
        )
    )

    assert len(approvals) == 2
    assert approvals[0].approval_scope != approvals[1].approval_scope
    assert all(item.arguments_sha256 for item in approvals)


@pytest.mark.asyncio
async def test_tool_invocations_are_available_through_api(data_dir, workspace):
    harness = create_test_harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    try:
        result = await harness.run(
            RunRequest(
                message='[fake:tools]read_file\n{"path":"a.txt"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            rows = await client.get(f"/api/runs/{result.run_id}/tool-invocations")
            assert rows.status_code == 200
            invocation_id = rows.json()[0]["id"]
            detail = await client.get(f"/api/tool-invocations/{invocation_id}")
            assert detail.status_code == 200
            assert detail.json()["arguments_sha256"]
            assert len(detail.json()["attempts_audit"]) == 1
            assert detail.json()["attempts_audit"][0]["status"] == "succeeded"
    finally:
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_parallel_segments_honor_concurrency_limit():
    from agentharness.engine.scheduler import EffectScheduler

    scheduler = EffectScheduler()
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def work() -> int:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return 1

    items = [(EffectKind.pure, work, None, True) for _ in range(8)]
    await scheduler.run_batch(items, max_concurrency=3)
    assert peak == 3


def test_builtin_tool_schemas_have_closed_and_bounded_inputs(data_dir):
    harness = create_test_harness(data_dir=data_dir)
    try:
        for tool in harness.tools.values():
            schema = tool.spec.parameters
            assert schema.get("additionalProperties") is False, tool.spec.name
            for name, prop in schema.get("properties", {}).items():
                prop_type = prop.get("type")
                if prop_type == "string" and "enum" not in prop:
                    assert "maxLength" in prop, f"{tool.spec.name}.{name}"
                elif prop_type in {"integer", "number"}:
                    assert "minimum" in prop, f"{tool.spec.name}.{name}"
                    assert "maximum" in prop, f"{tool.spec.name}.{name}"
                elif prop_type == "array":
                    assert "maxItems" in prop, f"{tool.spec.name}.{name}"
                elif prop_type == "object":
                    assert "maxProperties" in prop, f"{tool.spec.name}.{name}"
    finally:
        harness.close()


def test_http_parallel_policy_preserves_write_barrier(data_dir):
    tool = HttpTool()
    harness = create_test_harness(data_dir=data_dir, tools={"http_request": tool})
    try:
        get_call = ToolCall(
            name="http_request", arguments={"url": "https://example.com", "method": "GET"}
        )
        post_call = ToolCall(
            name="http_request",
            arguments={"url": "https://example.com", "method": "POST"},
        )
        assert harness.engine.tool_executor._parallel_safe_for(  # noqa: SLF001 - scheduling contract
            tool, get_call, tool.effect_for(get_call.arguments)
        )
        assert not harness.engine.tool_executor._parallel_safe_for(  # noqa: SLF001
            tool, post_call, tool.effect_for(post_call.arguments)
        )
    finally:
        harness.close()


def test_mcp_catalog_becomes_namespaced_first_class_tools(data_dir):
    bridge = MCPBridge()
    bridge._tools_cache["docs"] = [
        {
            "name": "lookup-page",
            "description": "Look up documentation.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        }
    ]
    harness = create_test_harness(data_dir=data_dir)
    try:
        harness.mcp_bridge._tools_cache = bridge._tools_cache
        specs = harness.engine._tool_specs(RunRequest(message="catalog"))
        proxy = next(spec for spec in specs if spec.name == "mcp__docs__lookup-page")
        assert proxy.effect == EffectKind.network
        assert proxy.replay_policy == ReplayPolicy.safe
        assert proxy.parallel_safe is True
        assert "mcp__docs__lookup-page" in harness.tools
    finally:
        harness.close()


def test_mcp_proxy_names_are_provider_valid_and_collision_resistant():
    bridge = MCPBridge()
    bridge._tools_cache["docs"] = [
        {"name": "lookup-page", "input_schema": {"type": "object"}},
        {"name": "lookup_page", "input_schema": {"type": "object"}},
        {"name": "x" * 100, "input_schema": {"type": "object"}},
    ]

    proxies = bridge.proxy_tools()

    assert len(proxies) == 3
    assert "mcp__docs__lookup-page" in proxies
    assert "mcp__docs__lookup_page" in proxies
    assert all(len(name) <= 64 for name in proxies)
    assert all(name.replace("-", "").replace("_", "").isalnum() for name in proxies)


def test_removed_mcp_proxy_is_not_exposed_after_catalog_refresh(data_dir):
    harness = create_test_harness(data_dir=data_dir)
    try:
        harness.mcp_bridge._tools_cache["docs"] = [
            {
                "name": "old_tool",
                "description": "Old tool.",
                "input_schema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True},
            }
        ]
        first = {spec.name for spec in harness.engine._tool_specs(RunRequest(message="catalog"))}
        assert "mcp__docs__old_tool" in first

        harness.mcp_bridge._tools_cache.clear()
        second = {spec.name for spec in harness.engine._tool_specs(RunRequest(message="catalog"))}
        assert "mcp__docs__old_tool" not in second
        assert "mcp__docs__old_tool" not in harness.tools
    finally:
        harness.close()


@pytest.mark.parametrize("name", ["tool.with.dot", "tool:with:colon", "x" * 65])
def test_tool_spec_rejects_provider_incompatible_names(name):
    with pytest.raises(ValueError, match="invalid tool name"):
        validate_tool_spec(ToolSpec(name=name, description="invalid provider name"))


def test_mcp_idempotent_tool_uses_safe_replay_without_parallel_execution(data_dir):
    harness = create_test_harness(data_dir=data_dir)
    try:
        harness.mcp_bridge._tools_cache["writer"] = [  # noqa: SLF001 - catalog fixture
            {
                "name": "upsert",
                "description": "Idempotent remote write.",
                "input_schema": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
                "annotations": {"idempotentHint": True},
            }
        ]
        specs = harness.engine._tool_specs(RunRequest(message="catalog"))
        proxy = next(spec for spec in specs if spec.name == "mcp__writer__upsert")
        assert proxy.effect == EffectKind.destructive
        assert proxy.replay_policy == ReplayPolicy.safe
        assert proxy.parallel_safe is False
        assert proxy.max_attempts == 2
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_mcp_stdio_connection_failure_is_not_reported_as_success(monkeypatch):
    bridge = MCPBridge()

    async def fail_connect(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return "MCP connect failed (isolated): executable missing"

    monkeypatch.setattr(bridge, "connect_stdio", fail_connect)
    result = await MCPTool(bridge).run(
        ToolContext(run_id="run", session_id="session", cwd=".", data_dir="."),
        {"action": "connect_stdio", "command": "missing"},
    )
    assert result.is_error is True
    assert result.error_code == "mcp_connect_failed"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_mcp_embedded_resource_becomes_structured_artifact(harness, workspace):
    class Session:
        async def call_tool(self, _tool, _arguments):  # type: ignore[no-untyped-def]
            resource = SimpleNamespace(
                uri="file:///report.txt",
                mimeType="text/plain",
                text="resource body",
            )
            return SimpleNamespace(
                content=[SimpleNamespace(type="resource", resource=resource)],
                structuredContent={},
                isError=False,
            )

    harness.mcp_bridge._sessions["docs"] = {"session": Session()}  # noqa: SLF001
    result = await harness.mcp_bridge.call_tool_result(
        ToolContext(
            run_id="run",
            session_id="session",
            cwd=str(workspace),
            data_dir=str(harness.storage.data_dir),
            harness=harness,
        ),
        "docs",
        "read-resource",
        {},
    )
    assert result.is_error is False
    assert len(result.parts) == 2
    assert result.parts[0].type == "resource"
    assert result.parts[0].data["uri"] == "file:///report.txt"
    assert result.parts[0].artifact_id is not None
    assert result.parts[1].type == "json"
    assert result.parts[1].data == {}
    assert harness.storage.get_artifact(result.parts[0].artifact_id) is not None


@pytest.mark.asyncio
async def test_mcp_artifact_bytes_cannot_bypass_result_budget(harness, workspace):
    class Session:
        async def call_tool(self, _tool, _arguments):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="image",
                        data=base64.b64encode(b"x" * 600).decode("ascii"),
                        mimeType="image/png",
                    ),
                    SimpleNamespace(
                        type="image",
                        data=base64.b64encode(b"y" * 600).decode("ascii"),
                        mimeType="image/png",
                    )
                ],
                isError=False,
            )

    harness.mcp_bridge._sessions["images"] = {"session": Session()}  # noqa: SLF001
    before = harness.storage.maintenance_stats()["artifacts"]
    result = await harness.mcp_bridge.call_tool_result(
        ToolContext(
            run_id="run",
            session_id="session",
            cwd=str(workspace),
            data_dir=str(harness.storage.data_dir),
            metadata={"budget": {"max_tool_result_bytes": 1_024}},
            harness=harness,
        ),
        "images",
        "large-image",
        {},
    )

    assert result.is_error is True
    assert result.error_code == "result_too_large"
    assert harness.storage.maintenance_stats()["artifacts"] == before


@pytest.mark.asyncio
async def test_mcp_transport_retryability_requires_registered_safe_semantics(
    harness, workspace
):
    class FailingSession:
        async def call_tool(self, _tool, _arguments):  # type: ignore[no-untyped-def]
            raise ConnectionError("transport unavailable")

    bridge = harness.mcp_bridge
    bridge._sessions["docs"] = {"session": FailingSession()}  # noqa: SLF001
    bridge._tools_cache["docs"] = [  # noqa: SLF001
        {
            "name": "lookup",
            "description": "Read docs.",
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        }
    ]
    ctx = ToolContext(
        run_id="run",
        session_id="session",
        cwd=str(workspace),
        data_dir=str(harness.storage.data_dir),
        harness=harness,
    )

    proxy_result = await bridge.proxy_tools()["mcp__docs__lookup"].run(ctx, {})
    generic_result = await MCPTool(bridge).run(
        ctx,
        {"action": "call_tool", "server": "docs", "tool": "lookup", "arguments": {}},
    )

    assert proxy_result.error_code == "mcp_transport_error"
    assert proxy_result.retryable is True
    assert generic_result.error_code == "outcome_indeterminate"
    assert generic_result.retryable is False


@pytest.mark.asyncio
async def test_mcp_disconnect_closes_session_and_transport():
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            closed.append(self.name)

    bridge = MCPBridge()
    bridge._sessions["docs"] = {  # noqa: SLF001 - connection lifecycle fixture
        "session": Resource("session"),
        "cm": Resource("transport"),
    }
    bridge._tools_cache["docs"] = []  # noqa: SLF001

    await bridge._disconnect("docs")  # noqa: SLF001 - lifecycle invariant

    assert closed == ["session", "transport"]
    assert "docs" not in bridge._sessions  # noqa: SLF001
    assert "docs" not in bridge._tools_cache  # noqa: SLF001
