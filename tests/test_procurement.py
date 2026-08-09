from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter
from scripts import evaluate_procurement as evaluation_script
from scripts import generate_procurement_demo as demo_script
from scripts.evaluate_procurement import _controlled_experiment, _load_manifest

from agentharness.api.server import create_app
from agentharness.contracts import (
    MessageRole,
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    ToolInvocationStatus,
)
from agentharness.engine.tool_execution import arguments_sha256, enabled_tool_names
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    PROCUREMENT_PROVIDER,
    PROCUREMENT_TOOL_NAMES,
    ProcurementAgent,
    ProcurementFakeProvider,
    _fake_run_profile,
    procurement_run_profile_from_env,
)
from agentharness.procurement.costing import CostingError, compare_quotes
from agentharness.procurement.evaluation import (
    FROZEN_DATASET_NAME,
    FROZEN_TRUTH_SHA256,
    MIN_FROZEN_CASES,
    MIN_FROZEN_LAYOUTS,
    build_case_document,
    evaluate_frozen_cases,
    load_frozen_truth,
    recompute_approach_metrics,
    recompute_human_trial_metrics,
)
from agentharness.procurement.parsing import (
    MAX_FILE_BYTES,
    QuoteParseError,
    fields_requiring_review,
    parse_quote,
)
from agentharness.procurement.service import (
    MAX_QUOTES_PER_REQUEST,
    ProcurementError,
    ProcurementService,
)
from tests.fake_provider import FakeModelAdapter


def test_procurement_profile_defaults_to_openai_and_is_priced_and_budgeted(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_keys = (
        "AGENTHARNESS_PROCUREMENT_PROVIDER",
        "AGENTHARNESS_PROCUREMENT_MODEL",
        "AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD",
        "AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD",
        "AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD",
        "AGENTHARNESS_PROCUREMENT_MAX_COST_USD",
        "AGENTHARNESS_PROCUREMENT_MAX_TOKENS",
        "AGENTHARNESS_PROCUREMENT_MAX_STEPS",
        "AGENTHARNESS_PROCUREMENT_MAX_WALL_TIME_S",
    )
    for key in config_keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "configured-model-must-not-opt-in")

    default_profile = procurement_run_profile_from_env()

    assert default_profile.provider == "openai"
    assert default_profile.model == "configured-model-must-not-opt-in"
    # Unconfigured live-provider rates are unknown (not silently zero), so cost
    # tracking reports "unknown" instead of a misleading $0.0000.
    assert default_profile.pricing.input_per_million_usd is None
    assert default_profile.budget.max_cost_usd is None

    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MODEL", "live-test-model")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD", "0.5")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD", "1.5")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD", "0.1")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MAX_COST_USD", "0.25")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MAX_TOKENS", "24000")
    profile = procurement_run_profile_from_env()

    harness = Harness(data_dir=data_dir, providers={"openai": FakeModelAdapter()})
    agent = ProcurementAgent(
        harness,
        ProcurementService(harness),
        run_profile=profile,
    )
    try:
        request = agent._run_request(
            request_id="request-live",
            session_id="session-live",
            message="分析报价",
            source="live_acceptance",
        )
    finally:
        harness.close()

    assert request.provider == "openai"
    assert request.model == "live-test-model"
    assert request.tools == list(PROCUREMENT_TOOL_NAMES)
    assert request.budget.max_tokens == 24_000
    assert request.budget.max_steps == 20
    assert request.budget.max_cost_usd == pytest.approx(0.25)
    assert request.pricing.input_per_million_usd == pytest.approx(0.5)
    assert request.pricing.output_per_million_usd == pytest.approx(1.5)
    assert request.metadata["procurement_provider_mode"] == "live"


def test_procurement_prompt_and_schema_pin_fx_rate_direction(data_dir: Path) -> None:
    """fx_rates must define 1 unit of the quoted currency -> base-currency units,
    so real models do not invert USD/CNY when the base currency is not CNY."""
    harness = Harness(data_dir=data_dir)
    try:
        agent = ProcurementAgent(
            harness,
            ProcurementService(harness),
            run_profile=_fake_run_profile(),
        )
        request = agent._run_request(
            request_id="request-fx",
            session_id="session-fx",
            message="分析报价",
            source="procurement_structured",
        )
        system = request.system or ""
        assert "1 单位该币种可兑换的本位币数量" in system
        assert "USD: 7.2" in system
        assert "CNY: 0.138888" in system
        assert "不要写反方向" in system
        schema = next(
            tool.spec.parameters
            for tool in agent.harness.tools.values()
            if tool.spec.name == "procurement_capture_requirement"
        )
        fx_description = schema["properties"]["constraints"]["properties"]["fx_rates"]["description"]
        assert "Never invert the direction" in fx_description
        assert "USD/CNY=7.2" in fx_description
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_procurement_model_config_redacts_api_key_and_applies_to_runs(
    data_dir: Path,
) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, execution_enabled=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            initial = await client.get("/api/procurement/config")
            assert initial.status_code == 200
            assert initial.json()["provider"] == PROCUREMENT_PROVIDER

            updated = await client.post(
                "/api/procurement/config",
                json={
                    "provider": "openai",
                    "model": "runtime-config-model",
                    "base_url": "https://gateway.example/v1",
                    "api_key": "sk-runtime-secret-1234",
                    "api_mode": "chat",
                    "reasoning_effort": "high",
                    "input_price_per_million_usd": 0.5,
                    "output_price_per_million_usd": 1.5,
                    "cached_input_price_per_million_usd": 0.1,
                    "max_cost_usd": 0.25,
                },
            )
            assert updated.status_code == 200
            payload = updated.json()
            assert payload["provider"] == "openai"
            assert payload["model"] == "runtime-config-model"
            assert payload["api_key_configured"] is True
            assert payload["api_key_preview"].endswith("1234")
            assert "sk-runtime-secret-1234" not in updated.text

            config = await client.get("/api/procurement/config")
            assert config.json() == payload
            assert harness.providers["openai"].api_key == "sk-runtime-secret-1234"
            run_request = app.state.procurement_agent._run_request(
                request_id="a" * 32,
                session_id="session-runtime-config",
                message="使用已保存的模型配置",
                source="runtime_config_test",
            )
            assert run_request.provider == "openai"
            assert run_request.model == "runtime-config-model"
            assert run_request.reasoning_effort == "high"
            assert run_request.budget.max_cost_usd == pytest.approx(0.25)
    finally:
        await app.state.procurement_agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_model_config_persists_and_restores_on_restart(
    data_dir: Path,
) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, execution_enabled=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            saved = await client.post(
                "/api/procurement/config",
                json={
                    "provider": "openai",
                    "model": "persisted-model",
                    "base_url": "https://persisted.example/v1",
                    "api_key": "sk-persisted-1234",
                    "api_mode": "chat",
                    "reasoning_effort": "medium",
                    "input_price_per_million_usd": 0.2,
                    "output_price_per_million_usd": 0.8,
                    "cached_input_price_per_million_usd": 0.1,
                    "max_cost_usd": 0.5,
                },
            )
            assert saved.status_code == 200
    finally:
        await app.state.procurement_agent.aclose()
        await harness.aclose()

    persisted_path = data_dir / "procurement-model-config.json"
    assert persisted_path.is_file()
    assert json.loads(persisted_path.read_text(encoding="utf-8"))["model"] == "persisted-model"

    restored = Harness(data_dir=data_dir)
    restored_app = create_app(harness=restored, execution_enabled=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=restored_app), base_url="http://test"
        ) as client:
            config = await client.get("/api/procurement/config")
            assert config.status_code == 200
            payload = config.json()
            assert payload["provider"] == "openai"
            assert payload["model"] == "persisted-model"
            assert payload["api_key_preview"].endswith("1234")
            assert restored.providers["openai"].api_key == "sk-persisted-1234"
    finally:
        await restored_app.state.procurement_agent.aclose()
        await restored.aclose()


def test_procurement_env_is_default_and_wins_over_persisted_ui_fields(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # .env is the single source of truth: a stale UI-saved config file must
    # never override OPENAI_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY.
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MODEL", "env-default-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-default")
    (data_dir / "procurement-model-config.json").write_text(
        json.dumps({"source": "ui", "model": "ui-override-model"}), encoding="utf-8"
    )

    harness = Harness(data_dir=data_dir)
    agent = ProcurementAgent(harness, ProcurementService(harness))
    try:
        assert agent.run_profile.model == "env-default-model"
        assert agent.run_profile.base_url == "https://env.example/v1"
        assert agent.run_profile.api_key == "sk-env-default"
    finally:
        asyncio.run(agent.aclose())
        harness.close()


def test_procurement_legacy_config_does_not_mask_environment_defaults(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENTHARNESS_PROCUREMENT_MODEL", "env-default-model")
    (data_dir / "procurement-model-config.json").write_text(
        json.dumps({"provider": "procurement_fake", "model": "procurement-fake-v1"}),
        encoding="utf-8",
    )
    harness = Harness(data_dir=data_dir)
    agent = ProcurementAgent(harness, ProcurementService(harness))
    try:
        assert agent.run_profile.provider == "openai"
        assert agent.run_profile.model == "env-default-model"
    finally:
        asyncio.run(agent.aclose())
        harness.close()


@pytest.mark.asyncio
async def test_procurement_live_profile_flows_through_api_with_fake_transport(
    data_dir: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_config = {
        "AGENTHARNESS_PROCUREMENT_PROVIDER": "openai",
        "AGENTHARNESS_PROCUREMENT_MODEL": "live-contract-test-model",
        "AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD": "0.5",
        "AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD": "1.5",
        "AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD": "0.1",
        "AGENTHARNESS_PROCUREMENT_MAX_COST_USD": "0.25",
        "AGENTHARNESS_PROCUREMENT_MAX_TOKENS": "24000",
    }
    for key, value in live_config.items():
        monkeypatch.setenv(key, value)

    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    class RecordingProvider(ProcurementFakeProvider):
        def __init__(self) -> None:
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            async for item in super().stream(request):
                yield item

    provider = RecordingProvider()
    harness = Harness(
        data_dir=data_dir,
        providers={"openai": provider},
    )
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/procurement/conversations",
            json={
                "message": (
                    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                    "厚度公差3微米。请比较附件报价并推荐供应商。"
                ),
                "attachments": [_upload(case) for case in cases],
            },
        )

        assert started.status_code == 202
        accepted = started.json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})

        run = harness.get_run(run_id)
        assert run is not None
        metadata = json.loads(run["metadata_json"])
        assert run["provider"] == "openai"
        assert run["model"] == "live-contract-test-model"
        assert metadata["procurement_provider_mode"] == "live"
        assert metadata["_agentharness_budget"]["max_cost_usd"] == pytest.approx(0.25)
        assert metadata["_agentharness_pricing"]["input_per_million_usd"] == pytest.approx(
            0.5
        )

        snapshot = detail["comparison"]
        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": snapshot["result"]["recommended_quote_id"],
                "confirmed": True,
                "actor": "服务级测试员",
            },
        )
        assert approved.status_code == 200
        await _wait_for_run_status(client, run_id, {"completed"})

        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert report["conclusion"]["status"] == "passed"
        assert report["usage"]["estimated_cost_usd"] > 0
        assert report["usage"]["estimated_cost_usd"] < 0.25
        assert report["approvals"][-1]["tool_name"] == "procurement_approve_supplier"
        assert provider.requests
        assert all(request.parallel_tool_calls is False for request in provider.requests)

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_approval_with_long_note_does_not_rely_on_truncated_summary(
    data_dir: Path,
    workspace: Path,
) -> None:
    """Regression: approval verification must bind the full arguments_sha256.

    A note longer than the 500-char arguments_summary truncation used to make
    json.loads(arguments_summary) fail with "采购审批参数不可验证" and break the
    human-in-the-loop approval path.
    """
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    note = "长备注：" + "确认该供应商报价与比价快照一致，且交期与开票要求均满足本次采购计划。" * 20

    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/procurement/conversations",
            json={
                "message": (
                    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                    "厚度公差3微米。请比较附件报价并推荐供应商。"
                ),
                "attachments": [_upload(case) for case in cases],
            },
        )
        assert started.status_code == 202
        accepted = started.json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})

        snapshot = detail["comparison"]
        selected_quote_id = snapshot["result"]["recommended_quote_id"]
        actor = "长备注回归测试员"
        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": selected_quote_id,
                "confirmed": True,
                "actor": actor,
                "note": note,
            },
        )
        assert approved.status_code == 200, approved.text
        await _wait_for_run_status(client, run_id, {"completed"})

        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert report["conclusion"]["status"] == "passed"
        assert report["approvals"][-1]["tool_name"] == "procurement_approve_supplier"

        stored = [
            row
            for row in reversed(harness.list_approvals(run_id))
            if row["tool_name"] == "procurement_approve_supplier"
        ][0]
        expected = arguments_sha256(
            {
                "request_id": request_id,
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": selected_quote_id,
                "actor": actor,
                "note": note,
            }
        )
        assert stored["arguments_sha256"] == expected
        # The stored summary is still truncated, but nothing may parse it back.
        assert len(stored["arguments_summary"]) <= 500

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_approval_recovers_from_missing_verification_marker(
    data_dir: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in {
        "AGENTHARNESS_PROCUREMENT_PROVIDER": "openai",
        "AGENTHARNESS_PROCUREMENT_MODEL": "verification-recovery-test-model",
        "AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD": "0.5",
        "AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD": "1.5",
        "AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD": "0.1",
        "AGENTHARNESS_PROCUREMENT_MAX_COST_USD": "0.25",
        "AGENTHARNESS_PROCUREMENT_MAX_TOKENS": "24000",
    }.items():
        monkeypatch.setenv(key, value)

    class MissingMarkerOnceProvider(ProcurementFakeProvider):
        async def stream(self, request):
            last_tool = next(
                (
                    message
                    for message in reversed(request.messages)
                    if message.role.value == "tool"
                ),
                None,
            )
            last_user = next(
                (
                    message
                    for message in reversed(request.messages)
                    if message.role.value == "user"
                ),
                None,
            )
            last_stage = (
                self._tool_payload(last_tool).get("stage")
                if last_tool is not None
                else None
            )
            if (
                last_stage == "supplier_approved"
                and last_user is not None
                and last_user.content.startswith("[verification_feedback]")
            ):
                async for item in self._text("【采购决策已验证】格式已纠正。"):
                    yield item
                return
            if last_stage == "supplier_approved":
                async for item in self._text("采购决策已验证，但首次输出缺少精确标记。"):
                    yield item
                return
            async for item in super().stream(request):
                yield item

    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    harness = Harness(
        data_dir=data_dir,
        providers={"openai": MissingMarkerOnceProvider()},
    )
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、"
                        "单色印刷，15天内交付上海松江，必须开票。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
            assert started.status_code == 202
            accepted = started.json()
            request_id = accepted["purchase_request_id"]
            run_id = accepted["run_id"]
            detail = await _wait_for_comparison(client, request_id, run_id=run_id)
            await _wait_for_run_status(client, run_id, {"require_human"})

            snapshot = detail["comparison"]
            approved = await client.post(
                f"/api/procurement/requests/{request_id}/decision",
                json={
                    "snapshot_id": snapshot["id"],
                    "input_sha256": snapshot["input_sha256"],
                    "quote_id": snapshot["result"]["recommended_quote_id"],
                    "confirmed": True,
                    "actor": "验证恢复测试员",
                },
            )

            assert approved.status_code == 200, approved.text
            await _wait_for_run_status(client, run_id, {"completed"})
            report = (await client.get(f"/api/runs/{run_id}/report")).json()
            assert report["conclusion"]["status"] == "passed"
            final_attempt = report["verification"]["attempts"][-1]
            assert final_attempt["passed"] is True
            assert final_attempt["evidence"]["0:output"]["contains"] == {
                "【采购决策已验证】": True
            }
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


def test_procurement_rejects_supplier_selection_before_result_verification(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    for case in cases:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "verification-gate-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )

    def fail_verification(_request_id: str, *, run_id: str) -> dict:
        raise ProcurementError(f"forced verification failure for {run_id}")

    monkeypatch.setattr(service, "verify_agent_result", fail_verification)

    with pytest.raises(ProcurementError, match="forced verification failure"):
        service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)

    report = service.audit_report(str(request["id"]))
    harness.close()

    assert not any(
        event["type"] == "supplier_selection_requested"
        for event in report["audit_events"]
    )


def _request_body(truth: dict) -> dict:
    request = truth["request"]
    return {
        "title": "华东仓快递袋询价",
        "category": "ecommerce_packaging",
        "item_name": request["item_name"],
        "quantity": request["quantity"],
        "unit": request["unit"],
        "specifications": request["specifications"],
        "constraints": request["constraints"],
    }


def _upload(case: dict) -> dict:
    return {
        "filename": case["filename"],
        "content_base64": base64.b64encode(build_case_document(case)).decode("ascii"),
    }


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quote"
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.mark.asyncio
async def test_all_procurement_posts_respect_web_execution_disabled(
    data_dir: Path,
    workspace: Path,
) -> None:
    truth = load_frozen_truth()
    request_body = _request_body(truth)
    quote_body = _upload(truth["quotes"][0])
    conversation_body = {
        "message": "采购快递袋并比较附件报价",
        "attachments": [_upload(case) for case in truth["quotes"][:2]],
    }
    attempts = [
        ("/api/procurement/requests", request_body),
        ("/api/procurement/conversations", conversation_body),
        ("/api/procurement/requests/missing/resume", {"message": "继续"}),
        ("/api/procurement/requests/missing/quotes", quote_body),
        (
            "/api/procurement/requests/missing/quotes/missing/corrections",
            {"field": "unit_price", "value": "1"},
        ),
        ("/api/procurement/requests/missing/analyze", None),
        (
            "/api/procurement/requests/missing/decision",
            {
                "snapshot_id": "missing",
                "input_sha256": "0" * 64,
                "quote_id": "missing",
                "confirmed": True,
            },
        ),
    ]

    harness = Harness(data_dir=data_dir)
    app = create_app(
        harness=harness,
        workspace_roots=[workspace],
        execution_enabled=False,
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for path, body in attempts:
                response = await client.post(path, json=body)
                assert response.status_code == 403, (path, response.text)
                assert response.json() == {
                    "detail": "Web execution is disabled for this server"
                }

            read_response = await client.get("/api/procurement/requests")
            assert read_response.status_code == 200
            assert read_response.json() == []
            assert harness.list_runs() == []
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_duplicate_conversation_attachments_leave_no_partial_state(
    data_dir: Path,
    workspace: Path,
) -> None:
    case = load_frozen_truth()["quotes"][0]
    first = _upload(case)
    duplicate = {**first, "filename": "duplicate.xlsx"}
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": "采购快递袋并比较两个附件",
                    "attachments": [first, duplicate],
                },
            )

            assert response.status_code == 400
            assert "同一报价文件" in response.json()["detail"]
            assert (await client.get("/api/procurement/requests")).json() == []
            assert harness.list_sessions() == []
            assert harness.list_runs() == []
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_conversation_creation_rolls_back_on_attachment_audit_failure(
    data_dir: Path,
    workspace: Path,
) -> None:
    cases = load_frozen_truth()["quotes"][:2]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    with harness.storage._lock:
        harness.storage._conn.execute(
            """CREATE TRIGGER fail_second_attachment_audit
               BEFORE INSERT ON procurement_audit_events
               WHEN NEW.type = 'attachment_staged'
                AND (SELECT COUNT(*) FROM procurement_audit_events
                     WHERE type = 'attachment_staged') >= 1
               BEGIN
                   SELECT RAISE(ABORT, 'forced attachment failure');
               END"""
        )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": "采购快递袋并比较附件报价",
                    "attachments": [_upload(case) for case in cases],
                },
            )

            assert response.status_code == 500
            assert (await client.get("/api/procurement/requests")).json() == []
            assert harness.list_sessions() == []
            assert harness.list_runs() == []
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


def test_agent_cannot_mutate_quote_facts(data_dir: Path) -> None:
    truth = load_frozen_truth()
    case = truth["quotes"][0]
    document = build_case_document(case)
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    run_id = "malicious-model-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    quote = service.import_quote(
        str(request["id"]),
        filename=case["filename"],
        data=document,
        extracted=parse_quote(case["filename"], document),
    )
    original_price = quote["extracted"]["fields"]["unit_price"]["value"]

    with pytest.raises(ProcurementError, match="只能由采购员"):
        service.correct_quote_from_agent(
            str(request["id"]),
            str(quote["id"]),
            field="unit_price",
            value="1",
            run_id=run_id,
        )

    report = service.audit_report(str(request["id"]))
    harness.close()

    assert report["quotes"][0]["extracted"]["fields"]["unit_price"]["value"] == original_price
    assert "procurement_correct_quote" not in PROCUREMENT_TOOL_NAMES
    assert not any(
        event["type"] in {"field_corrected", "clarification_applied"}
        for event in report["audit_events"]
    )


@pytest.mark.parametrize("invoice_phrase", ["不可开票", "不提供专票"])
def test_quote_parser_preserves_negative_shipping_and_invoice_semantics(
    invoice_phrase: str,
) -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "否定语义供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["运费", "600"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["备注", f"不包邮；{invoice_phrase}"],
        ]
    )

    extracted = parse_quote("negative-semantics.xlsx", document)
    fields = extracted["fields"]

    assert fields["shipping_included"]["value"] is False
    assert fields["shipping_fee"]["value"] == "600"
    assert fields["supports_invoice"]["value"] is False


@pytest.mark.parametrize(
    ("field", "label", "invalid_value"),
    [
        ("unit_price", "单价", "0"),
        ("moq", "MOQ", "-50"),
        ("lead_time_days", "交期", "-3"),
        ("lead_time_days", "交期", "1.9"),
    ],
)
def test_quote_parser_requires_review_for_invalid_business_numbers(
    field: str,
    label: str,
    invalid_value: str,
) -> None:
    rows = [
        ["供应商", "数值边界供应商"],
        ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
        ["币种", "CNY"],
        ["单价", "500"],
        ["计价数量", "1000"],
        ["税率", "13%"],
        ["是否含税", "是"],
        ["是否包邮", "是"],
        ["MOQ", "1000"],
        ["交期", "7"],
        ["是否可开票", "是"],
    ]
    next(row for row in rows if row[0] == label)[1] = invalid_value

    extracted = parse_quote("invalid-number.xlsx", _xlsx_bytes(rows))

    assert extracted["fields"][field]["value"] is None
    assert extracted["fields"][field]["status"] == "needs_review"
    assert field in fields_requiring_review(extracted)


def test_quote_parser_requires_review_for_conflicting_high_confidence_values() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "冲突报价供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["单价", "900"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("conflicting-price.xlsx", document)
    price = extracted["fields"]["unit_price"]

    assert price["value"] == "500"
    assert price["status"] == "needs_review"
    assert {candidate["value"] for candidate in price["conflicts"]} == {"500", "900"}
    assert "unit_price" in fields_requiring_review(extracted)


def test_quote_parser_accepts_duplicate_evidence_with_the_same_value() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "重复证据供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("duplicate-price.xlsx", document)
    price = extracted["fields"]["unit_price"]

    assert price["status"] == "accepted"
    assert "conflicts" not in price
    assert "unit_price" not in fields_requiring_review(extracted)


def test_quote_parser_accepts_semantically_equivalent_identity_evidence() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "同义证据供应商"],
            ["品名", "PE black mailer 250x350mm 60um 单色印刷"],
            ["颜色", "black"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("equivalent-color.xlsx", document)
    color = extracted["fields"]["color"]

    assert color["status"] == "accepted"
    assert "conflicts" not in color
    assert "color" not in fields_requiring_review(extracted)


def test_quote_parser_does_not_join_pdf_columns_into_invoice_conflict() -> None:
    case = next(case for case in load_frozen_truth()["quotes"] if case["id"] == "q-psi")

    extracted = parse_quote(case["filename"], build_case_document(case))
    invoice = extracted["fields"]["supports_invoice"]

    assert invoice["value"] is True
    assert invoice["status"] == "accepted"
    assert "conflicts" not in invoice


def test_quote_parser_preserves_per_ten_thousand_price_basis() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "万件计价供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "5000"],
            ["计价数量", "每10000个"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("per-ten-thousand.xlsx", document)

    assert extracted["fields"]["price_basis"]["value"] == 10_000


def test_requirement_rejects_unknown_spec_and_constraint_fields() -> None:
    """Strict validation: a mistyped or stale field must not be silently
    ignored, otherwise the model/API could build a requirement with wrong
    tolerances or limits without anyone noticing."""
    from agentharness.procurement.service import _validated_requirement

    payload = {
        "title": "快递袋",
        "item_name": "PE快递袋",
        "quantity": 1000,
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
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 15,
            "invoice_required": True,
        },
    }
    bad_specs = dict(payload)
    bad_specs["specifications"] = dict(payload["specifications"])
    bad_specs["specifications"]["width_cm"] = "25"
    with pytest.raises(ProcurementError, match="不支持的字段"):
        _validated_requirement(bad_specs)

    bad_constraints = dict(payload)
    bad_constraints["constraints"] = dict(payload["constraints"])
    bad_constraints["constraints"]["budget"] = "0.5"
    with pytest.raises(ProcurementError, match="不支持的字段"):
        _validated_requirement(bad_constraints)


def test_quote_parser_recognizes_chinese_dimension_labels() -> None:
    """Regression: 宽度（mm）/长度（mm） labels must map to width_mm/length_mm.

    The Chinese labels normalize to 宽度mm/长度mm which were missing from the
    alias table, so explicit dimension cells were silently ignored and the
    parser fell back to description inference.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "中文规格供应商"],
            ["品名", "PE 白色快递袋 510x350mm 60um 单色印刷"],
            ["材质", "PE"],
            ["颜色", "白色"],
            ["印刷色数", "1"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["宽度（mm）", "510"],
            ["长度（mm）", "350"],
            ["厚度（微米）", "60"],
        ]
    )

    extracted = parse_quote("chinese-dimension-labels.xlsx", document)
    fields = extracted["fields"]

    assert fields["width_mm"]["value"] == "510"
    assert fields["width_mm"]["status"] == "accepted"
    assert fields["length_mm"]["value"] == "350"
    assert fields["length_mm"]["status"] == "accepted"
    assert fields["thickness_um"]["value"] == "60"
    assert fields["thickness_um"]["status"] == "accepted"
    assert "width_mm" not in fields_requiring_review(extracted)
    assert "length_mm" not in fields_requiring_review(extracted)


def test_quote_parser_does_not_conflict_when_invoice_label_contains_positive_word() -> None:
    """Regression: “是否可开票: 否” must stay accepted.

    The label itself contains 可开, and the free-text inference regex matched
    the label text, creating a false cross-source conflict that forced every
    Chinese “cannot invoice” quote into human review.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "不可开票供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "否"],
        ]
    )

    extracted = parse_quote("invoice-no-label.xlsx", document)
    invoice = extracted["fields"]["supports_invoice"]

    assert invoice["value"] is False
    assert invoice["status"] == "accepted"
    assert "conflicts" not in invoice
    assert "supports_invoice" not in fields_requiring_review(extracted)


def test_quote_parser_still_flags_genuine_prose_invoice_contradiction() -> None:
    """The lookbehind fix must not hide real contradictions: an explicit
    “是否可开票: 是” plus prose “本公司不可开票” is a genuine cross-source
    conflict and must go to human review."""
    document = _xlsx_bytes(
        [
            ["供应商", "矛盾发票供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["备注", "本公司不可开票"],
        ]
    )

    extracted = parse_quote("invoice-contradiction.xlsx", document)
    invoice = extracted["fields"]["supports_invoice"]

    assert invoice["status"] == "needs_review"
    assert "supports_invoice" in fields_requiring_review(extracted)


def test_quote_parser_zhuanpiao_label_no_is_accepted() -> None:
    """Regression: “是否可开专票: 否” must parse as invoice-capable False.

    The bare substring 专票 inside the label used to self-trigger the positive
    free-text inference and replace the explicit 否 with True, so a supplier
    that cannot issue special VAT invoices would be treated as invoice-capable
    and would never be excluded by the invoice_required hard constraint.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "专票供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开专票", "否"],
        ]
    )

    extracted = parse_quote("invoice-zhuanpiao-no.xlsx", document)
    invoice = extracted["fields"]["supports_invoice"]

    assert invoice["value"] is False
    assert invoice["status"] == "accepted"
    assert "conflicts" not in invoice
    assert "supports_invoice" not in fields_requiring_review(extracted)


def test_quote_parser_bare_keipiao_header_no_is_accepted() -> None:
    """Regression: a bare “可开票” table header (no 是否 prefix) with value 否
    must stay accepted.

    The label-only positive inference guard must not treat the header text as
    prose evidence; otherwise every Chinese “cannot invoice” quote using the
    compact header is forced into human review.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "品名", "币种", "单价", "计价数量", "税率", "是否含税", "是否包邮", "MOQ", "交期", "可开票"],
            ["可开票表头供应商", "PE 白色快递袋 250x350mm 60um 单色印刷", "CNY", "500", "1000", "13%", "是", "是", "1000", "7", "否"],
        ]
    )

    extracted = parse_quote("invoice-keipiao-header-no.xlsx", document)
    invoice = extracted["fields"]["supports_invoice"]

    assert invoice["value"] is False
    assert invoice["status"] == "accepted"
    assert "conflicts" not in invoice
    assert "supports_invoice" not in fields_requiring_review(extracted)


def test_supports_invoice_negative_special_invoice_phrases() -> None:
    """Regression: negative special-invoice phrases must never parse as True.

    The positive marker 专票/普票 used to win over 不可/不能/不开/无法/不支持
    phrases, which would make a supplier that cannot issue special VAT invoices
    look invoice-capable (violating the invoice_required hard constraint).
    """
    from agentharness.procurement.parsing import coerce_field_value

    for phrase in (
        "不可开专票",
        "不能开专票",
        "不开专票",
        "无法开专票",
        "不可开普票",
        "不能开普票",
        "不开普票",
        "无法开普票",
        "不可开具专票",
        "不能开具专票",
        "无法开具专票",
        "不能开具普票",
        "不支持开专票",
        "不支持开普票",
        "不支持开具专票",
        "不支持开具普票",
        "不提供专票",
        "不提供普票",
        "不提供增值税专用发票",
        "不提供增值税普通发票",
        "不能开具增值税专用发票",
        "不可开具增值税专用发票",
        "无法开具增值税专用发票",
        "不能开具增值税普通发票",
        "不可开具增值税普通发票",
        "无法开具增值税普通发票",
        "不能开增值税专用发票",
        "不开增值税专用发票",
        "无法开增值税专用发票",
        "不能开增值税普通发票",
        "不开增值税普通发票",
        "无法开增值税普通发票",
    ):
        assert coerce_field_value("supports_invoice", phrase) is False, phrase
    assert coerce_field_value("supports_invoice", "可开专票") is True
    assert coerce_field_value("supports_invoice", "可开普票") is True


def test_quote_parser_thickness_si_converts_to_um() -> None:
    """Regression: “5丝” must become 50 µm (1 丝 = 10 µm), not 5 µm.

    The old regex stored the raw number for any unit token including 丝, which
    silently mis-typed Chinese packaging quotes and could both wrongly exclude
    eligible quotes and wrongly admit ineligible ones under the thickness
    hard constraint.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "丝单位供应商"],
            ["品名", "PE 白色快递袋 250x350mm 5丝 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["宽度（mm）", "250"],
            ["长度（mm）", "350"],
        ]
    )

    extracted = parse_quote("thickness-si.xlsx", document)
    thickness = extracted["fields"]["thickness_um"]

    assert thickness["value"] == "50"
    assert thickness["status"] == "accepted"


def test_quote_parser_cm_dimensions_convert_to_mm() -> None:
    """Regression: “20*30cm” must become 200×300 mm, not 20×30 mm."""
    document = _xlsx_bytes(
        [
            ["供应商", "厘米单位供应商"],
            ["品名", "PE 白色快递袋 20*30cm 5丝 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("dimension-cm.xlsx", document)
    assert extracted["fields"]["width_mm"]["value"] == "200"
    assert extracted["fields"]["length_mm"]["value"] == "300"


def test_quote_parser_three_column_table_header_is_detected() -> None:
    """Regression: a 3-column header row must use the table branch.

    The old >=4 header threshold treated 3-column tables as key-value rows,
    writing the second header cell (e.g. 单价) as the supplier name with high
    confidence and no review flag.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "单价", "MOQ"],
            ["甲包装", "500", "1000"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷", ""],
            ["币种", "CNY", ""],
            ["交期", "7", ""],
        ]
    )

    extracted = parse_quote("three-column-table.xlsx", document)
    fields = extracted["fields"]

    assert fields["supplier_name"]["value"] == "甲包装"
    assert fields["supplier_name"]["status"] == "accepted"
    assert fields["unit_price"]["value"] == "500"
    assert fields["moq"]["value"] == 1000


def test_quote_parser_shipping_notax_does_not_trigger_freight_excluded() -> None:
    """Regression: “报价含运费（运费不含税）” is a tax note, not “运费另计”.

    The old negative regex matched 运费不含 inside 运费不含税 and inferred
    shipping_included=False with a hardcoded excerpt, double-counting the
    freight cost. It must stay shipping_included=True and the excerpt must be
    the actual matched text.
    """
    document = _xlsx_bytes(
        [
            ["供应商", "含运费供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "否"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["备注", "报价含运费（运费不含税），运费500元"],
        ]
    )

    extracted = parse_quote("shipping-notax.xlsx", document)
    shipping = extracted["fields"]["shipping_included"]
    excerpt = str(shipping.get("source", {}).get("excerpt") or "")

    assert shipping["value"] is True
    assert shipping["status"] == "accepted"
    assert "运费另计" not in excerpt
    assert "报价含运费" in excerpt


def test_api_spec_accepts_roll_goods_length_mm() -> None:
    """The Web API must accept the same roll-goods length boundary as the
    domain service (10,000,000 mm), not the flat-sheet 10,000 mm cap."""
    from agentharness.api.procurement import CreateProcurementRequestBody

    body = CreateProcurementRequestBody(
        title="气泡膜卷材",
        category="ecommerce_packaging",
        item_name="气泡膜",
        quantity=1000,
        unit="piece",
        specifications={
            "width_mm": "600",
            "length_mm": "50000",
            "thickness_um": "90",
            "material": "PE",
            "color": "透明",
            "print_colors": 0,
        },
        constraints={
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 14,
            "invoice_required": True,
        },
    )
    assert body.specifications.length_mm == 50_000
    assert body.model_dump(mode="json")["specifications"]["length_mm"] == "50000"


@pytest.mark.asyncio
async def test_api_create_request_accepts_roll_goods_length(
    data_dir: Path,
    workspace: Path,
) -> None:
    """End-to-end: POST /api/procurement/requests must accept a roll-goods
    length that the domain supports (1.2 m = 1,200,000 mm)."""
    harness = Harness(data_dir=data_dir, providers={"procurement_fake": FakeModelAdapter()})
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/procurement/requests",
            json={
                "title": "气泡膜卷材",
                "category": "ecommerce_packaging",
                "item_name": "气泡膜",
                "quantity": 1000,
                "unit": "piece",
                "specifications": {
                    "width_mm": "600",
                    "length_mm": "1200000",
                    "thickness_um": "90",
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
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["specifications"]["length_mm"] == "1200000"
    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


def test_requirement_accepts_roll_goods_length_in_mm() -> None:
    """Regression: roll goods (tape/film/foam) are quoted in mm with lengths
    far above the old 10000 mm flat-sheet cap; they must not be rejected."""
    from agentharness.procurement.service import _validated_requirement

    payload = {
        "title": "气泡膜卷材",
        "item_name": "气泡膜",
        "quantity": 1000,
        "unit": "piece",
        "specifications": {
            "width_mm": "600",
            "length_mm": "50000",
            "thickness_um": "90",
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
    assert validated["specifications"]["length_mm"] == "50000"

    payload["specifications"]["length_mm"] = "1200000"
    validated = _validated_requirement(payload)
    assert validated["specifications"]["length_mm"] == "1200000"


def test_material_identity_constraints_exclude_cheaper_wrong_product() -> None:
    request = {
        **load_frozen_truth()["request"],
        "id": "material-identity-request",
    }

    def quote(
        quote_id: str,
        *,
        supplier: str,
        description: str,
        material: str,
        color: str,
        print_colors: int,
        price: str,
    ) -> dict:
        values = {
            "supplier_name": supplier,
            "item_description": description,
            "material": material,
            "color": color,
            "print_colors": print_colors,
            "currency": "CNY",
            "unit_price": price,
            "price_basis": 1000,
            "tax_rate": "0.13",
            "tax_included": True,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 1000,
            "lead_time_days": 7,
            "supports_invoice": True,
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "valid_until": "2026-12-31",
        }
        return {
            "id": quote_id,
            "supplier_name": supplier,
            "source_sha256": quote_id * 8,
            "extracted": {
                "fields": {name: {"value": value} for name, value in values.items()}
            },
        }

    correct = quote(
        "correct1",
        supplier="正确供应商",
        description="PE 白色快递袋 250x350mm 60um 单色印刷",
        material="PE",
        color="白色",
        print_colors=1,
        price="520",
    )
    wrong = quote(
        "wrong001",
        supplier="错误低价供应商",
        description="PVC 黑色垃圾袋 250x350mm 60um 无印刷",
        material="PVC",
        color="黑色",
        print_colors=0,
        price="100",
    )

    result = compare_quotes(request, [wrong, correct], analysis_as_of="2026-07-27")
    wrong_result = next(item for item in result["quotes"] if item["quote_id"] == "wrong001")

    assert result["recommended_quote_id"] == "correct1"
    assert wrong_result["eligible"] is False
    assert {reason["code"] for reason in wrong_result["exclusion_reasons"]} >= {
        "item_identity",
        "spec_material",
        "spec_color",
        "spec_print_colors",
    }
    assert wrong_result["match"]["passed"] is False


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "PE 白色快递袋 250x350mm 60um 单色印刷",
            {"material": "PE", "color": "白色", "print_colors": 1},
        ),
        (
            "PVC 黑色垃圾袋 250x350mm 60um 无印刷",
            {"material": "PVC", "color": "黑色", "print_colors": 0},
        ),
    ],
)
def test_quote_parser_structures_material_color_and_printing(
    description: str,
    expected: dict[str, object],
) -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "身份字段供应商"],
            ["品名", description],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("identity-fields.xlsx", document)

    assert {
        field: extracted["fields"][field]["value"] for field in expected
    } == expected
    assert not set(expected) & set(fields_requiring_review(extracted))


def _rich_quote_rows() -> list[tuple[str, str]]:
    return [
        ("\u4f9b\u5e94\u5546\u540d\u79f0", "\u4f9b\u5e94\u5546\u7532"),
        ("\u54c1\u540d", "PE \u5feb\u9012\u888b"),
        ("\u6750\u8d28", "PE"),
        ("\u989c\u8272", "\u767d\u8272"),
        ("\u5370\u5237\u8272\u6570", "1"),
        ("\u5e01\u79cd", "CNY"),
        ("\u5355\u4ef7", "0.5"),
        ("\u8ba1\u4ef7\u6570\u91cf", "1"),
        ("\u7a0e\u7387", "13%"),
        ("\u662f\u5426\u542b\u7a0e", "\u5426"),
        ("\u8fd0\u8d39", "0"),
        ("\u662f\u5426\u542b\u8fd0\u8d39", "\u662f"),
        ("\u8d77\u8ba2\u91cf", "1000"),
        ("\u4ea4\u671f\uff08\u5929\uff09", "10"),
        ("\u662f\u5426\u53ef\u5f00\u7968", "\u662f"),
        ("\u5bbd\u5ea6", "250"),
        ("\u957f\u5ea6", "350"),
        ("\u539a\u5ea6\uff08\u5fae\u7c73\uff09", "60"),
        ("\u4ed8\u6b3e\u6761\u4ef6", "Net 30"),
        ("\u62a5\u4ef7\u6709\u6548\u671f", "2026-12-31"),
        ("\u62a5\u4ef7\u5355\u53f7", "QT-001"),
        ("\u8054\u7cfb\u4eba", "\u738b\u7ecf\u7406"),
        ("\u5907\u6ce8", "\u542b\u4e13\u7968"),
    ]


def test_parser_captures_unknown_xlsx_fields_as_informational() -> None:
    """Unknown label/value rows in XLSX are kept as read-only evidence fields."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Quote"
    for label, value in _rich_quote_rows():
        ws.append([label, value])
    buffer = io.BytesIO()
    wb.save(buffer)

    extracted = parse_quote("rich.xlsx", buffer.getvalue())
    fields = extracted["fields"]
    assert fields["supplier_name"]["value"] == "\u4f9b\u5e94\u5546\u7532"
    assert fields["item_description"]["value"] == "PE \u5feb\u9012\u888b"
    assert fields["width_mm"]["value"] == "250"
    assert fields_requiring_review(extracted) == []

    info = extracted["informational_fields"]
    assert info["\u62a5\u4ef7\u5355\u53f7"]["value"] == "QT-001"
    assert info["\u62a5\u4ef7\u5355\u53f7"]["informational"] is True
    assert info["\u62a5\u4ef7\u5355\u53f7"]["label"] == "\u62a5\u4ef7\u5355\u53f7"
    assert info["\u62a5\u4ef7\u5355\u53f7"]["source"]["locator"] == "Quote!B21"
    assert info["\u8054\u7cfb\u4eba"]["value"] == "\u738b\u7ecf\u7406"
    assert info["\u5907\u6ce8"]["value"] == "\u542b\u4e13\u7968"


def test_parser_captures_unknown_pdf_fields_as_informational() -> None:
    """Unknown labelled lines in PDFs are kept as read-only evidence fields."""
    from reportlab.pdfgen import canvas

    from agentharness.procurement.evaluation import _pdf_font

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    font = _pdf_font("zh-CN")
    pdf.setFont(font, 12)
    y = 800
    for label, value in _rich_quote_rows():
        pdf.drawString(50, y, f"{label}: {value}")
        y -= 20
    pdf.save()

    extracted = parse_quote("rich.pdf", buffer.getvalue())
    assert extracted["fields"]["supplier_name"]["value"] == "\u4f9b\u5e94\u5546\u7532"
    assert extracted["fields"]["width_mm"]["value"] == "250"
    assert fields_requiring_review(extracted) == []
    info = extracted["informational_fields"]
    assert info["\u62a5\u4ef7\u5355\u53f7"]["value"] == "QT-001"
    assert info["\u8054\u7cfb\u4eba"]["value"] == "\u738b\u7ecf\u7406"


def test_quote_parser_requires_review_when_identity_facts_are_missing() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "身份缺失供应商"],
            ["品名", "快递袋 250x350mm 60um"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )

    extracted = parse_quote("missing-identity-fields.xlsx", document)

    assert set(fields_requiring_review(extracted)) >= {
        "material",
        "color",
        "print_colors",
    }


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("quantity",), 0),
        (("constraints", "fx_rates", "USD"), "-7.2"),
        (("specifications", "width_mm"), "-1"),
        (("specifications", "print_colors"), 1.5),
        (("constraints", "max_lead_days"), 0),
    ],
)
def test_service_rejects_invalid_requirement_before_persisting(
    data_dir: Path,
    path: tuple[str, ...],
    invalid_value: object,
) -> None:
    payload = json.loads(json.dumps(_request_body(load_frozen_truth())))
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid_value
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)

    with pytest.raises(ProcurementError):
        service.create_request(payload)

    stored = service.list_requests()
    harness.close()

    assert stored == []


def test_request_list_uses_batched_related_rows(data_dir: Path, monkeypatch) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    created = service.create_request(_request_body(load_frozen_truth()))

    def reject_n_plus_one(*args, **kwargs):
        del args, kwargs
        raise AssertionError("request list must not load related rows one request at a time")

    monkeypatch.setattr(service.repo, "list_quotes", reject_n_plus_one)
    monkeypatch.setattr(service.repo, "get_decision", reject_n_plus_one)
    try:
        rows = service.list_requests()
    finally:
        harness.close()

    assert [row["id"] for row in rows] == [created["id"]]
    assert rows[0]["quote_count"] == 0
    assert rows[0]["decision"] is None


def test_agent_requirement_defaults_optional_tolerances(data_dir: Path) -> None:
    payload = json.loads(json.dumps(_request_body(load_frozen_truth())))
    payload["constraints"].pop("size_tolerance_mm")
    payload["constraints"].pop("thickness_tolerance_um")
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("缺少公差也应使用业务默认值")
    run_id = "default-tolerance-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(draft["session_id"]),
        root_run_id=run_id,
    )

    stored = service.capture_requirement(str(draft["id"]), payload, run_id=run_id)
    harness.close()

    assert stored["constraints"]["size_tolerance_mm"] == "2"
    assert stored["constraints"]["thickness_tolerance_um"] == "3"


def test_procurement_tool_prerequisites_hide_analysis_until_capture() -> None:
    request = SimpleNamespace(
        tools=[
            "procurement_read_request",
            "procurement_capture_requirement",
            "procurement_execute_analysis",
            "procurement_approve_supplier",
        ],
        metadata={
            "tool_prerequisites": {
                "procurement_execute_analysis": ["procurement_capture_requirement"],
                "procurement_approve_supplier": ["procurement_capture_requirement"],
            }
        },
    )
    initial = enabled_tool_names(request, [], set(request.tools))
    after_capture = enabled_tool_names(
        request,
        [
            SimpleNamespace(
                tool_name="procurement_capture_requirement",
                status=ToolInvocationStatus.succeeded,
            )
        ],
        set(request.tools),
    )

    assert "procurement_execute_analysis" not in initial
    assert "procurement_approve_supplier" not in initial
    assert "procurement_execute_analysis" in after_capture
    assert "procurement_approve_supplier" in after_capture


def test_agent_requirement_validation_leaves_draft_unchanged(data_dir: Path) -> None:
    payload = json.loads(json.dumps(_request_body(load_frozen_truth())))
    payload["constraints"]["fx_rates"]["USD"] = "-7.2"
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("采购包装耗材")
    run_id = "invalid-requirement-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(draft["session_id"]),
        root_run_id=run_id,
    )

    with pytest.raises(ProcurementError):
        service.capture_requirement(str(draft["id"]), payload, run_id=run_id)

    stored = service.get_request(str(draft["id"]))
    harness.close()

    assert stored["status"] == "draft"
    assert stored["quantity"] == 1
    assert stored["constraints"] == {}


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("unit_price", "0"),
        ("moq", -50),
        ("lead_time_days", -3),
        ("lead_time_days", "1.9"),
        ("fx_rate", "-7.2"),
    ],
)
def test_costing_rejects_invalid_business_numbers(
    field: str,
    invalid_value: object,
) -> None:
    truth = load_frozen_truth()
    request = json.loads(json.dumps(truth["request"]))
    quotes = []
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        extracted = parse_quote(case["filename"], document)
        quotes.append(
            {
                "id": case["id"],
                "supplier_name": case["fields"]["supplier_name"],
                "source_sha256": hashlib.sha256(document).hexdigest(),
                "extracted": extracted,
            }
        )
    if field == "fx_rate":
        request["constraints"]["fx_rates"]["CNY"] = invalid_value
    else:
        quotes[0]["extracted"]["fields"][field]["value"] = invalid_value

    with pytest.raises(CostingError):
        compare_quotes(request, quotes, analysis_as_of="2026-07-27")


@pytest.mark.parametrize(
    ("filename", "document", "message"),
    [
        (
            "too-many-rows.xlsx",
            _xlsx_bytes([[f"row-{index}"] for index in range(501)]),
            "500",
        ),
        (
            "too-many-columns.xlsx",
            _xlsx_bytes([[f"column-{index}" for index in range(41)]]),
            "40",
        ),
    ],
)
def test_quote_parser_rejects_xlsx_outside_declared_dimensions(
    filename: str,
    document: bytes,
    message: str,
) -> None:
    with pytest.raises(QuoteParseError, match=message):
        parse_quote(filename, document)


def test_xlsx_table_source_uses_actual_data_cell_after_blank_row() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "品名", "币种", "单价", "计价数量"],
            [None, None, None, None, None],
            ["坐标供应商", "PE 白色快递袋", "CNY", "500", "1000"],
        ]
    )

    extracted = parse_quote("source-coordinate.xlsx", document)

    assert extracted["fields"]["unit_price"]["source"]["locator"] == "Quote!D3"


def test_unparseable_valid_until_requires_human_review() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "有效期复核供应商"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["报价有效期", "另行通知"],
        ]
    )

    extracted = parse_quote("invalid-validity.xlsx", document)

    assert extracted["fields"]["valid_until"]["status"] == "needs_review"
    assert "valid_until" in fields_requiring_review(extracted)


def test_comparison_uses_explicit_analysis_date_for_validity_and_delivery() -> None:
    truth = load_frozen_truth()
    request = json.loads(json.dumps(truth["request"]))
    request["created_at"] = "2026-07-01T00:00:00+00:00"
    request["constraints"]["required_delivery_date"] = "2026-07-30"
    quotes = []
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        quotes.append(
            {
                "id": case["id"],
                "supplier_name": case["fields"]["supplier_name"],
                "source_sha256": hashlib.sha256(document).hexdigest(),
                "extracted": parse_quote(case["filename"], document),
            }
        )
    alpha_fields = quotes[0]["extracted"]["fields"]
    beta_fields = quotes[1]["extracted"]["fields"]
    alpha_fields["unit_price"]["value"] = "100"
    alpha_fields["valid_until"]["value"] = "2026-07-10"
    alpha_fields["lead_time_days"]["value"] = 7
    beta_fields["valid_until"]["value"] = "2026-07-31"
    beta_fields["lead_time_days"]["value"] = 2

    result = compare_quotes(request, quotes, analysis_as_of="2026-07-27")
    alpha = next(item for item in result["quotes"] if item["quote_id"] == "q-alpha")

    assert result["analysis_as_of"] == "2026-07-27"
    assert result["recommended_quote_id"] == "q-beta", result["quotes"]
    assert {reason["code"] for reason in alpha["exclusion_reasons"]} >= {
        "expired",
        "required_delivery_date",
    }


def test_artifact_store_preserves_binary_pdf_byte_for_byte(data_dir: Path) -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "token=abcdefghijklmnop"})
    writer.write(output)
    original = (
        output.getvalue().replace(b"%\xe2\xe3\xcf\xd3", b"%ABCD")
        + b"\n% token=abcdefghijklmnop\n"
    )
    assert len(PdfReader(io.BytesIO(original), strict=True).pages) == 1
    assert all(byte < 128 for byte in original)

    harness = Harness(data_dir=data_dir)
    metadata = harness.storage.artifacts.put(
        original,
        content_type="application/pdf",
        summary="采购报价原件",
    )
    stored = harness.storage.artifacts.get_bytes(metadata["sha256"])
    text_metadata = harness.storage.artifacts.put(
        b'{"token":"abcdefghijklmnop"}',
        content_type="application/json",
        summary="派生文本",
    )
    stored_text = harness.storage.artifacts.get_bytes(text_metadata["sha256"])
    harness.close()

    assert metadata["sha256"] == hashlib.sha256(original).hexdigest()
    assert metadata["size_bytes"] == len(original)
    assert stored == original
    assert len(PdfReader(io.BytesIO(stored), strict=True).pages) == 1
    assert b"abcdefghijklmnop" not in stored_text


def test_approval_rechecks_quote_validity_on_approval_date(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    imported = []
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        imported.append(
            service.import_quote(
                str(request["id"]),
                filename=case["filename"],
                data=document,
                extracted=parse_quote(case["filename"], document),
            )
        )
    alpha = imported[0]
    service.correct_field(
        str(request["id"]),
        str(alpha["id"]),
        field="valid_until",
        value="2026-07-28",
        actor="采购员",
    )
    run_id = "validity-approval-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    assert snapshot["result"]["analysis_as_of"] == "2026-07-27"
    assert snapshot["result"]["recommended_quote_id"] == alpha["id"]

    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 29))
    with pytest.raises(ProcurementError, match="审批日"):
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=str(alpha["id"]),
            run_id=run_id,
            approval_id="approval-validity-test",
            note=None,
            actor="采购员",
        )

    stored = service.get_request(str(request["id"]))
    harness.close()

    assert stored["decision"] is None
    assert stored["status"] == "analyzed"


def test_supplier_decision_request_and_audit_are_atomic(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "atomic-decision-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    selected_id = str(snapshot["result"]["recommended_quote_id"])
    with harness.storage._lock:
        harness.storage._conn.execute(
            """CREATE TRIGGER fail_supplier_approved_audit
               BEFORE INSERT ON procurement_audit_events
               WHEN NEW.type = 'supplier_approved'
               BEGIN
                   SELECT RAISE(ABORT, 'forced audit failure');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced audit failure"):
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="approval-atomic-test",
            note="事务故障注入",
            actor="采购员",
        )

    stored = service.get_request(str(request["id"]))
    report = service.audit_report(str(request["id"]))
    harness.close()

    assert stored["decision"] is None
    assert stored["approved_quote_id"] is None
    assert stored["status"] == "analyzed"
    assert not any(event["type"] == "supplier_approved" for event in report["audit_events"])


def test_no_award_decision_closes_zero_eligible_comparison_with_audit(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    body = _request_body(truth)
    body["constraints"] = {
        **body["constraints"],
        "max_landed_unit_cost": "0.01",
    }
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(body)
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "no-award-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    assert snapshot["result"]["eligible_count"] == 0

    closed = service.record_no_award(
        str(request["id"]),
        snapshot_id=str(snapshot["id"]),
        input_sha256=str(snapshot["input_sha256"]),
        note="全部报价超过预算，重新询价",
        actor="采购员王敏",
    )
    report = service.audit_report(str(request["id"]))
    harness.close()

    assert closed["status"] == "no_award"
    assert closed["approved_quote_id"] is None
    assert closed["decision"]["decision"] == "no_award"
    assert closed["decision"]["quote_id"] is None
    assert report["decision"] == closed["decision"]
    assert report["audit_events"][-1]["type"] == "procurement_no_award"


def test_no_award_decision_rejects_comparison_with_eligible_quote(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "no-award-rejected-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    assert snapshot["result"]["eligible_count"] > 0

    with pytest.raises(ProcurementError, match="仍有满足全部硬性条件"):
        service.record_no_award(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            note=None,
            actor="采购员王敏",
        )
    harness.close()


@pytest.mark.asyncio
async def test_no_award_decision_api_accepts_zero_eligible_snapshot(
    data_dir: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    body = _request_body(truth)
    body["constraints"] = {
        **body["constraints"],
        "max_landed_unit_cost": "0.01",
    }
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    service = app.state.procurement_service
    request = service.create_request(body)
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "no-award-api-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/procurement/requests/{request['id']}/decision",
            json={
                "decision": "no_award",
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "confirmed": True,
                "note": "全部报价不符合预算",
                "actor": "采购员王敏",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "no_award"
    assert response.json()["decision"]["decision"] == "no_award"
    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


def test_quote_correction_and_snapshot_invalidation_are_atomic(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    imported = []
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        imported.append(
            service.import_quote(
                str(request["id"]),
                filename=case["filename"],
                data=document,
                extracted=parse_quote(case["filename"], document),
            )
        )
    run_id = "atomic-correction-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    before = service.compare_for_agent(str(request["id"]), run_id=run_id)
    original = service.get_request(str(request["id"]))
    original_supplier = next(
        quote["supplier_name"] for quote in original["quotes"] if quote["id"] == imported[0]["id"]
    )
    with harness.storage._lock:
        harness.storage._conn.execute(
            """CREATE TRIGGER fail_field_corrected_audit
               BEFORE INSERT ON procurement_audit_events
               WHEN NEW.type = 'field_corrected'
               BEGIN
                   SELECT RAISE(ABORT, 'forced correction audit failure');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced correction audit failure"):
        service.correct_field(
            str(request["id"]),
            str(imported[0]["id"]),
            field="supplier_name",
            value="不应持久化",
            actor="采购员",
        )

    stored = service.get_request(str(request["id"]))
    report = service.audit_report(str(request["id"]))
    harness.close()

    stored_quote = next(quote for quote in stored["quotes"] if quote["id"] == imported[0]["id"])
    assert stored_quote["supplier_name"] == original_supplier
    assert stored["current_snapshot_id"] == before["id"]
    assert stored["status"] == "analyzed"
    assert not any(event["type"] == "field_corrected" for event in report["audit_events"])


def test_correcting_last_review_field_moves_status_to_ready(data_dir: Path) -> None:
    """Regression: correcting the final review field must advance status to
    ready, not stay stuck at review (stale committed copy was double-counted)."""
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(load_frozen_truth()))
    request_id = str(request["id"])

    def extracted(supplier: str, *, needs_review: bool) -> dict[str, object]:
        values = {
            "supplier_name": supplier,
            "item_description": "PE \u5feb\u9012\u888b",
            "material": "PE",
            "color": "\u767d\u8272",
            "print_colors": 1,
            "currency": "CNY",
            "unit_price": "0.5",
            "price_basis": 1,
            "tax_rate": "0.13",
            "tax_included": False,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 1000,
            "lead_time_days": 10,
            "supports_invoice": True,
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "payment_terms": "Net 30",
            "valid_until": "2026-12-31",
        }
        fields = {
            name: {
                "value": value,
                "confidence": 0.5 if (needs_review and name == "supplier_name") else 1.0,
                "status": (
                    "needs_review" if (needs_review and name == "supplier_name") else "accepted"
                ),
                "source": {
                    "document_kind": "xlsx",
                    "locator": "test",
                    "excerpt": "",
                    "method": "test",
                },
            }
            for name, value in values.items()
        }
        return {
            "schema_version": 1,
            "parser_version": "test",
            "document_kind": "xlsx",
            "fields": fields,
            "processing_ms": 0,
        }

    first = service.import_quote(
        request_id,
        filename="\u4f9b\u5e94\u5546\u7532\u62a5\u4ef7.xlsx",
        data=b"quote-a",
        extracted=extracted("\u4f9b\u5e94\u5546\u7532", needs_review=True),
    )
    service.import_quote(
        request_id,
        filename="\u4f9b\u5e94\u5546\u4e59\u62a5\u4ef7.xlsx",
        data=b"quote-b",
        extracted=extracted("\u4f9b\u5e94\u5546\u4e59", needs_review=False),
    )
    assert service.get_request(request_id)["status"] == "review"
    assert service.get_request(request_id)["unresolved_field_count"] == 1

    service.correct_field(
        request_id,
        first["id"],
        field="supplier_name",
        value="\u4f9b\u5e94\u5546\u7532",
        actor="\u91c7\u8d2d\u5458",
    )

    detail = service.get_request(request_id)
    assert detail["status"] == "ready"
    assert detail["unresolved_field_count"] == 0
    harness.close()


def test_comparison_snapshot_and_audit_are_atomic(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "atomic-comparison-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    with harness.storage._lock:
        harness.storage._conn.execute(
            """CREATE TRIGGER fail_comparison_audit
               BEFORE INSERT ON procurement_audit_events
               WHEN NEW.type = 'comparison_created_by_agent'
               BEGIN
                   SELECT RAISE(ABORT, 'forced comparison audit failure');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced comparison audit failure"):
        service.compare_for_agent(str(request["id"]), run_id=run_id)

    stored = service.get_request(str(request["id"]))
    report = service.audit_report(str(request["id"]))
    harness.close()

    assert stored["status"] == "ready"
    assert stored["current_snapshot_id"] is None
    assert stored["comparison"] is None
    assert not any(
        event["type"] == "comparison_created_by_agent" for event in report["audit_events"]
    )


@pytest.mark.asyncio
async def test_cancelled_approval_request_cannot_commit_in_background(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "cancelled-approval-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    monkeypatch.setattr("agentharness.procurement.service._today", lambda: date(2026, 7, 27))
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    selected_id = str(snapshot["result"]["recommended_quote_id"])
    broker = SimpleNamespace(resolve=lambda *_args: None)
    agent = ProcurementAgent(harness, service, approval_broker=broker)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def delayed_resume(_run_id: str, *, input: str):
        del input
        started.set()
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="cancelled-http-approval",
            note=None,
            actor="采购员",
        )
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            error=None,
        )

    async def approval_ready(*_args):
        return SimpleNamespace(id="cancelled-http-approval")

    monkeypatch.setattr(harness, "resume", delayed_resume)
    monkeypatch.setattr(agent, "_wait_for_approval", approval_ready)
    approval_task = asyncio.create_task(
        agent.approve(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            note=None,
            actor="采购员",
        )
    )
    await started.wait()
    approval_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await approval_task
    await asyncio.sleep(0.1)

    stored = service.get_request(str(request["id"]))
    await agent.aclose()
    await harness.aclose()

    assert cancelled.is_set()
    assert stored["decision"] is None
    assert stored["status"] == "analyzed"


@pytest.mark.asyncio
async def test_complete_procurement_scenario_uses_one_run_and_four_model_turns(
    data_dir: Path,
    workspace: Path,
) -> None:
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            started = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                        "厚度公差3微米。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
            assert started.status_code == 202
            request_id = started.json()["purchase_request_id"]
            run_id = started.json()["run_id"]
            detail = await _wait_for_comparison(client, request_id, run_id=run_id)
            await _wait_for_run_status(client, run_id, {"require_human"})
            snapshot = detail["comparison"]
            approved = await client.post(
                f"/api/procurement/requests/{request_id}/decision",
                json={
                    "snapshot_id": snapshot["id"],
                    "input_sha256": snapshot["input_sha256"],
                    "quote_id": snapshot["result"]["recommended_quote_id"],
                    "confirmed": True,
                    "actor": "模型回合验收员",
                },
            )
            assert approved.status_code == 200, approved.text
            await _wait_for_run_status(client, run_id, {"completed"})
            runtime_report = (await client.get(f"/api/runs/{run_id}/report")).json()
            invocations = (
                await client.get(f"/api/runs/{run_id}/tool-invocations")
            ).json()
            audit = (
                await client.get(f"/api/procurement/requests/{request_id}/report")
            ).json()

            assert len(harness.list_runs()) == 1
            # 两阶段拆分后：capture -> execute_analysis -> 文本 -> approve = 4 轮
            assert runtime_report["usage"]["model_turns"] == 4
            assert {item["tool_name"] for item in invocations} == {
                "procurement_capture_requirement",
                "procurement_execute_analysis",
                "procurement_approve_supplier",
            }
            assert any(
                event["type"] == "deterministic_pipeline_completed"
                for event in audit["audit_events"]
            )
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


def test_procurement_requirement_normalizes_base_currency_rate(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("采购包装耗材")
    run_id = "normalization-test-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(draft["session_id"]),
        root_run_id=run_id,
    )
    service.capture_requirement(
        str(draft["id"]),
        {
            "title": "包装耗材询价",
            "item_name": "快递袋",
                "quantity": 10000,
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
                    "base_currency": "cny",
                    "fx_rates": {"USD/CNY": "7.2"},
                    "max_lead_days": 15,
                    "invoice_required": True,
                    "size_tolerance_mm": "2",
                    "thickness_tolerance_um": "3",
                },
        },
        run_id=run_id,
    )

    request = service.get_request(str(draft["id"]))
    state = service.agent_state(str(draft["id"]))
    harness.close()

    assert request["constraints"]["base_currency"] == "CNY"
    assert request["constraints"]["fx_rates"] == {"USD": "7.2", "CNY": "1"}
    assert state["request"]["quantity"] == 10000
    assert state["request"]["constraints"] == request["constraints"]


async def _wait_for_run_status(
    client: AsyncClient,
    run_id: str,
    expected: set[str],
    *,
    timeout_s: float = 5,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] in expected:
            return response.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {sorted(expected)}")


async def _wait_for_comparison(
    client: AsyncClient,
    request_id: str,
    *,
    run_id: str | None = None,
    timeout_s: float = 5,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/procurement/requests/{request_id}")
        if response.status_code == 200 and response.json().get("comparison") is not None:
            return response.json()
        if run_id:
            run = await client.get(f"/api/runs/{run_id}")
            if run.status_code == 200 and run.json()["status"] in {
                "failed",
                "cancelled",
                "interrupted",
            }:
                raise AssertionError(
                    f"run {run_id} stopped before comparison: {run.json().get('error')}"
                )
        await asyncio.sleep(0.01)
    detail = ""
    if run_id:
        run = await client.get(f"/api/runs/{run_id}")
        invocations = await client.get(f"/api/runs/{run_id}/tool-invocations")
        invocation_rows = invocations.json() if invocations.status_code == 200 else []
        last_result = invocation_rows[-1].get("result", {}) if invocation_rows else {}
        result_content = str(last_result.get("content") or "")[:500]
        try:
            result_payload = json.loads(str(last_result.get("content") or "{}"))
            result_content = str((result_payload.get("state") or {}).get("quotes"))
        except json.JSONDecodeError:
            pass
        detail = (
            f"; status={run.json().get('status') if run.status_code == 200 else run.status_code}"
            f"; tools={[item['tool_name'] for item in invocation_rows]}"
            f"; last_result={result_content}"
        )
    raise AssertionError(
        f"procurement request {request_id} did not produce a comparison{detail}"
    )


def test_frozen_procurement_evaluation_covers_real_exceptions() -> None:
    result = evaluate_frozen_cases()
    baseline = result["approaches"]["deterministic_baseline"]
    assisted = result["approaches"]["agent_assisted"]
    public_summary = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/evidence/evaluation-summary.json")
        .read_text(encoding="utf-8")
    )

    assert result["dataset"] == FROZEN_DATASET_NAME
    assert result["dataset_label"] == "电商包装耗材询价冻结集 v3"
    assert result["truth_sha256"] == FROZEN_TRUTH_SHA256
    assert result["case_count"] == MIN_FROZEN_CASES == 31
    assert f"{result['case_count']} 份合成" in result["limitations"][0]
    assert result["layout_coverage"]["count"] >= MIN_FROZEN_LAYOUTS
    assert result["anomaly_coverage"]["count"] >= 8
    assert result["metrics"]["field_extraction"]["accuracy"] >= 0.95
    assert result["metrics"]["item_matching"]["accuracy"] == 1
    assert result["metrics"]["cost_calculation"]["accuracy"] == 1
    assert result["metrics"]["hard_constraint_miss"]["miss_rate"] == 0
    assert result["metrics"]["recommendation_accuracy"]["rate"] == 1
    assert result["metrics"]["recommendation_consistency"]["rate"] == 1
    assert result["metrics"]["model_usage"]["estimated_cost_usd"] == 0
    assert result["metrics"]["incorrect_eligible_selection"]["count"] == 0
    assert result["metrics"]["manual_review"]["reviewed_fields"] >= 2
    assert baseline["metrics"]["risk_control"]["unresolved_eligible_quote_count"] >= 2
    assert baseline["metrics"]["recommendation_accuracy"]["rate"] == 0
    assert baseline["metrics"]["recommendation_consistency"]["rate"] == 1
    assert assisted["metrics"]["risk_control"]["unresolved_eligible_quote_count"] == 0
    assert all(result["acceptance"].values())
    assert recompute_approach_metrics(baseline["raw"]) == baseline["metrics"]
    assert recompute_approach_metrics(assisted["raw"]) == assisted["metrics"]
    assert result["approaches"]["human"]["status"] == "awaiting_observation"
    assert public_summary["dataset"] == result["dataset"]
    assert public_summary["truth_sha256"] == result["truth_sha256"]
    assert public_summary["case_count"] == result["case_count"]
    assert public_summary["layout_count"] == result["layout_coverage"]["count"]
    assert public_summary["anomaly_count"] == result["anomaly_coverage"]["count"]
    for metric in (
        "field_extraction",
        "post_review_fields",
        "item_matching",
        "cost_calculation",
        "hard_constraint_miss",
        "incorrect_eligible_selection",
        "recommendation_accuracy",
        "recommendation_consistency",
        "model_usage",
    ):
        assert public_summary["metrics"][metric] == result["metrics"][metric]
    assert public_summary["acceptance"] == result["acceptance"]


def test_evaluation_acceptance_gates_false_positive_quotes() -> None:
    """Regression: excluding eligible quotes must fail the acceptance gate.

    The frozen acceptance used to only check missed constraints and wrongly
    selected quotes; a rule regression that starts wrongly excluding eligible
    quotes stayed green. false_positive_count must be gated too.
    """
    from agentharness.procurement.evaluation import evaluation_acceptance

    metrics = {
        "field_extraction": {"accuracy": 1.0},
        "item_matching": {"accuracy": 1.0},
        "cost_calculation": {"accuracy": 1.0},
        "hard_constraint_miss": {"missed": 0, "false_positive_count": 1},
        "incorrect_eligible_selection": {"count": 0},
    }
    acceptance = evaluation_acceptance(
        case_count=31,
        layout_count=6,
        metrics=metrics,
    )
    assert acceptance["false_positive_quote_zero"] is False
    assert not all(acceptance.values())


def test_evaluation_verifier_accepts_current_frozen_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = tmp_path / "raw-results.json"
    raw_path.write_text(
        json.dumps(evaluate_frozen_cases(), ensure_ascii=False),
        encoding="utf-8",
    )

    evaluation_script.verify_evaluation(SimpleNamespace(input=raw_path))

    output = capsys.readouterr().out
    assert "原始评测结果复算通过" in output
    assert "case_count_at_least_31" in output


def test_each_frozen_layout_builds_deterministically_and_parses() -> None:
    truth = load_frozen_truth()
    by_layout = {case["layout"]: case for case in truth["quotes"]}

    assert len(by_layout) >= MIN_FROZEN_LAYOUTS
    for case in by_layout.values():
        for locale in ("en", "zh-CN"):
            first = build_case_document(case, locale=locale)
            second = build_case_document(case, locale=locale)
            assert first == second
            parsed = parse_quote(case["filename"], first)
            assert parsed["document_kind"] == case["kind"]
            assert parsed["fields"]["unit_price"]["value"] == case["fields"]["unit_price"]


@pytest.mark.asyncio
async def test_conversation_accepts_full_frozen_set_and_resumes_all_review_gaps(
    data_dir, workspace
) -> None:
    truth = load_frozen_truth()
    attachments = [_upload(case) for case in truth["quotes"]]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            meta = (await client.get("/api/procurement/meta")).json()
            assert meta["max_quotes_per_request"] == MAX_QUOTES_PER_REQUEST
            assert len(attachments) >= MIN_FROZEN_CASES

            too_many = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": "采购快递袋",
                    "attachments": attachments + attachments[:21],
                },
            )
            assert too_many.status_code == 422

            started = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个白色PE快递袋，规格250x350mm，厚度60微米，"
                        "单色印刷，15天内交付，要求开票，到货单价上限0.70元，"
                        "USD/CNY 7.2，EUR/CNY 7.8。"
                    ),
                    "attachments": attachments,
                    "actor": "盲测采购员",
                },
            )
            assert started.status_code == 202
            accepted = started.json()
            run_id = accepted["run_id"]
            request_id = accepted["purchase_request_id"]
            await _wait_for_run_status(client, run_id, {"require_human"}, timeout_s=15)

            paused = (await client.get(f"/api/procurement/requests/{request_id}")).json()
            assert paused["quote_count"] == len(attachments)
            assert paused["unresolved_field_count"] == 2
            assert paused["constraints"]["fx_rates"]["EUR"] == "7.8"
            assert paused["constraints"]["max_landed_unit_cost"] == "0.7"

            quotes_by_filename = {
                quote["source_filename"]: quote for quote in paused["quotes"]
            }
            theta = next(item for item in truth["quotes"] if item["id"] == "q-theta")
            psi = next(item for item in truth["quotes"] if item["id"] == "q-psi")
            corrected_supplier = await client.post(
                f"/api/procurement/requests/{request_id}/quotes/"
                f"{quotes_by_filename[theta['filename']]['id']}/corrections",
                json={
                    "field": "supplier_name",
                    "value": "Theta Packaging",
                    "actor": "盲测采购员",
                },
            )
            corrected_shipping = await client.post(
                f"/api/procurement/requests/{request_id}/quotes/"
                f"{quotes_by_filename[psi['filename']]['id']}/corrections",
                json={
                    "field": "shipping_fee",
                    "value": "650",
                    "actor": "盲测采购员",
                },
            )
            resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
            assert corrected_supplier.status_code == 200
            assert corrected_shipping.status_code == 200
            assert resumed.status_code == 202
            assert resumed.json()["run_id"] == run_id
            detail = await _wait_for_comparison(
                client, request_id, run_id=run_id, timeout_s=15
            )
            comparison = detail["comparison"]["result"]
            by_supplier = {item["supplier_name"]: item for item in comparison["quotes"]}
            recommended = next(
                item
                for item in comparison["quotes"]
                if item["quote_id"] == comparison["recommended_quote_id"]
            )
            assert recommended["supplier_name"] == "Alpha Packaging"
            assert by_supplier["Nu Trading"]["cost"]["landed_total_base"] == "5460.00"
            assert {item["code"] for item in by_supplier["Chi Materials"]["exclusion_reasons"]} == {
                "budget"
            }
            assert detail["unresolved_field_count"] == 0
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


def test_quote_parser_rejects_unsupported_and_oversized_inputs() -> None:
    with pytest.raises(QuoteParseError, match="仅支持"):
        parse_quote("quote.csv", b"supplier,price")
    with pytest.raises(QuoteParseError, match="不得超过"):
        parse_quote("quote.pdf", b"x" * (MAX_FILE_BYTES + 1))


def test_human_trial_metrics_are_recomputed_from_raw_observations() -> None:
    truth = load_frozen_truth()
    cases_by_id = {case["id"]: case for case in truth["quotes"]}
    cases = [cases_by_id[case_id] for case_id in evaluation_script.HUMAN_TRIAL_CASE_IDS]
    trial = {
        "truth_sha256": FROZEN_TRUTH_SHA256,
        "case_ids": list(evaluation_script.HUMAN_TRIAL_CASE_IDS),
        "active_time_seconds": 600,
        "rework_count": 2,
        "recommended_quote_id": truth["expected_recommended_quote_id"],
        "observations": [
            {
                "case_id": case["id"],
                "landed_total_base": case["expected_landed_total_base"],
                "item_match": case["expected_match"],
                "exclusion_codes": case["expected_exclusions"],
            }
            for case in cases
        ],
    }

    metrics = recompute_human_trial_metrics(trial)

    assert metrics["cost_calculation"]["accuracy"] == 1
    assert metrics["cost_calculation"]["total"] == len(
        evaluation_script.HUMAN_TRIAL_CASE_IDS
    )
    assert metrics["hard_constraint_miss"]["miss_rate"] == 0
    assert metrics["human_experiment"]["error_count"] == 0
    assert metrics["human_experiment"]["rework_count"] == 2
    assert metrics["processing"]["active_time_seconds"] == 600


def test_human_trial_manifest_rejects_changed_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        demo_script,
        "build_case_document",
        lambda case, *, locale: f"{locale}:{case['id']}".encode(),
    )
    demo_script.generate_demo(tmp_path)

    manifest, items, evidence = _load_manifest(tmp_path)

    assert manifest["清单版本"] == 3
    expected_count = len(load_frozen_truth()["quotes"])
    assert len(items) == expected_count
    assert len(evidence["quote_files"]) == expected_count
    selected_items, selected_evidence = evaluation_script._human_trial_inputs(items, evidence)
    trial_dir = evaluation_script._prepare_human_trial_view(
        tmp_path,
        tmp_path / "blind-input",
        selected_evidence,
    )
    assert [item["案例ID"] for item in selected_items] == list(
        evaluation_script.HUMAN_TRIAL_CASE_IDS
    )
    assert {path.name for path in trial_dir.iterdir()} == {
        selected_evidence["request_file"]["filename"],
        *(item["filename"] for item in selected_evidence["quote_files"]),
    }
    assert not (trial_dir / "manifest.json").exists()
    changed = tmp_path / str(items[0]["文件"])
    changed.write_bytes(changed.read_bytes() + b":changed")
    with pytest.raises(ValueError, match="SHA-256"):
        _load_manifest(tmp_path)


def test_assisted_trial_preflight_requires_an_empty_local_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = {
        "server_started_at": "2026-07-27T01:00:00+00:00",
        "backend_version": "0.3.0",
        "web_build_id": "build-id",
    }

    monkeypatch.setattr(
        evaluation_script,
        "_get_json",
        lambda _base_url, endpoint: health if endpoint == "/api/health" else [],
    )
    preflight = evaluation_script._prepare_assisted_trial("http://127.0.0.1:8766")
    assert preflight["server_started_at"] == health["server_started_at"]

    monkeypatch.setattr(
        evaluation_script,
        "_get_json",
        lambda _base_url, endpoint: (
            health if endpoint == "/api/health" else [{"id": "existing-request"}]
        ),
    )
    with pytest.raises(ValueError, match="空白环境"):
        evaluation_script._prepare_assisted_trial("http://127.0.0.1:8766")


def test_workflow_runtime_usage_uses_model_turns() -> None:
    assert evaluation_script._workflow_runtime_usage(
        {
            "usage": {
                "model_turns": 5,
                "total_tokens": 212,
                "estimated_cost_usd": 0,
            }
        }
    ) == {
        "model_turns": 5,
        "model_tokens": 212,
        "model_cost_usd": 0,
    }


def test_assisted_trial_selection_is_derived_from_audited_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_evidence = {
        "manifest_sha256": "manifest-sha",
        "request_file": {"filename": "request.json", "sha256": "request-sha"},
        "quote_files": [
            {
                "case_id": "q-alpha",
                "filename": "alpha.xlsx",
                "sha256": "alpha-sha",
            },
            {
                "case_id": "q-beta",
                "filename": "beta.pdf",
                "sha256": "beta-sha",
            },
        ],
    }
    health = {
        "server_started_at": "2026-07-27T00:30:00+00:00",
        "backend_version": "0.3.0",
        "web_build_id": "build-id",
    }
    decision = {
        "id": "decision-1",
        "quote_id": "quote-alpha",
        "run_id": "run-1",
        "snapshot_id": "snapshot-1",
        "approval_id": "approval-1",
        "decision": "approved",
        "created_at": "2026-07-27T01:08:00+00:00",
    }
    detail = {
        "id": "request-1",
        "reference": "RFQ-TEST-001",
        "session_id": "session-1",
        "analysis_run_id": "run-1",
        "current_snapshot_id": "snapshot-1",
        "approved_quote_id": "quote-alpha",
        "status": "approved",
        "created_at": "2026-07-27T01:01:00+00:00",
        "unresolved_field_count": 0,
        "decision": decision,
        "quotes": [
            {
                "id": "quote-alpha",
                "supplier_name": "Alpha",
                "source_filename": "alpha.xlsx",
                "source_sha256": "alpha-sha",
            },
            {
                "id": "quote-beta",
                "supplier_name": "Beta",
                "source_filename": "beta.pdf",
                "source_sha256": "beta-sha",
            },
        ],
    }
    report = {
        "request": {"id": "request-1"},
        "comparison": {"id": "snapshot-1"},
        "decision": decision,
    }
    report["evidence_sha256"] = evaluation_script._canonical_sha256(report)
    required_tools = (
        "procurement_capture_requirement",
        "procurement_execute_analysis",
        "procurement_approve_supplier",
    )
    runtime_report = {
        "run": {"status": "completed"},
        "conclusion": {"status": "passed"},
        "usage": {
            "model_turns": 6,
            "total_tokens": 320,
            "estimated_cost_usd": 0,
            "provider_attempts": [
                {
                    "provider": "procurement_fake",
                    "model": "procurement-fake-v1",
                }
            ],
        },
        "tools": [
            {"tool_name": name, "status": "succeeded"} for name in required_tools
        ],
        "approvals": [{"id": "approval-1", "decision": "allow_once"}],
    }
    runtime_report["evidence_sha256"] = evaluation_script._canonical_sha256(runtime_report)
    checkpoint = {
        "run_id": "run-1",
        "phase": "terminal",
        "status": "completed",
        "messages": [
            {
                "role": "tool",
                "name": "procurement_approve_supplier",
                "tool_result": {"is_error": False},
            }
        ],
    }
    responses = {
        "/api/health": health,
        "/api/procurement/requests?limit=200": [{"id": "request-1"}],
        "/api/procurement/requests/request-1": detail,
        "/api/procurement/requests/request-1/report": report,
        "/api/runs/run-1/report": runtime_report,
        "/api/runs/run-1/checkpoint": checkpoint,
    }
    monkeypatch.setattr(
        evaluation_script,
        "_get_json",
        lambda _base_url, endpoint: responses[endpoint],
    )

    evidence = evaluation_script._capture_assisted_trial_evidence(
        base_url="http://127.0.0.1:8766",
        input_evidence=input_evidence,
        preflight={"server_started_at": health["server_started_at"]},
        started_at=evaluation_script.datetime.fromisoformat(
            "2026-07-27T01:00:00+00:00"
        ),
        finished_at=evaluation_script.datetime.fromisoformat(
            "2026-07-27T01:10:00+00:00"
        ),
    )

    assert evidence["summary"]["selected_case_id"] == "q-alpha"
    assert evidence["summary"]["run_id"] == "run-1"
    assert all(evidence["checks"].values())
    tampered = json.loads(json.dumps(evidence))
    tampered["summary"]["selected_case_id"] = "q-beta"
    with pytest.raises(ValueError, match="指纹校验失败"):
        evaluation_script._validate_assisted_trial_evidence(tampered, input_evidence)


def test_controlled_experiment_requires_same_observer_and_input() -> None:
    truth = load_frozen_truth()
    cases_by_id = {case["id"]: case for case in truth["quotes"]}
    cases = [cases_by_id[case_id] for case_id in evaluation_script.HUMAN_TRIAL_CASE_IDS]
    input_evidence = {
        "manifest_sha256": "manifest-sha",
        "request_file": {"filename": "request.json", "sha256": "request-sha"},
        "quote_files": [
            {
                "case_id": case["id"],
                "filename": case["filename"],
                "sha256": f"sha-{case['id']}",
            }
            for case in cases
        ],
    }
    manual = {
        "schema_version": evaluation_script.TRIAL_SCHEMA_VERSION,
        "mode": "manual",
        "dataset": FROZEN_DATASET_NAME,
        "truth_sha256": FROZEN_TRUTH_SHA256,
        "case_ids": list(evaluation_script.HUMAN_TRIAL_CASE_IDS),
        "input_evidence": input_evidence,
        "observer": "匿名测试员-01",
        "blind_confirmed": True,
        "started_at": "2026-07-27T01:00:00+00:00",
        "finished_at": "2026-07-27T01:10:00+00:00",
        "active_time_seconds": 600,
        "rework_count": 1,
        "recommended_quote_id": truth["expected_recommended_quote_id"],
        "observations": [
            {
                "case_id": case["id"],
                "landed_total_base": case["expected_landed_total_base"],
                "item_match": case["expected_match"],
                "exclusion_codes": case["expected_exclusions"],
            }
            for case in cases
        ],
    }
    assisted = {
        "schema_version": evaluation_script.TRIAL_SCHEMA_VERSION,
        "mode": "assisted",
        "dataset": FROZEN_DATASET_NAME,
        "truth_sha256": FROZEN_TRUTH_SHA256,
        "case_ids": list(evaluation_script.HUMAN_TRIAL_CASE_IDS),
        "input_evidence": input_evidence,
        "observer": "匿名测试员-01",
        "blind_confirmed": True,
        "started_at": "2026-07-27T02:00:00+00:00",
        "finished_at": "2026-07-27T02:05:00+00:00",
        "active_time_seconds": 300,
        "rework_count": 0,
        "reported_error_count": 0,
        "recommended_quote_id": truth["expected_recommended_quote_id"],
        "observations": [],
    }
    assisted_evidence = {
        "schema_version": 1,
        "input_evidence_sha256": evaluation_script._canonical_sha256(input_evidence),
        "summary": {
            "selected_case_id": truth["expected_recommended_quote_id"],
            "reference": "RFQ-TEST-001",
            "run_id": "run-1",
        },
        "checks": {"audited_approval": True},
    }
    assisted_evidence["evidence_sha256"] = evaluation_script._canonical_sha256(
        assisted_evidence
    )
    assisted["assisted_evidence"] = assisted_evidence

    result = _controlled_experiment(manual, assisted)

    assert result["status"] == "completed"
    assert result["metrics"]["active_time_reduction_rate"] == 0.5
    with pytest.raises(ValueError, match="同一测试员"):
        _controlled_experiment(manual, {**assisted, "observer": "匿名测试员-02"})
    with pytest.raises(ValueError, match="未确认测试员"):
        _controlled_experiment(manual, {**assisted, "blind_confirmed": False})
    different_input = {
        **input_evidence,
        "manifest_sha256": "different-manifest-sha",
    }
    with pytest.raises(ValueError, match="内容指纹完全一致"):
        _controlled_experiment(
            manual,
            {**assisted, "input_evidence": different_input},
        )
    assisted_first = {
        **assisted,
        "started_at": "2026-07-27T00:30:00+00:00",
        "finished_at": "2026-07-27T00:45:00+00:00",
    }
    with pytest.raises(ValueError, match="必须先完成纯人工实验"):
        _controlled_experiment(manual, assisted_first)
    tampered_evidence = json.loads(json.dumps(assisted_evidence))
    tampered_evidence["summary"]["selected_case_id"] = "q-beta"
    with pytest.raises(ValueError, match="指纹校验失败"):
        _controlled_experiment(
            manual,
            {**assisted, "assisted_evidence": tampered_evidence},
        )


@pytest.mark.asyncio
async def test_procurement_conversation_uses_harness_and_pauses_for_quote_review(
    data_dir,
    workspace,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/procurement/conversations",
            json={
                "message": (
                    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                    "厚度公差3微米。请比较附件中的三家报价并推荐供应商。"
                ),
                "attachments": [_upload(case) for case in cases],
            },
        )

        assert started.status_code == 202
        accepted = started.json()
        assert accepted["purchase_request_id"]
        assert accepted["session_id"]
        assert accepted["run_id"]

        run = await _wait_for_run_status(client, accepted["run_id"], {"require_human"})
        detail = (
            await client.get(
                f"/api/procurement/requests/{accepted['purchase_request_id']}"
            )
        ).json()
        checkpoint = (await client.get(f"/api/runs/{accepted['run_id']}/checkpoint")).json()
        invocations = (
            await client.get(f"/api/runs/{accepted['run_id']}/tool-invocations")
        ).json()

        assert run["session_id"] == accepted["session_id"] == detail["session_id"]
        assert detail["id"] == accepted["purchase_request_id"]
        assert detail["analysis_run_id"] == accepted["run_id"]
        assert detail["quote_count"] == 3
        assert detail["unresolved_field_count"] == 1
        assert detail["comparison"] is None
        assert checkpoint["status"] == "require_human"
        # capture 只保存需求，execute_analysis 执行比价并停在待复核门禁
        assert [item["tool_name"] for item in invocations] == [
            "procurement_capture_requirement",
            "procurement_execute_analysis",
        ]

    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_human_review_resumes_same_run_and_builds_comparison(
    data_dir,
    workspace,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = (
            await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                        "厚度公差3微米。请比较附件中的三家报价并推荐供应商。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
        ).json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})

        pending = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        theta_quote = next(
            quote
            for quote in pending["quotes"]
            if quote["source_filename"] == cases[2]["filename"]
        )
        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{theta_quote['id']}/corrections",
            json={
                "field": "supplier_name",
                "value": "Theta Packaging",
                "actor": "采购员",
            },
        )
        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")

        assert corrected.status_code == 200
        assert corrected.json()["review_fields"] == []
        assert resumed.status_code == 202
        assert resumed.json()["run_id"] == run_id
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        run = await _wait_for_run_status(client, run_id, {"require_human"})
        invocations = (
            await client.get(f"/api/runs/{run_id}/tool-invocations")
        ).json()
        checkpoint = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()

        alpha_quote = next(
            quote for quote in detail["quotes"] if quote["source_filename"] == cases[0]["filename"]
        )
        assert run["id"] == detail["analysis_run_id"] == run_id
        assert detail["unresolved_field_count"] == 0
        assert detail["comparison"]["result"]["recommended_quote_id"] == alpha_quote["id"]
        assert checkpoint["status"] == "require_human"
        actual_tools = {item["tool_name"] for item in invocations}
        assert actual_tools == {
            "procurement_capture_requirement",
            "procurement_execute_analysis",
        }
        assert "procurement_correct_quote" not in actual_tools
        usage = json.loads(run["usage_json"])
        # 初始 capture+execute+文本=3，人工修正后 analyze 重跑 execute=2，合计 5
        assert usage["model_turns"] <= 6

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()

@pytest.mark.asyncio
async def test_procurement_supplier_decision_is_a_harness_approval(
    data_dir,
    workspace,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = (
            await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                        "厚度公差3微米。请比较附件中的三家报价并推荐供应商。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
        ).json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})
        pending = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        theta_quote = next(
            quote
            for quote in pending["quotes"]
            if quote["source_filename"] == cases[2]["filename"]
        )
        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{theta_quote['id']}/corrections",
            json={
                "field": "supplier_name",
                "value": "Theta Packaging",
                "actor": "采购员王敏",
            },
        )
        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert corrected.status_code == 200
        assert resumed.status_code == 202
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})
        snapshot = detail["comparison"]
        selected_quote_id = snapshot["result"]["recommended_quote_id"]

        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": selected_quote_id,
                "confirmed": True,
                "note": "已核对报价原件、硬性条件与到货成本",
                "actor": "采购员王敏",
            },
        )

        assert approved.status_code == 200
        completed = await _wait_for_run_status(client, run_id, {"completed"})
        approvals = (await client.get(f"/api/runs/{run_id}/approvals")).json()
        invocations = (
            await client.get(f"/api/runs/{run_id}/tool-invocations")
        ).json()
        checkpoint = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()
        report = (await client.get(f"/api/procurement/requests/{request_id}/report")).json()

        assert completed["id"] == run_id
        assert approved.json()["decision"]["quote_id"] == selected_quote_id
        assert approvals[-1]["tool_name"] == "procurement_approve_supplier"
        assert approvals[-1]["decision"] == "allow_once"
        assert approvals[-1]["status"] == "resolved"
        approval_tool = next(
            item for item in invocations if item["tool_name"] == "procurement_approve_supplier"
        )
        assert approval_tool["status"] == "succeeded"
        assert checkpoint["status"] == "completed"
        assert report["decision"]["approval_id"] == approvals[-1]["id"]
        assert report["runtime"]["run_id"] == run_id
        usage = json.loads(completed["usage_json"])
        # 初始 capture+execute+文本=3，修正后 execute=1，analyze 重跑 execute=1，审批=1 → 6
        assert usage["model_turns"] <= 7

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()

    restored = Harness(data_dir=data_dir)
    restored_app = create_app(harness=restored, workspace_roots=[workspace])
    restored_transport = ASGITransport(app=restored_app)
    async with AsyncClient(
        transport=restored_transport,
        base_url="http://test",
    ) as restored_client:
        restored_detail = (
            await restored_client.get(f"/api/procurement/requests/{request_id}")
        ).json()
        restored_report = (
            await restored_client.get(f"/api/procurement/requests/{request_id}/report")
        ).json()
        restored_run = (await restored_client.get(f"/api/runs/{run_id}")).json()
        restored_checkpoint = (
            await restored_client.get(f"/api/runs/{run_id}/checkpoint")
        ).json()

        assert restored_detail["status"] == "approved"
        assert restored_detail["decision"] == approved.json()["decision"]
        assert restored_report["evidence_sha256"] == report["evidence_sha256"]
        assert restored_run["status"] == "completed"
        assert restored_checkpoint["status"] == "completed"

    await restored_app.state.procurement_agent.aclose()
    await restored_app.state.run_supervisor.aclose()
    await restored.aclose()


@pytest.mark.asyncio
async def test_procurement_clarification_resumes_after_process_restart(
    data_dir,
    workspace,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = (
            await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                        "厚度公差3微米。请比较附件中的三家报价并推荐供应商。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
        ).json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})
        checkpoint_before = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()
        assert checkpoint_before["status"] == "require_human"

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()

    restored = Harness(data_dir=data_dir)
    restored_app = create_app(harness=restored, workspace_roots=[workspace])
    restored_transport = ASGITransport(app=restored_app)
    async with AsyncClient(
        transport=restored_transport,
        base_url="http://test",
    ) as client:
        restored_detail = (
            await client.get(f"/api/procurement/requests/{request_id}")
        ).json()
        assert restored_detail["analysis_run_id"] == run_id
        assert restored_detail["unresolved_field_count"] == 1

        theta_quote = next(
            quote
            for quote in restored_detail["quotes"]
            if quote["source_filename"] == cases[2]["filename"]
        )
        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{theta_quote['id']}/corrections",
            json={
                "field": "supplier_name",
                "value": "Theta Packaging",
                "actor": "采购员王敏",
            },
        )
        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert corrected.status_code == 200
        assert resumed.status_code == 202
        assert resumed.json()["run_id"] == run_id
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})
        snapshot = detail["comparison"]

        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": snapshot["result"]["recommended_quote_id"],
                "confirmed": True,
                "actor": "采购员王敏",
            },
        )
        assert approved.status_code == 200
        await _wait_for_run_status(client, run_id, {"completed"})
        checkpoint_after = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()
        assert checkpoint_after["status"] == "completed"

    await restored_app.state.procurement_agent.aclose()
    await restored_app.state.run_supervisor.aclose()
    await restored.aclose()


@pytest.mark.asyncio
async def test_procurement_quote_change_invalidates_stale_snapshot(
    data_dir,
    workspace,
) -> None:
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = (
            await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                        "厚度公差3微米。请比较附件中的三家报价并推荐供应商。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
        ).json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})
        pending = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        theta_quote = next(
            quote
            for quote in pending["quotes"]
            if quote["source_filename"] == cases[2]["filename"]
        )
        corrected_theta = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{theta_quote['id']}/corrections",
            json={
                "field": "supplier_name",
                "value": "Theta Packaging",
                "actor": "采购员王敏",
            },
        )
        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert corrected_theta.status_code == 200
        assert resumed.status_code == 202
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})
        old_snapshot = detail["comparison"]
        alpha_quote = next(
            quote for quote in detail["quotes"] if quote["source_filename"] == cases[0]["filename"]
        )

        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{alpha_quote['id']}/corrections",
            json={"field": "shipping_fee", "value": "25", "actor": "采购员王敏"},
        )
        assert corrected.status_code == 200
        invalidated = (
            await client.get(f"/api/procurement/requests/{request_id}")
        ).json()
        checkpoint = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()
        approvals = (await client.get(f"/api/runs/{run_id}/approvals")).json()
        report = (await client.get(f"/api/procurement/requests/{request_id}/report")).json()

        assert invalidated["status"] == "ready"
        assert invalidated["current_snapshot_id"] is None
        assert invalidated["comparison"] is None
        assert checkpoint["status"] == "require_human"
        assert "superseded_reason" not in checkpoint["metadata"]
        assert approvals == []
        invalidation = next(
            event for event in report["audit_events"] if event["type"] == "comparison_superseded"
        )
        assert invalidation["payload"]["snapshot_id"] == old_snapshot["id"]

        stale = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": old_snapshot["id"],
                "input_sha256": old_snapshot["input_sha256"],
                "quote_id": old_snapshot["result"]["recommended_quote_id"],
                "confirmed": True,
                "actor": "采购员王敏",
            },
        )
        assert stale.status_code == 409

        reanalyzed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert reanalyzed.status_code == 202
        assert reanalyzed.json()["run_id"] == run_id
        refreshed = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})
        new_snapshot = refreshed["comparison"]
        assert new_snapshot["version"] == old_snapshot["version"] + 1
        assert new_snapshot["id"] != old_snapshot["id"]
        assert new_snapshot["input_sha256"] != old_snapshot["input_sha256"]

        stale_after_reanalysis = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": old_snapshot["id"],
                "input_sha256": old_snapshot["input_sha256"],
                "quote_id": old_snapshot["result"]["recommended_quote_id"],
                "confirmed": True,
                "actor": "采购员王敏",
            },
        )
        assert stale_after_reanalysis.status_code == 409
        assert (await client.get(f"/api/runs/{run_id}/approvals")).json() == []

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


class _RefusingResumeProvider(ProcurementFakeProvider):
    """Fake provider that refuses to repeat the analysis tool on resume,
    mimicking real-model behavior observed after a correction invalidated
    the comparison snapshot."""

    async def stream(self, request: ModelRequest):
        user_text = "".join(
            message.content
            for message in request.messages
            if message.role == MessageRole.user
        )
        if (
            sum(1 for m in request.messages if m.role == MessageRole.user) > 1
            and "[procurement_supplier_selection]" not in user_text
        ):
            async for item in self._text(
                "比价此前已执行完毕且复算通过，本轮不重复调用分析工具。"
            ):
                yield item
            return
        async for item in super().stream(request):
            yield item


@pytest.mark.asyncio
async def test_reanalysis_after_correction_is_deterministic_when_model_refuses(
    data_dir, workspace
) -> None:
    """开始比价 must regenerate the comparison even when the resumed model
    refuses to repeat the analysis tool (real-model behavior observed after a
    correction invalidated the snapshot)."""
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    harness = Harness(
        data_dir=data_dir,
        providers={"procurement_fake": _RefusingResumeProvider()},
    )
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/procurement/conversations",
            json={
                "message": (
                    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                    "厚度公差3微米。请比较附件报价并推荐供应商。"
                ),
                "attachments": [_upload(case) for case in cases],
            },
        )
        assert started.status_code == 202
        accepted = started.json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})
        detail = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        old_snapshot = detail["comparison"]
        alpha = next(
            item
            for item in detail["quotes"]
            if item["source_filename"] == cases[0]["filename"]
        )
        service = app.state.procurement_service
        assert service.agent_state(request_id)["requires_reanalysis"] is False

        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{alpha['id']}/corrections",
            json={"field": "shipping_fee", "value": "25", "actor": "采购员王敏"},
        )
        assert corrected.status_code == 200
        invalidated = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        assert invalidated["status"] == "ready"
        assert invalidated["current_snapshot_id"] is None
        assert service.agent_state(request_id)["requires_reanalysis"] is True

        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert resumed.status_code == 202
        assert resumed.json()["run_id"] == run_id

        refreshed = await _wait_for_comparison(
            client, request_id, run_id=run_id, timeout_s=10
        )
        await _wait_for_run_status(client, run_id, {"require_human"})
        new_snapshot = refreshed["comparison"]
        assert refreshed["status"] == "analyzed"
        assert new_snapshot["version"] == old_snapshot["version"] + 1
        assert new_snapshot["id"] != old_snapshot["id"]
        assert new_snapshot["input_sha256"] != old_snapshot["input_sha256"]
        # The deterministic fallback must publish a refresh event so the web UI
        # notices the new snapshot without an extra model turn.
        events = (await client.get(f"/api/runs/{run_id}/events?limit=2000")).json()["items"]
        assert any(
            event["type"] == "run_status"
            and event["payload"].get("reason") == "比价快照已重新生成，等待人工选择供应商"
            for event in events
        )
    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


@pytest.mark.asyncio
async def test_procurement_flow_requires_review_and_survives_restart(data_dir, workspace) -> None:
    truth = load_frozen_truth()
    alpha = next(item for item in truth["quotes"] if item["id"] == "q-alpha")
    theta = next(item for item in truth["quotes"] if item["id"] == "q-theta")
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/procurement/requests", json=_request_body(truth))
        assert created.status_code == 201
        request_id = created.json()["id"]

        first = await client.post(
            f"/api/procurement/requests/{request_id}/quotes", json=_upload(alpha)
        )
        second = await client.post(
            f"/api/procurement/requests/{request_id}/quotes", json=_upload(theta)
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["review_fields"] == ["supplier_name"]
        raw_source = await client.get(f"/api/artifacts/{second.json()['source_artifact_id']}/raw")
        assert raw_source.status_code == 200
        assert raw_source.headers["content-type"] == "application/pdf"
        assert raw_source.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(raw_source.content).hexdigest() == second.json()["source_sha256"]

        blocked = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert blocked.status_code == 409
        assert "低置信度" in blocked.json()["detail"]

        quote_id = second.json()["id"]
        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{quote_id}/corrections",
            json={"field": "supplier_name", "value": "Theta Packaging", "actor": "采购员王敏"},
        )
        assert corrected.status_code == 200
        assert corrected.json()["review_fields"] == []
        supplier_field = corrected.json()["extracted"]["fields"]["supplier_name"]
        assert supplier_field["status"] == "corrected"
        assert supplier_field["original_value"] == "Theta Packaging"

        analyzed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert analyzed.status_code == 202
        accepted = analyzed.json()
        run_id = accepted["run_id"]
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})
        assert detail["status"] == "analyzed"
        assert detail["comparison"]["result"]["recommended_quote_id"] == first.json()["id"]
        assert detail["comparison"]["result"]["quotes"][0]["cost"]["landed_total_base"]
        assert detail["analysis_run_id"] == run_id
        snapshot_id = detail["current_snapshot_id"]
        input_sha = detail["comparison"]["input_sha256"]

        checkpoint = (await client.get(f"/api/runs/{run_id}/checkpoint")).json()
        runtime_report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert checkpoint["status"] == "require_human"
        assert runtime_report["conclusion"]["status"] == "needs_review"
        assert runtime_report["source"]["artifact_count"] >= 2
        assert runtime_report["usage"]["estimated_cost_usd"] == 0

        # Repeated analysis is idempotent while the current comparison is valid.
        reanalyzed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert reanalyzed.status_code == 202
        assert reanalyzed.json()["run_id"] == run_id

        unconfirmed = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot_id,
                "input_sha256": input_sha,
                "quote_id": first.json()["id"],
                "confirmed": False,
                "actor": "采购员王敏",
            },
        )
        assert unconfirmed.status_code == 409

        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot_id,
                "input_sha256": input_sha,
                "quote_id": first.json()["id"],
                "confirmed": True,
                "note": "成本及交期符合本次采购计划",
                "actor": "采购员王敏",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        await _wait_for_run_status(client, run_id, {"completed"})
        before = (await client.get(f"/api/procurement/requests/{request_id}/report")).json()
        runtime_after = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert before["decision"]["decision"] == "approved"
        assert runtime_after["conclusion"]["status"] == "passed"
        assert runtime_after["approvals"][-1]["decision"] == "allow_once"
        assert any(event["type"] == "supplier_approved" for event in before["audit_events"])

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()

    restored = Harness(data_dir=data_dir)
    restored_app = create_app(harness=restored, workspace_roots=[workspace])
    restored_transport = ASGITransport(app=restored_app)
    async with AsyncClient(transport=restored_transport, base_url="http://test") as restored_client:
        after = (await restored_client.get(f"/api/procurement/requests/{request_id}/report")).json()
        restored_detail = (
            await restored_client.get(f"/api/procurement/requests/{request_id}")
        ).json()
        assert after["evidence_sha256"] == before["evidence_sha256"]
        assert after["decision"] == before["decision"]
        assert restored_detail["status"] == "approved"
        assert restored_detail["comparison"]["input_sha256"] == input_sha

    await restored_app.state.procurement_agent.aclose()
    await restored_app.state.run_supervisor.aclose()
    await restored.aclose()


class _WrongToolFirstProvider(ProcurementFakeProvider):
    """Deviant offline provider that deliberately calls the gated analysis tool first.

    Mirrors the real-model failure in evidence run 1f9ebe (proc-review-runtime),
    where a fresh conversation jumped straight to ``procurement_execute_analysis`` on
    a draft request. The tool is in the run allowlist, so only tool_prerequisites
    gating can stop it — the model never calls it again by itself.
    """

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        request_id = self._request_id(request.system or "")
        if not any(message.role == MessageRole.tool for message in request.messages):
            async for item in self._tool_call(
                "procurement_execute_analysis", {"request_id": request_id}
            ):
                yield item
            return
        async for item in ProcurementFakeProvider.stream(self, request):
            yield item


@pytest.mark.asyncio
async def test_gated_analysis_tool_blocked_before_capture(data_dir: Path) -> None:
    """Even a provider that picks the gated tool first must be blocked with a hint.

    ``procurement_execute_analysis`` is present in the run's tool allowlist, yet it
    must not run before ``procurement_capture_requirement`` succeeds. The run then
    reaches the safe ``require_human`` terminal state instead of hanging.
    """
    truth = load_frozen_truth()
    alpha = next(item for item in truth["quotes"] if item["id"] == "q-alpha")
    theta = next(item for item in truth["quotes"] if item["id"] == "q-theta")
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    # Register the deviant provider first so ProcurementAgent keeps it.
    harness.register_provider(PROCUREMENT_PROVIDER, _WrongToolFirstProvider())
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(
            message=(
                "华东仓采购 1000000 个 PE 快递袋，尺寸 250x350 毫米，厚 60 微米，"
                "PE 材质，白色，单色印刷。"
            ),
            attachments=[
                (alpha["filename"], build_case_document(alpha)),
                (theta["filename"], build_case_document(theta)),
            ],
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        assert [item.tool_name for item in invocations] == [
            "procurement_execute_analysis"
        ]
        blocked = invocations[0]
        assert blocked.status == ToolInvocationStatus.failed
        assert blocked.error_code == "tool_disabled"
        assert blocked.result is not None
        assert (
            "Complete the required earlier tool successfully"
            in blocked.result.recovery_hint
        )
        assert blocked.result.retryable

        # The gated attempt is the only invocation; capture never ran.
        assert not any(
            item.tool_name == "procurement_capture_requirement"
            for item in invocations
        )
        run = harness.get_run(run_id)
        assert run["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_failed_capture_terminates_at_require_human(data_dir: Path) -> None:
    """A failed requirement capture is a safe, human-gated exit, not a hang.

    The stock fake extractor yields ``width=0`` for natural-language dimensions, so
    ``procurement_capture_requirement`` fails validation. The run must finish
    ``require_human`` and any resume must terminate the same way instead of looping.
    """
    truth = load_frozen_truth()
    alpha = next(item for item in truth["quotes"] if item["id"] == "q-alpha")
    theta = next(item for item in truth["quotes"] if item["id"] == "q-theta")
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start(
            message=(
                "华东仓需要采购 100 万个 PE 快递袋。规格：宽 250 毫米、长 350 毫米、"
                "厚 60 微米，PE 材质，白色，单色印刷，15 天内交货。"
            ),
            attachments=[
                (alpha["filename"], build_case_document(alpha)),
                (theta["filename"], build_case_document(theta)),
            ],
        )
        run_id = accepted["run_id"]
        await agent._tasks[run_id]

        invocations = harness.storage.list_tool_invocations(run_id)
        assert [item.tool_name for item in invocations] == [
            "procurement_capture_requirement"
        ]
        failed = invocations[0]
        assert failed.status == ToolInvocationStatus.failed
        assert failed.error_code == "tool_exception"
        assert failed.result is not None
        assert failed.result.retryable is False
        # Natural-language dimensions are not extracted, so the captured width is 0
        # and fails the domain's exclusive-minimum validation.
        assert "宽度" in failed.result.content
        # 两阶段失败分离：校验失败必须带字段级原因与可操作修正提示
        assert "需求结构化校验失败" in failed.result.content
        assert "请修正" in failed.result.content

        run = harness.get_run(run_id)
        assert run["status"] == "require_human"
        assert run["error"] and "verification requires human review" in run["error"]

        # Repeated resume must reach the same safe terminal state, never hang or loop.
        for _ in range(2):
            accepted = await agent.resume(
                accepted["purchase_request_id"], message="请继续。"
            )
            await agent._tasks[accepted["run_id"]]
            run = harness.get_run(accepted["run_id"])
            assert run["status"] == "require_human"
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_cancel_run_guards_terminal_runs(data_dir, workspace) -> None:
    """The cancel-run endpoint only stops active runs; terminal runs are rejected."""
    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-theta")
    ]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = (
            await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "华东仓采购 10000 个 PE 快递袋，尺寸 250x350 毫米，厚 60 微米，"
                        "PE 材质，白色，单色印刷。"
                    ),
                    "attachments": [_upload(case) for case in cases],
                },
            )
        ).json()
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})

        # A run waiting for human review is not cancelable.
        blocked = await client.post(
            f"/api/procurement/requests/{accepted['purchase_request_id']}/cancel-run"
        )
        assert blocked.status_code == 409
        assert "不可停止" in blocked.json()["detail"]

    await app.state.run_supervisor.aclose()
    await harness.aclose()

@pytest.mark.asyncio
async def test_failed_conversation_recovers_via_start_existing(
    data_dir: Path,
    workspace: Path,
) -> None:
    """A conversation whose run fails at step 0 (before parsing quotes) must be
    recoverable through "重新分析" (start_existing): the staged attachments are
    re-parsed by a conversation-style relaunch instead of the strict
    "至少上传 2 家供应商报价后才能比价" error.
    """

    class FailOnceProvider(ProcurementFakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def stream(self, request: ModelRequest):
            self.calls += 1
            if self.calls == 1:
                yield ModelStreamItem(
                    type=StreamItemType.error,
                    error="injected provider failure",
                    error_kind="provider",
                )
                return
            async for item in super().stream(request):
                yield item

    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    provider = FailOnceProvider()
    harness = Harness(data_dir=data_dir, providers={"procurement_fake": provider})
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service)
    try:
        started = await agent.start(
            message=(
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                "厚度公差3微米。请比较附件报价并推荐供应商。"
            ),
            attachments=[(case["filename"], build_case_document(case)) for case in cases],
        )
        request_id = started["purchase_request_id"]
        first_run_id = started["run_id"]

        # Wait for the injected step-0 provider failure.
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            run = harness.get_run(first_run_id)
            if run is not None and run["status"] == "failed":
                break
            await asyncio.sleep(0.02)
        assert run is not None
        assert run["status"] == "failed"
        assert "injected provider failure" in str(run["error"])
        detail = service.get_request(request_id)
        assert detail["quote_count"] == 0

        # "重新分析" must recover instead of refusing.
        accepted = await agent.start_existing(request_id)
        new_run_id = accepted["run_id"]
        assert new_run_id != first_run_id

        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            detail = service.get_request(request_id)
            if detail["quote_count"] >= 2:
                break
            await asyncio.sleep(0.02)
        assert detail["quote_count"] >= 2
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            recovered = harness.get_run(new_run_id)
            if recovered is not None and recovered["status"] in {
                "require_human",
                "completed",
            }:
                break
            await asyncio.sleep(0.02)
        assert recovered is not None
        # 星河包装 supplier name needs human review -> safe human boundary.
        assert recovered["status"] in {"require_human", "completed"}
        assert provider.calls >= 2
    finally:
        await agent.aclose()
        harness.close()

@pytest.mark.asyncio
async def test_approval_tolerates_model_invented_actor_and_note(
    data_dir: Path,
    workspace: Path,
) -> None:
    """A live model may fill its own actor/note in the approve tool call while
    keeping the decision-critical fields (request/snapshot/input-hash/quote)
    identical to the buyer's selection. The human approval must still succeed
    instead of failing with '采购审批参数与用户选择不一致', AND the formal
    decision record must keep the buyer's actor/note (model values must not
    pollute the audit trail)."""

    class MeddlingProvider(ProcurementFakeProvider):
        async def _tool_call(self, name, arguments):
            if name == "procurement_approve_supplier":
                arguments = {
                    **arguments,
                    "actor": "采购决策Agent",
                    "note": "模型自写备注：" + "长" * 600,
                }
            async for item in super()._tool_call(name, arguments):
                yield item

    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta", "q-theta")
    ]
    harness = Harness(data_dir=data_dir, providers={"procurement_fake": MeddlingProvider()})
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/procurement/conversations",
            json={
                "message": (
                    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                    "厚度公差3微米。请比较附件报价并推荐供应商。"
                ),
                "attachments": [_upload(case) for case in cases],
            },
        )
        assert started.status_code == 202
        accepted = started.json()
        request_id = accepted["purchase_request_id"]
        run_id = accepted["run_id"]
        await _wait_for_run_status(client, run_id, {"require_human"})
        detail = (await client.get(f"/api/procurement/requests/{request_id}")).json()

        # 星河包装 supplier name is low-confidence -> human correction first.
        theta = next(
            item
            for item in detail["quotes"]
            if "supplier_name" in item["review_fields"]
        )
        corrected = await client.post(
            f"/api/procurement/requests/{request_id}/quotes/{theta['id']}/corrections",
            json={"field": "supplier_name", "value": "星河包装", "actor": "测试采购员"},
        )
        assert corrected.status_code == 200
        assert corrected.json()["review_fields"] == []

        resumed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
        assert resumed.status_code == 202
        assert resumed.json()["run_id"] == run_id
        detail = await _wait_for_comparison(client, request_id, run_id=run_id)
        await _wait_for_run_status(client, run_id, {"require_human"})

        snapshot = detail["comparison"]
        approved = await client.post(
            f"/api/procurement/requests/{request_id}/decision",
            json={
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "quote_id": snapshot["result"]["recommended_quote_id"],
                "confirmed": True,
                "actor": "测试采购员",
                "note": "采购员真实备注：同意华东优包",
            },
        )
        assert approved.status_code == 200, approved.text
        await _wait_for_run_status(client, run_id, {"completed"})
        report = (await client.get(f"/api/runs/{run_id}/report")).json()
        assert report["conclusion"]["status"] == "passed"
        assert report["approvals"][-1]["tool_name"] == "procurement_approve_supplier"
        final = await client.get(f"/api/procurement/requests/{request_id}")
        decision = final.json()["decision"]
        assert decision["decision"] == "approved"
        # 审计权威值必须是采购员提交的 actor/note，而不是模型伪造的。
        assert decision["actor"] == "测试采购员"
        assert decision["note"] == "采购员真实备注：同意华东优包"

    await app.state.procurement_agent.aclose()
    await app.state.run_supervisor.aclose()
    await harness.aclose()


# ---------------------------------------------------------------- adversarial-review fixes (2026-08-08)
def test_unrecognized_required_material_color_are_fail_closed() -> None:
    """P1: requirement material/color outside the canonical enums (HDPE / 米白)
    must not be silently skipped; both PE and PP quotes must be ineligible with
    a spec_material/spec_color exclusion (never eligible=True with no exclusion).
    (需求值如 HDPE / 牛皮色 不在可识别枚举内；注意“米白”会被现有别名识别为白色，
    因此这里用真正无法识别的“荧光黄”。)"""
    truth = load_frozen_truth()
    request = json.loads(json.dumps(truth["request"]))
    request["id"] = "hdpe-request"
    request["specifications"]["material"] = "HDPE"
    request["specifications"]["color"] = "荧光黄"

    def quote(quote_id: str, supplier: str, description: str, material: str, color: str) -> dict:
        values = {
            "supplier_name": supplier,
            "item_description": description,
            "material": material,
            "color": color,
            "print_colors": 1,
            "currency": "CNY",
            "unit_price": "500",
            "price_basis": 1000,
            "tax_rate": "0.13",
            "tax_included": True,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 1000,
            "lead_time_days": 7,
            "supports_invoice": True,
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "valid_until": "2026-12-31",
        }
        return {
            "id": quote_id,
            "supplier_name": supplier,
            "source_sha256": quote_id * 8,
            "extracted": {
                "fields": {name: {"value": value} for name, value in values.items()}
            },
        }

    result = compare_quotes(
        request,
        [
            quote("pe-q1", "PE厂", "PE 白色快递袋 250x350mm 60um 单色印刷", "PE", "白色"),
            quote("pp-q1", "PP厂", "PP 白色快递袋 250x350mm 60um 单色印刷", "PP", "白色"),
        ],
        analysis_as_of="2026-07-27",
    )
    assert result["recommended_quote_id"] is None
    for item in result["quotes"]:
        assert item["eligible"] is False
        assert item["match"]["passed"] is False
        codes = {reason["code"] for reason in item["exclusion_reasons"]}
        assert "spec_material" in codes
        assert "spec_color" in codes


def test_unidentifiable_item_name_is_fail_closed() -> None:
    """P1: an item name outside the canonical identity groups must not silently
    skip the item-identity hard constraint."""
    truth = load_frozen_truth()
    request = json.loads(json.dumps(truth["request"]))
    request["id"] = "bubble-request"
    request["item_name"] = "太空袋"
    request["specifications"]["material"] = "PE"

    def quote(quote_id: str, supplier: str, description: str) -> dict:
        values = {
            "supplier_name": supplier,
            "item_description": description,
            "material": "PE",
            "color": "透明",
            "print_colors": 0,
            "currency": "CNY",
            "unit_price": "500",
            "price_basis": 1000,
            "tax_rate": "0.13",
            "tax_included": True,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 1000,
            "lead_time_days": 7,
            "supports_invoice": True,
            "width_mm": "600",
            "length_mm": "50000",
            "thickness_um": "90",
            "valid_until": "2026-12-31",
        }
        return {
            "id": quote_id,
            "supplier_name": supplier,
            "source_sha256": quote_id * 8,
            "extracted": {
                "fields": {name: {"value": value} for name, value in values.items()}
            },
        }

    result = compare_quotes(
        request,
        [
            quote("bq1", "气泡膜厂甲", "PE 太空袋 600mm 50m 90um"),
            quote("bq2", "气泡膜厂乙", "PE 太空袋 600mm 50m 90um"),
        ],
        analysis_as_of="2026-07-27",
    )
    for item in result["quotes"]:
        assert item["eligible"] is False
        assert any(
            reason["code"] == "item_identity" for reason in item["exclusion_reasons"]
        ), item["exclusion_reasons"]



def test_carton_requirement_is_verifiable_and_recommends() -> None:
    """Regression (2026-08-08): the fail-closed identity checks must recognize
    the documented packaging taxonomy (five-layer corrugated carton / kraft /
    corrugated board). A carton RFQ must yield a recommendation instead of
    rejecting every quote with 'cannot verify item/material/color'."""
    from agentharness.procurement.costing import compare_quotes

    request = {
        "id": "carton-request",
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
        },
    }

    def quote(quote_id: str, supplier: str, *, invoice: bool, unit_price: str,
              currency: str = "CNY", freight: str = "0", shipping_included: bool = True,
              moq: int = 5000, lead: int = 15, height: str = "250") -> dict:
        values = {
            "supplier_name": supplier,
            "item_description": "五层瓦楞纸箱 400x300x250mm 5000um 牛皮色 单色印刷",
            "material": "瓦楞纸",
            "color": "牛皮色",
            "print_colors": 1,
            "currency": currency,
            "unit_price": unit_price,
            "price_basis": 1,
            "tax_rate": "0.13",
            "tax_included": True,
            "shipping_fee": freight,
            "shipping_included": shipping_included,
            "moq": moq,
            "lead_time_days": lead,
            "supports_invoice": invoice,
            "width_mm": "400",
            "length_mm": "300",
            "height_mm": height,
            "thickness_um": "5000",
            "valid_until": "2026-12-31",
        }
        return {
            "id": quote_id,
            "supplier_name": supplier,
            "source_sha256": quote_id * 8,
            "extracted": {
                "fields": {name: {"value": value} for name, value in values.items()}
            },
        }

    zj = quote("zj", "浙江箱业", invoice=True, unit_price="3.20")
    hn = quote("hn", "沪宁纸品", invoice=True, unit_price="3.45")
    south = quote("south", "华南纸业", invoice=False, unit_price="0.38",
                  currency="USD", freight="300")
    short = quote("short", "矮箱供应商", invoice=True, unit_price="2.80", height="100")

    result = compare_quotes(
        request,
        [hn, south, short, zj],
        analysis_as_of="2026-08-08",
    )
    by_id = {item["quote_id"]: item for item in result["quotes"]}
    assert by_id["zj"]["eligible"] is True, by_id["zj"]["exclusion_reasons"]
    assert by_id["hn"]["eligible"] is True, by_id["hn"]["exclusion_reasons"]
    assert by_id["south"]["eligible"] is False
    assert by_id["short"]["eligible"] is False
    assert any(
        reason["code"] == "spec_height_mm"
        for reason in by_id["short"]["exclusion_reasons"]
    )
    codes = {r["code"] for r in by_id["south"]["exclusion_reasons"]}
    assert "invoice" in codes
    # Identity checks must now be verifiable, not 'cannot review'.
    for item in (by_id["zj"], by_id["hn"]):
        checks = {c["field"]: c["passed"] for c in item["match"]["spec_checks"]}
        assert checks["item_identity"] is True
        assert checks["material"] is True
        assert checks["color"] is True
    assert result["recommended_quote_id"] == "zj"


def test_carton_description_extracts_third_dimension_as_height() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "三维纸箱厂"],
            ["品名", "五层瓦楞纸箱 400x300x250mm 5000um 牛皮色 单色印刷"],
        ]
    )
    extracted = parse_quote("carton-height.xlsx", document)
    assert extracted["fields"]["width_mm"]["value"] == "400"
    assert extracted["fields"]["length_mm"]["value"] == "300"
    assert extracted["fields"]["height_mm"]["value"] == "250"

def test_ambiguous_free_shipping_with_delivery_fee_requires_review() -> None:
    """P1: 江浙沪包邮 + 新疆西藏运费到付 must NOT parse as shipping_included=True
    with shipping_fee=0 at high confidence; it must require human review."""
    from agentharness.procurement.parsing import coerce_field_value

    document = _xlsx_bytes(
        [
            ["供应商", "华东物流包装"],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
            ["备注", "江浙沪包邮，新疆西藏运费到付"],
        ]
    )
    extracted = parse_quote("ambig-shipping.xlsx", document)
    shipping = extracted["fields"]["shipping_included"]
    assert shipping["value"] is None
    assert shipping["status"] == "needs_review"
    assert "shipping_included" in fields_requiring_review(extracted)
    assert extracted["fields"].get("shipping_fee", {}).get("value") is None

    # _boolean / coerce path: negative markers must not be overridden by 包邮.
    assert coerce_field_value("shipping_included", "江浙沪包邮，新疆西藏运费到付") is None
    assert coerce_field_value("shipping_included", "运费到付") is False
    assert coerce_field_value("shipping_included", "运费自理") is False
    assert coerce_field_value("shipping_included", "运费自付") is False
    assert coerce_field_value("shipping_included", "运费另算") is False
    assert coerce_field_value("shipping_included", "不含运费") is False
    assert coerce_field_value("shipping_included", "包邮") is True
    assert coerce_field_value("shipping_included", "不包邮") is False


def test_pdf_page_count_limit_rejected_before_extraction() -> None:
    """P2: a PDF over MAX_PDF_PAGES pages is rejected right after PdfReader
    construction, before any per-page text extraction runs."""
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(21):  # MAX_PDF_PAGES = 20
        writer.add_blank_page(width=72, height=72)
    writer.write(output)
    with pytest.raises(QuoteParseError, match="不得超过"):
        parse_quote("many-pages.pdf", output.getvalue())


def test_text_field_value_rejects_oversized_length() -> None:
    """P2: manual/text values over 2000 chars are rejected; parser marks them
    needs_review instead of raising, and numbers/booleans are unaffected."""
    from agentharness.procurement.parsing import coerce_field_value

    with pytest.raises(ValueError, match="长度不能超过"):
        coerce_field_value("supplier_name", "x" * 2001)
    assert coerce_field_value("unit_price", "1.5") == "1.5"
    assert coerce_field_value("shipping_included", True) is True
    assert coerce_field_value("supplier_name", "正常供应商") == "正常供应商"

    document = _xlsx_bytes(
        [
            ["供应商", "超" * 2001],
            ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
            ["币种", "CNY"],
            ["单价", "500"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "是"],
        ]
    )
    extracted = parse_quote("oversized-supplier.xlsx", document)
    supplier = extracted["fields"]["supplier_name"]
    assert supplier["value"] is None
    assert supplier["status"] == "needs_review"
    assert "supplier_name" in fields_requiring_review(extracted)


def test_correct_quote_field_body_value_max_length() -> None:
    """P2: CorrectQuoteFieldBody.value string is capped at 2000 chars."""
    from pydantic import ValidationError

    from agentharness.api.procurement import CorrectQuoteFieldBody

    with pytest.raises(ValidationError):
        CorrectQuoteFieldBody(field="supplier_name", value="x" * 2001, actor="采购员")
    ok = CorrectQuoteFieldBody(field="shipping_fee", value=25, actor="采购员")
    assert ok.value == 25


def test_api_currency_codes_require_three_uppercase_letters() -> None:
    """P2: base_currency and fx_rates keys must be 3 uppercase letters after
    normalization; '123' / 'CN¥' must be rejected with a Chinese message."""
    from decimal import Decimal

    from pydantic import ValidationError

    from agentharness.api.procurement import ProcurementConstraints

    ok = ProcurementConstraints(base_currency="cny", fx_rates={"cny": "1", "usd": "7.2"})
    assert ok.base_currency == "CNY"
    assert ok.fx_rates == {"CNY": Decimal("1"), "USD": Decimal("7.2")}

    with pytest.raises(ValidationError) as exc_info:
        ProcurementConstraints(base_currency="123", fx_rates={"123": "1"})
    assert "3 位大写字母" in str(exc_info.value) or "3 个大写字母" in str(exc_info.value)

    with pytest.raises(ValidationError):
        ProcurementConstraints(base_currency="CN¥", fx_rates={"CNY": "1"})
    with pytest.raises(ValidationError):
        ProcurementConstraints(base_currency="CNY", fx_rates={"CN¥": "1"})
    with pytest.raises(ValidationError):
        ProcurementConstraints(base_currency="CNY", fx_rates={"US": "1"})
    with pytest.raises(ValidationError, match="重复"):
        ProcurementConstraints(
            base_currency="CNY",
            fx_rates={"CNY": "1", "usd": "7.2", "USD": "7.3"},
        )


@pytest.mark.asyncio
async def test_procurement_get_endpoints_redact_sensitive_quote_text(
    data_dir: Path, workspace: Path
) -> None:
    """P2: GET /requests, /requests/{id} and /purchase-order apply the same
    public redaction as /report (paths and API keys must never leak)."""
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    secret_path = r"C:\Users\secret\keys\service-account.json"
    api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/procurement/requests",
                json=_request_body(load_frozen_truth()),
            )
            assert created.status_code == 201, created.text
            request_id = created.json()["id"]

            document = _xlsx_bytes(
                [
                    ["供应商", secret_path],
                    ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
                    ["币种", "CNY"],
                    ["单价", "500"],
                    ["计价数量", "1000"],
                    ["税率", "13%"],
                    ["是否含税", "是"],
                    ["是否包邮", "是"],
                    ["MOQ", "1000"],
                    ["交期", "7"],
                    ["是否可开票", "是"],
                    ["备注", api_key],
                ]
            )
            imported = await client.post(
                f"/api/procurement/requests/{request_id}/quotes",
                json={
                    "filename": "sensitive.xlsx",
                    "content_base64": base64.b64encode(document).decode("ascii"),
                },
            )
            assert imported.status_code == 201, imported.text

            detail = (await client.get(f"/api/procurement/requests/{request_id}")).json()
            raw = json.dumps(detail, ensure_ascii=False)
            assert secret_path not in raw
            assert api_key not in raw
            assert "[REDACTED_PATH]" in raw and "[REDACTED_API_KEY]" in raw

            listing = (await client.get("/api/procurement/requests?limit=200")).json()
            raw_listing = json.dumps(listing, ensure_ascii=False)
            assert secret_path not in raw_listing
            assert api_key not in raw_listing

            app.state.procurement_service.purchase_order = lambda rid: {
                "id": "po-1",
                "po_number": "PO-RFQ-1",
                "request_id": request_id,
                "reference": "RFQ-1",
                "title": "测试订单",
                "item_name": "快递袋",
                "quantity": 1,
                "unit": "piece",
                "supplier_name": secret_path,
                "quote_id": "quote-1",
                "currency": "CNY",
                "unit_price_base": "1.0000",
                "total_amount_base": "1.00",
                "snapshot_id": "snap-1",
                "snapshot_version": 1,
                "input_sha256": "0" * 64,
                "approval_id": "approval-1",
                "decision_id": "decision-1",
                "created_at": "2026-08-08T00:00:00+00:00",
                "evidence_sha256": "e" * 64,
            }
            po = (
                await client.get(f"/api/procurement/requests/{request_id}/purchase-order")
            ).json()
            raw_po = json.dumps(po, ensure_ascii=False)
            assert secret_path not in raw_po
            assert "[REDACTED_PATH]" in raw_po
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


def test_record_no_award_rechecks_eligibility_with_today(data_dir: Path, monkeypatch) -> None:
    """P3: record_no_award must recompute current eligibility with _today()
    (like the approved path), not the stale snapshot analysis date."""
    import agentharness.procurement.service as service_module
    from agentharness.harness import Harness
    from agentharness.procurement.service import ProcurementService

    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    try:
        request = service.create_request(_request_body(load_frozen_truth()))
        request_id = str(request["id"])

        def quote_extracted(supplier: str) -> dict:
            values = {
                "supplier_name": supplier,
                "item_description": "PE 白色快递袋 250x350mm 60um 单色印刷",
                "material": "PE",
                "color": "白色",
                "print_colors": 1,
                "currency": "CNY",
                "unit_price": "0.5",
                "price_basis": 1,
                "tax_rate": "0.13",
                "tax_included": False,
                "shipping_fee": "0",
                "shipping_included": True,
                "moq": 1000,
                "lead_time_days": 30,  # 超过 max_lead_days=15 -> 全部不合格
                "supports_invoice": True,
                "width_mm": "250",
                "length_mm": "350",
                "thickness_um": "60",
                "valid_until": "2026-12-31",
            }
            return {
                "schema_version": 1,
                "parser_version": "test",
                "document_kind": "xlsx",
                "fields": {
                    name: {
                        "value": value,
                        "confidence": 1.0,
                        "status": "accepted",
                        "source": {
                            "document_kind": "xlsx",
                            "locator": "test",
                            "excerpt": "",
                            "method": "test",
                        },
                    }
                    for name, value in values.items()
                },
                "processing_ms": 0,
            }

        service.import_quote(
            request_id,
            filename="供应商甲报价.xlsx",
            data=b"quote-a",
            extracted=quote_extracted("供应商甲"),
        )
        service.import_quote(
            request_id,
            filename="供应商乙报价.xlsx",
            data=b"quote-b",
            extracted=quote_extracted("供应商乙"),
        )
        harness.storage.runs.create_run(
            run_id="run-noaward",
            session_id=str(request["session_id"]),
            root_run_id="run-noaward",
        )
        snapshot = service.compare_for_agent(request_id, run_id="run-noaward")
        assert snapshot["result"]["eligible_count"] == 0

        captured = {}

        def fake_compare(req, quotes, *, analysis_as_of):
            captured["analysis_as_of"] = analysis_as_of
            return {"eligible_count": 0, "quotes": []}

        monkeypatch.setattr(service_module, "_today", lambda: date(2030, 1, 1))
        monkeypatch.setattr(service_module, "compare_quotes", fake_compare)
        result = service.record_no_award(
            request_id,
            snapshot_id=snapshot["id"],
            input_sha256=snapshot["input_sha256"],
            note="全部不合格",
            actor="采购员",
        )
        assert result["decision"]["decision"] == "no_award"
        assert captured["analysis_as_of"] == date(2030, 1, 1)
        assert captured["analysis_as_of"] != date.fromisoformat(
            snapshot["result"]["analysis_as_of"]
        )
    finally:
        harness.close()
