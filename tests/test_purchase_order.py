"""Phase-5.1 purchase-order export tests (docs/agent-upgrade-2026-08-05.md 5.1)."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.harness import Harness
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth


def _upload(case):
    import base64

    return {
        "filename": case["filename"],
        "content_base64": base64.b64encode(build_case_document(case)).decode("ascii"),
    }


async def _wait_for_run_status(client: AsyncClient, run_id: str, statuses: set[str]):
    for _ in range(200):
        run = (await client.get(f"/api/runs/{run_id}")).json()
        if run["status"] in statuses:
            return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


async def _wait_for_comparison(client: AsyncClient, request_id: str, run_id: str):
    for _ in range(200):
        response = (await client.get(f"/api/procurement/requests/{request_id}")).json()
        if response.get("comparison") is not None:
            return response
        run = (await client.get(f"/api/runs/{run_id}")).json()
        if run["status"] in {"failed", "cancelled", "interrupted"}:
            raise AssertionError(f"run stopped: {run.get('error')}")
        await asyncio.sleep(0.02)
    raise AssertionError("no comparison")


@pytest.mark.asyncio
async def test_purchase_order_export_after_approval(data_dir, workspace) -> None:
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
            detail = await _wait_for_comparison(client, request_id, run_id)
            await _wait_for_run_status(client, run_id, {"require_human"})
            snapshot = detail["comparison"]

            approved = await client.post(
                f"/api/procurement/requests/{request_id}/decision",
                json={
                    "snapshot_id": snapshot["id"],
                    "input_sha256": snapshot["input_sha256"],
                    "quote_id": snapshot["result"]["recommended_quote_id"],
                    "confirmed": True,
                    "actor": "采购员",
                    "note": "同意下单",
                },
            )
            assert approved.status_code == 200, approved.text
            await _wait_for_run_status(client, run_id, {"completed"})

            order = (
                await client.get(
                    f"/api/procurement/requests/{request_id}/purchase-order"
                )
            ).json()
            assert order["request_id"] == request_id
            assert order["po_number"].startswith("PO-")
            assert order["supplier_name"]
            assert order["quantity"] == 10000
            assert order["unit"] == "piece"
            assert order["snapshot_id"] == snapshot["id"]
            assert order["input_sha256"] == snapshot["input_sha256"]
            assert order["approval_id"]
            assert order["evidence_sha256"]

            # CSV endpoint downloads the order with headers + values.
            csv_response = await client.get(
                f"/api/procurement/requests/{request_id}/purchase-order.csv"
            )
            assert csv_response.status_code == 200
            assert "text/csv" in csv_response.headers["content-type"]
            assert "attachment" in csv_response.headers["content-disposition"]
            body = csv_response.text
            assert "采购订单号" in body
            assert order["po_number"] in body
            assert order["supplier_name"] in body

            # Idempotent: same PO number on second call.
            again = (
                await client.get(
                    f"/api/procurement/requests/{request_id}/purchase-order"
                )
            ).json()
            assert again["po_number"] == order["po_number"]

            report = (
                await client.get(f"/api/procurement/requests/{request_id}/report")
            ).json()
            assert any(
                event["type"] == "purchase_order_created"
                for event in report["audit_events"]
            )
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()


@pytest.mark.asyncio
async def test_purchase_order_rejected_before_decision(data_dir, workspace) -> None:
    harness = Harness(data_dir=data_dir)
    app = create_app(harness=harness, workspace_roots=[workspace])
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/procurement/requests",
                json={
                    "title": "未审批采购",
                    "category": "ecommerce_packaging",
                    "item_name": "PE快递袋",
                    "quantity": 10,
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
            )
            assert created.status_code == 201
            request_id = created.json()["id"]
            response = await client.get(
                f"/api/procurement/requests/{request_id}/purchase-order"
            )
            assert response.status_code == 409
            assert "审批结论" in response.json()["detail"]
    finally:
        await app.state.procurement_agent.aclose()
        await app.state.run_supervisor.aclose()
        await harness.aclose()
