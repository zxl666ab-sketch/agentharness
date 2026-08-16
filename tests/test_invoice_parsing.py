"""P3-1: invoice parsing (xlsx + text pdf) and diff explanation numeric consistency."""

from __future__ import annotations

import io
import re

from openpyxl import Workbook
from reportlab.pdfgen import canvas

from agentharness.procurement.invoice_parsing import (
    build_diff_explanation,
    parse_invoice,
)


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoice"
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    y = 780
    for line in text.splitlines():
        pdf.drawString(60, y, line)
        y -= 18
    pdf.save()
    return output.getvalue()


def _standard_xlsx() -> bytes:
    return _xlsx_bytes([
        ["发票代码", "144032600111"],
        ["发票号码", "202608160001"],
        ["开票日期", "2026-08-16"],
        ["销售方名称", "华东优包"],
        ["税率", "13%"],
        ["价税合计", "5200.00"],
        ["税额", "598.23"],
        ["金额", "4601.77"],
        ["数量", "1000"],
        ["单价", "4.60"],
        ["品名", "PE 快递袋 250x350mm"],
        ["计量单位", "个"],
    ])


def test_parses_xlsx_invoice_fields() -> None:
    parsed = parse_invoice("invoice.xlsx", _standard_xlsx())
    assert parsed["parser_version"] == "invoice-v1"
    invoice = parsed["invoice"]
    assert invoice["invoice_code"] == "144032600111"
    assert invoice["invoice_no"] == "202608160001"
    assert invoice["issue_date"] == "2026-08-16"
    assert invoice["supplier_name"] == "华东优包"
    assert invoice["total_amount"] == "5200"  # 规范化小数（5200.00 → 5200）
    assert invoice["tax_amount"] == "598.23"
    assert invoice["amount_excluding_tax"] == "4601.77"
    assert invoice["quantity"] == "1000"
    assert invoice["tax_rate"] == "0.13"  # 13% → 小数


def test_parses_pdf_invoice_text() -> None:
    text = (
        "VAT Electronic Invoice\n"
        "Invoice Code: 144032600222  Invoice No: 202608160002\n"
        "Issue Date: 2026-08-16\n"
        "Seller: EastPack\n"
        "Tax Rate: 13%\n"
        "Grand Total: 5200.00\n"
        "Excl Tax Amount: 4601.77  Tax Amount: 598.23\n"
    )
    parsed = parse_invoice("invoice.pdf", _pdf_bytes(text))
    invoice = parsed["invoice"]
    assert invoice["invoice_no"] == "202608160002"
    assert invoice["tax_rate"] == "0.13"
    assert invoice["total_amount"] == "5200"
    assert invoice["supplier_name"] == "EastPack"
    assert invoice["amount_excluding_tax"] == "4601.77"
    assert invoice["tax_amount"] == "598.23"


def test_rejects_unsupported_extension() -> None:
    try:
        parse_invoice("quote.txt", b"data")
        raise AssertionError("expected InvoiceParseError")
    except ValueError as exc:
        assert "xlsx or text pdf" in str(exc)


def test_explanation_numbers_come_only_from_diffs() -> None:
    diffs = [
        {"field": "quantity", "expected": "1000", "actual": "900", "diff": "-100"},
        {"field": "total_amount", "expected": "5200.00", "actual": "5300.00", "diff": "100.00"},
    ]
    explanation = build_diff_explanation(diffs)
    text = explanation["reason"]
    # 每个数字必须存在于注入的结构化差异中（数值引用一致性硬校验）
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    allowed = {"1000", "900", "-100", "5200.00", "5300.00", "100.00", "2"}
    for number in numbers:
        assert number in allowed, f"数字 {number} 不在结构化差异中"
    assert "数量" in text and "价税合计" in text
    assert len(explanation["suggestions"]) == 2
    assert explanation["source"] == "deterministic_agent"


def test_explanation_requires_diffs() -> None:
    try:
        build_diff_explanation([])
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "at least one diff" in str(exc)
