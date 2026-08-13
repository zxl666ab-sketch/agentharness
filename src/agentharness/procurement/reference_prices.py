"""K5 历史报价 RAG：参考区间注入（软提示，冻结设计 4.10）。

参考区间只进入比价解释文本与风险 flag：
- 不参与确定性比价排序（排序由 Java ComparisonEngine 计算）；
- 不排除任何报价（资格判定仍由 Java 硬约束完成）；
- 不影响冻结评测指标（frozen-evaluation.json 一个字节不动，扩展用例在
  frozen-evaluation-ext.json）。
"""

from __future__ import annotations

from typing import Any

FLAG_BELOW = "PRICE_BELOW_REFERENCE"
FLAG_ABOVE = "PRICE_ABOVE_REFERENCE"


def unit_price(quote: dict[str, Any]) -> float | None:
    """报价的归一化单价（unit_price / price_basis，纯算术，不引入新的解析逻辑）。

    兼容两种形状：Java task context 的 quote["extracted"]["fields"][name]={value,...}，
    以及扁平 quote["fields"][name]=裸值。
    """
    if not isinstance(quote, dict):
        return None
    fields: dict[str, Any] = {}
    if isinstance(quote.get("fields"), dict):
        fields = quote["fields"]
    elif isinstance(quote.get("extracted"), dict) and isinstance(
        quote["extracted"].get("fields"), dict
    ):
        fields = quote["extracted"]["fields"]
    raw_price = fields.get("unit_price")
    raw_basis = fields.get("price_basis")

    def unwrap(value: Any) -> Any:
        if isinstance(value, dict):
            return value.get("value", value)
        return value

    try:
        price = float(unwrap(raw_price))
        basis_value = unwrap(raw_basis)
        basis = float(basis_value) if basis_value is not None else 1.0
    except (TypeError, ValueError):
        return None
    if basis <= 0:
        return None
    return price / basis


def apply_reference_interval(
    structured_result: dict[str, Any],
    interval: dict[str, Any] | None,
    quotes: list[dict[str, Any]],
) -> dict[str, Any]:
    """把参考区间注入结构化结果：可选字段 reference_price_interval + 解释文本 + 软风险 flag。

    区间为 None（历史成交不足 3 条）时不注入任何内容；RPC 失败由调用方吞掉，
    分析流程不受影响（软提示语义）。
    """
    if not isinstance(interval, dict):
        return structured_result

    p25 = str(interval.get("p25") or "")
    p75 = str(interval.get("p75") or "")
    count = int(interval.get("count") or 0)
    structured_result["reference_price_interval"] = {
        "p25": p25,
        "p75": p75,
        "p25_unit": str(interval.get("p25_unit") or ""),
        "p75_unit": str(interval.get("p75_unit") or ""),
        "count": count,
        "basis": str(interval.get("basis") or "landed_total_base"),
    }

    summary = str(structured_result.get("summary") or "")
    summary += f"；历史成交参考区间 {p25}–{p75}（{count} 条已批准成交，软提示不参与比价）"
    structured_result["summary"] = summary

    risk_flags = structured_result.setdefault("risk_flags", [])
    if isinstance(risk_flags, list):
        try:
            low = float(interval.get("p25_unit") or "")
            high = float(interval.get("p75_unit") or "")
        except (TypeError, ValueError):
            low = high = 0.0
        for quote in quotes:
            price = unit_price(quote) if isinstance(quote, dict) else None
            if price is None:
                continue
            if low and price < low and FLAG_BELOW not in risk_flags:
                risk_flags.append(FLAG_BELOW)
            if high and price > high and FLAG_ABOVE not in risk_flags:
                risk_flags.append(FLAG_ABOVE)
    return structured_result
