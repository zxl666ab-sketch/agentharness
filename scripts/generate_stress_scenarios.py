"""Generate real-model stress-test scenario sets for the procurement agent.

Each scenario is one directory under ``output/procurement-scenarios/stress-batch-N``:
  - request.json  : service-compatible requirement payload
  - prompt.txt    : the exact Chinese prompt sent to the real model
  - manifest.json : input fingerprints + deterministic expected results
  - quote files   : deterministic XLSX/PDF quotes via ``build_case_document``

Deterministic expectations (parse + compare_quotes, 0 model calls) are computed
at generation time and stored in manifest.json so a later live run can be
compared against them.

Usage:
  uv run python scripts/generate_stress_scenarios.py --count 20 --start 1 --output output/procurement-scenarios/stress-batch-1
  uv run python scripts/generate_stress_scenarios.py --count 5 --start 21 --output output/procurement-scenarios/stress-batch-2
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agentharness.procurement.costing import compare_quotes
from agentharness.procurement.evaluation import build_case_document
from agentharness.procurement.parsing import fields_requiring_review, parse_quote

DEFAULT_OUTPUT = Path("output/procurement-scenarios/stress-batch-1")

# 压测场景的单一基准日期：预期结果、created_at、有效期边界都锚定它，
# 避免 8 月 8 日后重新生成时用 date.today() 使预期随日期漂移。
AS_OF = date(2026, 8, 8)

XLSX_LAYOUTS = ["xlsx_vertical", "xlsx_horizontal", "xlsx_sections"]
PDF_LAYOUTS = ["pdf_vertical", "pdf_columns", "pdf_table"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inject_pdf_tail(data: bytes, lines: list[str]) -> bytes:
    """Append an extra PDF page carrying untrusted text (e.g. prompt-injection
    payloads) without touching the deterministic quote layout."""
    import io

    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from agentharness.procurement.evaluation import _pdf_font

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font = _pdf_font("zh-CN")
    pdf.setFont(font, 11)
    y = 770
    for line in lines:
        pdf.drawString(54, y, line)
        y -= 22
    pdf.save()
    extra = io.BytesIO(buffer.getvalue())
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.append(extra)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _base_fields(sc: dict[str, Any], supplier: str) -> dict[str, Any]:
    specs = sc["specs"]
    max_lead = int(sc["constraints"]["max_lead_days"])
    default_lead = max(1, min(10, max_lead - 2))
    default_moq = max(1, int(sc["quantity"] // 5))
    return {
        "supplier_name": supplier,
        "item_description": (
            f"{specs['material']}{sc['item_name']} {specs['width_mm']}x{specs['length_mm']}mm "
            f"厚{specs['thickness_um']}微米 {specs['color']} {specs['print_colors']}色印刷"
        ),
        "material": specs["material"],
        "color": specs["color"],
        "print_colors": specs["print_colors"],
        "currency": "CNY",
        "unit_price": "0.50",
        "price_basis": 1,
        "tax_rate": "0.13",
        "tax_included": True,
        "shipping_fee": "0",
        "shipping_included": True,
        "moq": default_moq,
        "lead_time_days": default_lead,
        "supports_invoice": True,
        "width_mm": specs["width_mm"],
        "length_mm": specs["length_mm"],
        "thickness_um": specs["thickness_um"],
        "payment_terms": "月结30天",
        "valid_until": "2026-12-31",
    }


def _quote_case(sc: dict[str, Any], quote: dict[str, Any], index: int) -> dict[str, Any]:
    fields = _base_fields(sc, quote["supplier"])
    fields.update(quote.get("fields", {}))
    suffix = "xlsx" if quote["kind"] == "xlsx" else "pdf"
    filename = quote.get("filename") or f"{quote['supplier']}-{sc['item_name']}-报价.{suffix}"
    return {
        "id": f"{sc['slug']}-q{index + 1}",
        "filename": filename,
        "kind": quote["kind"],
        "layout": quote["layout"],
        "fields": fields,
        "omit_fields": quote.get("omit_fields", []),
    }


def _internal_request(sc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sc["slug"],
        "item_name": sc["item_name"],
        "quantity": sc["quantity"],
        "unit": "piece",
        "specifications": deepcopy(sc["specs"]),
        "constraints": deepcopy(sc["constraints"]),
        "created_at": f"{AS_OF.isoformat()}T00:00:00+00:00",
    }


def _expected_analysis(sc: dict[str, Any], cases: list[dict[str, Any]], files: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Deterministic parse + compare (no model) used for manifest expectations."""
    request = _internal_request(sc)
    quotes: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for case, (filename, data) in zip(cases, files, strict=True):
        extracted = parse_quote(filename, data, time_budget_s=15.0)
        quote_id = f"{sc['slug']}-q{len(quotes) + 1}"
        quotes.append(
            {
                "id": quote_id,
                "source_sha256": _sha256(data),
                "supplier_name": case["fields"].get("supplier_name"),
                "extracted": extracted,
            }
        )
        missing = fields_requiring_review(extracted)
        if missing:
            gaps.append({"quote_id": quote_id, "filename": filename, "fields": missing})
    if gaps:
        return {
            "status": "needs_review",
            "review_gaps": gaps,
            "eligible_count": None,
            "recommended_quote_id": None,
            "recommended_supplier": None,
            "exclusions": [],
            "note": "存在待复核字段，确定性比价被人工复核门禁拦截（这是预期行为，不是失败）。",
        }
    result = compare_quotes(request, quotes, analysis_as_of=AS_OF)
    by_id = {quote["quote_id"]: quote for quote in result["quotes"]}
    recommended = None
    if result.get("recommended_quote_id"):
        rec = by_id[result["recommended_quote_id"]]
        recommended = rec["supplier_name"]
    exclusions = {
        quote["quote_id"]: [item["code"] for item in quote["exclusion_reasons"]]
        for quote in result["quotes"]
    }
    return {
        "status": "completed",
        "review_gaps": [],
        "eligible_count": result["eligible_count"],
        "excluded_count": result["excluded_count"],
        "recommended_quote_id": result.get("recommended_quote_id"),
        "recommended_supplier": recommended,
        "exclusions": exclusions,
        "explanation": result["recommendation_explanation"],
        "analysis_as_of": result["analysis_as_of"],
    }


def generate(target: Path, definitions: list[dict[str, Any]], start: int) -> int:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    for offset, sc in enumerate(definitions):
        index = start + offset
        scenario_dir = target / f"{index:02d}-{sc['slug']}"
        scenario_dir.mkdir(parents=True, exist_ok=True)

        request_payload = {
            "title": sc["title"],
            "category": "ecommerce_packaging",
            "item_name": sc["item_name"],
            "quantity": sc["quantity"],
            "unit": "piece",
            "specifications": sc["specs"],
            "constraints": sc["constraints"],
        }
        request_bytes = (json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        prompt_bytes = (sc["prompt"].strip() + "\n").encode("utf-8")

        cases = [_quote_case(sc, quote, i) for i, quote in enumerate(sc["quotes"])]
        files: list[tuple[str, bytes]] = []
        for case, quote in zip(cases, sc["quotes"], strict=True):
            data = build_case_document(case, locale="zh-CN")
            extra = quote.get("extra_text")
            if extra:
                data = _inject_pdf_tail(data, list(extra))
            (scenario_dir / case["filename"]).write_bytes(data)
            files.append((case["filename"], data))

        (scenario_dir / "request.json").write_bytes(request_bytes)
        (scenario_dir / "prompt.txt").write_bytes(prompt_bytes)

        expected = _expected_analysis(sc, cases, files)
        quote_entries = []
        for case, (filename, data) in zip(cases, files, strict=True):
            # _expected_analysis 使用顺序 id：{slug}-q1/q2/q3
            seq = case["id"].rsplit("-", 1)[-1]
            exclusions = (expected.get("exclusions") or {}).get(f"{sc['slug']}-{seq}", [])
            quote_entries.append(
                {
                    "文件": filename,
                    "SHA-256": _sha256(data),
                    "供应商": case["fields"].get("supplier_name"),
                    "版式": case["layout"],
                    "预期淘汰": exclusions,
                    "说明": sc.get("quote_notes", {}).get(case["fields"].get("supplier_name"), ""),
                }
            )

        manifest = {
            "清单版本": 1,
            "说明": "真实模型压测场景：全部字段为合成数据，不含真实企业或个人信息。预期结果由确定性解析+比价（0 模型调用）在生成时计算。",
            "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
            "采购任务": sc["title"],
            "压测重点": sc.get("stress_focus", ""),
            "提示词": {"文件": "prompt.txt", "SHA-256": _sha256(prompt_bytes)},
            "采购需求": {"文件": "request.json", "SHA-256": _sha256(request_bytes)},
            "预期状态": expected["status"],
            "预期合格数": expected.get("eligible_count"),
            "预期淘汰数": expected.get("excluded_count"),
            "预期推荐供应商": expected.get("recommended_supplier"),
            "预期推荐理由": expected.get("explanation"),
            "报价文件": quote_entries,
        }
        _write_json(scenario_dir / "manifest.json", manifest)
        print(f"[{index:02d}] {sc['slug']}: expected={expected.get('recommended_supplier')} eligible={expected.get('eligible_count')} status={expected['status']}")
    return len(definitions)


def scenario_definitions() -> list[dict[str, Any]]:
    return [
        # ----------------------------------------------------------------- 01
        {
            "slug": "pe-kuaididai-biaozhun",
            "title": "华东仓 PE 快递袋标准询价",
            "item_name": "PE快递袋",
            "quantity": 10000,
            "specs": {"width_mm": "250", "length_mm": "350", "thickness_um": "60", "material": "PE", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1", "USD": "7.2"}, "max_lead_days": 15, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "3", "max_landed_unit_cost": "0.70", "destination": "上海松江"},
            "stress_focus": "基线：三家报价全部合格，验证正常链路与推荐排序。",
            "prompt": "华东仓要采购 10000 个 PE 白色 PE快递袋，规格 250×350mm、厚度 60 微米、单色印刷，15 天内必须送到上海松江，需要开增值税专用发票，到货单价预算不超过 0.70 元。USD/CNY 按 7.2 折算，尺寸公差 2mm、厚度公差 3 微米。请比较附件里的三家报价，把合格供应商按到货总成本从低到高排序并给出推荐。",
            "quotes": [
                {"supplier": "华东优包", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.52", "moq": 3000, "lead_time_days": 9}},
                {"supplier": "沪上包装", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.55", "moq": 2000, "lead_time_days": 12}},
                {"supplier": "星河包装", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "0.49", "moq": 5000, "lead_time_days": 10}},
            ],
            "quote_notes": {"华东优包": "合格，成本居中。", "沪上包装": "合格，成本最高。", "星河包装": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 02
        {
            "slug": "gongjimo-kuaididai-shuangse",
            "title": "共挤膜快递袋双色印刷询价",
            "item_name": "共挤膜快递袋",
            "quantity": 20000,
            "specs": {"width_mm": "300", "length_mm": "400", "thickness_um": "70", "material": "PE", "color": "白色", "print_colors": 2},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 12, "invoice_required": True, "size_tolerance_mm": "3", "thickness_tolerance_um": "5", "destination": "义乌仓"},
            "stress_focus": "双色印刷 + 三家全合格，验证印刷色数忠实保留。",
            "prompt": "义乌仓双 11 备货，采购 2 万个 PE 白色共挤膜快递袋，尺寸 300×400mm，厚 70 微米，双色印刷，12 天内交付义乌仓，必须开票。尺寸公差 3mm、厚度公差 5 微米。附件是三家供应商报价，请比价并推荐最划算的一家，说明理由。",
            "quotes": [
                {"supplier": "义乌腾飞包装", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.60", "moq": 5000, "lead_time_days": 8}},
                {"supplier": "金华优材", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.56", "moq": 10000, "lead_time_days": 10}},
                {"supplier": "宁波宏达", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "0.53", "moq": 8000, "lead_time_days": 9}},
            ],
            "quote_notes": {"义乌腾飞包装": "合格，成本最高。", "金华优材": "合格。", "宁波宏达": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 03
        {
            "slug": "kejiangjie-kuaididai-pla",
            "title": "可降解 PLA 快递袋询价",
            "item_name": "可降解快递袋",
            "quantity": 8000,
            "specs": {"width_mm": "260", "length_mm": "380", "thickness_um": "55", "material": "PLA", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 20, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "3", "destination": "深圳仓"},
            "stress_focus": "MOQ 硬约束：一家 MOQ 高于采购量必须被淘汰。",
            "prompt": "深圳仓需要环保可降解快递袋，材质 PLA，白色，单色印刷，规格 260×380mm、厚度 55 微米，采购 8000 个，20 天内交付，需开 13% 专票，尺寸公差 2mm、厚度公差 3 微米。附件有三份报价，请比较并推荐合格供应商。",
            "quotes": [
                {"supplier": "深圳绿源材料", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.68", "moq": 4000, "lead_time_days": 12}},
                {"supplier": "东莞可降解包装", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.62", "moq": 9000, "lead_time_days": 10}},
                {"supplier": "惠州新材科技", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "0.65", "moq": 5000, "lead_time_days": 15}},
            ],
            "quote_notes": {"深圳绿源材料": "合格。", "东莞可降解包装": "MOQ 9000 高于采购量 8000，预期淘汰。", "惠州新材科技": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 04
        {
            "slug": "lajidai-hdpe-jiqi",
            "title": "HDPE 垃圾袋急单询价",
            "item_name": "垃圾袋",
            "quantity": 30000,
            "specs": {"width_mm": "500", "length_mm": "600", "thickness_um": "25", "material": "HDPE", "color": "黑色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 7, "invoice_required": False, "size_tolerance_mm": "5", "thickness_tolerance_um": "5", "destination": "广州仓"},
            "stress_focus": "急单：交期硬约束 + 无需开票；一家交期超标必须被淘汰。",
            "prompt": "广州仓急用，采购 3 万个 HDPE 黑色垃圾袋，规格 500×600mm、厚 25 微米，无印刷，7 天内必须到货广州仓，不需要发票。尺寸公差 5mm、厚度公差 5 微米。请用附件报价比价，交期超过 7 天的直接排除，推荐合格供应商。",
            "quotes": [
                {"supplier": "广州洁达", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.18", "moq": 10000, "lead_time_days": 5, "supports_invoice": False}},
                {"supplier": "佛山环卫用品", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.16", "moq": 20000, "lead_time_days": 9, "supports_invoice": False}},
                {"supplier": "中山塑业", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "0.17", "moq": 15000, "lead_time_days": 6, "supports_invoice": False}},
            ],
            "quote_notes": {"广州洁达": "合格。", "佛山环卫用品": "交期 9 天超上限 7 天，预期淘汰。", "中山塑业": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 05
        {
            "slug": "qipao-dai-pe",
            "title": "PE 气泡袋采购",
            "item_name": "气泡袋",
            "quantity": 15000,
            "specs": {"width_mm": "200", "length_mm": "300", "thickness_um": "100", "material": "PE", "color": "白色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 18, "invoice_required": True, "size_tolerance_mm": "3", "thickness_tolerance_um": "10", "destination": "武汉仓"},
            "stress_focus": "开票硬约束：一家不可开票必须被淘汰；另一家低置信度供应商名待复核。",
            "prompt": "武汉仓采购 15000 个白色 PE 气泡袋，规格 200×300mm、厚 100 微米，无印刷，18 天内交付，必须开票。尺寸公差 3mm、厚度公差 10 微米。附件三家报价请比价，不能开票的不要选。",
            "quotes": [
                {"supplier": "武汉申通包装", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.42", "moq": 5000, "lead_time_days": 10}},
                {"supplier": "孝感缓冲材料", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "0.39", "moq": 8000, "lead_time_days": 12, "supports_invoice": False}},
                {"supplier": "黄石气泡包装", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "0.40", "moq": 6000, "lead_time_days": 11}},
            ],
            "quote_notes": {"武汉申通包装": "合格。", "孝感缓冲材料": "不可开票，预期淘汰。", "黄石气泡包装": "合格且到货成本最低，预期推荐。"},
        },
        # ----------------------------------------------------------------- 06
        {
            "slug": "qipao-mo-pe",
            "title": "PE 气泡膜卷材采购",
            "item_name": "气泡膜",
            "quantity": 12000,
            "specs": {"width_mm": "600", "length_mm": "50000", "thickness_um": "90", "material": "PE", "color": "透明", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 14, "invoice_required": True, "size_tolerance_mm": "5", "thickness_tolerance_um": "10", "destination": "成都仓"},
            "stress_focus": "报价有效期硬约束：过期报价必须被淘汰。",
            "prompt": "成都仓采购 12000 卷透明 PE 气泡膜，宽 600mm、长 50000mm、厚 90 微米，无印刷，14 天内交付，需开票，尺寸公差 5mm、厚度公差 10 微米。附件三家报价，请检查报价有效期并只比较仍在有效期内的报价。",
            "quotes": [
                {"supplier": "成都缓冲包装", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.85", "moq": 3000, "lead_time_days": 8, "valid_until": "2026-12-31"}},
                {"supplier": "重庆华美包装", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "0.80", "moq": 5000, "lead_time_days": 10, "valid_until": "2026-01-31"}},
                {"supplier": "绵阳鑫达", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.82", "moq": 4000, "lead_time_days": 9, "valid_until": "2026-12-31"}},
            ],
            "quote_notes": {"成都缓冲包装": "合格。", "重庆华美包装": "报价已于 2026-01-31 过期，预期淘汰。", "绵阳鑫达": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 07
        {
            "slug": "zhenzhumian-epe",
            "title": "EPE 珍珠棉卷材采购",
            "item_name": "珍珠棉",
            "quantity": 6000,
            "specs": {"width_mm": "1000", "length_mm": "100000", "thickness_um": "3000", "material": "EPE", "color": "白色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 16, "invoice_required": True, "size_tolerance_mm": "5", "thickness_tolerance_um": "300", "max_landed_unit_cost": "1.50", "destination": "苏州工厂"},
            "stress_focus": "预算硬约束：到货单价超预算的报价必须被淘汰。",
            "prompt": "苏州工厂需要 6000 卷白色 EPE 珍珠棉，宽 1000mm、长 100000mm、厚 3mm（3000 微米），无印刷，16 天内交付，必须开票，到货单价预算 1.50 元。尺寸公差 5mm、厚度公差 300 微米。请比价附件三家报价，超预算的排除掉。",
            "quotes": [
                {"supplier": "苏州海棉材料", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "1.45", "moq": 2000, "lead_time_days": 12}},
                {"supplier": "无锡珍珠棉厂", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "1.60", "moq": 3000, "lead_time_days": 10}},
                {"supplier": "昆山宏盛", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "1.38", "moq": 3000, "lead_time_days": 14}},
            ],
            "quote_notes": {"苏州海棉材料": "合格。", "无锡珍珠棉厂": "到货单价 1.60 超预算 1.50，预期淘汰。", "昆山宏盛": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 08
        {
            "slug": "chanraomo-pe-stretch",
            "title": "PE 缠绕膜采购",
            "item_name": "缠绕膜",
            "quantity": 10000,
            "specs": {"width_mm": "500", "length_mm": "300000", "thickness_um": "20", "material": "PE", "color": "透明", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 12, "invoice_required": True, "size_tolerance_mm": "4", "thickness_tolerance_um": "2", "destination": "宁波港"},
            "stress_focus": "规格硬约束：宽度超出公差的报价必须被淘汰。",
            "prompt": "宁波港出口包装用，采购 10000 卷透明 PE 缠绕膜，规格 500×300000mm、厚 20 微米，无印刷，12 天内交付宁波港，需开票，尺寸公差 4mm、厚度公差 2 微米。附件三家报价，请比价并淘汰规格不符的。",
            "quotes": [
                {"supplier": "宁波拉伸膜", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "1.10", "moq": 3000, "lead_time_days": 8}},
                {"supplier": "北仑塑胶", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "1.05", "moq": 4000, "lead_time_days": 10, "width_mm": "510", "item_description": "PE缠绕膜 510x300000mm 厚20微米 透明 0色印刷"}},
                {"supplier": "镇海包装材料", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "1.08", "moq": 5000, "lead_time_days": 9}},
            ],
            "quote_notes": {"宁波拉伸膜": "合格。", "北仑塑胶": "宽度 510mm 超公差 4mm，预期淘汰。", "镇海包装材料": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 09
        {
            "slug": "bopp-fengxiangjiaodai",
            "title": "BOPP 透明封箱胶带美元报价",
            "item_name": "BOPP透明封箱胶带",
            "quantity": 5000,
            "specs": {"width_mm": "48", "length_mm": "100000", "thickness_um": "50", "material": "BOPP", "color": "透明", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1", "USD": "7.2"}, "max_lead_days": 15, "invoice_required": True, "size_tolerance_mm": "1", "thickness_tolerance_um": "5", "destination": "上海仓"},
            "stress_focus": "外币（USD）报价按汇率折算到 CNY 后再比价。",
            "prompt": "上海仓采购 5000 卷 BOPP 透明封箱胶带，宽 48mm、长 100000mm、厚 50 微米，无印刷，15 天内交付上海仓，需开票。USD/CNY 按 7.2，尺寸公差 1mm、厚度公差 5 微米。附件里有美元报价，请统一折算成人民币后比价并推荐。",
            "quotes": [
                {"supplier": "上海胶带工业", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "3.60", "moq": 2000, "lead_time_days": 9}},
                {"supplier": "嘉兴胶粘", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.50", "price_basis": 1, "currency": "USD", "moq": 3000, "lead_time_days": 11}},
                {"supplier": "沪上胶带", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "3.80", "moq": 1000, "lead_time_days": 7}},
            ],
            "quote_notes": {"上海胶带工业": "合格，与嘉兴同价且交期更短（9 vs 11 天），预期推荐。", "嘉兴胶粘": "USD 报价 0.50×7.2=3.60 CNY，与上海胶带工业同价但交期更长，排序第二。", "沪上胶带": "合格。"},
        },
        # ----------------------------------------------------------------- 10
        {
            "slug": "niupizhi-jiaodai",
            "title": "牛皮纸胶带采购",
            "item_name": "牛皮纸胶带",
            "quantity": 8000,
            "specs": {"width_mm": "60", "length_mm": "50000", "thickness_um": "120", "material": "牛皮纸", "color": "牛皮色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 18, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "20", "destination": "天津港"},
            "stress_focus": "未税价 + 运费另计的报价必须正确加到到货成本。",
            "prompt": "天津港采购 8000 卷牛皮纸胶带，宽 60mm、长 50000mm、厚 120 微米，无印刷，18 天内交付，必须开票，尺寸公差 2mm、厚度公差 20 微米。附件三家报价中有未税价和运费另计的，请正确计算到货总成本后比价。",
            "quotes": [
                {"supplier": "天津胶带制品", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "4.20", "moq": 2000, "lead_time_days": 10}},
                {"supplier": "河北包装材料", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "3.90", "moq": 3000, "lead_time_days": 12, "tax_included": False, "shipping_fee": "800", "shipping_included": False}},
                {"supplier": "唐山胶粘带", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "4.00", "moq": 3000, "lead_time_days": 9}},
            ],
            "quote_notes": {"天津胶带制品": "合格。", "河北包装材料": "未税+运费另计，到货成本较高，仍合格。", "唐山胶粘带": "合格，预期推荐。"},
        },
        # ----------------------------------------------------------------- 11
        {
            "slug": "remin-buganliao-biaoqian",
            "title": "热敏不干胶标签采购（每千个计价）",
            "item_name": "热敏不干胶标签",
            "quantity": 20000,
            "specs": {"width_mm": "100", "length_mm": "150", "thickness_um": "80", "material": "铜版纸", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 10, "invoice_required": True, "size_tolerance_mm": "1", "thickness_tolerance_um": "5", "destination": "华东仓"},
            "stress_focus": "每千个计价的报价必须归一化到每个后再比价。",
            "prompt": "华东仓采购 20000 个白色热敏不干胶标签（铜版纸），尺寸 100×150mm、厚 80 微米、单色印刷，10 天内交付华东仓，必须开票，尺寸公差 1mm、厚度公差 5 微米。附件报价里有按“每千个”计价的，请换算成每个后比较到货总成本并推荐。",
            "quotes": [
                {"supplier": "华南标签", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "520", "price_basis": 1000, "moq": 5000, "lead_time_days": 7}},
                {"supplier": "宁波印联", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.56", "price_basis": 1, "moq": 3000, "lead_time_days": 6}},
                {"supplier": "苏州标联", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.54", "price_basis": 1, "moq": 8000, "lead_time_days": 8}},
            ],
            "quote_notes": {"华南标签": "520/千个=0.52/个，合格且预期推荐。", "宁波印联": "合格。", "苏州标联": "合格。"},
        },
        # ----------------------------------------------------------------- 12
        {
            "slug": "tongbanzhi-biaoqian",
            "title": "铜版纸不干胶标签采购（组合约束）",
            "item_name": "铜版纸标签",
            "quantity": 25000,
            "specs": {"width_mm": "80", "length_mm": "120", "thickness_um": "100", "material": "铜版纸", "color": "白色", "print_colors": 4},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 15, "invoice_required": True, "size_tolerance_mm": "1", "thickness_tolerance_um": "5", "destination": "郑州仓"},
            "stress_focus": "组合硬约束：同一家同时违反 MOQ 和交期，必须两条都报出来。",
            "prompt": "郑州仓采购 25000 个白色铜版纸标签，四色印刷，尺寸 80×120mm、厚 100 微米，15 天内交付，需开专票，尺寸公差 1mm、厚度公差 5 微米。附件三家报价，请逐条检查 MOQ、交期、开票和规格，把不合格的都排除后推荐。",
            "quotes": [
                {"supplier": "郑州印务", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "0.09", "moq": 10000, "lead_time_days": 10}},
                {"supplier": "洛阳彩印", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "0.08", "moq": 30000, "lead_time_days": 20}},
                {"supplier": "开封标签厂", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.085", "moq": 12000, "lead_time_days": 12}},
            ],
            "quote_notes": {"郑州印务": "合格，预期推荐。", "洛阳彩印": "MOQ 30000 超采购量且交期 20 天超上限，预期双重淘汰。", "开封标签厂": "合格。"},
        },
        # ----------------------------------------------------------------- 13
        {
            "slug": "wuceng-walleng-zhixiang",
            "title": "五层瓦楞纸箱出口采购（欧元报价）",
            "item_name": "五层瓦楞纸箱",
            "quantity": 5000,
            "specs": {"width_mm": "400", "length_mm": "300", "thickness_um": "5000", "material": "瓦楞纸", "color": "牛皮色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1", "EUR": "8.0"}, "max_lead_days": 20, "invoice_required": True, "size_tolerance_mm": "3", "thickness_tolerance_um": "500", "destination": "苏州工厂"},
            "stress_focus": "厚材质（5mm）+ 欧元报价 + 每百个计价，验证单位换算与汇率换算同时正确。",
            "prompt": "苏州工厂出口订单需要 5000 个牛皮色五层瓦楞纸箱，规格 400×300mm、厚度 5mm（5000 微米）、单色印刷，20 天内交付苏州工厂，必须开票，尺寸公差 3mm、厚度公差 500 微米。EUR/CNY 按 8.0。附件报价有欧元和按每百个计价的，请统一换算到人民币每个后再比价。",
            "quotes": [
                {"supplier": "华南纸业", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "3.10", "moq": 2000, "lead_time_days": 12}},
                {"supplier": "沪宁纸品", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "290", "price_basis": 100, "moq": 3000, "lead_time_days": 15}},
                {"supplier": "浙江箱业", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "0.42", "price_basis": 1, "currency": "EUR", "moq": 2000, "lead_time_days": 10}},
            ],
            "quote_notes": {"华南纸业": "合格，3.10/个。", "沪宁纸品": "290/百个=2.90/个，合格且预期推荐。", "浙江箱业": "EUR 0.42×8.0=3.36/个，合格。"},
        },
        # ----------------------------------------------------------------- 14
        {
            "slug": "sanceng-walleng-zhixiang",
            "title": "三层瓦楞纸箱公差边界采购",
            "item_name": "三层瓦楞纸箱",
            "quantity": 9000,
            "specs": {"width_mm": "350", "length_mm": "250", "thickness_um": "3000", "material": "瓦楞纸", "color": "牛皮色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 16, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "300", "destination": "合肥仓"},
            "stress_focus": "公差边界：规格差异恰好等于公差上限时应算合格（边界不能误杀）。",
            "prompt": "合肥仓采购 9000 个牛皮色三层瓦楞纸箱，规格 350×250mm、厚度 3000 微米，无印刷，16 天内交付，必须开票，尺寸公差 2mm、厚度公差 300 微米。附件三家报价，请比价推荐。注意：宽度差 2mm 内、厚度差 300 微米内都算合格。",
            "quotes": [
                {"supplier": "合肥纸箱厂", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "2.30", "moq": 3000, "lead_time_days": 10}},
                {"supplier": "芜湖包装", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "2.20", "moq": 4000, "lead_time_days": 12, "width_mm": "352", "thickness_um": "3300", "item_description": "瓦楞纸 三层瓦楞纸箱 352x250mm 厚3300微米 牛皮色 0色印刷"}},
                {"supplier": "蚌埠纸品", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "2.35", "moq": 3000, "lead_time_days": 9}},
            ],
            "quote_notes": {"合肥纸箱厂": "合格。", "芜湖包装": "宽 352 差 2mm、厚 3300 差 300μm，均等于公差上限，应合格且预期推荐。", "蚌埠纸品": "合格。"},
        },
        # ----------------------------------------------------------------- 15
        {
            "slug": "kuaidi-mian-dan",
            "title": "快递面单采购（要求到货日期）",
            "item_name": "快递面单",
            "quantity": 50000,
            "specs": {"width_mm": "100", "length_mm": "180", "thickness_um": "70", "material": "热敏纸", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 20, "invoice_required": True, "size_tolerance_mm": "1", "thickness_tolerance_um": "5", "required_delivery_date": "2026-08-15", "destination": "杭州仓"},
            "stress_focus": "要求到货日期：按今天（2026-08-08）计算交期是否能在截止日前到货。",
            "prompt": "杭州仓 8 月 15 日前必须到货 50000 个白色热敏纸快递面单，尺寸 100×180mm、厚 70 微米、单色印刷，必须开票，尺寸公差 1mm、厚度公差 5 微米。附件三家报价，请按“今天下单、交期 N 天”判断能否在 8 月 15 日前到货，超期的排除后推荐。",
            "quotes": [
                {"supplier": "杭州面单科技", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.035", "moq": 20000, "lead_time_days": 5}},
                {"supplier": "绍兴热敏材料", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.032", "moq": 30000, "lead_time_days": 12}},
                {"supplier": "宁波面单印刷", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "0.033", "moq": 25000, "lead_time_days": 6}},
            ],
            "quote_notes": {"杭州面单科技": "交期 5 天，8-13 到货，合格。", "绍兴热敏材料": "交期 12 天，8-20 到货晚于 8-15，预期淘汰。", "宁波面单印刷": "交期 6 天，8-14 到货，合格且预期推荐。"},
        },
        # ----------------------------------------------------------------- 16
        {
            "slug": "bianzhi-dai-pp",
            "title": "PP 编织袋采购（MOQ 边界）",
            "item_name": "编织袋",
            "quantity": 20000,
            "specs": {"width_mm": "550", "length_mm": "850", "thickness_um": "200", "material": "PP", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 25, "invoice_required": True, "size_tolerance_mm": "5", "thickness_tolerance_um": "30", "destination": "乌鲁木齐仓"},
            "stress_focus": "MOQ 边界：MOQ 恰好等于采购量时应合格（边界不能误杀）。",
            "prompt": "乌鲁木齐仓采购 20000 条白色 PP 编织袋，规格 550×850mm、厚 200 微米、单色印刷，25 天内交付，必须开票，尺寸公差 5mm、厚度公差 30 微米。附件三家报价，请比价推荐。MOQ 等于或低于 20000 的才算合格。",
            "quotes": [
                {"supplier": "乌鲁木齐编织袋厂", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.95", "moq": 10000, "lead_time_days": 15}},
                {"supplier": "昌吉塑编", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.90", "moq": 20000, "lead_time_days": 18}},
                {"supplier": "石河子包装", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.92", "moq": 15000, "lead_time_days": 16}},
            ],
            "quote_notes": {"乌鲁木齐编织袋厂": "合格。", "昌吉塑编": "MOQ 20000 恰等于采购量，应合格且预期推荐。", "石河子包装": "合格。"},
        },
        # ----------------------------------------------------------------- 17
        {
            "slug": "wufangbu-dai",
            "title": "无纺布袋采购（交期边界）",
            "item_name": "无纺布袋",
            "quantity": 12000,
            "specs": {"width_mm": "320", "length_mm": "400", "thickness_um": "150", "material": "无纺布", "color": "红色", "print_colors": 2},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 21, "invoice_required": True, "size_tolerance_mm": "3", "thickness_tolerance_um": "20", "destination": "西安仓"},
            "stress_focus": "交期边界：交期恰好等于上限时应合格（边界不能误杀）。",
            "prompt": "西安仓采购 12000 个红色无纺布袋，双色印刷，规格 320×400mm、厚 150 微米，21 天内交付，必须开票，尺寸公差 3mm、厚度公差 20 微米。附件三家报价，请比价推荐。交期刚好 21 天也算合格。",
            "quotes": [
                {"supplier": "西安无纺布制品", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "1.80", "moq": 3000, "lead_time_days": 14}},
                {"supplier": "咸阳环保袋", "kind": "xlsx", "layout": "xlsx_sections", "fields": {"unit_price": "1.70", "moq": 5000, "lead_time_days": 21}},
                {"supplier": "宝鸡纺织包装", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "1.75", "moq": 4000, "lead_time_days": 18}},
            ],
            "quote_notes": {"西安无纺布制品": "合格。", "咸阳环保袋": "交期 21 天恰等于上限，应合格且预期推荐。", "宝鸡纺织包装": "合格。"},
        },
        # ----------------------------------------------------------------- 18
        {
            "slug": "zifeng-dai-pe",
            "title": "PE 自封袋采购（供应商名待复核）",
            "item_name": "自封袋",
            "quantity": 40000,
            "specs": {"width_mm": "120", "length_mm": "180", "thickness_um": "80", "material": "PE", "color": "透明", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 15, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "5", "destination": "南京仓"},
            "stress_focus": "低置信度供应商名触发人工复核门禁，Agent 必须停止而不是强行比价。",
            "prompt": "南京仓采购 40000 个透明 PE 自封袋，规格 120×180mm、厚 80 微米，无印刷，15 天内交付，必须开票，尺寸公差 2mm、厚度公差 5 微米。附件三家报价，请先解析再比价。注意：其中一份报价单上没写供应商名称，请识别出需要人工复核的字段并停下等待采购员确认。",
            "quotes": [
                {"supplier": "南京自封袋厂", "kind": "pdf", "layout": "pdf_vertical", "fields": {"unit_price": "0.028", "moq": 15000, "lead_time_days": 8}},
                {"supplier": "苏州塑胶制品", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.026", "moq": 20000, "lead_time_days": 9}, "omit_fields": ["supplier_name"]},
                {"supplier": "无锡包装材料", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.027", "moq": 18000, "lead_time_days": 7}},
            ],
            "quote_notes": {"南京自封袋厂": "合格。", "苏州塑胶制品": "缺少供应商名称，低置信度，预期触发人工复核。", "无锡包装材料": "合格，但整体需等待复核后才能比价。"},
        },
        # ----------------------------------------------------------------- 19
        {
            "slug": "sugang-dadai-pet",
            "title": "PET 塑钢打包带采购（有效期边界）",
            "item_name": "塑钢打包带",
            "quantity": 30000,
            "specs": {"width_mm": "16", "length_mm": "1200000", "thickness_um": "1200", "material": "PET", "color": "绿色", "print_colors": 0},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1"}, "max_lead_days": 30, "invoice_required": True, "size_tolerance_mm": "1", "thickness_tolerance_um": "100", "destination": "青岛港"},
            "stress_focus": "有效期边界：有效期恰为今天（2026-08-08）不算过期；三家全合格验证排序。",
            "prompt": "青岛港采购 30000 卷绿色 PET 塑钢打包带，宽 16mm、长 1200000mm、厚 1200 微米，无印刷，30 天内交付，需开票，尺寸公差 1mm、厚度公差 100 微米。附件三家报价，报价有效期到今天（2026-08-08）的仍算有效，请比价推荐。",
            "quotes": [
                {"supplier": "青岛打包带", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.68", "moq": 10000, "lead_time_days": 15, "valid_until": "2026-08-08"}},
                {"supplier": "烟台塑带厂", "kind": "pdf", "layout": "pdf_columns", "fields": {"unit_price": "0.65", "moq": 15000, "lead_time_days": 20, "valid_until": "2026-08-20"}},
                {"supplier": "威海打包材料", "kind": "xlsx", "layout": "xlsx_horizontal", "fields": {"unit_price": "0.66", "moq": 12000, "lead_time_days": 18, "valid_until": "2026-09-30"}},
            ],
            "quote_notes": {"青岛打包带": "有效期恰为今天，应合格。", "烟台塑带厂": "合格且预期推荐。", "威海打包材料": "合格。"},
        },
        # ----------------------------------------------------------------- 20
        {
            "slug": "kuaididai-shenfen-cuopei",
            "title": "快递袋采购（身份错配对抗）",
            "item_name": "PE快递袋",
            "quantity": 10000,
            "specs": {"width_mm": "250", "length_mm": "350", "thickness_um": "60", "material": "PE", "color": "白色", "print_colors": 1},
            "constraints": {"base_currency": "CNY", "fx_rates": {"CNY": "1", "USD": "7.2"}, "max_lead_days": 15, "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "3", "destination": "上海松江"},
            "stress_focus": "物料身份对抗：一家报价单写的是垃圾袋（低价）必须被物料身份校验淘汰，不能因低价入选。",
            "prompt": "上海松江采购 10000 个 PE 白色 PE快递袋，规格 250×350mm、厚 60 微米、单色印刷，15 天内交付，必须开票；USD/CNY 按 7.2，尺寸公差 2mm、厚度公差 3 微米。附件三家报价，其中有一家报的可能不是快递袋而是别的袋子，请仔细核对物料身份，规格不符或身份不符的低价报价要排除，再推荐合格供应商。",
            "quotes": [
                {"supplier": "华东优包", "kind": "xlsx", "layout": "xlsx_vertical", "fields": {"unit_price": "0.52", "moq": 3000, "lead_time_days": 9}},
                {"supplier": "低价垃圾袋厂", "kind": "pdf", "layout": "pdf_vertical", "fields": {"supplier_name": "低价垃圾袋厂", "item_description": "PE 垃圾袋 250x350mm 厚60微米 白色 单色印刷", "unit_price": "0.30", "moq": 2000, "lead_time_days": 5}},
                {"supplier": "星河包装", "kind": "pdf", "layout": "pdf_table", "fields": {"unit_price": "0.49", "moq": 5000, "lead_time_days": 10}},
            ],
            "quote_notes": {"华东优包": "合格。", "低价垃圾袋厂": "物料为垃圾袋而非快递袋，身份不符，预期淘汰（即使价格最低）。", "星河包装": "合格且预期推荐。"},
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成真实模型采购压测场景集")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--defs-file", type=Path, default=None, help="可选：包含 scenario_definitions() 的 Python 文件")
    args = parser.parse_args()

    if args.defs_file is not None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("stress_defs", args.defs_file)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        definitions = module.scenario_definitions()
    else:
        definitions = scenario_definitions()
    if args.count < 1 or args.count > len(definitions):
        raise SystemExit(f"--count 必须在 1 到 {len(definitions)} 之间")
    # --start 只控制输出目录的数字前缀（例如 21-xx），定义始终从头取 count 个。
    selected = definitions[: args.count]
    generate(args.output, selected, start=args.start)
    print(f"已生成 {len(selected)} 个场景到 {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
