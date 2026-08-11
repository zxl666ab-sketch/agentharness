"""AI experience layer: read-only interpretation and review-suggestion helpers.

These features follow the procurement invariants: the model only explains or
suggests; it never writes amounts, eligibility, or decisions, and every call is
recorded in the audit trail. The buyer confirms suggestions through the normal
correction endpoint.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
)
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent, _fake_run_profile
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementError, ProcurementService


class _AiProvider:
    """Scripted read-only AI provider for the experience endpoints.

    ``reply`` may be a static string or a callable that receives the user
    prompt and returns the reply text (used when the test needs to echo a
    real generated id back into the response).
    """

    name = "reviewer"

    def __init__(self, reply) -> None:
        self.reply = reply
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.calls += 1
        user_prompt = next(
            (m.content for m in request.messages if m.role.value == "user"),
            "",
        )
        text = self.reply(user_prompt) if callable(self.reply) else self.reply
        yield ModelStreamItem(type=StreamItemType.text_delta, text=text)
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        yield ModelStreamItem(type=StreamItemType.done)


def _request_body(truth: dict) -> dict:
    request = truth["request"]
    return {
        "title": "AI 体验测试询价",
        "category": "ecommerce_packaging",
        "item_name": request["item_name"],
        "quantity": request["quantity"],
        "unit": request["unit"],
        "specifications": request["specifications"],
        "constraints": request["constraints"],
    }


async def _conversation_to_comparison(agent: ProcurementAgent, cases):
    accepted = await agent.start(
        message=(
            "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
            "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。"
        ),
        attachments=cases,
    )
    await agent._tasks[accepted["run_id"]]
    return accepted["purchase_request_id"]


@pytest.mark.asyncio
async def test_ai_interpretation_explains_snapshot_and_audits(data_dir) -> None:
    truth = load_frozen_truth()
    cases = [
        (case["filename"], build_case_document(case)) for case in truth["quotes"][:2]
    ]
    harness = Harness(data_dir=data_dir)
    reviewer = _AiProvider("结论：Alpha Packaging 最低。理由：确定性到货总成本最低。下一步：人工审批。")
    harness.register_provider("reviewer", reviewer)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    agent.review_provider = "reviewer"
    try:
        request_id = await _conversation_to_comparison(agent, cases)
        detail = service.get_request(request_id)
        assert detail["comparison"] is not None
        result = await agent.ai_interpretation(request_id)
        assert result["text"].startswith("结论：")
        assert result["snapshot_id"] == detail["comparison"]["id"]
        assert reviewer.calls == 1
        events = service.audit_report(request_id)["audit_events"]
        ai_events = [event for event in events if event["type"] == "ai_interpretation"]
        assert len(ai_events) == 1
        assert ai_events[0]["payload"]["snapshot_id"] == detail["comparison"]["id"]
        assert "Alpha Packaging" in ai_events[0]["payload"]["output"]
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_ai_interpretation_requires_snapshot(data_dir) -> None:
    harness = Harness(data_dir=data_dir)
    harness.register_provider("reviewer", _AiProvider("x"))
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    agent.review_provider = "reviewer"
    try:
        request = service.create_request(_request_body(load_frozen_truth()))
        with pytest.raises(ProcurementError):
            await agent.ai_interpretation(str(request["id"]))
        events = service.audit_report(str(request["id"]))["audit_events"]
        assert not any(event["type"] == "ai_interpretation" for event in events)
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_ai_review_suggestions_are_filtered_to_needs_review(data_dir) -> None:
    harness = Harness(data_dir=data_dir)

    def reviewer_reply(prompt: str) -> str:
        import json as _json

        payload = _json.loads(prompt)
        by_field = {field["field"]: field["quote_id"] for field in payload["fields"]}
        return _json.dumps(
            [
                {
                    "quote_id": by_field["supplier_name"],
                    "field": "supplier_name",
                    "suggested_value": "供应商甲",
                    "reason": "原文一致",
                    "confidence": 0.9,
                },
                {
                    "quote_id": by_field["moq"],
                    "field": "moq",
                    "suggested_value": 2000,
                    "reason": "原文可见起订量",
                    "confidence": 0.8,
                },
                {
                    "quote_id": by_field["supplier_name"],
                    "field": "unit_price",
                    "suggested_value": "999",
                    "reason": "stale",
                    "confidence": 0.5,
                },
            ],
            ensure_ascii=False,
        )

    reviewer = _AiProvider(reviewer_reply)
    harness.register_provider("reviewer", reviewer)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, run_profile=_fake_run_profile())
    agent.review_provider = "reviewer"
    try:
        request = service.create_request(_request_body(load_frozen_truth()))
        request_id = str(request["id"])

        def extracted(supplier: str, *, needs_review: bool, missing_moq: bool = False) -> dict:
            values = {
                "supplier_name": supplier,
                "item_description": "PE 快递袋",
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
                "lead_time_days": 10,
                "supports_invoice": True,
                "width_mm": "250",
                "length_mm": "350",
                "thickness_um": "60",
                "payment_terms": "Net 30",
                "moq": None if missing_moq else 1000,
            }
            fields = {}
            for name, value in values.items():
                needs = needs_review and name == "supplier_name"
                fields[name] = {
                    "value": value,
                    "confidence": 0.5 if needs else 1.0,
                    "status": "needs_review" if needs else "accepted",
                    "source": {
                        "document_kind": "xlsx",
                        "locator": "test",
                        "excerpt": "供应商甲" if needs else "",
                        "method": "test",
                    },
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
            filename="供应商甲报价.xlsx",
            data=b"quote-a",
            extracted=extracted("供应商甲", needs_review=True),
        )
        second = service.import_quote(
            request_id,
            filename="供应商乙报价.xlsx",
            data=b"quote-b",
            extracted=extracted("供应商乙", needs_review=False, missing_moq=True),
        )
        result = await agent.ai_review_suggestions(request_id)
        assert {s["field"] for s in result["suggestions"]} == {"supplier_name", "moq"}
        by_field = {s["field"]: s for s in result["suggestions"]}
        assert by_field["supplier_name"]["quote_id"] == first["id"]
        assert by_field["supplier_name"]["suggested_value"] == "供应商甲"
        assert by_field["supplier_name"]["confidence"] == 0.9
        assert by_field["moq"]["quote_id"] == second["id"]
        assert by_field["moq"]["suggested_value"] == 2000
        events = service.audit_report(request_id)["audit_events"]
        ai_events = [
            event for event in events if event["type"] == "ai_review_suggestions"
        ]
        assert len(ai_events) == 1
        assert ai_events[0]["payload"]["count"] == 2
    finally:
        await agent.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_ai_endpoints_over_http(data_dir, workspace) -> None:
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    harness = Harness(data_dir=data_dir)
    reviewer = _AiProvider("结论：推荐 Alpha Packaging。理由：确定性到货成本最低。下一步：人工审批。")
    harness.register_provider("reviewer", reviewer)
    app = create_app(harness=harness, workspace_roots=[workspace], execution_enabled=True)
    app.state.procurement_agent.review_provider = "reviewer"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            started = await client.post(
                "/api/procurement/conversations",
                json={
                    "message": (
                        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。"
                    ),
                    "attachments": [
                        {
                            "filename": case["filename"],
                            "content_base64": base64.b64encode(
                                build_case_document(case)
                            ).decode("ascii"),
                        }
                        for case in cases
                    ],
                },
            )
            assert started.status_code == 202, started.text
            request_id = started.json()["purchase_request_id"]
            detail = None
            for _ in range(300):
                detail = (
                    await client.get(f"/api/procurement/requests/{request_id}")
                ).json()
                if detail.get("comparison"):
                    break
                await asyncio.sleep(0.05)
            assert detail is not None and detail.get("comparison")

            resp = await client.post(
                f"/api/procurement/requests/{request_id}/ai-interpretation"
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["text"].startswith("结论：")
            assert body["snapshot_id"] == detail["comparison"]["id"]

            report = (
                await client.get(f"/api/procurement/requests/{request_id}/report")
            ).json()
            types = [event["type"] for event in report["audit_events"]]
            assert "ai_interpretation" in types
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()
