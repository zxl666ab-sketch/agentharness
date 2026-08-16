"""P3-2: contract drafting (mode B template + clause-library soft hints) and risk flags."""

from __future__ import annotations

import re

import pytest

from agentharness.procurement.contract_drafting import build_contract_draft

PAYLOAD = {
    "contract_id": "c" * 32,
    "contract_no": "CT-RFQ-20260813-834927",
    "task_id": "t" * 32,
    "supplier_name": "华东优包",
    "item_name": "PE 快递袋",
    "amount": "7500.00",
    "lead_days": 15,
}


def test_draft_injects_fields_into_text_and_clauses() -> None:
    draft = build_contract_draft(PAYLOAD)
    assert draft["source"] == "deterministic_contract_template"
    assert "华东优包" in draft["draft_text"]
    assert "7500.00" in draft["draft_text"]
    assert "15 天" in draft["draft_text"]
    titles = [clause["title"] for clause in draft["clauses"]]
    assert "金额条款" in titles and "交期条款" in titles
    amount_clause = next(clause for clause in draft["clauses"] if clause["title"] == "金额条款")
    assert "7500.00" in amount_clause["content"]


def test_risk_flags_are_deterministic() -> None:
    draft = build_contract_draft(PAYLOAD)
    risk_by_title = {clause["title"]: clause["risk_level"] for clause in draft["clauses"]}
    # 金额 ≥ 5000 → 提示；交期 15 天 → 低；质量标准缺失附件 → 高风险
    assert risk_by_title["金额条款"] == "提示"
    assert risk_by_title["交期条款"] == "低"
    assert risk_by_title["质量标准条款"] == "高风险"
    assert all(clause["risk_reason"] for clause in draft["clauses"])


def test_soft_hints_triggered_by_thresholds() -> None:
    draft = build_contract_draft(PAYLOAD)
    hints = {hint["clause"]: hint["hint"] for hint in draft["soft_hints"]}
    assert "金额条款" in hints  # 7500 ≥ 5000
    assert "交期条款" not in hints  # 15 天 ≥ 10
    small = build_contract_draft({**PAYLOAD, "amount": "1200.00", "lead_days": 5})
    small_hints = {hint["clause"] for hint in small["soft_hints"]}
    assert "交期条款" in small_hints  # 5 天 < 10
    assert "金额条款" not in small_hints  # 1200 < 5000


def test_draft_contains_required_clause_keywords() -> None:
    draft = build_contract_draft(PAYLOAD)
    titles = " ".join(clause["title"] for clause in draft["clauses"])
    assert "金额" in titles
    assert "交期" in titles


def test_consistency_by_construction_amount_and_lead_days_match() -> None:
    """草拟文本中的金额/交期必须与注入字段一致（Java 侧也会做权威校验）。"""
    draft = build_contract_draft(PAYLOAD)
    amount_in_text = re.search(r"金额为人民币\s*([0-9.]+)", draft["draft_text"])
    lead_in_text = re.search(r"生效后\s*(\d+)\s*天", draft["draft_text"])
    assert amount_in_text and amount_in_text.group(1) == "7500.00"
    assert lead_in_text and lead_in_text.group(1) == "15"


def test_requires_contract_id_and_amount() -> None:
    from agentharness.procurement.contract_drafting import build_contract_draft as build

    with pytest.raises(ValueError):
        build({"contract_no": "CT-1"})
