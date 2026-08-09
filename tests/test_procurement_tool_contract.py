"""Tool-contract regression tests: the capture tool schema and system prompt
must stay in sync with the procurement domain field sets (single source of
truth in agentharness.procurement.service)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.harness import Harness
from agentharness.procurement.agent import (
    PROCUREMENT_TOOL_NAMES,
    ProcurementAgent,
    _fake_run_profile,
)
from agentharness.procurement.service import (
    REQUIRED_SPEC_FIELDS,
    SUPPORTED_CONSTRAINT_FIELDS,
    SUPPORTED_SPEC_FIELDS,
    ProcurementService,
    _validated_requirement,
)


def _capture_schema(agent: ProcurementAgent) -> dict:
    capture = next(
        tool.spec.parameters
        for tool in agent.harness.tools.values()
        if tool.spec.name == "procurement_capture_requirement"
    )
    return capture


@pytest.fixture()
async def agent(data_dir: Path) -> ProcurementAgent:  # noqa: ANN001
    harness = Harness(data_dir=data_dir)
    agent = ProcurementAgent(
        harness,
        ProcurementService(harness),
        run_profile=_fake_run_profile(),
    )
    try:
        yield agent
    finally:
        await agent.aclose()


def test_capture_schema_covers_all_domain_fields(agent: ProcurementAgent) -> None:
    schema = _capture_schema(agent)
    spec_props = set(schema["properties"]["specifications"]["properties"])
    constraint_props = set(schema["properties"]["constraints"]["properties"])

    assert REQUIRED_SPEC_FIELDS <= spec_props
    assert SUPPORTED_SPEC_FIELDS == spec_props
    assert "height_mm" not in set(schema["properties"]["specifications"]["required"])
    assert SUPPORTED_CONSTRAINT_FIELDS <= constraint_props
    assert "required_delivery_date" in constraint_props


def test_all_four_whitelist_tools_are_registered(agent: ProcurementAgent) -> None:
    names = {tool.spec.name for tool in agent.harness.tools.values()}
    assert PROCUREMENT_TOOL_NAMES == tuple(sorted(names, key=PROCUREMENT_TOOL_NAMES.index))


def test_system_prompt_covers_date_and_roll_guidance(agent: ProcurementAgent) -> None:
    request = agent._run_request(
        request_id="request-contract",
        session_id="session-contract",
        message="分析报价",
        source="procurement_conversation",
    )
    system = request.system or ""
    assert "required_delivery_date" in system
    assert "当前年份" in system
    assert "不得丢弃" in system
    schema = _capture_schema(agent)
    length_desc = schema["properties"]["specifications"]["properties"]["length_mm"]["description"]
    assert "1000m=1000000" in length_desc
    delivery_desc = schema["properties"]["constraints"]["properties"]["required_delivery_date"]["description"]
    assert "max_lead_days" in delivery_desc


def test_service_accepts_roll_goods_length() -> None:
    payload = {
        "title": "缠绕膜卷材",
        "item_name": "缠绕膜",
        "quantity": 1000,
        "unit": "piece",
        "specifications": {
            "width_mm": "500",
            "length_mm": "1200000",
            "thickness_um": "20",
            "material": "PE",
            "color": "透明",
            "print_colors": 0,
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 14,
            "invoice_required": True,
        },
    }
    validated = _validated_requirement(payload)
    assert validated["specifications"]["length_mm"] == "1200000"
