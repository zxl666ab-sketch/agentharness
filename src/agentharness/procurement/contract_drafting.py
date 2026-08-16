"""P3-2 合同草拟（模式 B：模板 + 条款库软提示）与条款风险识别（模式 A+C）。

- 草拟：确定性中文合同模板，金额/交期/供应商/标的物从定标结果注入（Java 侧同样注入，
  草拟文本只进 draft_text，不进正式业务字段）；
- 条款库软提示：按金额阈值给「金额条款」附加风险提示（参照 K5 ReferencePriceService
  的软提示边界——只进草拟/解释文本，不参与业务判断）；
- 风险识别：条款级 risk_level（高风险/提示/低）+ risk_reason，全部由确定性规则产出；
- 一致性：草拟文本中的金额/交期由 Java 侧权威校验（本模块只生成文本）。
"""

from __future__ import annotations

import math
from typing import Any

CONTRACT_TEMPLATE = """采购合同

合同编号：{contract_no}

甲方（采购方）：采购工作台演示企业
乙方（供应商）：{supplier_name}

一、标的物
乙方按本协议向甲方供应：{item_name}。

二、金额条款
本合同金额为人民币 {amount} 元（价税合计，含运费）。

三、交期条款
乙方应于合同生效后 {lead_days} 天内完成交货。

四、质量标准条款
质量标准以双方书面确认的样品与规格书为准。

五、付款条款
验收合格并完成三单匹配（发票、收货、订单）后 {payment_days} 日内付款。

六、违约条款
任何一方违约的，守约方有权要求赔偿实际损失；逾期交货超过 {grace_days} 天的，甲方有权解除合同。

七、争议解决
因本合同引起的争议，双方应友好协商；协商不成的，提交甲方所在地人民法院诉讼解决。
"""

CLAUSE_LIBRARY_SOFT_HINTS: list[dict[str, Any]] = [
    {
        "title": "金额条款",
        "hint": "金额不少于 5000 元，建议复核预算与三单匹配口径",
        "threshold": 5000,
    },
    {
        "title": "交期条款",
        "hint": "交期短于 10 天，建议确认供应商产能",
        "threshold": 10,
    },
]


def build_contract_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """由定标注入字段生成草拟文本 + 结构化条款（含风险分级）+ 软提示。"""
    if not str(payload.get("contract_id") or "") or not str(payload.get("amount") or ""):
        raise ValueError("draft_contract requires contract_id and amount")
    contract_no = str(payload.get("contract_no") or "")
    supplier = str(payload.get("supplier_name") or "")
    item = str(payload.get("item_name") or "")
    amount = str(payload.get("amount") or "0")
    lead_days = int(payload.get("lead_days") or 0)
    payment_days = int(payload.get("payment_days") or 30)
    grace_days = int(payload.get("grace_days") or 15)

    draft_text = CONTRACT_TEMPLATE.format(
        contract_no=contract_no,
        supplier_name=supplier,
        item_name=item,
        amount=amount,
        lead_days=lead_days,
        payment_days=payment_days,
        grace_days=grace_days,
    )

    amount_value = float(amount) if _is_number(amount) else 0.0
    clauses: list[dict[str, Any]] = [
        {
            "title": "金额条款",
            "content": f"本合同金额为人民币 {amount} 元（价税合计，含运费）。",
            "risk_level": "提示" if amount_value >= 5000 else "低",
            "risk_reason": "金额较大，建议复核预算与三单匹配口径" if amount_value >= 5000 else "与定标结果一致",
        },
        {
            "title": "交期条款",
            "content": f"乙方应于合同生效后 {lead_days} 天内完成交货。",
            "risk_level": "提示" if 0 < lead_days < 10 else "低",
            "risk_reason": "交期短于 10 天，建议确认供应商产能" if 0 < lead_days < 10 else "与定标结果一致",
        },
        {
            "title": "质量标准条款",
            "content": "质量标准以双方书面确认的样品与规格书为准。",
            "risk_level": "高风险",
            "risk_reason": "建议补充书面质量标准附件，避免验收争议",
        },
        {
            "title": "付款条款",
            "content": f"验收合格并完成三单匹配（发票、收货、订单）后 {payment_days} 日内付款。",
            "risk_level": "低",
            "risk_reason": "付款与三单匹配联动，符合平台纪律",
        },
        {
            "title": "违约条款",
            "content": (
                f"任何一方违约的，守约方有权要求赔偿实际损失；逾期交货超过 {grace_days} 天的，"
                "甲方有权解除合同。"
            ),
            "risk_level": "低",
            "risk_reason": "含违约金与解约权，风险受控",
        },
        {
            "title": "争议解决条款",
            "content": "因本合同引起的争议，双方应友好协商；协商不成的，提交甲方所在地人民法院诉讼解决。",
            "risk_level": "低",
            "risk_reason": "管辖约定明确",
        },
    ]

    return {
        "draft_text": draft_text,
        "clauses": clauses,
        "soft_hints": [
            {"clause": hint["title"], "hint": hint["hint"]}
            for hint in CLAUSE_LIBRARY_SOFT_HINTS
            if (hint["threshold"] == 5000 and amount_value >= hint["threshold"])
            or (hint["threshold"] == 10 and 0 < lead_days < hint["threshold"])
        ],
        "source": "deterministic_contract_template",
    }


def _is_number(value: str) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)  # 拒绝 inf/nan（避免触发金额提示阈值）


__all__ = ["build_contract_draft"]
