"""Generate three deterministic procurement demo packages outside the frozen truth set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from agentharness.procurement.evaluation import build_case_document


def _quote(
    item: dict[str, Any],
    *,
    case_id: str,
    supplier: str,
    kind: str,
    layout: str,
    unit_price: str,
    price_basis: int,
    moq: int,
    lead_time_days: int,
    expected_total: str,
    currency: str = "CNY",
    tax_rate: str = "0.13",
    tax_included: bool = True,
    shipping_fee: str = "0",
    shipping_included: bool = True,
    supports_invoice: bool = True,
    note: str = "",
) -> dict[str, Any]:
    fields = {
        "supplier_name": supplier,
        "item_description": item["description"],
        "material": item["material"],
        "color": item["color"],
        "print_colors": item["print_colors"],
        "currency": currency,
        "unit_price": unit_price,
        "price_basis": price_basis,
        "tax_rate": tax_rate,
        "tax_included": tax_included,
        "shipping_fee": shipping_fee,
        "shipping_included": shipping_included,
        "moq": moq,
        "lead_time_days": lead_time_days,
        "supports_invoice": supports_invoice,
        "width_mm": item["width_mm"],
        "length_mm": item["length_mm"],
        "thickness_um": item["thickness_um"],
        "payment_terms": "月结 30 天",
        "valid_until": "2026-12-31",
    }
    if item.get("height_mm"):
        fields["height_mm"] = item["height_mm"]
    return {
        "id": case_id,
        "supplier": supplier,
        "kind": kind,
        "layout": layout,
        "expected_landed_total_base": expected_total,
        "note": note,
        "fields": fields,
    }


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "directory": "01-仓储热敏标签",
        "title": "华东仓热敏不干胶标签采购",
        "item_name": "热敏不干胶标签",
        "quantity": 20_000,
        "specifications": {
            "width_mm": "100",
            "length_mm": "150",
            "thickness_um": "80",
            "material": "铜版纸",
            "color": "白色",
            "print_colors": 1,
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 10,
            "invoice_required": True,
            "size_tolerance_mm": "1",
            "thickness_tolerance_um": "5",
            "max_landed_unit_cost": "0.20",
            "destination": "华东仓",
        },
        "recommendation": "苏州标联",
        "recommendation_reason": "满足交期、开票和起订量约束，预计到货总成本最低。",
        "quotes": (
            {
                "case_id": "label-01",
                "supplier": "苏州标联",
                "kind": "xlsx",
                "layout": "xlsx_vertical",
                "unit_price": "180",
                "price_basis": 1000,
                "moq": 10_000,
                "lead_time_days": 7,
                "expected_total": "3600.00",
                "note": "合格，报价按每千张计价。",
            },
            {
                "case_id": "label-02",
                "supplier": "宁波印联",
                "kind": "pdf",
                "layout": "pdf_table",
                "unit_price": "0.16",
                "price_basis": 1,
                "moq": 5_000,
                "lead_time_days": 9,
                "shipping_fee": "600",
                "shipping_included": False,
                "expected_total": "3800.00",
                "note": "合格，但运费另计。",
            },
            {
                "case_id": "label-03",
                "supplier": "华南标签",
                "kind": "xlsx",
                "layout": "xlsx_horizontal",
                "unit_price": "150",
                "price_basis": 1000,
                "moq": 50_000,
                "lead_time_days": 6,
                "expected_total": "3000.00",
                "note": "低价但 MOQ 高于本次采购量，应被硬约束淘汰。",
            },
        ),
    },
    {
        "directory": "02-出口瓦楞纸箱",
        "title": "苏州工厂出口瓦楞纸箱采购",
        "item_name": "五层瓦楞纸箱",
        "quantity": 5_000,
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
        "recommendation": "浙江箱业",
        "recommendation_reason": "在满足开票、交期、规格和预算的报价中到货总成本最低。",
        "quotes": (
            {
                "case_id": "carton-01",
                "supplier": "浙江箱业",
                "kind": "xlsx",
                "layout": "xlsx_sections",
                "unit_price": "3.20",
                "price_basis": 1,
                "moq": 3_000,
                "lead_time_days": 18,
                "shipping_fee": "800",
                "shipping_included": False,
                "expected_total": "16800.00",
                "note": "合格，含税价，工厂送货。",
            },
            {
                "case_id": "carton-02",
                "supplier": "华南纸业",
                "kind": "pdf",
                "layout": "pdf_columns",
                "unit_price": "0.38",
                "price_basis": 1,
                "moq": 2_000,
                "lead_time_days": 15,
                "currency": "USD",
                "tax_rate": "0",
                "shipping_fee": "300",
                "shipping_included": False,
                "supports_invoice": False,
                "expected_total": "15840.00",
                "note": "外币报价成本较低，但不支持合规发票，应被硬约束淘汰。",
            },
            {
                "case_id": "carton-03",
                "supplier": "沪宁纸品",
                "kind": "xlsx",
                "layout": "xlsx_horizontal",
                "unit_price": "3.45",
                "price_basis": 1,
                "moq": 5_000,
                "lead_time_days": 16,
                "expected_total": "17250.00",
                "note": "合格，但综合成本高于浙江箱业。",
            },
        ),
    },
    {
        "directory": "03-透明封箱胶带",
        "title": "上海仓 BOPP 透明封箱胶带采购",
        "item_name": "BOPP透明封箱胶带",
        "quantity": 3_000,
        "specifications": {
            "width_mm": "48",
            "length_mm": "100000",
            "thickness_um": "50",
            "material": "BOPP",
            "color": "透明",
            "print_colors": 0,
        },
        "constraints": {
            "base_currency": "CNY",
            "fx_rates": {"CNY": "1"},
            "max_lead_days": 12,
            "invoice_required": True,
            "size_tolerance_mm": "1",
            "thickness_tolerance_um": "5",
            "max_landed_unit_cost": "4.20",
            "destination": "上海仓",
        },
        "recommendation": "嘉兴胶粘",
        "recommendation_reason": "在满足 MOQ、交期、开票和预算的报价中到货总成本最低。",
        "quotes": (
            {
                "case_id": "tape-01",
                "supplier": "沪上胶带",
                "kind": "xlsx",
                "layout": "xlsx_vertical",
                "unit_price": "3.60",
                "price_basis": 1,
                "moq": 1_000,
                "lead_time_days": 8,
                "shipping_fee": "600",
                "shipping_included": False,
                "expected_total": "11400.00",
                "note": "合格，交期最短但成本略高。",
            },
            {
                "case_id": "tape-02",
                "supplier": "嘉兴胶粘",
                "kind": "pdf",
                "layout": "pdf_vertical",
                "unit_price": "3.30",
                "price_basis": 1,
                "moq": 2_000,
                "lead_time_days": 10,
                "shipping_fee": "500",
                "shipping_included": False,
                "expected_total": "10400.00",
                "note": "合格，预计到货总成本最低。",
            },
            {
                "case_id": "tape-03",
                "supplier": "北辰耗材",
                "kind": "xlsx",
                "layout": "xlsx_sections",
                "unit_price": "2.80",
                "price_basis": 1,
                "moq": 10_000,
                "lead_time_days": 7,
                "expected_total": "8400.00",
                "note": "低价但 MOQ 高于本次采购量，应被硬约束淘汰。",
            },
        ),
    },
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remove_previous_generation(target: Path) -> None:
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    owned_names = {"request.json", "README.md", "manifest.json"}
    owned_names.update(
        str(item.get("文件") or "")
        for item in previous.get("报价文件", [])
        if isinstance(item, dict)
    )
    for name in owned_names:
        path = (target / name).resolve()
        if name and path.parent == target and path.is_file():
            path.unlink()


def _request_payload(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "title": scenario["title"],
        "category": "ecommerce_packaging",
        "item_name": scenario["item_name"],
        "quantity": scenario["quantity"],
        "unit": "piece",
        "specifications": scenario["specifications"],
        "constraints": scenario["constraints"],
    }


def _render_readme(target: Path) -> None:
    rows = [
        "# 三套采购演示单",
        "",
        "这些文件是本地演示数据，不含真实企业或个人信息。每个目录都可以作为一个独立采购任务，上传 `request.json` 中的采购目标，并上传同目录下的三份报价文件。",
        "",
        "| 目录 | 场景 | 预期推荐 |",
        "|---|---|---|",
    ]
    for scenario in SCENARIOS:
        rows.append(
            f"| `{scenario['directory']}` | {scenario['title']} | {scenario['recommendation']} |"
        )
    rows.extend(
        [
            "",
            "生成命令：",
            "",
            "```powershell",
            "uv run python scripts/generate_procurement_scenarios.py",
            "```",
            "",
            "每套报价都包含不同文件版式、计价口径或硬约束，推荐结果以服务端确定性规则为准。",
            "",
        ]
    )
    (target / "README.md").write_text("\n".join(rows), encoding="utf-8")


def generate_scenarios(target: Path) -> int:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        scenario_dir = target / scenario["directory"]
        scenario_dir.mkdir(parents=True, exist_ok=True)
        _remove_previous_generation(scenario_dir)
        request = _request_payload(scenario)
        request_bytes = (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (scenario_dir / "request.json").write_bytes(request_bytes)

        quote_manifest: list[dict[str, Any]] = []
        item = {
            "description": (
                f"{scenario['item_name']} {scenario['specifications']['width_mm']}×"
                f"{scenario['specifications']['length_mm']}"
                + (f"×{scenario['specifications']['height_mm']}" if scenario["specifications"].get("height_mm") else "")
                + f" mm，厚度 {scenario['specifications']['thickness_um']} 微米，"
                + f"{scenario['specifications']['color']}"
            ),
            "material": scenario["specifications"]["material"],
            "color": scenario["specifications"]["color"],
            "print_colors": scenario["specifications"]["print_colors"],
            "width_mm": scenario["specifications"]["width_mm"],
            "length_mm": scenario["specifications"]["length_mm"],
            "thickness_um": scenario["specifications"]["thickness_um"],
            "height_mm": scenario["specifications"].get("height_mm"),
        }
        structured_quotes = []
        for quote_spec in scenario["quotes"]:
            quote = _quote(item, **quote_spec)
            suffix = ".xlsx" if quote["kind"] == "xlsx" else ".pdf"
            filename = f"{quote['supplier']}-{scenario['item_name']}-报价{suffix}"
            quote["filename"] = filename
            document = build_case_document({"fields": quote["fields"], "layout": quote["layout"], "filename": filename}, locale="zh-CN")
            (scenario_dir / filename).write_bytes(document)
            quote_manifest.append(
                {
                    "案例ID": quote["id"],
                    "文件": filename,
                    "SHA-256": _sha256(document),
                    "供应商": quote["supplier"],
                    "版式": quote["layout"],
                    "预计到货总成本CNY": quote["expected_landed_total_base"],
                    "说明": quote["note"],
                }
            )
            structured_quotes.append({
                "id": quote["id"],
                "supplier": quote["supplier"],
                "kind": quote["kind"],
                "layout": quote["layout"],
                "filename": quote["filename"],
                "expected_landed_total_base": quote["expected_landed_total_base"],
                "note": quote["note"],
                "fields": quote["fields"],
            })
        (scenario_dir / "quotes.json").write_text(
            json.dumps({"quotes": structured_quotes}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest = {
            "清单版本": 1,
            "说明": "合成采购演示数据，仅用于本地产品演示，不含真实企业或个人信息。",
            "采购单文件": {"文件": "request.json", "SHA-256": _sha256(request_bytes)},
            "采购任务": scenario["title"],
            "预期推荐供应商": scenario["recommendation"],
            "预期推荐理由": scenario["recommendation_reason"],
            "报价文件": quote_manifest,
            "结构化报价": "quotes.json",
        }
        (scenario_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _render_readme(target)
    print(f"已生成 {len(SCENARIOS)} 套采购演示单：{target}")
    return len(SCENARIOS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/procurement-scenarios"))
    args = parser.parse_args()
    generate_scenarios(args.output)


if __name__ == "__main__":
    main()
