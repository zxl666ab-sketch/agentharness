from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import sqlite3
import threading
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

from agentharness.api.procurement import (
    ConversationAttachment,
    StartProcurementConversationBody,
)
from agentharness.api.server import create_app
from agentharness.contracts import Message, MessageRole, RunStatus
from agentharness.harness import Harness
from agentharness.procurement import parsing as parsing_module
from agentharness.procurement.agent import (
    PROCUREMENT_PROVIDER,
    PROCUREMENT_TOOL_NAMES,
    ProcurementAgent,
    ProcurementFakeProvider,
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
    assert default_profile.pricing.input_per_million_usd == 0
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


def test_procurement_fake_extracts_spaced_chinese_requirement_without_silent_zero_defaults() -> None:
    message = Message(
        role=MessageRole.user,
        content=(
            "请采购白色 PE 快递袋，250×350 mm，60 微米，单色印刷，数量 10000 个，"
            "最长交期 15 天，需要开票，送货到华东仓，预算到货单价不超过 0.70 元。"
        ),
    )

    payload = ProcurementFakeProvider._extract_requirement([message])

    assert payload["quantity"] == 10_000
    assert payload["specifications"]["width_mm"] == "250"
    assert payload["specifications"]["length_mm"] == "350"
    assert payload["specifications"]["thickness_um"] == "60"
    assert payload["constraints"]["max_lead_days"] == 15
    assert payload["constraints"]["destination"] == "华东仓"
    assert payload["constraints"]["fx_rates"] == {"CNY": "1"}

    with pytest.raises(ValueError, match="采购数量"):
        ProcurementFakeProvider._extract_requirement(
            [Message(role=MessageRole.user, content="采购一批包装袋")]
        )


def test_procurement_fake_extracts_dynamic_non_packaging_requirement() -> None:
    payload = ProcurementFakeProvider._extract_requirement(
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
    assert payload["specifications"]["length"]["unit"] == "米"
    assert payload["specifications"]["material"]["value"] == "BOPP"


def test_v2_requirement_allows_long_measurement_and_decimal_quantity(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "schema_version": 2,
            "title": "胶带采购",
            "category": "general",
            "item_name": "透明封箱胶带",
            "quantity": "12.5",
            "unit": "卷",
            "specifications": {
                "length": {
                    "label": "长度",
                    "type": "number",
                    "value": "100",
                    "unit": "m",
                    "match": "exact",
                    "priority": "hard",
                },
                "adhesive_type": {
                    "label": "胶粘剂类型",
                    "type": "text",
                    "value": "丙烯酸",
                    "match": "exact",
                    "priority": "preference",
                },
            },
            "constraints": {
                "base_currency": "CNY",
                "fx_rates": {"CNY": "1"},
                "max_lead_days": 30,
                "invoice_required": False,
            },
        }
    )
    harness.close()

    assert request["schema_version"] == 2
    assert request["quantity"] == "12.5"
    assert request["unit"] == "卷"
    assert request["specifications"]["length"]["value"] == "100"


def test_dynamic_spec_comparison_supports_units_and_preference_warnings() -> None:
    request = {
        "schema_version": 2,
        "id": "dynamic-spec-request",
        "item_name": "透明封箱胶带",
        "quantity": "12.5",
        "unit": "卷",
        "specifications": {
            "length": {
                "label": "长度",
                "type": "number",
                "value": "100",
                "unit": "m",
                "match": "gte",
                "priority": "hard",
            },
            "adhesive_type": {
                "label": "胶粘剂类型",
                "type": "text",
                "value": "丙烯酸",
                "match": "exact",
                "priority": "preference",
            },
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 30,
            "invoice_required": False,
        },
    }

    def quote(quote_id: str, length: str, adhesive_type: str) -> dict:
        values = {
            "supplier_name": quote_id,
            "item_description": "透明封箱胶带",
            "currency": "CNY",
            "unit_price": "100",
            "price_basis": 1,
            "tax_rate": "0",
            "tax_included": True,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 1,
            "lead_time_days": 7,
            "supports_invoice": True,
        }
        return {
            "id": quote_id,
            "source_sha256": "0" * 64,
            "supplier_name": quote_id,
            "extracted": {
                "fields": {name: {"value": value} for name, value in values.items()},
                "specifications": {
                    "length": {"value": length, "unit": "cm"},
                    "adhesive_type": {"value": adhesive_type},
                },
            },
        }

    result = compare_quotes(
        request,
        [quote("dynamic-good", "10000", "热熔胶"), quote("dynamic-bad", "90", "丙烯酸")],
        analysis_as_of="2026-07-27",
    )
    good = next(item for item in result["quotes"] if item["quote_id"] == "dynamic-good")
    bad = next(item for item in result["quotes"] if item["quote_id"] == "dynamic-bad")

    assert result["ruleset_version"] == "landed-cost-v2"
    assert result["eligible_count"] == 1
    assert good["eligible"] is True
    assert any("偏好规格" in warning for warning in good["warnings"])
    assert bad["eligible"] is False
    assert any(reason["code"] == "spec_length" for reason in bad["exclusion_reasons"])


def test_v2_quote_review_only_requires_dynamic_hard_specs(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "schema_version": 2,
            "title": "胶带采购",
            "category": "general",
            "item_name": "封箱胶带",
            "quantity": "12.5",
            "unit": "卷",
            "specifications": {
                "length": {
                    "label": "长度",
                    "type": "number",
                    "value": "100",
                    "unit": "m",
                    "match": "gte",
                    "priority": "hard",
                },
            },
            "constraints": {
                "base_currency": "CNY",
                "fx_rates": {"CNY": "1"},
                "max_lead_days": 30,
                "invoice_required": False,
            },
        }
    )
    commercial = {
        "supplier_name": "动态供应商",
        "item_description": "封箱胶带",
        "currency": "CNY",
        "unit_price": "100",
        "price_basis": 1,
        "tax_rate": "0",
        "tax_included": True,
        "shipping_included": True,
        "moq": 1,
        "lead_time_days": 7,
        "supports_invoice": True,
    }
    extracted = {
        "fields": {
            name: {
                "value": value,
                "confidence": 0.99,
                "status": "accepted",
            }
            for name, value in commercial.items()
        },
        "specifications": {
            "length": {
                "label": "长度",
                "value": "100",
                "unit": "m",
                "confidence": 0.99,
                "status": "accepted",
            }
        },
    }
    first = service.import_quote(
        str(request["id"]),
        filename="dynamic-one.xlsx",
        data=b"dynamic-one",
        extracted=extracted,
    )
    second = service.import_quote(
        str(request["id"]),
        filename="dynamic-two.xlsx",
        data=b"dynamic-two",
        extracted={
            **extracted,
            "fields": {
                **extracted["fields"],
                "supplier_name": {
                    **extracted["fields"]["supplier_name"],
                    "value": "动态供应商二",
                },
            },
        },
    )
    detail = service.get_request(str(request["id"]))
    harness.close()

    assert first["review_fields"] == []
    assert second["review_fields"] == []
    assert detail["unresolved_field_count"] == 0


def test_v2_quote_review_matches_unit_suffixed_dynamic_spec_labels(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "schema_version": 2,
            "title": "瓦楞纸箱采购",
            "category": "general",
            "item_name": "五层瓦楞纸箱",
            "quantity": "5000",
            "unit": "个",
            "specifications": {
                "layers": {"label": "瓦楞层数", "type": "number", "value": "5", "unit": "层", "match": "exact", "priority": "hard"},
                "length": {"label": "长度", "type": "number", "value": "400", "unit": "mm", "match": "exact", "priority": "hard"},
                "width": {"label": "宽度", "type": "number", "value": "300", "unit": "mm", "match": "exact", "priority": "hard"},
            },
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 30, "invoice_required": True},
        }
    )
    commercial = {"supplier_name": "箱业供应商", "item_description": "五层瓦楞纸箱", "currency": "CNY", "unit_price": "3", "price_basis": 1, "tax_rate": "0.13", "tax_included": True, "shipping_included": True, "moq": 1, "lead_time_days": 7, "supports_invoice": True}
    extracted = {
        "fields": {name: {"value": value, "confidence": 0.99, "status": "accepted"} for name, value in commercial.items()},
        "specifications": {
            "layers": {"label": "瓦楞层数", "value": "5", "confidence": 0.99, "status": "accepted"},
            "长度mm": {"label": "长度（mm）", "value": "400", "confidence": 0.99, "status": "accepted"},
            "宽度mm": {"label": "宽度（mm）", "value": "300", "confidence": 0.99, "status": "accepted"},
        },
    }
    quote = service.import_quote(str(request["id"]), filename="carton.xlsx", data=b"carton", extracted=extracted)
    harness.close()

    assert quote["review_fields"] == []


def test_quote_parser_extracts_corrugated_layers_from_item_description() -> None:
    fields = {
        "item_description": {
            "value": "五层瓦楞纸箱 400×300 mm，厚度 5000 微米",
            "source": {"locator": "Quote!B2"},
        }
    }

    parsing_module._extract_specs(fields, "xlsx")

    assert fields["layers"]["value"] == 5


def test_v2_quote_review_maps_standard_fields_to_dynamic_requirement_labels(
    data_dir: Path,
) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "schema_version": 2,
            "title": "热敏标签采购",
            "category": "ecommerce_packaging",
            "item_name": "热敏不干胶标签",
            "quantity": "20000",
            "unit": "张",
            "specifications": {
                "尺寸": {
                    "label": "成品尺寸",
                    "type": "text",
                    "value": "100×150",
                    "match": "exact",
                    "priority": "hard",
                },
                "厚度": {
                    "label": "材料厚度",
                    "type": "number",
                    "value": "80",
                    "unit": "μm",
                    "match": "tolerance",
                    "tolerance": "5",
                    "priority": "hard",
                },
                "材质": {
                    "label": "面材",
                    "type": "text",
                    "value": "铜版纸",
                    "match": "exact",
                    "priority": "hard",
                },
                "颜色": {
                    "label": "底色",
                    "type": "text",
                    "value": "白色",
                    "match": "exact",
                    "priority": "hard",
                },
                "印刷色数": {
                    "label": "印刷色数",
                    "type": "number",
                    "value": "1",
                    "unit": "色",
                    "match": "exact",
                    "priority": "hard",
                },
                "MOQ": {
                    "label": "最小起订量",
                    "type": "number",
                    "value": "20000",
                    "unit": "张",
                    "match": "lte",
                    "priority": "hard",
                },
            },
            "constraints": {
                "base_currency": "CNY",
                "fx_rates": {"CNY": "1"},
                "max_lead_days": 10,
                "invoice_required": True,
            },
        }
    )
    commercial = {
        "supplier_name": "苏州标联",
        "item_description": "热敏不干胶标签",
        "currency": "CNY",
        "unit_price": "180",
        "price_basis": 1000,
        "tax_rate": "0.13",
        "tax_included": True,
        "shipping_included": True,
        "moq": 10000,
        "lead_time_days": 7,
        "supports_invoice": True,
        "width_mm": "100",
        "length_mm": "150",
        "thickness_um": "80",
        "material": "铜版纸",
        "color": "白色",
        "print_colors": 1,
    }
    extracted = {
        "fields": {
            name: {
                "value": value,
                "confidence": 0.97,
                "status": "accepted",
            }
            for name, value in commercial.items()
        },
        "specifications": {},
    }

    try:
        quote = service.import_quote(
            str(request["id"]),
            filename="thermal-label.xlsx",
            data=b"thermal-label",
            extracted=extracted,
        )
        second_quote = service.import_quote(
            str(request["id"]),
            filename="thermal-label-two.xlsx",
            data=b"thermal-label-two",
            extracted={
                **extracted,
                "fields": {
                    **extracted["fields"],
                    "supplier_name": {
                        **extracted["fields"]["supplier_name"],
                        "value": "宁波印联",
                    },
                },
            },
        )
        service.repo.update_quote(
            str(quote["id"]),
            extracted=quote["extracted"],
            supplier_name=quote["supplier_name"],
            status="needs_review",
            review_count=6,
        )
        service.repo.update_request(str(request["id"]), status="review")
        detail = service.get_request(str(request["id"]))
    finally:
        harness.close()

    comparison = compare_quotes(
        request,
        [quote, second_quote],
        analysis_as_of=date(2026, 8, 4),
    )

    assert quote["review_fields"] == []
    assert detail["unresolved_field_count"] == 0
    assert detail["status"] == "ready"
    assert detail["quotes"][0]["status"] == "ready"
    assert detail["quotes"][0]["review_count"] == 0
    assert comparison["eligible_count"] == 2
    assert all(
        check["passed"]
        for result in comparison["quotes"]
        for check in result["match"]["spec_checks"]
    )


def test_v2_parse_staged_quotes_maps_labelled_dimensions(data_dir: Path) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "schema_version": 2,
            "title": "动态标签采购",
            "category": "label_printing",
            "item_name": "热敏标签",
            "quantity": "10000",
            "unit": "张",
            "specifications": {
                "size": {
                    "label": "尺寸",
                    "type": "text",
                    "value": "100×150 mm",
                    "match": "exact",
                    "priority": "hard",
                },
            },
            "constraints": {
                "base_currency": "CNY",
                "fx_rates": {"CNY": "1"},
                "max_lead_days": 15,
                "invoice_required": True,
            },
        }
    )
    run_id = "dynamic-review-gap-label-test"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    try:
        for case in truth["quotes"][:2]:
            service.stage_attachment(
                str(request["id"]),
                filename=str(case["filename"]),
                data=build_case_document(case),
            )

        result = service.parse_staged_quotes(str(request["id"]), run_id=run_id)

        assert result["quote_count"] == 2
        assert result["review_gaps"] == []

        pipeline = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)

        assert pipeline["status"] == "completed"
    finally:
        harness.close()


def test_all_excluded_snapshot_can_be_marked_no_award_but_not_approved(
    data_dir: Path,
) -> None:
    truth = load_frozen_truth()
    payload = _request_body(truth)
    payload["constraints"]["max_lead_days"] = 1
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(payload)
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "no-award-test-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )

    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    assert snapshot["result"]["eligible_count"] == 0
    assert snapshot["result"]["recommended_quote_id"] is None

    with pytest.raises(ProcurementError, match="只能选定通过全部硬性条件的报价"):
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=str(snapshot["result"]["quotes"][0]["quote_id"]),
            run_id=run_id,
            approval_id="approval-must-not-approve",
            note=None,
            actor="采购员",
        )

    finalized = service.approve_supplier_from_agent(
        str(request["id"]),
        snapshot_id=str(snapshot["id"]),
        input_sha256=str(snapshot["input_sha256"]),
        quote_id=None,
        decision="no_award",
        run_id=run_id,
        approval_id="approval-no-award",
        note="当前报价均不满足最长交期，本轮流标",
        actor="采购员",
    )
    report = service.audit_report(str(request["id"]))
    harness.close()

    assert finalized["status"] == "no_award"
    assert finalized["decision"]["decision"] == "no_award"
    assert finalized["decision"]["quote_id"] is None
    assert report["execution_artifacts"] == []
    assert any(event["type"] == "supplier_no_award" for event in report["audit_events"])


@pytest.mark.asyncio
async def test_api_creates_v2_dynamic_requirement(data_dir: Path, workspace: Path) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    body = {
        "schema_version": 2,
        "title": "封箱胶带询价",
        "category": "general",
        "item_name": "透明封箱胶带",
        "quantity": "12.5",
        "unit": "卷",
        "specifications": {
            "length": {
                "label": "长度",
                "type": "number",
                "value": "100",
                "unit": "m",
                "match": "gte",
                "priority": "hard",
            },
            "adhesive_type": {
                "label": "胶粘剂类型",
                "type": "text",
                "value": "丙烯酸",
                "match": "exact",
                "priority": "preference",
            },
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 30,
            "invoice_required": False,
            "destination": "华东仓",
        },
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post("/api/procurement/requests", json=body)
            assert created.status_code == 201
            payload = created.json()
            assert payload["schema_version"] == 2
            assert payload["category"] == "general"
            assert payload["quantity"] == "12.5"
            assert payload["specifications"]["length"]["unit"] == "m"

            meta = await client.get("/api/procurement/meta")
            assert meta.status_code == 200
            assert 2 in meta.json()["requirement_schema_versions"]
            assert "landed-cost-v2" in meta.json()["ruleset_versions"]
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_api_deletes_one_procurement_request(data_dir: Path, workspace: Path) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/procurement/requests",
                json=_request_body(load_frozen_truth()),
            )
            assert created.status_code == 201
            request_id = created.json()["id"]

            deleted = await client.delete(f"/api/procurement/requests/{request_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {
                "request_id": request_id,
                "reference": created.json()["reference"],
                "deleted": True,
            }
            assert (await client.get(f"/api/procurement/requests/{request_id}")).status_code == 404
            assert all(
                item["id"] != request_id
                for item in (await client.get("/api/procurement/requests")).json()
            )
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_api_no_award_can_be_reopened_with_copied_quotes(
    data_dir: Path,
    workspace: Path,
) -> None:
    truth = load_frozen_truth()
    body = _request_body(truth)
    body["constraints"] = {**body["constraints"], "max_lead_days": 1}
    cases = [next(item for item in truth["quotes"] if item["id"] == case_id) for case_id in ("q-alpha", "q-beta")]
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post("/api/procurement/requests", json=body)
            assert created.status_code == 201
            request_id = created.json()["id"]
            for case in cases:
                imported = await client.post(
                    f"/api/procurement/requests/{request_id}/quotes",
                    json=_upload(case),
                )
                assert imported.status_code == 201

            analyzed = await client.post(f"/api/procurement/requests/{request_id}/analyze")
            assert analyzed.status_code == 202
            run_id = analyzed.json()["run_id"]
            detail = await _wait_for_comparison(client, request_id, run_id=run_id)
            await _wait_for_run_status(client, run_id, {"require_human"})
            snapshot = detail["comparison"]
            assert snapshot["result"]["eligible_count"] == 0

            finalized = await client.post(
                f"/api/procurement/requests/{request_id}/decision",
                json={
                    "decision": "no_award",
                    "snapshot_id": snapshot["id"],
                    "input_sha256": snapshot["input_sha256"],
                    "confirmed": True,
                    "note": "两家报价均超过本次交期要求，本轮流标",
                    "actor": "采购员",
                },
            )
            assert finalized.status_code == 200
            assert finalized.json()["status"] == "no_award"
            await _wait_for_run_status(client, run_id, {"completed"})

            report = await client.get(f"/api/procurement/requests/{request_id}/report")
            assert report.status_code == 200
            assert report.json()["decision"]["decision"] == "no_award"
            assert report.json()["execution_artifacts"] == []

            reopened = await client.post(
                f"/api/procurement/requests/{request_id}/reopen",
                json={"copy_quotes": True, "actor": "采购员"},
            )
            assert reopened.status_code == 200
            copied = reopened.json()
            assert copied["id"] != request_id
            assert copied["status"] == "collecting"
            assert copied["quote_count"] == len(cases)
            assert copied["decision"] is None
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


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
        ("/api/procurement/config", {}),
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

            for path in (
                "/api/runtime",
                "/api/procurement/meta",
                "/api/procurement/config",
                "/api/procurement/requests",
                "/api/runs",
                "/api/sessions",
                "/api/stream",
            ):
                read_response = await client.get(path)
                assert read_response.status_code == 403, (path, read_response.text)

            health = await client.get("/api/health")
            assert health.status_code == 200
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


def test_conversation_batch_checks_base64_budget_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentharness.api.procurement as procurement_api

    monkeypatch.setattr(procurement_api, "MAX_CONVERSATION_UPLOAD_BYTES", 10)
    encoded = base64.b64encode(b"123456").decode("ascii")
    body = StartProcurementConversationBody(
        message="上传报价",
        attachments=[
            ConversationAttachment(filename="one.pdf", content_base64=encoded),
            ConversationAttachment(filename="two.pdf", content_base64=encoded),
        ],
    )

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("aggregate budget must be checked before decoding")

    monkeypatch.setattr(procurement_api.base64, "b64decode", fail_decode)
    with pytest.raises(ProcurementError, match="总计不得超过 20 MB"):
        body.decoded_attachments()


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


@pytest.mark.asyncio
async def test_failed_conversation_recovery_parses_staged_quotes_before_retry(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    message = "采购白色 PE 快递袋，250x350mm，60 微米，10000 个。"
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_conversation(
        message,
        [(str(case["filename"]), build_case_document(case)) for case in cases],
    )
    run_id = "failed-conversation-recovery-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
        status=RunStatus.failed,
        provider="openai",
        model="streaming-gateway-model",
    )
    harness.storage.save_message(
        run_id,
        str(request["session_id"]),
        Message(role=MessageRole.user, content=message),
        seq=0,
    )
    service.bind_run(str(request["id"]), run_id=run_id)
    service.repo.update_request(str(request["id"]), status="ready")
    launched: dict[str, str] = {}

    async def fake_launch(
        request_id: str,
        *,
        message: str,
        source: str,
    ) -> dict[str, str]:
        launched.update(request_id=request_id, message=message, source=source)
        return {
            "purchase_request_id": request_id,
            "session_id": str(request["session_id"]),
            "run_id": "recovery-retry-run",
            "status": "accepted",
        }

    agent = ProcurementAgent(harness, service)
    monkeypatch.setattr(agent, "_launch", fake_launch)
    try:
        accepted = await agent.start_existing(str(request["id"]))
        detail = service.get_request(str(request["id"]))
    finally:
        harness.close()

    assert accepted["status"] == "accepted"
    assert detail["quote_count"] == 2
    assert detail["unresolved_field_count"] == 0
    assert launched == {
        "request_id": str(request["id"]),
        "message": message,
        "source": "procurement_conversation",
    }


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
            ["是否可开票", "否"],
            ["备注", f"不包邮；{invoice_phrase}"],
        ]
    )

    extracted = parse_quote("negative-semantics.xlsx", document)
    fields = extracted["fields"]

    assert fields["shipping_included"]["value"] is False
    assert fields["shipping_fee"]["value"] == "600"
    assert fields["supports_invoice"]["value"] is False
    assert fields["supports_invoice"]["status"] == "accepted"


def test_quote_parser_treats_explicit_no_invoice_as_accepted_false() -> None:
    document = _xlsx_bytes(
        [
            ["供应商", "明确否定供应商"],
            ["品名", "三层瓦楞纸箱 400x300x200mm"],
            ["币种", "CNY"],
            ["单价", "2.5"],
            ["计价数量", "1000"],
            ["税率", "13%"],
            ["是否含税", "是"],
            ["是否包邮", "是"],
            ["MOQ", "1000"],
            ["交期", "7"],
            ["是否可开票", "否"],
        ]
    )

    invoice = parse_quote("explicit-no-invoice.xlsx", document)["fields"]["supports_invoice"]

    assert invoice["value"] is False
    assert invoice["status"] == "accepted"
    assert "conflicts" not in invoice


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
    ("width", "length", "thickness", "expected_eligible"),
    [
        pytest.param("400", "300", "5", True, id="direct-orientation"),
        pytest.param("300", "400", "5", True, id="swapped-orientation"),
        pytest.param("410", "300", "5", False, id="width-out-of-tolerance"),
        pytest.param("400", "300", "8", False, id="thickness-out-of-tolerance"),
    ],
)
def test_compare_quotes_accepts_box_dimensions_in_either_orientation(
    width: str,
    length: str,
    thickness: str,
    expected_eligible: bool,
) -> None:
    request = {
        "id": "box-orientation-request",
        "item_name": "纸箱",
        "quantity": 1000,
        "specifications": {
            "width_mm": "400",
            "length_mm": "300",
            "thickness_um": "5",
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "size_tolerance_mm": "3",
            "thickness_tolerance_um": "0",
        },
    }

    def quote(quote_id: str, actual_width: str, actual_length: str, actual_thickness: str) -> dict:
        values = {
            "supplier_name": quote_id,
            "item_description": "纸箱 400x300mm 5mm",
            "currency": "CNY",
            "unit_price": "100",
            "price_basis": 1000,
            "tax_rate": "0",
            "tax_included": True,
            "shipping_fee": "0",
            "shipping_included": True,
            "moq": 100,
            "lead_time_days": 7,
            "supports_invoice": True,
            "width_mm": actual_width,
            "length_mm": actual_length,
            "thickness_um": actual_thickness,
        }
        return {
            "id": quote_id,
            "source_sha256": "0" * 64,
            "extracted": {"fields": {name: {"value": value} for name, value in values.items()}},
        }

    result = compare_quotes(
        request,
        [
            quote("candidate", width, length, thickness),
            quote("baseline", "400", "300", "5"),
        ],
        analysis_as_of="2026-07-27",
    )
    candidate = next(item for item in result["quotes"] if item["quote_id"] == "candidate")

    assert candidate["eligible"] is expected_eligible
    assert candidate["match"]["passed"] is expected_eligible


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


def test_service_delete_request_removes_procurement_projection(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("待删除的采购任务")
    request_id = str(draft["id"])

    deleted = service.delete_request(request_id)

    assert deleted == {
        "request_id": request_id,
        "reference": draft["reference"],
        "deleted": True,
    }
    with pytest.raises(KeyError):
        service.get_request(request_id)
    assert service.repo.list_audit_events(request_id) == []
    assert all(item["id"] != request_id for item in service.list_requests())
    harness.close()


def test_service_delete_request_rejects_active_run(data_dir: Path) -> None:
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("不可删除的运行中任务")
    run_id = "active-delete-test-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(draft["session_id"]),
        root_run_id=run_id,
        status=RunStatus.running,
    )
    service.bind_run(str(draft["id"]), run_id=run_id)

    with pytest.raises(ProcurementError, match="正在运行"):
        service.delete_request(str(draft["id"]))

    assert service.get_request(str(draft["id"]))["id"] == draft["id"]
    harness.close()


@pytest.mark.parametrize(
    ("explicit_date", "expected_date"),
    [
        (None, "2026-08-24"),
        ("2026-08-21", "2026-08-21"),
    ],
)
def test_requirement_derives_delivery_deadline_but_preserves_explicit_date(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_date: str | None,
    expected_date: str,
) -> None:
    import agentharness.procurement.service as service_module

    monkeypatch.setattr(service_module, "_utcnow", lambda: "2026-08-04T09:00:00+00:00")
    truth = load_frozen_truth()
    payload = _request_body(truth)
    payload["constraints"]["max_lead_days"] = 20
    if explicit_date is None:
        payload["constraints"].pop("required_delivery_date", None)
    else:
        payload["constraints"]["required_delivery_date"] = explicit_date

    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    draft = service.create_draft("采购纸箱")
    run_id = "delivery-date-test-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(draft["session_id"]),
        root_run_id=run_id,
    )

    captured = service.capture_requirement(str(draft["id"]), payload, run_id=run_id)
    harness.close()

    assert captured["constraints"]["required_delivery_date"] == expected_date


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


def test_requirement_correction_revalidates_and_records_before_after_audit(
    data_dir: Path,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    payload = _request_body(truth)
    payload["quantity"] = 12_000
    payload["constraints"]["destination"] = "华东二仓"

    corrected = service.replace_requirement(
        str(request["id"]),
        payload,
        actor="采购员王敏",
    )
    report = service.audit_report(str(request["id"]))
    harness.close()

    assert corrected["quantity"] == 12_000
    assert corrected["constraints"]["destination"] == "华东二仓"
    event = next(item for item in report["audit_events"] if item["type"] == "requirement_corrected")
    assert event["actor"] == "采购员王敏"
    assert event["payload"]["after"]["quantity"] == 12_000
    assert "quantity" in event["payload"]["changed_fields"]


@pytest.mark.asyncio
async def test_requirement_confirmation_allows_put_through_write_guard(
    data_dir: Path,
    workspace: Path,
) -> None:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/procurement/requests", json=_request_body(truth))
            assert created.status_code == 201, created.text
            request_id = created.json()["id"]

            payload = _request_body(truth)
            payload["constraints"]["destination"] = "华东二仓"
            corrected = await client.put(
                f"/api/procurement/requests/{request_id}/requirement",
                json={**payload, "actor": "采购员"},
            )

            assert corrected.status_code == 200, corrected.text
            assert corrected.json()["constraints"]["destination"] == "华东二仓"
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


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
async def test_complete_procurement_scenario_uses_one_run_and_three_model_turns(
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
            assert runtime_report["usage"]["model_turns"] == 3
            assert {item["tool_name"] for item in invocations} == {
                "procurement_capture_requirement",
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
        assert [item["tool_name"] for item in invocations] == [
            "procurement_capture_requirement",
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
        assert usage["model_turns"] <= 4

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
        assert {item["kind"] for item in report["execution_artifacts"]} == {
            "purchase_order_draft",
            "supplier_confirmation_email",
        }
        order_artifact = next(
            item for item in report["execution_artifacts"] if item["kind"] == "purchase_order_draft"
        )
        order_response = await client.get(f"/api/artifacts/{order_artifact['artifact_id']}")
        assert order_response.status_code == 200
        assert "采购订单草稿" in order_response.json()["content"]
        usage = json.loads(completed["usage_json"])
        assert usage["model_turns"] <= 5

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
        assert corrected_theta.json()["status"] == "ready"
        assert (await client.get(f"/api/procurement/requests/{request_id}")).json()["status"] == "ready"
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


def test_comparison_rejects_quote_change_during_snapshot_build(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentharness.procurement.service as service_module

    truth = load_frozen_truth()
    cases = [
        next(item for item in truth["quotes"] if item["id"] == case_id)
        for case_id in ("q-alpha", "q-beta")
    ]
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(_request_body(truth))
    imported = []
    for case in cases:
        document = build_case_document(case)
        imported.append(
            service.import_quote(
                str(request["id"]),
                filename=case["filename"],
                data=document,
                extracted=parse_quote(case["filename"], document),
            )
        )

    initial_run = "concurrency-initial-run"
    concurrent_run = "concurrency-stale-run"
    for run_id in (initial_run, concurrent_run):
        harness.storage.create_run(
            run_id=run_id,
            session_id=str(request["session_id"]),
            root_run_id=run_id,
        )
    service.compare_for_agent(str(request["id"]), run_id=initial_run)

    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    original_compare_quotes = service_module.compare_quotes

    def blocked_compare(*args, **kwargs):
        result = original_compare_quotes(*args, **kwargs)
        started.set()
        if not release.wait(5):
            raise AssertionError("comparison test was not released")
        return result

    monkeypatch.setattr(service_module, "compare_quotes", blocked_compare)

    def analyze() -> None:
        try:
            service.compare_for_agent(str(request["id"]), run_id=concurrent_run)
        except BaseException as exc:  # noqa: BLE001 - assert the worker outcome below
            errors.append(exc)

    worker = threading.Thread(target=analyze)
    worker.start()
    try:
        assert started.wait(5)
        corrected = service.correct_field(
            str(request["id"]),
            str(imported[0]["id"]),
            field="shipping_fee",
            value="25",
        )
        assert corrected["status"] == "ready"
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ProcurementError)
    stored = service.get_request(str(request["id"]))
    assert stored["current_snapshot_id"] is None
    assert stored["comparison"] is None
    harness.close()


@pytest.mark.asyncio
async def test_invalid_currency_code_is_rejected_as_client_input(
    data_dir: Path,
    workspace: Path,
) -> None:
    truth = load_frozen_truth()
    body = _request_body(truth)
    body["constraints"] = {
        **body["constraints"],
        "base_currency": "12$",
        "fx_rates": {"12$": "1"},
    }
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/procurement/requests", json=body)
            assert response.status_code == 422
            assert (await client.get("/api/procurement/requests")).json() == []
    finally:
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
