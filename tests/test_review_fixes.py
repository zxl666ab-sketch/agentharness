"""Regression tests for review fixes (2026-08-07).

Covers:
- H1  tool-result truncation must not break the deterministic stage machine
- H2  non-loopback binds refuse to start without --allow-remote-execution
- M4  API key is encrypted at rest (never plaintext in the config file)
- M5  edit/approval TOCTOU: decision is re-checked inside the write transaction
- M6  staged attachments disappear once parsed into quotes
- M7  deterministic snapshot guard regenerates a missing snapshot after a run
- L2  quote import after approval returns 409 (state conflict, like corrections)
- L4  supports_invoice parse prefers invoice markers over generic negation
- L7  RAG LIKE fallback escapes % and _ wildcards
"""

from __future__ import annotations

import pytest

from agentharness.api.execution import PendingApprovalBroker
from agentharness.contracts import (
    RunRequest,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
)
from agentharness.engine.tool_execution import _invocation_stage, current_stage_index
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent, _fake_run_profile
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import coerce_field_value
from agentharness.procurement.service import ProcurementError, ProcurementService
from agentharness.storage.sqlite import Storage
from agentharness.web_main import _is_loopback, validate_bind


# ---------------------------------------------------------------- H1
def test_invocation_stage_survives_truncated_json() -> None:
    """A tool result truncated to max_inline_tool_result_bytes with an artifact
    suffix must still expose its stage marker for the deterministic stage machine."""
    content = (
        '{"ok":true,"stage":"requirement_captured","request_id":"abcdef0123456789abcdef0123456789",'
        '"requirement":{...'
        "\n...[artifact:0123456789abcdef sha=0123456789ab]"
    )
    invocation = ToolInvocationRecord(
        id="inv-1",
        run_id="run-1",
        session_id="sess-1",
        step=0,
        ordinal=0,
        provider_call_id="p-1",
        tool_name="procurement_capture_requirement",
        status=ToolInvocationStatus.succeeded,
        result=ToolResult(tool_call_id="p-1", name="procurement_capture_requirement", content=content),
    )
    assert _invocation_stage(invocation) == "requirement_captured"


def test_stage_machine_advances_to_analysis_after_capture() -> None:
    """After a successful capture (even a truncated one) the run advances from
    the capture stage to the analysis stage; only a succeeded
    ``procurement_execute_analysis`` advances to the approve stage."""
    request = RunRequest(
        message="x",
        metadata={
            "tool_stage_matrix": [
                {
                    "name": "capture",
                    "tools": ["procurement_capture_requirement"],
                    "advance_on": ["procurement_capture_requirement"],
                },
                {
                    "name": "analysis",
                    "tools": ["procurement_execute_analysis"],
                    "advance_on": ["procurement_execute_analysis"],
                },
                {
                    "name": "approve",
                    "tools": ["procurement_approve_supplier"],
                    "advance_on": ["procurement_approve_supplier"],
                },
            ],
            "tool_stage_initial": 0,
        },
    )
    invocation = ToolInvocationRecord(
        id="inv-1",
        run_id="run-1",
        session_id="sess-1",
        step=0,
        ordinal=0,
        provider_call_id="p-1",
        tool_name="procurement_capture_requirement",
        status=ToolInvocationStatus.succeeded,
        result=ToolResult(
            tool_call_id="p-1",
            name="procurement_capture_requirement",
            content=(
                '{"ok":true,"stage":"requirement_captured","request_id":"abcdef0123456789abcdef0123456789"'
                "\n...[artifact:0123456789abcdef sha=0123456789ab]"
            ),
        ),
    )
    assert current_stage_index(request, [invocation]) == 1


# ---------------------------------------------------------------- H2
def test_validate_bind_refuses_non_loopback_without_flag() -> None:
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0")
    validate_bind("127.0.0.1", False)
    validate_bind("localhost", False)
    validate_bind("0.0.0.0", True)
    with pytest.raises(SystemExit):
        validate_bind("0.0.0.0", False)
    with pytest.raises(SystemExit):
        validate_bind("192.168.1.10", False)


# ---------------------------------------------------------------- M4
@pytest.mark.asyncio
async def test_api_key_encrypted_at_rest(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    secret = "sk-test-secret-1234"
    try:
        await agent.configure_model(
            provider="openai",
            model="gpt-test-fake",
            base_url="https://fake.example/v1",
            api_key=secret,
            api_mode="auto",
            reasoning_effort=None,
            input_price_per_million_usd=0,
            output_price_per_million_usd=0,
            cached_input_price_per_million_usd=0,
            max_cost_usd=None,
        )
        raw = agent.model_config_path.read_text(encoding="utf-8")
        assert secret not in raw
        assert "enc:v1:" in raw
        key_file = agent._config_key_path()
        assert key_file.exists()
        restored = agent._read_persisted_model_config()
        assert restored is not None
        assert restored["api_key"] == secret
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- helpers
def _truth_cases(count: int = 2) -> list[tuple[str, bytes]]:
    truth = load_frozen_truth()
    cases = truth["quotes"][:count]
    return [(case["filename"], build_case_document(case)) for case in cases]


async def _run_conversation_to_require_human(
    agent: ProcurementAgent, cases: list[tuple[str, bytes]]
) -> tuple[str, str]:
    accepted = await agent.start(
        message=(
            "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
            "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。"
        ),
        attachments=cases,
    )
    run_id = accepted["run_id"]
    request_id = accepted["purchase_request_id"]
    await agent._tasks[run_id]
    assert agent.harness.get_run(run_id)["status"] == "require_human"
    return run_id, request_id


async def _approve(agent: ProcurementAgent, service: ProcurementService, request_id: str) -> None:
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


# ---------------------------------------------------------------- H1 integration
@pytest.mark.asyncio
async def test_conversation_full_set_approval_after_truncated_capture(data_dir) -> None:  # type: ignore[no-untyped-def]
    """A capture result exceeding max_inline_tool_result_bytes is truncated with
    an artifact pointer; the deterministic stage machine must still advance and
    the buyer approval must succeed (regression for the 409 stage lock)."""
    from dataclasses import replace

    from agentharness.contracts import BudgetConfig

    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    profile = replace(
        _fake_run_profile(),
        budget=BudgetConfig(
            max_steps=20,
            max_wall_time_s=120,
            max_tokens=20_000,
            max_context_tokens=16_000,
            max_output_length=20_000,
            max_tool_calls=30,
            max_tool_calls_per_turn=1,
            max_inline_tool_result_bytes=256,
        ),
    )
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=profile
    )
    try:
        run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        invocations = harness.list_tool_invocations(run_id)
        capture = next(
            item
            for item in invocations
            if item.tool_name == "procurement_capture_requirement"
        )
        assert capture.result is not None
        assert "...[artifact:" in capture.result.content  # truncation did happen
        await _approve(agent, service, request_id)
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- M5
@pytest.mark.asyncio
async def test_correct_field_rechecks_decision_inside_transaction(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    try:
        _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        await _approve(agent, service, request_id)
        quote_id = service.get_request(request_id)["quotes"][0]["id"]
        # Bypass the pre-check on purpose to prove the in-transaction re-check
        # alone blocks post-approval edits.
        service._editable_request = lambda rid: service.repo.get_request(rid)  # type: ignore[method-assign]
        with pytest.raises(ProcurementError, match="已形成审批结论"):
            service.correct_field(
                request_id, quote_id, field="shipping_fee", value="1", actor="采购员"
            )
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- M6
@pytest.mark.asyncio
async def test_staged_attachments_consumed_after_parse(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    try:
        _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        detail = service.get_request(request_id)
        assert detail["quote_count"] == 2
        assert detail["attachments"] == []
        assert service.staged_attachment_count(request_id) == 0
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- M7
@pytest.mark.asyncio
async def test_snapshot_guard_regenerates_missing_snapshot(data_dir) -> None:  # type: ignore[no-untyped-def]
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness, service, approval_broker=broker, run_profile=_fake_run_profile()
    )
    try:
        run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
        quote_id = service.get_request(request_id)["quotes"][0]["id"]
        # Invalidate the snapshot (simulates a correction after the run ended).
        service.correct_field(
            request_id, quote_id, field="shipping_fee", value="100", actor="采购员"
        )
        detail = service.get_request(request_id)
        assert detail["status"] == "ready"
        assert detail["comparison"] is None
        await agent._ensure_snapshot_after_run(request_id, run_id)
        regenerated = service.get_request(request_id)
        assert regenerated["comparison"] is not None
        assert regenerated["comparison"]["id"]
    finally:
        await agent.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- L2 (API semantics)
@pytest.mark.asyncio
async def test_quote_import_after_approval_returns_409(data_dir, workspace) -> None:  # type: ignore[no-untyped-def]
    from httpx import ASGITransport, AsyncClient

    from agentharness.api.server import create_app

    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    app = create_app(harness=harness, workspace_roots=[workspace])
    agent = app.state.procurement_agent
    service = app.state.procurement_service
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            _run_id, request_id = await _run_conversation_to_require_human(agent, _truth_cases(2))
            await _approve(agent, service, request_id)
            filename, data = _truth_cases(3)[2]  # a file not already imported
            import base64 as _b64

            response = await client.post(
                f"/api/procurement/requests/{request_id}/quotes",
                json={
                    "filename": filename,
                    "content_base64": _b64.b64encode(data).decode("ascii"),
                },
            )
            assert response.status_code == 409
            assert "已形成审批结论" in response.json()["detail"]
    finally:
        await agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


# ---------------------------------------------------------------- L4
def test_supports_invoice_prefers_invoice_markers() -> None:
    assert coerce_field_value("supports_invoice", "发票：不含税可开专票") is True
    assert coerce_field_value("supports_invoice", "可开增值税专票") is True
    assert coerce_field_value("supports_invoice", "不可开票") is False
    assert coerce_field_value("supports_invoice", "不支持开票") is False
    # Generic negation still applies to tax parsing.
    assert coerce_field_value("tax_included", "不含税可开专票") is False
    assert coerce_field_value("tax_included", "含税") is True


# ---------------------------------------------------------------- L7
def test_like_search_escapes_wildcards(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            {
                "chunk_sha256": "1" * 64,
                "request_id": "req-aaaaaa",
                "quote_id": "quote-aaaaaa",
                "artifact_id": "art-aaaaaa",
                "artifact_sha256": "f" * 64,
                "request_reference": "RFQ-TEST-A",
                "supplier_name": "华东优包",
                "item_name": "快递袋",
                "category": "ecommerce_packaging",
                "specifications": {},
                "unit_price": "0.42",
                "currency": "CNY",
                "landed_unit_cost": "0.4521",
                "lead_days": 10,
                "moq": 5000,
                "decision": "approved",
                "decision_at": "2026-07-01T00:00:00+00:00",
                "content": "快递袋 250x350mm PE 白色 单色 0.42 CNY",
                "quality_flags": [],
                "created_at": "2026-07-01T00:00:00+00:00",
            }
        )
        storage.rag.upsert_chunk(
            {
                "chunk_sha256": "2" * 64,
                "request_id": "req-bbbbbb",
                "quote_id": "quote-bbbbbb",
                "artifact_id": "art-bbbbbb",
                "artifact_sha256": "f" * 64,
                "request_reference": "RFQ-TEST-B",
                "supplier_name": "特价%优惠",
                "item_name": "快递袋",
                "category": "ecommerce_packaging",
                "specifications": {},
                "unit_price": "0.40",
                "currency": "CNY",
                "landed_unit_cost": "0.4500",
                "lead_days": 10,
                "moq": 5000,
                "decision": "approved",
                "decision_at": "2026-07-02T00:00:00+00:00",
                "content": "特价%优惠 快递袋",
                "quality_flags": [],
                "created_at": "2026-07-02T00:00:00+00:00",
            }
        )
        hits = storage.rag._like_search("%", limit=10)
        assert len(hits) == 1
        assert hits[0]["chunk_sha256"] == "2" * 64
        hits_underscore = storage.rag._like_search("_", limit=10)
        assert hits_underscore == []
    finally:
        storage.close()








# ---------------------------------------------------------------- M8
def test_parse_quote_respects_time_budget() -> None:
    from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
    from agentharness.procurement.parsing import QuoteParseError, parse_quote

    truth = load_frozen_truth()
    case = truth["quotes"][0]
    data = build_case_document(case)
    with pytest.raises(QuoteParseError, match="时间预算"):
        parse_quote(case["filename"], data, time_budget_s=0.0)
    parsed = parse_quote(case["filename"], data)
    assert parsed["fields"]["unit_price"]["value"] == case["fields"]["unit_price"]
