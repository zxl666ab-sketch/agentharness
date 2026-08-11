"""Feature-roadmap regression tests (2026-08-07): near-term items F1-F4.

F3: prompt / tool-schema / parser / ruleset versions are recorded per run and
    exposed in the run report.
"""

from __future__ import annotations

import json

import pytest

from agentharness.api.execution import PendingApprovalBroker
from agentharness.api.reporting import build_run_report
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    PROCUREMENT_PROMPT_VERSION,
    PROCUREMENT_TOOL_SCHEMA_VERSION,
    ProcurementAgent,
    _fake_run_profile,
)
from agentharness.procurement.costing import RULESET_VERSION
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import PARSER_VERSION
from agentharness.procurement.service import ProcurementService


def _truth_cases(count: int = 2) -> list[tuple[str, bytes]]:
    truth = load_frozen_truth()
    return [
        (case["filename"], build_case_document(case)) for case in truth["quotes"][:count]
    ]



async def _run_conversation_to_require_human(
    agent: ProcurementAgent, cases: list[tuple[str, bytes]]
) -> tuple[str, str]:
    accepted = await agent.start(
        message=(
            "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
            "15天内交付上海松江，必须开票；USD/CNY按7.2。"
        ),
        attachments=cases,
    )
    run_id = accepted["run_id"]
    request_id = accepted["purchase_request_id"]
    await agent._tasks[run_id]
    assert agent.harness.get_run(run_id)["status"] == "require_human"
    return run_id, request_id


@pytest.mark.asyncio
async def test_run_report_exposes_versions(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2。"
            ),
            attachments=_truth_cases(2),
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        run = harness.get_run(run_id)
        metadata = json.loads(run["metadata_json"])
        assert metadata["procurement_prompt_version"] == PROCUREMENT_PROMPT_VERSION
        assert metadata["procurement_tool_schema_version"] == PROCUREMENT_TOOL_SCHEMA_VERSION
        assert metadata["procurement_parser_version"] == PARSER_VERSION
        assert metadata["procurement_ruleset_version"] == RULESET_VERSION
        assert len(metadata["procurement_prompt_sha256"]) == 64
        assert len(metadata["procurement_tool_schema_sha256"]) == 64

        report = build_run_report(harness, run_id)
        assert report is not None
        versions = report["versions"]
        assert versions["prompt_version"] == PROCUREMENT_PROMPT_VERSION
        assert versions["tool_schema_version"] == PROCUREMENT_TOOL_SCHEMA_VERSION
        assert versions["parser_version"] == PARSER_VERSION
        assert versions["ruleset_version"] == RULESET_VERSION
        assert versions["model"] == "procurement-fake-v1"
        assert len(versions["prompt_sha256"]) == 64
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- F1
@pytest.mark.asyncio
async def test_run_timeline_merges_events_and_tools(data_dir) -> None:  # type: ignore[no-untyped-def]
    from agentharness.api.reporting import build_run_timeline

    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2。"
            ),
            attachments=_truth_cases(2),
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        timeline = build_run_timeline(harness, run_id)
        assert timeline is not None
        assert timeline["run_id"] == run_id
        assert timeline["total"] > 0
        assert timeline["event_count"] > 0
        assert timeline["tool_count"] >= 1
        tool_items = [item for item in timeline["items"] if item["kind"] == "tool"]
        assert tool_items
        assert any(item["tool_name"] == "procurement_capture_requirement" for item in tool_items)
        assert any(item["kind"] == "event" for item in timeline["items"])
        ats = [item["at"] for item in timeline["items"] if item["at"]]
        assert ats == sorted(ats)
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- F2
@pytest.mark.asyncio
async def test_usage_summary_aggregates_runs(data_dir, workspace) -> None:  # type: ignore[no-untyped-def]
    from httpx import ASGITransport, AsyncClient

    from agentharness.api.reporting import build_usage_summary
    from agentharness.api.server import create_app

    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    app = create_app(harness=harness, workspace_roots=[workspace])
    agent = app.state.procurement_agent
    try:
        accepted = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2。"
            ),
            attachments=_truth_cases(2),
        )
        await agent._tasks[accepted["run_id"]]

        summary = build_usage_summary(harness)
        assert summary["runs"] >= 1
        assert summary["by_status"].get("require_human", 0) >= 1
        assert summary["by_model"].get("procurement-fake-v1", 0) >= 1
        assert summary["tokens"]["input"] > 0
        assert summary["tokens"]["total"] > 0
        assert "budget_warnings" in summary

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            runs = await client.get("/api/runs")
            assert runs.status_code == 200
            body = runs.json()
            assert body["items"]
            assert body["items"][0]["id"] == accepted["run_id"]
            metrics = await client.get("/api/metrics/summary")
            assert metrics.status_code == 200
            assert metrics.json()["runs"] >= 1
    finally:
        await agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- F4
class _FailReviewerProvider:
    """Second provider that always disagrees with the approval."""

    name = "reviewer"

    async def stream(self, request):  # type: ignore[no-untyped-def]
        from agentharness.contracts import (
            ModelStreamItem,
            StreamItemType,
            Usage,
        )

        yield ModelStreamItem(
            type=StreamItemType.text_delta,
            text='{"pass": false, "reason": "推荐与审批不一致（模拟异议）"}',
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        yield ModelStreamItem(type=StreamItemType.done)


async def _approve_with_review(
    agent: ProcurementAgent,
    service: ProcurementService,
    request_id: str,
    *,
    review_ack: bool = False,
) -> dict:
    request = service.get_request(request_id)
    snapshot = request["comparison"]
    return await agent.approve(
        request_id,
        snapshot_id=snapshot["id"],
        input_sha256=snapshot["input_sha256"],
        quote_id=snapshot["result"]["recommended_quote_id"],
        note="同意",
        actor="采购员",
        review_ack=review_ack,
    )


@pytest.mark.asyncio
async def test_review_policy_gate_requires_ack_on_fail(data_dir) -> None:  # type: ignore[no-untyped-def]
    from agentharness.procurement.service import ProcurementError

    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    harness.register_provider("reviewer", _FailReviewerProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    agent.ai_review_enabled = True
    agent.review_provider = "reviewer"
    agent.review_policy = "gate"
    try:
        _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        with pytest.raises(ProcurementError, match="独立评审对本次审批提出异议"):
            await _approve_with_review(agent, service, request_id, review_ack=False)
        assert service.get_request(request_id)["decision"] is None

        detail = await _approve_with_review(agent, service, request_id, review_ack=True)
        assert detail["status"] == "approved"
        events = service.audit_report(request_id)["audit_events"]
        reviews = [event for event in events if event["type"] == "ai_review"]
        assert reviews
        assert reviews[-1]["payload"]["verdict"] == "fail"
        assert reviews[-1]["payload"]["policy"] == "gate"
        assert reviews[-1]["payload"]["before_approval"] is True
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_review_policy_warn_does_not_block(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    harness.register_provider("reviewer", _FailReviewerProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    agent.ai_review_enabled = True
    agent.review_provider = "reviewer"
    agent.review_policy = "warn"
    try:
        _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        detail = await _approve_with_review(agent, service, request_id, review_ack=False)
        assert detail["status"] == "approved"
        events = service.audit_report(request_id)["audit_events"]
        reviews = [event for event in events if event["type"] == "ai_review"]
        assert reviews and reviews[-1]["payload"]["verdict"] == "fail"
        assert reviews[-1]["payload"]["before_approval"] is True
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_review_policy_off_disables_review(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    harness.register_provider("reviewer", _FailReviewerProvider())
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    agent.ai_review_enabled = True
    agent.review_provider = "reviewer"
    agent.review_policy = "off"
    try:
        _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        detail = await _approve_with_review(agent, service, request_id)
        assert detail["status"] == "approved"
        events = service.audit_report(request_id)["audit_events"]
        assert not any(event["type"] == "ai_review" for event in events)
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_review_policy_roundtrip_via_config(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        config = await agent.configure_model(
            provider="procurement_fake",
            model="procurement-fake-v1",
            base_url=None,
            api_key=None,
            api_mode="auto",
            reasoning_effort=None,
            input_price_per_million_usd=0,
            output_price_per_million_usd=0,
            cached_input_price_per_million_usd=0,
            max_cost_usd=None,
            ai_review_enabled=True,
            review_provider="reviewer",
            review_policy="gate",
        )
        assert config["review_policy"] == "gate"
        restored = agent._read_persisted_model_config()
        assert restored["review_policy"] == "gate"
        assert restored["ai_review_enabled"] is True
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- cardboard-box bug fixes
def _carton_requirement() -> dict:
    return {
        "title": "苏州工厂出口瓦楞纸箱采购",
        "item_name": "五层瓦楞纸箱",
        "quantity": 5000,
        "unit": "piece",
        "specifications": {
            "width_mm": "400",
            "length_mm": "300",
            "height_mm": "250",
            "thickness_um": "5000",
            "material": "瓦楞纸",
            "color": "牛皮色",
            "print_colors": 1,
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1", "USD": "7.2"},
            "max_lead_days": 20,
            "invoice_required": True,
            "size_tolerance_mm": "3",
            "thickness_tolerance_um": "500",
            "max_landed_unit_cost": "3.50",
            "destination": "苏州工厂",
        },
    }


def test_thickness_tolerance_accepts_thick_materials() -> None:
    from agentharness.procurement.service import ProcurementError, _validated_requirement

    payload = _carton_requirement()
    validated = _validated_requirement(payload)
    assert validated["constraints"]["thickness_tolerance_um"] == "500"

    payload["constraints"]["thickness_tolerance_um"] = "5000"
    validated = _validated_requirement(payload)
    assert validated["constraints"]["thickness_tolerance_um"] == "5000"

    payload["constraints"]["thickness_tolerance_um"] = "5001"
    with pytest.raises(ProcurementError):
        _validated_requirement(payload)


@pytest.mark.asyncio
async def test_api_accepts_carton_request_with_500um_tolerance(data_dir, workspace) -> None:  # type: ignore[no-untyped-def]
    from httpx import ASGITransport, AsyncClient

    from agentharness.api.server import create_app

    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/procurement/requests",
                json=_carton_requirement(),
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["constraints"]["thickness_tolerance_um"] == "500"
            assert body["specifications"]["width_mm"] == "400"
            assert body["specifications"]["length_mm"] == "300"
            assert body["specifications"]["height_mm"] == "250"
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_prompt_pins_spec_orientation(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        request = agent._run_request(
            request_id="a" * 32,
            session_id="session",
            message="x",
            source="procurement_conversation",
        )
        assert "第一个数字是宽度" in request.system
        assert "不要把宽度与长度写反" in request.system
        assert request.metadata["procurement_prompt_version"] == PROCUREMENT_PROMPT_VERSION
        assert request.metadata["procurement_prompt_version"] == "procurement-prompt-v3"
        assert "最终汇报必须按三段式组织" in request.system
        assert "采购确认单" in request.system
    finally:
        await agent.aclose()
        await harness.aclose()


def test_fake_extraction_keeps_spec_orientation() -> None:
    from agentharness.contracts import Message, MessageRole
    from agentharness.procurement.agent import ProcurementFakeProvider

    text = (
        "采购5000个五层瓦楞纸箱，规格400x300x250mm、厚度5000微米、材质瓦楞纸、牛皮色、单色印刷，"
        "20天内交付苏州工厂，必须开票；USD/CNY按7.2，尺寸公差3mm、厚度公差500微米，"
        "到货单价预算上限3.50元。请比较附件报价并推荐供应商。"
    )
    requirement = ProcurementFakeProvider._extract_requirement(
        [Message(role=MessageRole.user, content=text)]
    )
    assert requirement["specifications"]["width_mm"] == "400"
    assert requirement["specifications"]["length_mm"] == "300"
    assert requirement["specifications"]["height_mm"] == "250"
    assert requirement["specifications"]["thickness_um"] == "5000"
    assert requirement["constraints"]["thickness_tolerance_um"] == "500"
    assert requirement["constraints"]["max_landed_unit_cost"] == "3.50"



# ---------------------------------------------------------------- env config is source of truth
@pytest.mark.asyncio
async def test_env_config_wins_over_persisted_ui_config(data_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json as _json

    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test-1234")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    # Write a stale UI-saved config that must NOT override .env.
    stale = {
        "source": "ui",
        "provider": "openai",
        "model": "stale-model",
        "base_url": "https://stale.example/v1",
        "api_key": "sk-stale-key",
        "api_mode": "chat",
    }
    config_path = data_dir / "procurement-model-config.json"
    config_path.write_text(_json.dumps(stale), encoding="utf-8")

    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=None)
    try:
        config = agent.model_config()
        assert config["model"] == "env-model"
        assert config["base_url"] == "https://env.example/v1"
        assert config["api_key_configured"] is True

        # Saving from the drawer must not write a masking override while .env
        # is configured: the stale file stays untouched.
        await agent.configure_model(
            provider="procurement_fake",
            model="procurement-fake-v1",
            base_url=None,
            api_key=None,
            api_mode="auto",
            reasoning_effort=None,
            input_price_per_million_usd=0,
            output_price_per_million_usd=0,
            cached_input_price_per_million_usd=0,
            max_cost_usd=None,
        )
        assert "stale-model" in config_path.read_text(encoding="utf-8")
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_configure_model_blank_key_falls_back_to_env(data_dir, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-5678")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=None)
    try:
        config = await agent.configure_model(
            provider="openai",
            model="env-model",
            base_url=None,
            api_key=None,
            api_mode="auto",
            reasoning_effort=None,
            input_price_per_million_usd=0,
            output_price_per_million_usd=0,
            cached_input_price_per_million_usd=0,
            max_cost_usd=None,
        )
        assert config["api_key_configured"] is True
        assert config["base_url"] == "https://env.example/v1"
        assert config["model"] == "env-model"
    finally:
        await agent.aclose()
        await harness.aclose()
