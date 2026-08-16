"""P3-1 发票评测：字段抽取准确率 + 差异解释数值引用一致性硬校验。

数据集：procurement-service/src/main/resources/frozen/frozen-evaluation-invoice.json
（synthetic 合成发票，README 已如实标注）。脚本只读冻结资源，输出写到
output/procurement-evaluation/invoice/。
"""

from __future__ import annotations

import argparse
import io
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from reportlab.pdfgen import canvas

from agentharness.procurement.invoice_parsing import build_diff_explanation, parse_invoice

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "procurement-service" / "src" / "main" / "resources" / "frozen" / "frozen-evaluation-invoice.json"
OUT = ROOT / "output" / "procurement-evaluation" / "invoice"

FIELDS = [
    "invoice_code",
    "invoice_no",
    "issue_date",
    "supplier_name",
    "quantity",
    "amount_excluding_tax",
    "tax_amount",
    "total_amount",
    "tax_rate",
]


def _xlsx_bytes(case: dict) -> bytes:
    e = case["expected"]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoice"
    rows = [
        ["发票代码", e["invoice_code"]],
        ["发票号码", e["invoice_no"]],
        ["开票日期", e["issue_date"]],
        ["销售方名称", e["supplier_name"]],
        ["税率", f"{float(e['tax_rate']) * 100:.0f}%"],
        ["价税合计", e["total_amount"]],
        ["税额", e["tax_amount"]],
        ["金额", e["amount_excluding_tax"]],
        ["数量", e["quantity"]],
        ["品名", "包装材料"],
        ["计量单位", "个"],
    ]
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _pdf_bytes(case: dict) -> bytes:
    e = case["expected"]
    lines = [
        "VAT Electronic Invoice",
        f"Invoice Code: {e['invoice_code']}  Invoice No: {e['invoice_no']}",
        f"Issue Date: {e['issue_date']}",
        f"Seller: {e['supplier_name']}",
        f"Tax Rate: {float(e['tax_rate']) * 100:.0f}%",
        f"Quantity: {e['quantity']}",
        f"Grand Total: {e['total_amount']}",
        f"Excl Tax Amount: {e['amount_excluding_tax']}  Tax Amount: {e['tax_amount']}",
    ]
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    y = 780
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 18
    pdf.save()
    return output.getvalue()


def _field_accuracy(observed: dict, expected: dict) -> tuple[int, int]:
    correct = 0
    total = 0
    for field in FIELDS:
        if field not in expected:
            continue
        total += 1
        if str(observed.get(field) or "") == str(expected[field]):
            correct += 1
    return correct, total


def _numeric_consistency_check() -> dict:
    """差异解释数值引用一致性：解释中每个数字必须存在于注入的结构化差异。"""
    sample_diffs = [
        {"field": "quantity", "expected": "1000", "actual": "900", "diff": "-100"},
        {"field": "total_amount", "expected": "5200.00", "actual": "5300.00", "diff": "100.00"},
        {"field": "tax_rate", "expected": "0.13", "actual": "0.09", "diff": "-0.04"},
    ]
    explanation = build_diff_explanation(sample_diffs)
    text = explanation["reason"]
    allowed = {"1000", "900", "-100", "5200.00", "5300.00", "100.00", "0.13", "0.09", "-0.04", "3"}
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    violations = [number for number in numbers if number not in allowed]
    return {
        "passed": not violations,
        "numbers_in_explanation": numbers,
        "violations": violations,
        "suggestion_count": len(explanation["suggestions"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P3-1 invoice evaluation (synthetic dataset)")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    correct = 0
    total = 0
    per_case: list[dict] = []
    for case in cases:
        document = _xlsx_bytes(case) if case["layout"] == "xlsx_cn" else _pdf_bytes(case)
        filename = "invoice.xlsx" if case["layout"] == "xlsx_cn" else "invoice.pdf"
        parsed = parse_invoice(filename, document)
        observed = parsed["invoice"]
        case_correct, case_total = _field_accuracy(observed, case["expected"])
        correct += case_correct
        total += case_total
        per_case.append({
            "id": case["id"],
            "layout": case["layout"],
            "correct": case_correct,
            "total": case_total,
            "missed": [
                field for field in FIELDS
                if field in case["expected"] and str(observed.get(field) or "") != str(case["expected"][field])
            ],
        })
    accuracy = correct / total if total else 0.0
    consistency = _numeric_consistency_check()

    evidence = {
        "dataset": dataset["dataset"],
        "dataset_label": dataset["dataset_label"],
        "synthetic": dataset["synthetic"],
        "case_count": len(cases),
        "field_extraction": {
            "accuracy": round(accuracy, 4),
            "correct": correct,
            "total": total,
        },
        "diff_explanation_numeric_consistency": consistency,
        "acceptance": {
            "field_extraction_at_least_99pct": accuracy >= 0.99,
            "numeric_consistency": consistency["passed"],
        },
        "per_case": per_case,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "invoice-evaluation.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "field_extraction_accuracy": round(accuracy, 4),
        "correct": correct,
        "total": total,
        "numeric_consistency": consistency["passed"],
        "acceptance": evidence["acceptance"],
    }, ensure_ascii=False, indent=2))
    ok = evidence["acceptance"]["field_extraction_at_least_99pct"] and consistency["passed"]
    print("INVOICE EVAL:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
