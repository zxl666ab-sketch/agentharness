"""Deterministic packaging quote normalization, qualification, and ranking."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from agentharness.procurement.parsing import requirement_quote_field_candidates

RULESET_VERSION = "landed-cost-v1"
DYNAMIC_RULESET_VERSION = "landed-cost-v2"
MONEY = Decimal("0.01")
UNIT_MONEY = Decimal("0.0001")

SPEC_LABELS = {
    "material": "材质",
    "color": "颜色",
    "print_colors": "印刷色数",
    "width_mm": "宽度",
    "length_mm": "长度",
    "thickness_um": "厚度",
}


class CostingError(ValueError):
    pass


def _canonical_item(value: Any) -> str | None:
    text = str(value or "").casefold()
    groups = {
        "mailer": ("快递袋", "快递包装袋", "mailer", "mailing bag", "courier bag"),
        "trash_bag": ("垃圾袋", "trash bag", "garbage bag", "bin liner"),
    }
    return next(
        (identity for identity, aliases in groups.items() if any(alias in text for alias in aliases)),
        None,
    )


def _canonical_material(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    aliases = {
        "PE": ("pe", "聚乙烯", "polyethylene"),
        "PVC": ("pvc", "聚氯乙烯", "polyvinyl chloride"),
        "PP": ("pp", "聚丙烯", "polypropylene"),
        "PET": ("pet", "聚对苯二甲酸乙二醇酯"),
        "PLA": ("pla", "聚乳酸"),
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
    if not result.is_finite():
        raise CostingError(f"{field} 不是有限数值")
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


def _request_schema_version(request: dict[str, Any]) -> int:
    try:
        version = int(request.get("schema_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= 2:
        return 2
    specs = request.get("specifications") or {}
    return 2 if any(isinstance(value, dict) and "type" in value for value in specs.values()) else 1


def _ruleset_version(request: dict[str, Any]) -> str:
    return DYNAMIC_RULESET_VERSION if _request_schema_version(request) == 2 else RULESET_VERSION


_UNIT_ALIASES = {
    "mm": ("length", Decimal("1")),
    "毫米": ("length", Decimal("1")),
    "cm": ("length", Decimal("10")),
    "厘米": ("length", Decimal("10")),
    "m": ("length", Decimal("1000")),
    "米": ("length", Decimal("1000")),
    "in": ("length", Decimal("25.4")),
    "inch": ("length", Decimal("25.4")),
    "英寸": ("length", Decimal("25.4")),
    "g": ("mass", Decimal("1")),
    "克": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "千克": ("mass", Decimal("1000")),
    "t": ("mass", Decimal("1000000")),
    "吨": ("mass", Decimal("1000000")),
    "mm2": ("area", Decimal("1")),
    "平方毫米": ("area", Decimal("1")),
    "cm2": ("area", Decimal("100")),
    "平方厘米": ("area", Decimal("100")),
    "m2": ("area", Decimal("1000000")),
    "平方米": ("area", Decimal("1000000")),
    "ml": ("volume", Decimal("1")),
    "毫升": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
    "升": ("volume", Decimal("1000")),
    "m3": ("volume", Decimal("1000000")),
    "立方米": ("volume", Decimal("1000000")),
}


def _unit_token(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().casefold())


def _dimension_pair(value: Any) -> tuple[Decimal, Decimal] | None:
    match = re.search(
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*[xX×*]\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))",
        str(value or ""),
    )
    if not match:
        return None
    try:
        return Decimal(match.group(1)), Decimal(match.group(2))
    except InvalidOperation:
        return None


def _text_values_equivalent(expected: Any, actual: Any) -> bool:
    if str(actual or "").strip().casefold() == str(expected or "").strip().casefold():
        return True
    expected_dimensions = _dimension_pair(expected)
    actual_dimensions = _dimension_pair(actual)
    return expected_dimensions is not None and expected_dimensions == actual_dimensions


def _convert_dynamic_number(
    value: Any,
    unit: Any,
    expected_unit: str,
) -> Decimal | None:
    if unit in (None, "") and isinstance(value, str):
        match = re.fullmatch(
            r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([^\d\s].*)?\s*",
            value,
        )
        if match:
            value = match.group(1)
            unit = match.group(2) or expected_unit
    actual_unit = _unit_token(unit)
    wanted_unit = _unit_token(expected_unit)
    try:
        number = _decimal(value, "规格数值")
    except CostingError:
        return None
    if actual_unit == wanted_unit:
        return number
    actual = _UNIT_ALIASES.get(actual_unit)
    wanted = _UNIT_ALIASES.get(wanted_unit)
    if actual is None or wanted is None or actual[0] != wanted[0]:
        return None
    return number * actual[1] / wanted[1]


def _dynamic_quote_specs(quote: dict[str, Any]) -> dict[str, dict[str, Any]]:
    extracted = quote.get("extracted") or {}
    raw = extracted.get("specifications") or extracted.get("custom_specifications") or {}
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, entry in raw.items():
            if isinstance(entry, dict):
                result[str(key)] = {
                    "label": entry.get("label") or str(key),
                    "value": entry.get("value"),
                    "unit": entry.get("unit"),
                }
            else:
                result[str(key)] = {"label": str(key), "value": entry, "unit": None}
    fields = extracted.get("fields") if isinstance(extracted, dict) else None
    if isinstance(fields, dict):
        for key, entry in fields.items():
            if key not in result and isinstance(entry, dict):
                result[str(key)] = {
                    "label": entry.get("label") or str(key),
                    "value": entry.get("value"),
                    "unit": entry.get("unit"),
                }
    return result


def _dynamic_requirement_entry(
    actual_specs: dict[str, dict[str, Any]],
    key: Any,
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    actual_entry = actual_specs.get(str(key))
    if actual_entry is None:
        expected_label = _unit_token(expected.get("label") or key)
        actual_entry = next(
            (
                entry
                for entry in actual_specs.values()
                if _unit_token(entry.get("label")) == expected_label
            ),
            None,
        )
    if actual_entry is not None:
        return actual_entry

    candidates = requirement_quote_field_candidates(key, expected.get("label") or key)
    entries = [actual_specs.get(candidate) for candidate in candidates]
    if not entries or any(entry is None or entry.get("value") is None for entry in entries):
        return None
    if len(entries) == 1:
        return entries[0]
    return {
        "label": expected.get("label") or str(key),
        "value": "×".join(str(entry["value"]) for entry in entries),
        "unit": "mm",
    }


def _dynamic_spec_checks(
    request: dict[str, Any], quote: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    checks: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    warnings: list[str] = []
    actual_specs = _dynamic_quote_specs(quote)
    for key, expected in (request.get("specifications") or {}).items():
        if not isinstance(expected, dict):
            continue
        label = str(expected.get("label") or key)
        kind = str(expected.get("type") or "text")
        match = str(expected.get("match") or "exact")
        priority = str(expected.get("priority") or "hard")
        actual_entry = _dynamic_requirement_entry(actual_specs, key, expected)
        actual_value = actual_entry.get("value") if actual_entry else None
        actual_unit = actual_entry.get("unit") if actual_entry else None
        passed = False
        display_expected = ""
        display_actual = str(actual_value if actual_value is not None else "未识别")
        tolerance = ""
        if kind == "number":
            expected_unit = str(expected.get("unit") or "")
            actual_number = _convert_dynamic_number(actual_value, actual_unit or expected_unit, expected_unit)
            if match == "range":
                minimum = _decimal(expected.get("min"), f"需求 {key} 最小值")
                maximum = _decimal(expected.get("max"), f"需求 {key} 最大值")
                display_expected = f"{minimum} 至 {maximum} {expected_unit}"
                passed = actual_number is not None and Decimal(minimum) <= actual_number <= Decimal(maximum)
            else:
                expected_number = _decimal(expected.get("value"), f"需求 {key} 数值")
                display_expected = f"{expected_number} {expected_unit}"
                if match == "tolerance":
                    tolerance_number = _decimal(expected.get("tolerance"), f"需求 {key} 公差")
                    tolerance = format(tolerance_number, "f")
                    passed = actual_number is not None and abs(actual_number - expected_number) <= tolerance_number
                elif match == "gte":
                    passed = actual_number is not None and actual_number >= expected_number
                elif match == "lte":
                    passed = actual_number is not None and actual_number <= expected_number
                else:
                    passed = actual_number is not None and actual_number == expected_number
            if actual_number is not None and actual_unit and _unit_token(actual_unit) != _unit_token(expected_unit):
                display_actual = f"{format(actual_number, 'f')} {expected_unit}（原单位 {actual_unit}）"
        elif kind == "boolean":
            expected_value = expected.get("value")
            display_expected = "是" if expected_value else "否"
            display_actual = "是" if actual_value is True else "否" if actual_value is False else "未识别"
            passed = actual_value is expected_value
        else:
            expected_value = str(expected.get("value") or "").strip()
            display_expected = expected_value
            passed = actual_value is not None and _text_values_equivalent(
                expected_value,
                actual_value,
            )
        check = {
            "field": str(key),
            "label": label,
            "expected": display_expected,
            "actual": display_actual,
            "tolerance": tolerance or match,
            "match": match,
            "priority": priority,
            "passed": passed,
        }
        checks.append(check)
        if not passed:
            if priority == "hard":
                exclusions.append(
                    {
                        "code": f"spec_{key}",
                        "message": f"{label} {display_actual} 不符合需求 {display_expected}",
                    }
                )
            else:
                warnings.append(f"偏好规格 {label} 未满足：实际为 {display_actual}，需求为 {display_expected}")
    return checks, exclusions, warnings


def _legacy_spec_checks(
    request: dict[str, Any],
    fields: dict[str, Any],
    constraints: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    specs = request.get("specifications", {})
    tolerance_mm = _decimal(constraints.get("size_tolerance_mm", "0"), "尺寸公差")
    tolerance_um = _decimal(constraints.get("thickness_tolerance_um", "0"), "厚度公差")
    checks: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
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
            continue
        passed = actual == expected
        checks.append(
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

    expected_width = _decimal(specs.get("width_mm"), "需求 width_mm")
    expected_length = _decimal(specs.get("length_mm"), "需求 length_mm")
    actual_width = _decimal(fields.get("width_mm"), "报价 width_mm")
    actual_length = _decimal(fields.get("length_mm"), "报价 length_mm")
    direct_dimensions_passed = (
        abs(actual_width - expected_width) <= tolerance_mm
        and abs(actual_length - expected_length) <= tolerance_mm
    )
    swapped_dimensions_passed = (
        abs(actual_width - expected_length) <= tolerance_mm
        and abs(actual_length - expected_width) <= tolerance_mm
    )
    dimension_orientation = (
        "direct"
        if direct_dimensions_passed
        else "swapped"
        if swapped_dimensions_passed
        else None
    )
    dimensions_passed = direct_dimensions_passed or swapped_dimensions_passed
    checks.append(
        {
            "field": "dimensions_mm",
            "expected": f"{format(expected_width, 'f')} × {format(expected_length, 'f')}",
            "actual": f"{format(actual_width, 'f')} × {format(actual_length, 'f')}",
            "tolerance": format(tolerance_mm, "f"),
            "passed": dimensions_passed,
            "orientation": dimension_orientation or "unmatched",
        }
    )
    if not dimensions_passed:
        for field, expected, actual in (
            ("width_mm", expected_width, actual_width),
            ("length_mm", expected_length, actual_length),
        ):
            if abs(actual - expected) <= tolerance_mm:
                continue
            exclusions.append(
                {
                    "code": f"spec_{field}",
                    "message": f"{SPEC_LABELS[field]} {format(actual, 'f')} 超出需求 {format(expected, 'f')}±{format(tolerance_mm, 'f')}",
                }
            )

    expected = _decimal(specs.get("thickness_um"), "需求 thickness_um")
    actual = _decimal(fields.get("thickness_um"), "报价 thickness_um")
    passed = abs(actual - expected) <= tolerance_um
    checks.append(
        {
            "field": "thickness_um",
            "expected": format(expected, "f"),
            "actual": format(actual, "f"),
            "tolerance": format(tolerance_um, "f"),
            "passed": passed,
        }
    )
    if not passed:
        exclusions.append(
            {
                "code": "spec_thickness_um",
                "message": f"{SPEC_LABELS['thickness_um']} {format(actual, 'f')} 超出需求 {format(expected, 'f')}±{format(tolerance_um, 'f')}",
            }
        )
    return checks, exclusions


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
    request_input: dict[str, Any] = {
        "id": request["id"],
        "item_name": request["item_name"],
        "quantity": request["quantity"],
        "unit": request["unit"],
        "specifications": request.get("specifications", {}),
        "constraints": request.get("constraints", {}),
        "created_at": request.get("created_at"),
    }
    if _request_schema_version(request) == 2:
        request_input["schema_version"] = 2
        request_input["category"] = request.get("category")
    quote_inputs: list[dict[str, Any]] = []
    for quote in sorted(quotes, key=lambda item: item["id"]):
        quote_input: dict[str, Any] = {
            "id": quote["id"],
            "source_sha256": quote["source_sha256"],
            "fields": _field_values(quote),
        }
        if _request_schema_version(request) == 2:
            quote_input["specifications"] = _dynamic_quote_specs(quote)
        quote_inputs.append(quote_input)
    return {
        "ruleset_version": _ruleset_version(request),
        "analysis_as_of": as_of.isoformat(),
        "request": request_input,
        "quotes": quote_inputs,
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

    spec_checks: list[dict[str, Any]] = []
    description = str(fields.get("item_description") or "")
    expected_item = _canonical_item(request.get("item_name"))
    actual_item = _canonical_item(description)
    if expected_item is not None:
        item_passed = actual_item == expected_item
        spec_checks.append(
            {
                "field": "item_identity",
                "expected": str(request.get("item_name") or ""),
                "actual": description,
                "tolerance": "exact",
                "passed": item_passed,
            }
        )
        if not item_passed:
            exclusions.append(
                {
                    "code": "item_identity",
                    "message": f"报价物料“{description or '未识别'}”与需求“{request.get('item_name')}”不一致",
                }
            )

    dimension_orientation: str | None = None
    if _request_schema_version(request) == 2:
        dynamic_checks, dynamic_exclusions, dynamic_warnings = _dynamic_spec_checks(
            request, quote
        )
        spec_checks.extend(dynamic_checks)
        exclusions.extend(dynamic_exclusions)
        warnings.extend(dynamic_warnings)
    else:
        legacy_checks, legacy_exclusions = _legacy_spec_checks(request, fields, constraints)
        spec_checks.extend(legacy_checks)
        exclusions.extend(legacy_exclusions)

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
            "dimension_orientation": dimension_orientation,
            "spec_checks": spec_checks,
            "passed": all(
                item["passed"] or item.get("priority") == "preference"
                for item in spec_checks
            ),
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
        "ruleset_version": _ruleset_version(request),
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
    "DYNAMIC_RULESET_VERSION",
    "RULESET_VERSION",
    "analysis_input_sha256",
    "canonical_analysis_input",
    "compare_quotes",
]
