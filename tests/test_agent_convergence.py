"""Phase-1 convergence tests: explicit stage state machine, anti-loop dedup and
human-action injection (docs/agent-upgrade-2026-08-05.md section 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agentharness.contracts import (
    EventType,
    MessageRole,
    ModelRequest,
    ModelStreamItem,
    ToolInvocationStatus,
)
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    PROCUREMENT_PROVIDER,
    ProcurementAgent,
    ProcurementFakeProvider,
    _fake_run_profile,
)
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementService


class _RepeatCaptureProvider(ProcurementFakeProvider):
    """Injects a stage-skipping re-capture: capture -> capture (illegal)."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        has_tool = any(message.role == MessageRole.tool for message in request.messages)
        if not has_tool:
            arguments = {
                "request_id": request_id,
                **self._extract_requirement(request.messages),
            }
            async for item in self._tool_call(
                "procurement_capture_requirement", arguments
            ):
                yield item
            return
        # After the first capture succeeded, illegally try to capture again
        # (exactly once; afterwards behave normally so the run can stop safely).
        tool_count = sum(
            1 for message in request.messages if message.role == MessageRole.tool
        )
        if tool_count == 1:
            arguments = {
                "request_id": request_id,
                **self._extract_requirement(request.messages),
            }
            async for item in self._tool_call(
                "procurement_capture_requirement", arguments
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


class _ReadTwiceProvider(ProcurementFakeProvider):
    """Injects the historical read_request x4-style polling: read -> read."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        has_tool = any(message.role == MessageRole.tool for message in request.messages)
        if not has_tool:
            async for item in self._tool_call(
                "procurement_read_request", {"request_id": request_id}
            ):
                yield item
            return
        # After the first read, poll again (no state change yet), exactly once.
        tool_count = sum(
            1 for message in request.messages if message.role == MessageRole.tool
        )
        if tool_count == 1:
            async for item in self._tool_call(
                "procurement_read_request", {"request_id": request_id}
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


@pytest.fixture
def truth_cases():
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]  # q-alpha + q-beta (same pair as the API tests)
    return [
        (case["filename"], build_case_document(case)) for case in cases
    ]


@pytest.mark.asyncio
async def test_stage_gate_rejects_repeated_capture_and_records_event(
    data_dir, truth_cases
) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    harness.register_provider(PROCUREMENT_PROVIDER, _RepeatCaptureProvider())
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                "厚度公差3微米。"
            ),
            attachments=truth_cases,
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        # capture (succeeded) + capture (blocked by stage gate)
        assert [item.tool_name for item in invocations] == [
            "procurement_capture_requirement",
            "procurement_capture_requirement",
        ]
        first, blocked = invocations
        assert first.status == ToolInvocationStatus.succeeded
        assert blocked.status == ToolInvocationStatus.failed
        assert blocked.error_code == "tool_stage_denied"
        assert blocked.error_category == "governance"
        assert blocked.result is not None
        assert "越权调用" in blocked.result.content
        assert "procurement_capture_requirement" in blocked.result.recovery_hint

        events = harness.get_events(run_id=run_id, limit=1000)
        denied = [
            event
            for event in events
            if event.type == EventType.tool_stage_denied
        ]
        assert len(denied) == 1
        assert denied[0].payload["tool_name"] == "procurement_capture_requirement"
        assert denied[0].payload["stage"] in {"analysis", "approve"}

        run = harness.get_run(run_id)
        # Run must not crash: it stops at the safe human-gated terminal state.
        assert run["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_duplicate_read_within_same_state_epoch_is_blocked(
    data_dir, truth_cases
) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    harness.register_provider(PROCUREMENT_PROVIDER, _ReadTwiceProvider())
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                "厚度公差3微米。"
            ),
            attachments=truth_cases,
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        reads = [
            item for item in invocations if item.tool_name == "procurement_read_request"
        ]
        assert len(reads) == 2
        assert reads[0].status == ToolInvocationStatus.succeeded
        assert reads[1].status == ToolInvocationStatus.failed
        assert reads[1].error_code == "duplicate_tool_call"
        assert reads[1].error_category == "governance"
        assert reads[1].result is not None
        assert "结果未变化" in reads[1].result.content

        events = harness.get_events(run_id=run_id, limit=1000)
        duplicates = [
            event
            for event in events
            if event.type == EventType.tool_call_duplicate
        ]
        assert len(duplicates) == 1
        assert duplicates[0].payload["tool_name"] == "procurement_read_request"

        run = harness.get_run(run_id)
        assert run["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_human_review_injection_reaches_analysis_in_one_turn(
    data_dir, truth_cases
) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                "厚度公差3微米。"
            ),
            attachments=truth_cases,
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]
        assert harness.get_run(run_id)["status"] == "require_human"

        # Human review result injected directly: resume must reach analysis in
        # a single model turn without polling read_request.
        resumed = await agent.resume(
            str(harness.storage.get_run(run_id)["session_id"]).replace("x", "y")
            if False
            else accepted["purchase_request_id"],
            message="[human_review_complete] 已在结构化报价面板完成人工复核，请继续执行确定性比价。",
        )
        assert resumed["status"] == "accepted"
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        assert invocations[-1].tool_name == "procurement_execute_analysis"
        assert invocations[-1].status == ToolInvocationStatus.succeeded
        request = service.get_request(accepted["purchase_request_id"])
        assert request.get("comparison") is not None
    finally:
        await agent.aclose()
        await harness.aclose()
