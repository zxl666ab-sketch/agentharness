"""Two-phase failure-separation regression tests.

``procurement_capture_requirement`` only structures/validates the requirement;
``procurement_execute_analysis`` is the explicit second step that builds the
comparison. Validation failures must surface with actionable hints instead of
an opaque generic failure.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agentharness.contracts import MessageRole, ModelRequest, ModelStreamItem
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    PROCUREMENT_PROVIDER,
    ProcurementAgent,
    ProcurementFakeProvider,
    _fake_run_profile,
)
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementService

_MESSAGE = (
    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。"
)


def _truth_cases():
    truth = load_frozen_truth()
    return [
        (case["filename"], build_case_document(case)) for case in truth["quotes"][:2]
    ]


class _CaptureOnlyProvider(ProcurementFakeProvider):
    """Stops right after capture; never calls execute_analysis."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        if not any(message.role == MessageRole.tool for message in request.messages):
            arguments = {
                "request_id": request_id,
                **self._extract_requirement(request.messages),
            }
            async for item in self._tool_call(
                "procurement_capture_requirement", arguments
            ):
                yield item
            return
        async for item in self._text("需求已保存，等待采购员确认下一步。"):
            yield item


class _InvalidLengthProvider(ProcurementFakeProvider):
    """Passes JSON schema but violates the domain length cap."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        if not any(message.role == MessageRole.tool for message in request.messages):
            arguments = {
                "request_id": request_id,
                **self._extract_requirement(request.messages),
            }
            arguments["specifications"]["length_mm"] = "500000000"
            async for item in self._tool_call(
                "procurement_capture_requirement", arguments
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


@pytest.mark.asyncio
async def test_capture_phase_alone_does_not_build_comparison(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _CaptureOnlyProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=_truth_cases())
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        assert [item.tool_name for item in invocations] == [
            "procurement_capture_requirement"
        ]
        capture = invocations[0]
        assert capture.status.value == "succeeded"
        assert capture.result is not None
        assert '"stage":"requirement_captured"' in capture.result.content.replace(" ", "")

        request = service.get_request(accepted["purchase_request_id"])
        assert request["status"] == "collecting"
        assert request.get("comparison") is None
        assert harness.get_run(run_id)["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_capture_validation_failure_is_actionable(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider(PROCUREMENT_PROVIDER, _InvalidLengthProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(message=_MESSAGE, attachments=_truth_cases())
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        assert [item.tool_name for item in invocations] == [
            "procurement_capture_requirement"
        ]
        failed = invocations[0]
        assert failed.status.value == "failed"
        content = failed.result.content if failed.result else ""
        # 字段级原因 + 可操作修正提示，而不是模糊的通用失败
        assert "需求结构化校验失败" in content
        assert "长度" in content
        assert "请修正" in content
        assert "不要编造" in content

        request = service.get_request(accepted["purchase_request_id"])
        assert request["status"] == "draft"
        assert request.get("comparison") is None
        assert harness.get_run(run_id)["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()
