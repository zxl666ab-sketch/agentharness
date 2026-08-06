"""Phase-4.2 agent behavior regression tests: fake providers inject bad behavior
(stage skipping, duplicate calls, fabricated arguments, premature success
claims) and the system must intercept or correct each
(docs/agent-upgrade-2026-08-05.md section 4.2)."""

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


@pytest.fixture
def truth_cases():
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    return [(case["filename"], build_case_document(case)) for case in cases]


_MESSAGE = (
    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
    "厚度公差3微米。"
)


class _StageSkipProvider(ProcurementFakeProvider):
    """Bad behavior 1: jumps straight to the approval tool before analysis."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        if not any(message.role == MessageRole.tool for message in request.messages):
            async for item in self._tool_call(
                "procurement_approve_supplier",
                {
                    "request_id": request_id,
                    "snapshot_id": "0" * 32,
                    "input_sha256": "0" * 64,
                    "quote_id": "0" * 32,
                    "actor": "越权模型",
                    "note": "跳过分析直接审批",
                },
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


class _DuplicateCallProvider(ProcurementFakeProvider):
    """Bad behavior 2: repeats the same read tool within the same state epoch."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        tool_count = sum(
            1 for message in request.messages if message.role == MessageRole.tool
        )
        if tool_count == 0:
            async for item in self._tool_call(
                "procurement_read_request", {"request_id": request_id}
            ):
                yield item
            return
        if tool_count == 1:
            async for item in self._tool_call(
                "procurement_read_request", {"request_id": request_id}
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


class _FabricatedArgsProvider(ProcurementFakeProvider):
    """Bad behavior 3: invents arguments that violate the tool JSON schema."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        if not any(message.role == MessageRole.tool for message in request.messages):
            async for item in self._tool_call(
                "procurement_capture_requirement",
                {
                    "request_id": request_id,
                    "title": "编造的采购",
                    "item_name": "快递袋",
                    "quantity": -5,  # invalid: must be > 0
                    "unit": "piece",
                    "specifications": {
                        "width_mm": "250",
                        "length_mm": "350",
                        "thickness_um": "60",
                        "material": "PE",
                        "color": "白色",
                        "print_colors": 1,
                    },
                    "constraints": {
                        "base_currency": "CNY",
                        "fx_rates": {"CNY": "1", "USD": "7.2"},
                        "max_lead_days": 15,
                        "invoice_required": True,
                    },
                },
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


class _PrematureSuccessProvider(ProcurementFakeProvider):
    """Bad behavior 4: claims 【采购决策已验证】 before approval succeeds."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        if not any(message.role == MessageRole.tool for message in request.messages):
            async for item in self._text(
                "【采购决策已验证】我已经完成了全部审批。"
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


@pytest.mark.asyncio
async def test_stage_skip_is_intercepted(data_dir, truth_cases) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _StageSkipProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=truth_cases)
        run_id = accepted["run_id"]
        await agent._tasks[run_id]
        invocations = harness.storage.list_tool_invocations(run_id)
        first = invocations[0]
        assert first.tool_name == "procurement_approve_supplier"
        assert first.status == ToolInvocationStatus.failed
        assert first.error_code in {"tool_stage_denied", "tool_disabled"}
        events = harness.get_events(run_id=run_id, limit=1000)
        denied_events = [
            event for event in events if event.type == EventType.tool_stage_denied
        ]
        # Conversation flows gate approval before capture through tool
        # prerequisites; structured flows would hit the stage matrix. Either
        # way the call is intercepted and recorded (event or invocation row).
        assert denied_events or first.error_code == "tool_disabled"
        assert harness.get_run(run_id)["status"] == "require_human"
        assert service.get_request(accepted["purchase_request_id"]).get(
            "decision"
        ) is None
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_duplicate_call_is_blocked(data_dir, truth_cases) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _DuplicateCallProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=truth_cases)
        run_id = accepted["run_id"]
        await agent._tasks[run_id]
        invocations = harness.storage.list_tool_invocations(run_id)
        reads = [
            item
            for item in invocations
            if item.tool_name == "procurement_read_request"
        ]
        assert len(reads) == 2
        assert reads[1].status == ToolInvocationStatus.failed
        assert reads[1].error_code == "duplicate_tool_call"
        events = harness.get_events(run_id=run_id, limit=1000)
        assert any(
            event.type == EventType.tool_call_duplicate for event in events
        )
        assert harness.get_run(run_id)["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_fabricated_arguments_are_rejected(data_dir, truth_cases) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _FabricatedArgsProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=truth_cases)
        run_id = accepted["run_id"]
        await agent._tasks[run_id]
        invocations = harness.storage.list_tool_invocations(run_id)
        first = invocations[0]
        assert first.tool_name == "procurement_capture_requirement"
        assert first.status == ToolInvocationStatus.failed
        assert first.error_code == "invalid_arguments"
        assert "quantity" in (first.result.content if first.result else "")
        # Run survives and stops safely.
        assert harness.get_run(run_id)["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_premature_success_claim_is_not_verified(data_dir, truth_cases) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _PrematureSuccessProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=truth_cases)
        run_id = accepted["run_id"]
        await agent._tasks[run_id]
        run = harness.get_run(run_id)
        # The marker alone must never make the run pass: approval tool is
        # missing, so the run stops at the human gate instead of completing.
        assert run["status"] == "require_human"
        assert "verification requires human review" in (run.get("error") or "")
        # The premature marker must not create a decision.
        assert service.get_request(accepted["purchase_request_id"]).get(
            "decision"
        ) is None
    finally:
        await agent.aclose()
        await harness.aclose()
