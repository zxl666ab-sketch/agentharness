from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agentharness.api.internal_agent import (
    AgentCommandBody,
    InternalAgentCommands,
    _canonical_sha256,
    _default_model_config,
    _render_interaction_answer,
)
from agentharness.api.server import create_app
from agentharness.contracts import (
    Checkpoint,
    Message,
    MessageRole,
    RunStatus,
    ToolContentPart,
    ToolContext,
    ToolResult,
    new_id,
)
from agentharness.harness import Harness
from agentharness.procurement.requirements import extract_requirement
from tests.fake_provider import FakeModelAdapter


def test_default_model_config_uses_the_shared_openai_model(monkeypatch) -> None:
    monkeypatch.delenv("AGENTHARNESS_PROCUREMENT_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")

    assert _default_model_config()["model"] == "deepseek-v4-flash"

    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MODEL", "runtime-override")
    assert _default_model_config()["model"] == "runtime-override"


def test_interaction_size_answer_is_rendered_as_explicit_dimensions() -> None:
    rendered = _render_interaction_answer(
        {"quantity": 5000, "unit": "个", "size": "300×400 mm", "max_lead_days": 15}
    )

    assert "尺寸：300×400 mm" in rendered
    assert "宽度：300 mm" in rendered
    assert "长度：400 mm" in rendered


def test_interaction_answer_remains_unambiguous_when_requirement_is_reparsed() -> None:
    rendered = _render_interaction_answer(
        {"quantity": 20_000, "unit": "piece", "max_lead_days": 10}
    )

    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content=f"物料：热敏不干胶标签\n{rendered}",
            )
        ]
    )

    assert payload["quantity"] == "20000"
    assert payload["unit"] == "piece"
    assert payload["constraints"]["max_lead_days"] == 10


def test_latest_tool_payload_reads_spilled_durable_result(tmp_path, monkeypatch) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    payload = {"quotes": [{"supplier_name": "嘉兴胶粘", "details": "x" * 8_000}]}
    full_result = ToolResult(
        tool_call_id="call-1",
        name="procurement_parse_uploaded_quotes",
        content=json.dumps(payload, ensure_ascii=False),
    )
    meta = harness.storage.artifacts.put(
        full_result.model_dump_json(), content_type="application/json"
    )
    artifact_id = harness.storage.register_artifact(meta)
    inline_result = full_result.model_copy(
        update={
            "content": '{"quotes": [...truncated',
            "artifact_id": artifact_id,
            "parts": [
                ToolContentPart(
                    type="resource",
                    text="Full tool result stored as artifact",
                    mime_type="application/json",
                    artifact_id=artifact_id,
                )
            ],
        }
    )
    monkeypatch.setattr(
        harness.storage,
        "list_tool_invocations",
        lambda _run_id: [
            SimpleNamespace(
                tool_name="procurement_parse_uploaded_quotes",
                result=inline_result,
            )
        ],
    )

    assert commands._latest_tool_payload("a" * 32, inline_result.name) == payload
    harness.close()


def test_restart_restores_provider_for_persisted_procurement_run(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    data_dir = tmp_path / "runtime"
    first = Harness(data_dir=data_dir)
    session_id = first.storage.create_session(title="采购长任务")
    run_id = new_id()
    first.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.require_human,
        provider="procurement_openai",
        model="deepseek-v4-flash",
        approval="ask",
        allow_write=False,
    )
    first.close()

    restarted = Harness(data_dir=data_dir)
    commands = InternalAgentCommands(restarted)
    assert "procurement_openai" not in restarted.providers

    commands._ensure_run_provider(run_id)

    assert "procurement_openai" in restarted.providers
    restarted.close()


@pytest.mark.asyncio
async def test_capture_requirement_falls_back_when_model_shape_is_invalid(
    tmp_path,
) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    session_id = harness.storage.create_session(title="采购需求降级")
    run_id = new_id()
    source_message = (
        "采购3000个BOPP透明封箱胶带，宽48毫米、长100米、厚50微米，"
        "透明无印刷，送上海仓，12天内交付，需要开票。"
    )
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        metadata={
            "purchase_request_id": "a" * 32,
            "procurement_source_message": source_message,
        },
    )
    context = ToolContext(
        run_id=run_id,
        session_id=session_id,
        cwd=str(tmp_path),
        data_dir=str(harness.data_dir),
        allow_write=False,
    )

    result = await commands.procurement_tools.capture_requirement(
        context,
        {
            "requirement": {
                "itemName": "BOPP透明封箱胶带",
                "quantity": 3000,
                "specifications": {"widthMm": 48},
            }
        },
    )
    payload = json.loads(result.content)
    run = harness.storage.get_run(run_id)
    metadata = json.loads(str(run["metadata_json"]))

    assert payload["source"] == "deterministic_validation_fallback"
    assert payload["requirement"]["quantity"] == "3000"
    assert metadata["procurement_model_requirement_error"]
    harness.close()


def test_extracts_spaced_chinese_packaging_requirement() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content=(
                    "请采购白色 PE 快递袋，250×350 mm，60 微米，单色印刷，"
                    "数量 10,000 个，最长交期 15 天，需要开票，送货到华东仓。"
                ),
            )
        ]
    )

    assert payload["quantity"] == 10_000
    assert payload["specifications"]["width_mm"] == "250"
    assert payload["specifications"]["length_mm"] == "350"
    assert payload["constraints"]["destination"] == "华东仓"


def test_extracts_dynamic_non_packaging_requirement() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content="请采购透明封箱胶带，数量 12.5 卷，长度 100 米，材质 BOPP，15 天内交付。",
            )
        ]
    )

    assert payload["schema_version"] == 2
    assert payload["item_name"] == "透明封箱胶带"
    assert payload["quantity"] == "12.5"
    assert payload["unit"] == "卷"
    assert payload["specifications"]["length"]["value"] == "100"
    assert payload["specifications"]["material"]["value"] == "BOPP"


def test_extracts_lead_time_not_exceeding_limit() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content="采购 5000 个纸箱，交期不超过 20 天。",
            )
        ]
    )

    assert payload["constraints"]["max_lead_days"] == 20


def test_extracts_numbered_label_procurement_requirement() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content=(
                    "请创建“华东仓热敏不干胶标签采购”任务。\n"
                    "1. 物料：热敏不干胶标签\n"
                    "2. 采购数量：20,000 个\n"
                    "3. 规格：宽 100 mm × 长 150 mm\n"
                    "4. 厚度：80 微米\n"
                    "5. 材质：铜版纸\n"
                    "6. 颜色：白色\n"
                    "7. 印刷：1 色\n"
                    "8. 目标仓库：华东仓\n"
                    "9. 币种：人民币 CNY\n"
                    "10. 最长交期：10 天\n"
                    "11. 必须支持开具增值税发票\n"
                    "12. 尺寸允许偏差：±1 mm\n"
                    "13. 厚度允许偏差：±5 微米\n"
                    "14. 目标落地单价不超过 0.20 元/个"
                ),
            )
        ]
    )

    assert payload["title"] == "华东仓热敏不干胶标签采购"
    assert payload["item_name"] == "热敏不干胶标签"
    assert payload["quantity"] == "20000"
    assert payload["unit"] == "个"
    assert payload["specifications"]["width"]["value"] == "100"
    assert payload["specifications"]["length"]["value"] == "150"
    assert payload["specifications"]["thickness"]["value"] == "80"
    assert payload["specifications"]["material"]["value"] == "铜版纸"
    assert payload["specifications"]["color"]["value"] == "白色"
    assert payload["specifications"]["print_colors"]["value"] == "1"
    assert payload["constraints"]["destination"] == "华东仓"
    assert payload["constraints"]["max_lead_days"] == 10
    assert payload["constraints"]["invoice_required"] is True
    assert payload["constraints"]["size_tolerance_mm"] == "1"
    assert payload["constraints"]["thickness_tolerance_um"] == "5"
    assert payload["constraints"]["max_landed_unit_cost"] == "0.20"


@pytest.mark.asyncio
async def test_start_conversation_falls_back_after_model_tool_protocol_error(
    tmp_path,
) -> None:
    adapter = FakeModelAdapter(
        script=[
            {
                "kind": "error",
                "error": "tool call arguments are invalid JSON",
                "error_kind": "provider_protocol",
            }
        ]
    )
    harness = Harness(data_dir=tmp_path / "runtime", providers={"openai": adapter})
    (harness.data_dir / "procurement-model-config.json").write_text(
        json.dumps({"provider": "openai", "model": "gpt-test", "api_key": "test-key"}),
        encoding="utf-8",
    )
    commands = InternalAgentCommands(harness)
    commands.procurement_tools._parse_attachment = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "artifact_id": "jb" + "a" * 30,
            "supplier_name": "华南标签",
            "status": "ready",
            "parser_version": "test",
            "processing_ms": "1",
            "extracted": {"fields": {}, "review_fields": []},
        }
    )
    payload = {
        "message": "物料：热敏不干胶标签\n采购数量：20,000 个\n最长交期：10天",
        "attachments": [{"artifact_id": "jb" + "a" * 30, "filename": "报价.xlsx"}],
    }
    body = AgentCommandBody(
        operation_id="11111111-1111-1111-1111-111111111111",
        operation_type="start_conversation",
        aggregate_id="a" * 32,
        generation=1,
        expected_task_version=0,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )

    result = await commands._start_conversation(body)

    assert result["requirement"]["item_name"] == "热敏不干胶标签"
    runs = harness.storage.list_runs(limit=10)
    assert {run["status"] for run in runs} >= {
        RunStatus.failed.value,
        RunStatus.require_human.value,
    }
    fallback = next(run for run in runs if run["status"] == RunStatus.require_human.value)
    assert fallback["provider"] == "procurement_internal"
    await harness.aclose()


@pytest.mark.asyncio
async def test_start_conversation_uses_configured_model_and_prefills_usd_rate(tmp_path) -> None:
    model_requirement = {
        "schema_version": 2,
        "title": "苏州工厂出口瓦楞纸箱采购",
        "category": "general",
        "item_name": "五层瓦楞纸箱",
        "quantity": "5000",
        "unit": "个",
        "specifications": {
            "width": {
                "label": "宽度",
                "type": "number",
                "value": "400",
                "unit": "mm",
                "match": "exact",
                "priority": "hard",
            },
            "printing": {
                "label": "印刷",
                "type": "text",
                "value": "单色印刷",
                "match": "exact",
                "priority": "hard",
            },
            "layer_count": {
                "label": "瓦楞纸层数",
                "type": "number",
                "value": "5",
                "unit": "层",
                "match": "exact",
                "priority": "hard",
            },
            "size_tolerance": {
                "label": "尺寸容差",
                "type": "number",
                "value": "3",
                "unit": "mm",
                "match": "max",
                "priority": "hard",
            },
            "thickness_tolerance": {
                "label": "厚度容差",
                "type": "number",
                "value": "500",
                "unit": "μm",
                "match": "max",
                "priority": "hard",
            },
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 20,
            "invoice_required": True,
            "destination": "苏州工厂",
            "size_tolerance_mm": "3",
            "thickness_tolerance_um": "500",
        },
    }
    adapter = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [
                    {
                        "name": "procurement_capture_requirement",
                        "arguments": {"requirement": model_requirement},
                    },
                    {"name": "procurement_parse_uploaded_quotes"},
                ],
            },
            {
                "kind": "tools",
                "tools": [{"name": "procurement_parse_uploaded_quotes"}],
            },
            {
                "kind": "tools",
                "tools": [{"name": "procurement_request_review"}],
            },
        ]
    )
    harness = Harness(data_dir=tmp_path / "runtime", providers={"openai": adapter})
    (harness.data_dir / "procurement-model-config.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-test",
                "api_key": "test-key",
                "api_mode": "chat",
            }
        ),
        encoding="utf-8",
    )
    commands = InternalAgentCommands(harness)
    commands.procurement_tools._parse_attachment = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "artifact_id": "jb" + "a" * 30,
            "supplier_name": "美元供应商",
            "status": "ready",
            "parser_version": "test",
            "processing_ms": "1",
            "extracted": {
                "fields": {"currency": {"value": "USD"}},
                "review_fields": [],
            },
        }
    )
    payload = {
        "message": "采购 5000 个五层瓦楞纸箱，交期不超过 20 天。",
        "attachments": [
            {"artifact_id": "jb" + "a" * 30, "filename": "美元报价.pdf"}
        ],
    }
    body = AgentCommandBody(
        operation_id="11111111-1111-1111-1111-111111111111",
        operation_type="start_conversation",
        aggregate_id="a" * 32,
        generation=1,
        expected_task_version=0,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )

    result = await commands._start_conversation(body)

    assert adapter.calls
    assert result["requirement"]["constraints"]["max_lead_days"] == 20
    assert result["requirement"]["constraints"]["fx_rates"]["USD"] == "7.2"
    specifications = result["requirement"]["specifications"]
    assert specifications["print_colors"]["value"] == "1"
    assert specifications["layers"]["value"] == "5"
    assert "size_tolerance" not in specifications
    assert "thickness_tolerance" not in specifications
    run = harness.storage.get_run(result["run_id"])
    assert run is not None
    assert run["provider"] == "openai"
    assert run["model"] == "gpt-test"
    assert {tool.name for tool in adapter.calls[0].tools} == {
        "procurement_capture_requirement",
        "procurement_parse_uploaded_quotes",
        "procurement_request_review",
        "procurement_request_comparison",
        "procurement_record_decision_evidence",
    }
    assert run["status"] == RunStatus.require_human.value
    assert adapter.calls[0].parallel_tool_calls is False
    assert [
        item.tool_name for item in harness.storage.list_tool_invocations(result["run_id"])
    ] == [
        "procurement_capture_requirement",
        "procurement_parse_uploaded_quotes",
        "procurement_request_review",
    ]
    await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_agent_resumes_the_same_run_until_formal_decision(tmp_path) -> None:
    requirement = {
        "schema_version": 2,
        "title": "快递袋采购",
        "category": "general",
        "item_name": "PE 快递袋",
        "quantity": "5000",
        "unit": "个",
        "specifications": {
            "color": {
                "label": "颜色",
                "type": "text",
                "value": "白色",
                "match": "exact",
                "priority": "hard",
            }
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 15,
            "invoice_required": True,
        },
    }
    adapter = FakeModelAdapter(
        script=[
            {"kind": "tools", "tools": [{"name": "procurement_capture_requirement", "arguments": {"requirement": requirement}}]},
            {"kind": "tools", "tools": [{"name": "procurement_parse_uploaded_quotes"}]},
            {"kind": "tools", "tools": [{"name": "procurement_request_review"}]},
            {"kind": "tools", "tools": [{"name": "procurement_request_comparison"}]},
            {"kind": "tools", "tools": [{"name": "procurement_record_decision_evidence"}]},
        ]
    )
    harness = Harness(data_dir=tmp_path / "runtime", providers={"openai": adapter})
    (harness.data_dir / "procurement-model-config.json").write_text(
        json.dumps({"provider": "openai", "model": "gpt-test", "api_key": "test-key"}),
        encoding="utf-8",
    )
    commands = InternalAgentCommands(harness)
    task_id = "a" * 32
    start_payload = {"message": "采购 5000 个白色 PE 快递袋，15 天内到货。", "attachments": []}
    start_body = AgentCommandBody(
        operation_id="11111111-1111-1111-1111-111111111111",
        operation_type="start_conversation",
        aggregate_id=task_id,
        generation=1,
        expected_task_version=0,
        payload_sha256=_canonical_sha256(start_payload),
        payload=start_payload,
    )

    started = await commands._start_conversation(start_body)
    run_id = started["run_id"]
    assert harness.storage.get_run(run_id)["status"] == RunStatus.require_human.value  # type: ignore[index]

    async def reviewed_context(_path: str) -> dict[str, object]:
        return {
            "analysis_run_id": run_id,
            "requirement_confirmed": True,
            "unresolved_field_count": 0,
            "task_version": 4,
            "pending_decisions": [],
        }

    commands._java_json = reviewed_context  # type: ignore[method-assign]
    analyze_payload = {"task_id": task_id}
    analyze_body = AgentCommandBody(
        operation_id="22222222-2222-2222-2222-222222222222",
        operation_type="analyze",
        aggregate_id=task_id,
        generation=1,
        expected_task_version=4,
        payload_sha256=_canonical_sha256(analyze_payload),
        payload=analyze_payload,
    )
    analyzed = await commands._analyze(analyze_body)
    assert analyzed == {"run_id": run_id, "status": "waiting_approval"}

    binding = {
        "pending_decision_id": "d" * 32,
        "run_id": run_id,
        "tool_name": "procurement_approve_supplier",
        "task_version": 4,
        "snapshot_id": "e" * 32,
        "input_sha256": "f" * 64,
        "business_decision": "approved",
        "quote_id": "1" * 32,
        "note_hash": "2" * 64,
    }

    async def pending_context(_path: str) -> dict[str, object]:
        return {
            "analysis_run_id": run_id,
            "requirement_confirmed": True,
            "unresolved_field_count": 0,
            "task_version": 4,
            "pending_decisions": [{**binding, "operation_id": "33333333-3333-3333-3333-333333333333", "status": "pending"}],
        }

    commands._java_json = pending_context  # type: ignore[method-assign]
    approve_payload = {**binding, "note": "采购员已正式确认"}
    approve_body = AgentCommandBody(
        operation_id="33333333-3333-3333-3333-333333333333",
        operation_type="approve_decision",
        aggregate_id=task_id,
        generation=1,
        expected_task_version=4,
        payload_sha256=_canonical_sha256(approve_payload),
        payload=approve_payload,
    )
    approved = await commands._approve(approve_body)

    assert approved["run_id"] == run_id
    assert approved["approval"]["confirmation_source"] == "java_control_plane"
    assert harness.storage.get_run(run_id)["status"] == RunStatus.completed.value  # type: ignore[index]
    assert [item.tool_name for item in harness.storage.list_tool_invocations(run_id)] == [
        "procurement_capture_requirement",
        "procurement_parse_uploaded_quotes",
        "procurement_request_review",
        "procurement_request_comparison",
        "procurement_record_decision_evidence",
    ]
    approvals = harness.storage.list_approvals(run_id)
    assert len(approvals) == 1
    assert approvals[0]["id"] == approved["approval"]["id"]
    assert approvals[0]["tool_name"] == "procurement_approve_supplier"
    assert approvals[0]["effect"] == "external_write"
    assert approvals[0]["requires_confirmation"] is True
    assert approvals[0]["decision"] == "allow_once"
    assert approvals[0]["status"] == "resolved"
    assert approvals[0]["arguments_sha256"] == _canonical_sha256(binding)
    await harness.aclose()


def test_extracts_quantity_when_item_name_precedes_common_unit() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content=(
                    "采购 BOPP 透明封箱胶带 3000 卷，宽 48 mm，长度 100 m，"
                    "厚度 50 µm，12 天内交付，要求开票"
                ),
            )
        ]
    )

    assert payload["quantity"] == "3000"
    assert payload["unit"] == "卷"
    assert payload["item_name"] == "BOPP 透明封箱胶带"
    assert payload["specifications"]["width"]["value"] == "48"
    assert payload["specifications"]["length"]["value"] == "100"
    assert payload["specifications"]["length"]["unit"] == "m"
    assert payload["specifications"]["material"]["value"] == "BOPP"
    assert payload["specifications"]["color"]["value"] == "透明"


def test_extracts_packaging_size_when_each_dimension_has_a_unit() -> None:
    payload = extract_requirement(
        [
            Message(
                role=MessageRole.user,
                content="采购 PE 快递袋，250mm x 350mm，厚度 60 微米，数量 10000 个。",
            )
        ]
    )

    assert payload["specifications"]["width_mm"] == "250"
    assert payload["specifications"]["length_mm"] == "350"


@pytest.mark.asyncio
async def test_internal_operation_is_durable_and_payload_bound(tmp_path) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    commands._dispatch = AsyncMock(return_value={"run_id": "a" * 32})  # type: ignore[method-assign]
    payload = {"task_id": "b" * 32}
    body = AgentCommandBody(
        operation_id="12345678-1234-1234-1234-123456789abc",
        operation_type="create_structured",
        aggregate_id="b" * 32,
        generation=1,
        expected_task_version=0,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )

    first = await commands.execute(body)
    second = await commands.execute(body)

    assert first == second
    assert first["status"] == "completed"
    commands._dispatch.assert_awaited_once()  # type: ignore[attr-defined]

    conflict_payload = {"task_id": "c" * 32}
    conflict = body.model_copy(
        update={
            "payload": conflict_payload,
            "payload_sha256": _canonical_sha256(conflict_payload),
        }
    )
    with pytest.raises(Exception, match="different payload"):
        await commands.execute(conflict)
    await harness.aclose()


@pytest.mark.asyncio
async def test_failed_human_interaction_operation_can_reopen_without_duplicate_dispatch(
    tmp_path,
) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    payload = {"interaction_id": "i1"}
    body = AgentCommandBody(
        operation_id="22345678-1234-1234-1234-123456789abc",
        operation_type="human_interaction_answer",
        aggregate_id="b" * 32,
        generation=1,
        expected_task_version=0,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )
    commands._dispatch = AsyncMock(side_effect=[RuntimeError("temporary"), {"run_id": "a" * 32}])  # type: ignore[method-assign]

    first = await commands.execute(body)
    second = await commands.execute(body)
    replay = await commands.execute(body)

    assert first["status"] == "failed"
    assert second["status"] == "completed"
    assert replay == second
    assert commands._dispatch.await_count == 2  # type: ignore[attr-defined]
    await harness.aclose()


@pytest.mark.asyncio
async def test_human_answer_repairs_checkpoint_without_duplicate_user_message(
    tmp_path, monkeypatch
) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    session_id = harness.storage.create_session(title="人工回答故障恢复")
    run_id = new_id()
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.require_human,
        provider="procurement_internal",
        model="deterministic-procurement",
        approval="ask",
        allow_write=False,
    )
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="model_turn",
            step=1,
            messages=[],
            status=RunStatus.require_human,
        )
    )
    answer = {
        "quantity": 5000,
        "unit": "个",
        "size": "300×400 mm",
        "max_lead_days": 15,
    }
    answer_text = _render_interaction_answer(answer)
    resume_input = (
        "采购员已回答当前问题。以下回答来自 Java 持久化交互，并已通过 Schema 校验：\n"
        f"{answer_text}\n继续当前采购资料解析；仍缺关键字段时再次结构化提问。"
    )
    persisted_answer = Message(role=MessageRole.user, content=resume_input)
    harness.storage.save_message(run_id, session_id, persisted_answer, seq=0)

    interaction_id = "interaction-1"
    checkpoint_id = "checkpoint-1"
    commands._java_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "generation": 2,
            "task_version": 4,
            "analysis_run_id": run_id,
            "source_message": "帮我采购一批白色快递袋",
            "attachments": [],
            "authorized_artifacts": [],
            "interactions": [
                {
                    "interaction_id": interaction_id,
                    "status": "ANSWERED",
                    "generation": 2,
                    "run_id": run_id,
                    "checkpoint_id": checkpoint_id,
                    "answer": answer,
                    "artifact_ids": [],
                }
            ],
        }
    )

    async def resume_from_repaired_checkpoint(
        resumed_run_id: str, input: str | None = None
    ):  # type: ignore[no-untyped-def]
        repaired = harness.storage.load_checkpoint(resumed_run_id)
        assert input is None
        assert repaired is not None
        assert [message.id for message in repaired.messages] == [persisted_answer.id]
        run = harness.storage.get_run(resumed_run_id)
        assert run is not None
        return commands._run_result(run)

    monkeypatch.setattr(harness, "resume", AsyncMock(side_effect=resume_from_repaired_checkpoint))
    monkeypatch.setattr(
        commands,
        "_latest_tool_payload",
        lambda _run_id, _tool_name: {
            "interaction": {"question": "请继续补充开票要求"}
        },
    )
    payload = {
        "interaction_id": interaction_id,
        "run_id": run_id,
        "checkpoint_id": checkpoint_id,
        "answer": answer,
    }
    body = AgentCommandBody(
        operation_id="32345678-1234-1234-1234-123456789abc",
        operation_type="human_interaction_answer",
        aggregate_id="b" * 32,
        generation=2,
        expected_task_version=4,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )

    result = await commands._answer_interaction(body)

    assert result["interaction_id"] == interaction_id
    assert result["interaction"]["question"] == "请继续补充开票要求"
    assert len(harness.storage.get_messages(run_id)) == 1
    assert harness.storage.get_messages(run_id)[0].id == persisted_answer.id
    harness.resume.assert_awaited_once_with(run_id, input=None)  # type: ignore[attr-defined]
    await harness.aclose()


@pytest.mark.asyncio
async def test_approval_only_allows_exact_java_binding(tmp_path) -> None:
    harness = Harness(data_dir=tmp_path / "runtime")
    commands = InternalAgentCommands(harness)
    session_id = harness.storage.create_session(title="采购测试")
    run_id = new_id()
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.waiting_approval,
        provider="procurement_internal",
        model="deterministic-parser",
        approval="ask",
        allow_write=False,
        metadata={"purchase_request_id": "a" * 32},
    )
    binding = {
        "pending_decision_id": "d" * 32,
        "run_id": run_id,
        "tool_name": "procurement_approve_supplier",
        "task_version": 4,
        "snapshot_id": "e" * 32,
        "input_sha256": "f" * 64,
        "business_decision": "approved",
        "quote_id": "1" * 32,
        "note_hash": "2" * 64,
    }
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="waiting_approval",
            step=0,
            messages=[],
            status=RunStatus.waiting_approval,
        )
    )
    operation_id = "87654321-4321-4321-4321-cba987654321"
    commands._java_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "task_version": 4,
            "analysis_run_id": run_id,
            "pending_decisions": [{**binding, "operation_id": operation_id, "status": "pending"}],
        }
    )
    payload = {**binding, "note": "已核对"}
    body = AgentCommandBody(
        operation_id=operation_id,
        operation_type="approve_decision",
        aggregate_id="a" * 32,
        generation=1,
        expected_task_version=4,
        payload_sha256=_canonical_sha256(payload),
        payload=payload,
    )

    result = await commands._approve(body)
    checkpoint = harness.storage.load_checkpoint(run_id)

    approvals = harness.storage.list_approvals(run_id)
    assert len(approvals) == 1
    assert approvals[0]["id"] == result["approval"]["id"]
    assert approvals[0]["tool_call_id"] == binding["pending_decision_id"]
    assert approvals[0]["invocation_id"] == operation_id
    assert approvals[0]["requires_confirmation"] is True
    assert approvals[0]["decision"] == "allow_once"
    assert approvals[0]["status"] == "resolved"
    assert approvals[0]["arguments_sha256"] == _canonical_sha256(binding)
    assert result["approval"]["confirmation_source"] == "java_control_plane"
    assert result["approval"]["decision"] == "formal_java_confirmation"
    assert result["approval"]["arguments_sha256"] == _canonical_sha256(binding)
    assert result["approval"]["pending_decision_id"] == binding["pending_decision_id"]
    assert checkpoint is not None
    assert checkpoint.status == RunStatus.completed

    stale = body.model_copy(
        update={"payload": {**payload, "snapshot_id": "0" * 32}}
    )
    with pytest.raises(ValueError, match="does not match"):
        await commands._approve(stale)

    commands._java_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "task_version": 5,
            "analysis_run_id": run_id,
            "pending_decisions": [
                {**binding, "operation_id": operation_id, "status": "stale"}
            ],
        }
    )
    with pytest.raises(ValueError, match="stale_approval"):
        await commands._approve(body)
    await harness.aclose()


@pytest.mark.asyncio
async def test_internal_only_requires_token_except_health(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    app = create_app(data_dir=tmp_path / "runtime", internal_only=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/sessions")).status_code == 401
        authorized = await client.get(
            "/api/sessions", headers={"X-Agent-Internal-Token": "test-token"}
        )
        assert authorized.status_code == 200


@pytest.mark.asyncio
async def test_internal_token_allows_runtime_reads_when_remote_execution_is_disabled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    app = create_app(
        data_dir=tmp_path / "runtime",
        internal_only=True,
        execution_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        denied = await client.get("/api/sessions")
        authorized = await client.get(
            "/api/sessions", headers={"X-Agent-Internal-Token": "test-token"}
        )

    assert denied.status_code == 401
    assert authorized.status_code == 200
