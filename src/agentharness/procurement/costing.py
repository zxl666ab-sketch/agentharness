"""Deterministic packaging quote normalization, qualification, and ranking."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from agentharness.procurement.parsing import decimal_is_resource_bounded

RULESET_VERSION = "landed-cost-v1"
MONEY = Decimal("0.01")
UNIT_MONEY = Decimal("0.0001")

SPEC_LABELS = {
    "material": "材质",
    "color": "颜色",
    "print_colors": "印刷色数",
    "width_mm": "宽度",
    "length_mm": "长度",
    "height_mm": "高度",
    "thickness_um": "厚度",
}


class CostingError(ValueError):
    pass


def _canonical_item(value: Any) -> str | None:
    text = str(value or "").casefold()
    groups = {
        "mailer": ("快递袋", "快递包装袋", "mailer", "mailing bag", "courier bag"),
        "trash_bag": ("垃圾袋", "trash bag", "garbage bag", "bin liner"),
        # 电商包装耗材已支持的其余品类：
        # 纸箱 / 气泡膜 / 缠绕膜 / 封箱胶带 / 珍珠棉
        "bubble": ("气泡膜", "气泡袋", "气泡垫", "bubble wrap", "bubble film", "bubble"),
        "stretch": ("缠绕膜", "拉伸膜", "stretch film", "stretch wrap", "stretch"),
        "tape": ("封箱胶带", "胶带", "tape"),
        "foam": ("珍珠棉", "epe", "pe foam", "foam"),
    }
    identity = next(
        (name for name, aliases in groups.items() if any(alias in text for alias in aliases)),
        None,
    )
    if identity is not None:
        return identity
    if any(alias in text for alias in ("纸箱", "包装箱", "carton", "corrugated")):
        return "carton"
    if re.search(r"\bbox(?:es)?\b", text):
        return "carton"
    return None

def _canonical_material(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    aliases = {
        "PE": ("pe", "聚乙烯", "polyethylene"),
        "PVC": ("pvc", "聚氯乙烯", "polyvinyl chloride"),
        "PP": ("pp", "聚丙烯", "polypropylene"),
        "PET": ("pet", "聚对苯二甲酸乙二醇"),
        "PLA": ("pla", "聚乳酸"),
        "corrugated": ("瓦楞", "corrugated", "cardboard"),
        "kraft": ("牛皮", "kraft"),
    }
    for canonical, values in aliases.items():
        if any(re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text) for alias in values):
            return canonical
    return None

def _canonical_color(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    aliases = {
        "white": ("白色", "白", "white"),
        "black": ("黑色", "黑", "black"),
        "transparent": ("透明", "transparent", "clear"),
        "red": ("红色", "红", "red"),
        "blue": ("蓝色", "蓝", "blue"),
        "kraft": ("牛皮色", "牛皮", "牛卡", "kraft"),
    }
    return next(
        (canonical for canonical, values in aliases.items() if any(alias in text for alias in values)),
        None,
    )

def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CostingError(f"{field} 不是有效数值") from exc
    if not decimal_is_resource_bounded(result):
        raise CostingError(f"{field} 超出安全数值范围")
    return result


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY, rounding=ROUND_HALF_UP), "f")


def _unit_money(value: Decimal) -> str:
    return format(value.quantize(UNIT_MONEY, rounding=ROUND_HALF_UP), "f")


def _field_values(quote: dict[str, Any]) -> dict[str, Any]:
    extracted = quote.get("extracted", {})
    fields = extracted.get("fields", {}) if isinstance(extracted, dict) else {}
    return {
        name: entry.get("value")
        for name, entry in fields.items()
        if isinstance(entry, dict)
    }


def _analysis_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CostingError("分析基准日期格式无效") from exc


def canonical_analysis_input(
    request: dict[str, Any],
    quotes: list[dict[str, Any]],
    *,
    analysis_as_of: date | str,
) -> dict[str, Any]:
    as_of = _analysis_date(analysis_as_of)
    return {
        "ruleset_version": RULESET_VERSION,
        "analysis_as_of": as_of.isoformat(),
        "request": {
            "id": request["id"],
            "item_name": request["item_name"],
            "quantity": request["quantity"],
            "unit": request["unit"],
            "specifications": request.get("specifications", {}),
            "constraints": request.get("constraints", {}),
            "created_at": request.get("created_at"),
        },
        "quotes": [
            {
                "id": quote["id"],
                "source_sha256": quote["source_sha256"],
                "fields": _field_values(quote),
            }
            for quote in sorted(quotes, key=lambda item: item["id"])
        ],
    }


def analysis_input_sha256(
    request: dict[str, Any],
    quotes: list[dict[str, Any]],
    *,
    analysis_as_of: date | str,
) -> str:
    canonical = json.dumps(
        canonical_analysis_input(request, quotes, analysis_as_of=analysis_as_of),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalized_quote(
    request: dict[str, Any],
    quote: dict[str, Any],
    *,
    analysis_as_of: date,
) -> dict[str, Any]:
    fields = _field_values(quote)
    constraints = request.get("constraints", {})
    specs = request.get("specifications", {})
    quantity = _decimal(request["quantity"], "采购数量")
    if quantity <= 0:
        raise CostingError("采购数量必须大于 0")

    currency = str(fields.get("currency") or "").upper()
    fx_rates = constraints.get("fx_rates", {"CNY": "1"})
    if currency not in fx_rates:
        raise CostingError(f"缺少 {currency or '报价币种'} 汇率")
    fx_rate = _decimal(fx_rates[currency], f"{currency} 汇率")
    if fx_rate <= 0:
        raise CostingError(f"{currency} 汇率必须大于 0")
    unit_price = _decimal(fields.get("unit_price"), "报价")
    price_basis = _decimal(fields.get("price_basis"), "计价数量")
    tax_rate = _decimal(fields.get("tax_rate"), "税率")
    shipping_fee = _decimal(fields.get("shipping_fee") or 0, "运费")
    moq = _decimal(fields.get("moq"), "MOQ")
    lead_time = _decimal(fields.get("lead_time_days"), "交期")
    if lead_time != lead_time.to_integral_value():
        raise CostingError("交期必须是整数天")
    lead_days = int(lead_time)
    if (
        unit_price <= 0
        or price_basis <= 0
        or tax_rate < 0
        or tax_rate > 1
        or shipping_fee < 0
        or moq <= 0
        or lead_days < 0
    ):
        raise CostingError("报价、计价数量、税率或运费超出允许范围")

    quoted_unit = unit_price / price_basis
    goods_before_tax = quoted_unit * quantity
    tax_included = fields.get("tax_included") is True
    if tax_included:
        goods_with_tax = goods_before_tax
        tax_amount = (
            goods_before_tax - goods_before_tax / (Decimal("1") + tax_rate)
            if tax_rate > 0
            else Decimal("0")
        )
    else:
        tax_amount = goods_before_tax * tax_rate
        goods_with_tax = goods_before_tax + tax_amount
    freight = Decimal("0") if fields.get("shipping_included") is True else shipping_fee
    landed_quote_currency = goods_with_tax + freight
    landed_base = landed_quote_currency * fx_rate
    landed_unit_base = landed_base / quantity

    exclusions: list[dict[str, str]] = []
    warnings: list[str] = []
    if quantity < moq:
        exclusions.append(
            {
                "code": "moq",
                "message": f"起订量（MOQ）{int(moq)} 高于采购量 {int(quantity)}",
            }
        )
    max_lead = int(constraints.get("max_lead_days", 0) or 0)
    if max_lead and lead_days > max_lead:
        exclusions.append({"code": "lead_time", "message": f"交期 {lead_days} 天超过上限 {max_lead} 天"})
    if constraints.get("invoice_required", False) and fields.get("supports_invoice") is not True:
        exclusions.append({"code": "invoice", "message": "不能提供要求的发票"})

    tolerance_mm = _decimal(constraints.get("size_tolerance_mm", "0"), "尺寸公差")
    tolerance_um = _decimal(constraints.get("thickness_tolerance_um", "0"), "厚度公差")
    spec_checks: list[dict[str, Any]] = []
    description = str(fields.get("item_description") or "")
    requested_item = str(request.get("item_name") or "").strip()
    expected_item = _canonical_item(requested_item)
    actual_item = _canonical_item(description)
    if requested_item:
        if expected_item is None:
            # 需求物料不在可识别枚举内：不能静默放行，必须按无法复核处理，
            # 否则错误物料报价会被判合格并进入采购订单。
            spec_checks.append(
                {
                    "field": "item_identity",
                    "expected": requested_item,
                    "actual": description,
                    "tolerance": "exact",
                    "passed": False,
                }
            )
            exclusions.append(
                {
                    "code": "item_identity",
                    "message": f"无法复核物料一致性（需求物料“{requested_item}”不在可识别范围内）",
                }
            )
        else:
            item_passed = actual_item == expected_item
            spec_checks.append(
                {
                    "field": "item_identity",
                    "expected": requested_item,
                    "actual": description,
                    "tolerance": "exact",
                    "passed": item_passed,
                }
            )
            if not item_passed:
                exclusions.append(
                    {
                        "code": "item_identity",
                        "message": f"报价物料“{description or '未识别'}”与需求“{requested_item}”不一致",
                    }
                )

    exact_specs = (
        (
            "material",
            _canonical_material(specs.get("material")),
            _canonical_material(fields.get("material")),
        ),
        ("color", _canonical_color(specs.get("color")), _canonical_color(fields.get("color"))),
        (
            "print_colors",
            str(specs.get("print_colors")) if specs.get("print_colors") is not None else None,
            str(fields.get("print_colors")) if fields.get("print_colors") is not None else None,
        ),
    )
    for field, expected, actual in exact_specs:
        if expected is None:
            if str(specs.get(field) or "").strip():
                # 期望值存在但无法规范化识别：不得跳过该硬约束，按无法复核
                # 处理，避免枚举外的需求值（如 HDPE、米白）被静默放行。
                spec_checks.append(
                    {
                        "field": field,
                        "expected": str(specs.get(field)),
                        "actual": actual or "未识别",
                        "tolerance": "exact",
                        "passed": False,
                    }
                )
                exclusions.append(
                    {
                        "code": f"spec_{field}",
                        "message": (
                            f"无法复核{SPEC_LABELS[field]}一致性"
                            f"（需求值“{specs.get(field)}”不在可识别范围内）"
                        ),
                    }
                )
            continue
        passed = actual == expected
        spec_checks.append(
            {
                "field": field,
                "expected": expected,
                "actual": actual or "未识别",
                "tolerance": "exact",
                "passed": passed,
            }
        )
        if not passed:
            exclusions.append(
                {
                    "code": f"spec_{field}",
                    "message": f"{SPEC_LABELS[field]} {actual or '未识别'} 不符合需求 {expected}",
                }
            )

    dimensional_checks = [
        ("width_mm", tolerance_mm),
        ("length_mm", tolerance_mm),
        ("thickness_um", tolerance_um),
    ]
    if specs.get("height_mm") not in (None, ""):
        dimensional_checks.insert(2, ("height_mm", tolerance_mm))
    for field, tolerance in dimensional_checks:
        expected = _decimal(specs.get(field), f"需求 {field}")
        raw_actual = fields.get(field)
        if raw_actual in (None, ""):
            spec_checks.append(
                {
                    "field": field,
                    "expected": format(expected, "f"),
                    "actual": "缺失",
                    "tolerance": format(tolerance, "f"),
                    "passed": False,
                }
            )
            exclusions.append(
                {
                    "code": f"spec_{field}",
                    "message": f"报价缺少{SPEC_LABELS[field]}，无法确认符合需求",
                }
            )
            continue
        actual = _decimal(raw_actual, f"报价 {field}")
        passed = abs(actual - expected) <= tolerance
        spec_checks.append(
            {
                "field": field,
                "expected": format(expected, "f"),
                "actual": format(actual, "f"),
                "tolerance": format(tolerance, "f"),
                "passed": passed,
            }
        )
        if not passed:
            exclusions.append(
                {
                    "code": f"spec_{field}",
                    "message": f"{SPEC_LABELS[field]} {format(actual, 'f')} 超出需求 {format(expected, 'f')}±{format(tolerance, 'f')}",
                }
            )

    max_unit_cost = constraints.get("max_landed_unit_cost")
    if max_unit_cost not in (None, "") and landed_unit_base > _decimal(max_unit_cost, "到货单价上限"):
        exclusions.append(
            {
                "code": "budget",
                "message": f"到货单价 {_unit_money(landed_unit_base)} 超过上限 {max_unit_cost}",
            }
        )
    valid_until = fields.get("valid_until")
    if valid_until:
        try:
            if date.fromisoformat(str(valid_until)) < analysis_as_of:
                exclusions.append({"code": "expired", "message": f"报价已于 {valid_until} 失效"})
        except ValueError:
            warnings.append("报价有效期格式无法复核")
    required_delivery_date = constraints.get("required_delivery_date")
    if required_delivery_date:
        try:
            deadline = date.fromisoformat(str(required_delivery_date))
        except ValueError as exc:
            raise CostingError("要求到货日期格式无效") from exc
        projected_delivery = analysis_as_of + timedelta(days=lead_days)
        if projected_delivery > deadline:
            exclusions.append(
                {
                    "code": "required_delivery_date",
                    "message": (
                        f"预计 {projected_delivery.isoformat()} 到货，晚于要求日期 "
                        f"{deadline.isoformat()}"
                    ),
                }
            )

    return {
        "quote_id": quote["id"],
        "supplier_name": fields.get("supplier_name") or quote.get("supplier_name"),
        "eligible": not exclusions,
        "exclusion_reasons": exclusions,
        "warnings": warnings,
        "match": {
            "item": request["item_name"],
            "quoted_description": description,
            "spec_checks": spec_checks,
            "passed": all(item["passed"] for item in spec_checks),
        },
        "commercial": {
            "moq": int(moq),
            "lead_time_days": lead_days,
            "tax_rate": format(tax_rate, "f"),
            "tax_included": tax_included,
            "shipping_included": fields.get("shipping_included") is True,
            "supports_invoice": fields.get("supports_invoice") is True,
            "payment_terms": fields.get("payment_terms"),
            "valid_until": valid_until,
        },
        "cost": {
            "quote_currency": currency,
            "base_currency": constraints.get("base_currency", "CNY"),
            "fx_rate": format(fx_rate, "f"),
            "quoted_price": format(unit_price, "f"),
            "price_basis": int(price_basis),
            "normalized_unit_quote_currency": _unit_money(quoted_unit),
            "goods_before_tax_quote_currency": _money(goods_before_tax),
            "tax_quote_currency": _money(tax_amount),
            "freight_quote_currency": _money(freight),
            "landed_total_quote_currency": _money(landed_quote_currency),
            "landed_total_base": _money(landed_base),
            "landed_unit_base": _unit_money(landed_unit_base),
        },
        "rank": None,
        "score": None,
    }


def compare_quotes(
    request: dict[str, Any],
    quotes: list[dict[str, Any]],
    *,
    analysis_as_of: date | str,
) -> dict[str, Any]:
    if len(quotes) < 2:
        raise CostingError("至少需要 2 家供应商报价才能比价")
    as_of = _analysis_date(analysis_as_of)
    normalized = [
        _normalized_quote(request, quote, analysis_as_of=as_of) for quote in quotes
    ]
    eligible = sorted(
        (item for item in normalized if item["eligible"]),
        key=lambda item: (
            Decimal(item["cost"]["landed_total_base"]),
            item["commercial"]["lead_time_days"],
            str(item["supplier_name"]),
            item["quote_id"],
        ),
    )
    if eligible:
        best_cost = Decimal(eligible[0]["cost"]["landed_total_base"])
        for rank, item in enumerate(eligible, start=1):
            cost = Decimal(item["cost"]["landed_total_base"])
            item["rank"] = rank
            item["score"] = format(
                (Decimal("100") if cost == 0 else Decimal("100") * best_cost / cost).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                ),
                "f",
            )

    recommended = eligible[0] if eligible else None
    explanation: list[str] = []
    if recommended:
        explanation.append(
            f"{recommended['supplier_name']} 在满足全部硬性条件的报价中到货总成本最低"
        )
        if len(eligible) > 1:
            saving = Decimal(eligible[1]["cost"]["landed_total_base"]) - Decimal(
                recommended["cost"]["landed_total_base"]
            )
            explanation.append(f"较第二名节省 {_money(saving)} {recommended['cost']['base_currency']}")
        explanation.append(
            f"起订量（MOQ）{recommended['commercial']['moq']}，交期 {recommended['commercial']['lead_time_days']} 天，均通过约束"
        )
    else:
        explanation.append("没有报价同时满足全部硬性条件，需要调整需求或重新询价")

    return {
        "schema_version": 1,
        "ruleset_version": RULESET_VERSION,
        "analysis_as_of": as_of.isoformat(),
        "request_id": request["id"],
        "base_currency": request.get("constraints", {}).get("base_currency", "CNY"),
        "quantity": request["quantity"],
        "quotes": normalized,
        "eligible_count": len(eligible),
        "excluded_count": len(normalized) - len(eligible),
        "recommended_quote_id": recommended["quote_id"] if recommended else None,
        "recommendation_explanation": explanation,
    }


__all__ = [
    "CostingError",
    "RULESET_VERSION",
    "analysis_input_sha256",
    "canonical_analysis_input",
    "compare_quotes",
]
