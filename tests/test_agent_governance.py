"""Phase-3 governance tests: tool-call reasons, convergence metrics in the run
report, and the configurable independent review (docs/agent-upgrade-2026-08-05.md
section 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agentharness.api.execution import PendingApprovalBroker
from agentharness.api.reporting import build_run_report
from agentharness.contracts import (
    ApprovalMode,
    EffectKind,
    ModelRequest,
    ModelStreamItem,
    RunRequest,
    StreamItemType,
    ToolContext,
    ToolResult,
    ToolSpec,
    Usage,
)
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    ProcurementAgent,
    _fake_run_profile,
)
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementService
from tests.fake_provider import FakeModelAdapter


class _StatusTool:
    name = "read_status"
    spec = ToolSpec(
        name="read_status",
        description="read status",
        parameters={"type": "object", "properties": {}},
        effect=EffectKind.pure,
    )

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        return ToolResult(tool_call_id="", name=self.name, content='{"status":"ok"}')


class _ReviewerProvider:
    name = "reviewer"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.calls += 1
        yield ModelStreamItem(
            type=StreamItemType.text_delta,
            text=(
                '{"pass": true, "reason": "与确定性比价一致"}'
                if self.payload.get("pass") is not False
                else '{"pass": false, "reason": "推荐与审批不一致"}'
            ),
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        yield ModelStreamItem(type=StreamItemType.done)


@pytest.mark.asyncio
async def test_tool_call_reason_recorded_in_invocation_and_report(
    data_dir, workspace
) -> None:
    harness = Harness(data_dir=data_dir, providers={"fake": FakeModelAdapter()})
    harness.register_tool(_StatusTool())
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]read_status",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        invocations = harness.storage.list_tool_invocations(result.run_id)
        report = build_run_report(harness, result.run_id)
    finally:
        await harness.aclose()

    assert invocations
    assert invocations[0].tool_name == "read_status"
    assert invocations[0].reason == "Calling tools..."
    reasons = report["convergence"]["tool_reasons"]
    assert reasons[0]["tool_name"] == "read_status"
    assert reasons[0]["reason"] == "Calling tools..."


@pytest.mark.asyncio
async def test_convergence_metrics_in_run_report(data_dir, workspace) -> None:
    harness = Harness(data_dir=data_dir, providers={"fake": FakeModelAdapter()})
    harness.register_tool(_StatusTool())
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]read_status",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        report = build_run_report(harness, result.run_id)
    finally:
        await harness.aclose()

    convergence = report["convergence"]
    assert convergence["model_turns"] >= 2
    assert convergence["tool_call_counts"].get("read_status", 0) >= 1
    assert convergence["total_tool_calls"] >= 1
    assert "duplicate_calls" in convergence
    assert "unauthorized_calls" in convergence
    assert "tool_reasons" in convergence


async def _run_conversation_to_require_human(
    agent: ProcurementAgent, truth_cases: list[tuple[str, bytes]]
) -> tuple[str, str]:
    accepted = await agent.start(
        message=(
            "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
            "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
            "厚度公差3微米。"
        ),
        attachments=truth_cases,
    )
    run_id = accepted["run_id"]
    request_id = accepted["purchase_request_id"]
    await agent._tasks[run_id]
    assert agent.harness.get_run(run_id)["status"] == "require_human"
    return run_id, request_id


@pytest.fixture
def truth_cases():
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    return [
        (case["filename"], build_case_document(case)) for case in cases
    ]


@pytest.mark.asyncio
async def test_ai_review_records_verdict_beside_approval(
    data_dir, truth_cases
) -> None:
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    reviewer = _ReviewerProvider({"pass": True})
    harness.register_provider("reviewer", reviewer)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    agent.ai_review_enabled = True
    agent.review_provider = "reviewer"
    try:
        run_id, request_id = await _run_conversation_to_require_human(agent, truth_cases)
        request = service.get_request(request_id)
        snapshot = request["comparison"]
        detail = await agent.approve(
            request_id,
            snapshot_id=snapshot["id"],
            input_sha256=snapshot["input_sha256"],
            quote_id=snapshot["result"]["recommended_quote_id"],
            note="同意",
            actor="采购员",
        )
        assert detail["status"] == "approved"
        assert reviewer.calls == 1
        events = service.audit_report(request_id)["audit_events"]
        review_events = [event for event in events if event["type"] == "ai_review"]
        assert len(review_events) == 1
        payload = review_events[0]["payload"]
        assert payload["verdict"] == "pass"
        assert payload["reason"] == "与确定性比价一致"
        assert payload["approval_id"]
        assert payload["run_id"] == run_id
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_ai_review_toggle_off_produces_no_review_event(
    data_dir, truth_cases
) -> None:
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    reviewer = _ReviewerProvider({"pass": True})
    harness.register_provider("reviewer", reviewer)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    agent.ai_review_enabled = False  # toggle off (default)
    agent.review_provider = "reviewer"
    try:
        run_id, request_id = await _run_conversation_to_require_human(agent, truth_cases)
        request = service.get_request(request_id)
        snapshot = request["comparison"]
        detail = await agent.approve(
            request_id,
            snapshot_id=snapshot["id"],
            input_sha256=snapshot["input_sha256"],
            quote_id=snapshot["result"]["recommended_quote_id"],
            note="同意",
            actor="采购员",
        )
        assert detail["status"] == "approved"
        assert reviewer.calls == 0
        events = service.audit_report(request_id)["audit_events"]
        assert not any(event["type"] == "ai_review" for event in events)
    finally:
        await agent.aclose()
        await harness.aclose()
