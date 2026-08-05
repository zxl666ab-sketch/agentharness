"""Deterministic procurement requirement extraction for the internal Agent boundary."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from agentharness.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    StreamItemType,
    Usage,
)

QUANTITY_UNITS = "个|只|件|套|份|包|卷|箱|盒|张|桶|瓶|吨|公斤|千克|kg"

MODEL_REQUIREMENT_SYSTEM_PROMPT = """你是采购需求结构化助手。把用户的中文采购描述转换为一个 JSON 对象，且只输出 JSON，不要 Markdown、解释或推荐供应商。

必须返回以下结构：
{
  "schema_version": 2,
  "title": "简洁采购任务标题",
  "category": "general",
  "item_name": "物料名称",
  "quantity": "十进制数字字符串",
  "unit": "采购单位",
  "specifications": {
    "字段键": {
      "label": "用户可读字段名",
      "type": "text|number|boolean",
      "value": "值",
      "unit": "可选单位",
      "match": "exact|tolerance|range|min|max",
      "priority": "hard|preference"
    }
  },
  "constraints": {
    "base_currency": "CNY",
    "fx_rates": {"CNY": "1"},
    "max_lead_days": 20,
    "invoice_required": true,
    "size_tolerance_mm": "可选十进制数字",
    "thickness_tolerance_um": "可选十进制数字",
    "max_landed_unit_cost": "可选十进制数字",
    "destination": "可选送货地点"
  }
}

提取规则：
- “交期不超过 20 天”“最多 20 天”“20 天内”都应写为 max_lead_days=20。
- specifications 对包装类常见字段必须使用规范键：width、length、thickness、material、color、print_colors、layers。print_colors 必须为数值色数（单色印刷为 "1"），layers 必须为数值层数（五层为 "5"）。
- 400 × 300 mm 应拆为 width 和 length；材质、颜色、单色印刷、容差和送货地点都要保留。尺寸容差只能写入 constraints.size_tolerance_mm，厚度容差只能写入 constraints.thickness_tolerance_um；不要在 specifications 中再输出容差字段。
- 不要编造汇率、价格、报价或供应商信息；附件报价由另一个受控步骤解析。
- 用户明确给出的硬性条件使用 priority=hard。"""


class RequirementModelError(ValueError):
    """A configured model did not return a usable procurement requirement."""


async def extract_requirement_with_model(
    adapter: Any,
    message: str,
    *,
    model: str,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, Any], Usage, str]:
    """Call the configured model and validate its JSON-only requirement response."""

    chunks: list[str] = []
    usage = Usage()
    request = ModelRequest(
        messages=[Message(role=MessageRole.user, content=message)],
        tools=[],
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=2_000,
        system=MODEL_REQUIREMENT_SYSTEM_PROMPT,
    )
    async for item in adapter.stream(request):
        if item.type == StreamItemType.text_delta and item.text:
            chunks.append(item.text)
        elif item.type == StreamItemType.usage and item.usage is not None:
            usage = item.usage
        elif item.type == StreamItemType.error:
            raise RequirementModelError(item.error or "采购模型调用失败")
    raw = "".join(chunks).strip()
    if not raw:
        raise RequirementModelError("采购模型未返回结构化需求")
    return _validate_model_requirement(_decode_model_json(raw)), usage, raw


def _decode_model_json(raw: str) -> Any:
    candidate = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RequirementModelError("采购模型未返回有效 JSON") from exc


def _specification_identity(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()


def _canonical_specification_key(key: str, spec: dict[str, Any]) -> str:
    identities = {
        _specification_identity(key),
        _specification_identity(spec.get("label")),
    }
    if identities & {
        "printing",
        "printcolor",
        "printcolors",
        "printcolour",
        "printcolours",
        "印刷",
        "印刷色数",
        "印刷颜色",
        "印刷颜色数",
    }:
        return "print_colors"
    if identities & {
        "layercount",
        "layers",
        "corrugatedlayers",
        "瓦楞纸层数",
        "瓦楞层数",
        "层数",
    }:
        return "layers"
    if identities & {"sizetolerance", "dimensiontolerance", "尺寸容差", "尺寸公差"}:
        return "size_tolerance"
    if identities & {"thicknesstolerance", "厚度容差", "厚度公差"}:
        return "thickness_tolerance"
    return key


def _integer_specification_value(value: Any, *, field: str, maximum: int) -> str:
    text = str("" if value is None else value).strip()
    numeric = re.fullmatch(r"(\d+)\s*(?:色|层)?", text)
    if numeric:
        result = int(numeric.group(1))
    else:
        chinese_numbers = {
            "零": 0,
            "无": 0,
            "一": 1,
            "单": 1,
            "二": 2,
            "两": 2,
            "双": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        match = re.fullmatch(r"([零无一单二两双三四五六七八九十])(?:色|层)(?:印刷)?", text)
        if match:
            result = chinese_numbers[match.group(1)]
        elif field == "print_colors" and text in {"无印刷", "不印刷", "素色"}:
            result = 0
        else:
            raise RequirementModelError(f"采购模型结果 {field} 无法规范化")
    if not 0 <= result <= maximum:
        raise RequirementModelError(f"采购模型结果 {field} 超出允许范围")
    return str(result)


def _tolerance_value(value: Any, *, field: str) -> str:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RequirementModelError(f"采购模型结果 {field} 无效") from exc
    if not result.is_finite() or result < 0:
        raise RequirementModelError(f"采购模型结果 {field} 无效")
    return str(result)


def _canonicalize_model_specifications(
    specifications: dict[str, Any], constraints: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Align model-friendly aliases with the quote fields consumed by comparison."""

    normalized_constraints = dict(constraints)
    result: dict[str, Any] = {}
    tolerances = {
        "size_tolerance": "size_tolerance_mm",
        "thickness_tolerance": "thickness_tolerance_um",
    }
    for key, raw_spec in specifications.items():
        spec = dict(raw_spec)
        target = _canonical_specification_key(key, spec)
        if target in tolerances:
            constraint_key = tolerances[target]
            value = _tolerance_value(spec["value"], field=constraint_key)
            existing = normalized_constraints.get(constraint_key)
            if existing not in {None, ""} and _tolerance_value(
                existing, field=constraint_key
            ) != value:
                raise RequirementModelError(f"采购模型结果 {constraint_key} 前后不一致")
            normalized_constraints[constraint_key] = value
            continue
        if target == "print_colors":
            spec.update(
                {
                    "label": "印刷色数",
                    "type": "number",
                    "value": _integer_specification_value(
                        spec["value"], field=target, maximum=12
                    ),
                }
            )
            spec.pop("unit", None)
        elif target == "layers":
            spec.update(
                {
                    "label": "瓦楞层数",
                    "type": "number",
                    "value": _integer_specification_value(
                        spec["value"], field=target, maximum=100
                    ),
                }
            )
            spec.pop("unit", None)
        if target in result and result[target] != spec:
            raise RequirementModelError(f"采购模型结果 {target} 存在冲突定义")
        result[target] = spec
    return result, normalized_constraints


def _validate_model_requirement(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RequirementModelError("采购模型结果必须是 JSON 对象")
    try:
        schema_version = int(value.get("schema_version", 2))
    except (TypeError, ValueError) as exc:
        raise RequirementModelError("采购模型结果缺少有效 schema_version") from exc
    if schema_version not in {1, 2}:
        raise RequirementModelError("采购模型结果 schema_version 不受支持")

    def text(field: str, maximum: int) -> str:
        item = str(value.get(field) or "").strip()
        if not item or len(item) > maximum:
            raise RequirementModelError(f"采购模型结果缺少有效 {field}")
        return item

    try:
        quantity = Decimal(str(value.get("quantity")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RequirementModelError("采购模型结果缺少有效 quantity") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise RequirementModelError("采购模型结果 quantity 必须大于零")

    specifications = value.get("specifications")
    if not isinstance(specifications, dict) or len(specifications) > 100:
        raise RequirementModelError("采购模型结果缺少有效 specifications")
    for key, spec in specifications.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(spec, dict):
            raise RequirementModelError("采购模型结果 specifications 格式无效")
        if not str(spec.get("label") or "").strip() or "value" not in spec:
            raise RequirementModelError("采购模型结果 specification 缺少 label 或 value")

    constraints = value.get("constraints")
    if not isinstance(constraints, dict):
        raise RequirementModelError("采购模型结果缺少有效 constraints")
    specifications, constraints = _canonicalize_model_specifications(
        specifications, constraints
    )
    try:
        max_lead_days = int(str(constraints.get("max_lead_days")))
    except (TypeError, ValueError) as exc:
        raise RequirementModelError("采购模型结果缺少有效 max_lead_days") from exc
    if not 1 <= max_lead_days <= 3_650:
        raise RequirementModelError("采购模型结果 max_lead_days 超出允许范围")
    fx_rates = constraints.get("fx_rates")
    if not isinstance(fx_rates, dict):
        raise RequirementModelError("采购模型结果缺少有效 fx_rates")
    normalized_rates: dict[str, str] = {}
    for currency, rate in fx_rates.items():
        code = str(currency).upper().strip()
        try:
            decimal_rate = Decimal(str(rate))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RequirementModelError(f"采购模型结果汇率 {code or '未知'} 无效") from exc
        if not re.fullmatch(r"[A-Z]{3}", code) or not decimal_rate.is_finite() or decimal_rate <= 0:
            raise RequirementModelError(f"采购模型结果汇率 {code or '未知'} 无效")
        normalized_rates[code] = str(decimal_rate)
    normalized_rates.setdefault("CNY", "1")

    normalized_constraints = dict(constraints)
    normalized_constraints["base_currency"] = str(
        constraints.get("base_currency") or "CNY"
    ).upper()
    normalized_constraints["fx_rates"] = normalized_rates
    normalized_constraints["max_lead_days"] = max_lead_days
    normalized_constraints["invoice_required"] = bool(constraints.get("invoice_required", False))
    return {
        "schema_version": schema_version,
        "title": text("title", 200),
        "category": text("category", 100),
        "item_name": text("item_name", 200),
        "quantity": str(quantity),
        "unit": text("unit", 50),
        "specifications": specifications,
        "constraints": normalized_constraints,
    }


def extract_requirement(messages: list[Message]) -> dict[str, Any]:
    text = "\n".join(
        message.content for message in messages if message.role == MessageRole.user
    )

    def number(pattern: str, default: str | None = None) -> str | None:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).replace(",", "") if match else default

    def first_number(patterns: tuple[str, ...], default: str | None = None) -> str | None:
        for pattern in patterns:
            value = number(pattern)
            if value is not None:
                return value
        return default

    size = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*[xX×*]\s*"
        r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?",
        text,
        re.IGNORECASE,
    )
    quantity_text = first_number(
        (
            rf"(?:采购|计划采购|需要|数量)\s*[:：]?\s*([\d,]+(?:\.\d+)?)\s*(?:{QUANTITY_UNITS})?",
            rf"([\d,]+(?:\.\d+)?)\s*(?:{QUANTITY_UNITS})",
        )
    )
    size_values = size.groups() if size else ()
    thickness = first_number(
        (
            r"(?:厚度|厚|膜厚)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:微米|μm|µm|um)?",
            r"(\d+(?:\.\d+)?)\s*(?:微米|μm|µm|um)",
        )
    )
    is_legacy_packaging = bool(
        re.search(r"快递袋|包装袋", text)
        and size_values
        and thickness is not None
    )
    if not is_legacy_packaging:
        if quantity_text is None:
            raise ValueError("采购数量无法从采购描述中识别")
        quantity_unit_match = re.search(
            r"(?:采购|计划采购|需要|数量)\s*[:：]?\s*[\d,]+(?:\.\d+)?\s*([\u4e00-\u9fffA-Za-z]+)",
            text,
        )
        if quantity_unit_match is None:
            quantity_unit_match = next(
                (
                    match
                    for match in re.finditer(
                        r"([\d,]+(?:\.\d+)?)\s*([\u4e00-\u9fffA-Za-z]+)", text
                    )
                    if match.group(1).replace(",", "") == quantity_text
                ),
                None,
            )
        unit = quantity_unit_match.group(quantity_unit_match.lastindex or 1) if quantity_unit_match else "件"
        first_clause = re.split(r"[，,；;。.!！?？\n]", text, maxsplit=1)[0]
        item_name = re.sub(r"^(?:请|计划)?\s*(?:采购|购买|需要)\s*", "", first_clause).strip()
        item_name = re.sub(r"^[\d,.]+\s*[\u4e00-\u9fffA-Za-z]+", "", item_name).strip()
        item_name = re.sub(
            rf"\s*[\d,]+(?:\.\d+)?\s*(?:{QUANTITY_UNITS})\s*$", "", item_name
        ).strip()
        item_name = item_name or "采购物品"
        dynamic_specs: dict[str, dict[str, Any]] = {}
        width_match = re.search(
            r"(?:宽度|宽)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)",
            text,
            re.IGNORECASE,
        )
        if width_match:
            dynamic_specs["width"] = {
                "label": "宽度",
                "type": "number",
                "value": width_match.group(1),
                "unit": width_match.group(2),
                "match": "exact",
                "priority": "hard",
            }
        length_match = re.search(
            r"(?:长度|长)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)",
            text,
            re.IGNORECASE,
        )
        if length_match:
            dynamic_specs["length"] = {
                "label": "长度",
                "type": "number",
                "value": length_match.group(1),
                "unit": length_match.group(2),
                "match": "exact",
                "priority": "hard",
            }
        thickness_match = re.search(
            r"(?:厚度|厚|膜厚)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(微米|μm|µm|um|mm|毫米)",
            text,
            re.IGNORECASE,
        )
        if thickness_match:
            dynamic_specs["thickness"] = {
                "label": "厚度",
                "type": "number",
                "value": thickness_match.group(1),
                "unit": thickness_match.group(2),
                "match": "exact",
                "priority": "hard",
            }
        for key, label, pattern in (
            ("material", "材质", r"材质\s*[:：]?\s*([^，,；;。\n]+)"),
            ("color", "颜色", r"颜色\s*[:：]?\s*([^，,；;。\n]+)"),
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                dynamic_specs[key] = {
                    "label": label,
                    "type": "text",
                    "value": match.group(1).strip(),
                    "match": "exact",
                    "priority": "hard",
                }
        if "material" not in dynamic_specs:
            material_match = re.search(r"\b(BOPP|PE|PVC|PET|PP)\b", text, re.IGNORECASE)
            if material_match:
                dynamic_specs["material"] = {
                    "label": "材质",
                    "type": "text",
                    "value": material_match.group(1).upper(),
                    "match": "exact",
                    "priority": "hard",
                }
        if "color" not in dynamic_specs:
            color_match = re.search(r"(透明|白色|黑色|红色|蓝色|绿色)", text)
            if color_match:
                dynamic_specs["color"] = {
                    "label": "颜色",
                    "type": "text",
                    "value": color_match.group(1),
                    "match": "exact",
                    "priority": "hard",
                }
        lead_days = int(
            first_number(
                (
                    r"(?:最长交期|交期|交货期)[^0-9]{0,12}?(?:不超过|不多于|最多|至多|小于等于|≤)\s*(\d+)\s*天",
                    r"最长交期\s*[:：]?\s*(\d+)\s*天",
                    r"交期\s*[:：]?\s*(\d+)\s*天",
                    r"(\d+)\s*天内",
                ),
                "15",
            )
        )
        usd_rate = first_number(
            (
                r"USD\s*/\s*CNY[^0-9]*(\d+(?:\.\d+)?)",
                r"美元[^，；。\n]{0,20}?[^0-9]*(\d+(?:\.\d+)?)",
            )
        )
        fx_rates = {"CNY": "1"}
        if usd_rate is not None:
            fx_rates["USD"] = usd_rate
        max_unit_cost = re.search(
            r"(?:到货单价|单价上限|预算单价|单价预算)[^0-9]*(\d+(?:\.\d+)?)",
            text,
        )
        constraints: dict[str, Any] = {
            "base_currency": "CNY",
            "fx_rates": fx_rates,
            "max_lead_days": lead_days,
            "invoice_required": "无需开票" not in text and "不开票" not in text,
            "destination": "",
        }
        destination_match = re.search(
            r"(?:送货|配送|交付|送达)\s*(?:到|至)?\s*([^，,；;。.!！?\n]+)",
            text,
        )
        if destination_match:
            constraints["destination"] = destination_match.group(1).strip()
        if max_unit_cost:
            constraints["max_landed_unit_cost"] = max_unit_cost.group(1)
        return {
            "schema_version": 2,
            "title": f"{item_name}采购询价",
            "category": "general",
            "item_name": item_name,
            "quantity": quantity_text,
            "unit": unit,
            "specifications": dynamic_specs,
            "constraints": constraints,
        }
    missing = []
    if quantity_text is None:
        missing.append("采购数量")
    if not size_values:
        missing.append("包装尺寸")
    if thickness is None:
        missing.append("厚度")
    if missing:
        raise ValueError("、".join(missing) + "无法从采购描述中识别")
    quantity_decimal = float(quantity_text)
    if not quantity_decimal.is_integer():
        raise ValueError("采购数量必须是整数")
    quantity = int(quantity_decimal)
    width = size.group(1) if size else "0"
    length = size.group(2) if size else "0"
    lead_days = int(
        first_number(
            (
                r"(?:最长交期|交期|交货期)[^0-9]{0,12}?(?:不超过|不多于|最多|至多|小于等于|≤)\s*(\d+)\s*天",
                r"最长交期\s*[:：]?\s*(\d+)\s*天",
                r"交期\s*[:：]?\s*(\d+)\s*天",
                r"(\d+)\s*天内",
            ),
            "15",
        )
    )
    usd_rate = first_number(
        (
            r"USD\s*/\s*CNY[^0-9]*(\d+(?:\.\d+)?)",
            r"美元[^，；。\n]{0,20}?[^0-9]*(\d+(?:\.\d+)?)",
        )
    )
    eur_rate = re.search(
        r"(?:EUR\s*/\s*CNY|欧元(?:兑人民币|汇率)?)[^0-9]*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    max_unit_cost = re.search(
        r"(?:到货单价|单价上限|预算单价|单价预算)[^0-9]*(\d+(?:\.\d+)?)",
        text,
    )
    size_tolerance = number(r"尺寸公差\s*[:：]?\s*(\d+(?:\.\d+)?)", "2")
    thickness_tolerance = number(r"厚度公差\s*[:：]?\s*(\d+(?:\.\d+)?)", "3")
    destination_match = re.search(
        r"(?:送货|配送|交付|送达)\s*(?:到|至)?\s*([^，,；;。.!！?\n]+)",
        text,
    )
    destination = destination_match.group(1).strip() if destination_match else ""
    fx_rates = {"CNY": "1"}
    if usd_rate is not None or re.search(r"USD|美元", text, re.IGNORECASE):
        if usd_rate is None:
            raise ValueError("USD/CNY 汇率无法识别")
        fx_rates["USD"] = usd_rate
    if eur_rate:
        fx_rates["EUR"] = eur_rate.group(1)
    constraints: dict[str, Any] = {
        "base_currency": "CNY",
        "fx_rates": fx_rates,
        "max_lead_days": lead_days,
        "invoice_required": "无需开票" not in text and "不开票" not in text,
        "size_tolerance_mm": size_tolerance,
        "thickness_tolerance_um": thickness_tolerance,
        "destination": destination,
    }
    if max_unit_cost:
        constraints["max_landed_unit_cost"] = max_unit_cost.group(1)
    return {
        "title": "快递袋采购询价",
        "item_name": "快递袋" if "快递袋" in text else "包装耗材",
        "quantity": quantity,
        "unit": "piece",
        "specifications": {
            "width_mm": width,
            "length_mm": length,
            "thickness_um": thickness,
            "material": "PE" if re.search(r"\bPE\b", text, re.IGNORECASE) else "未说明",
            "color": "白色" if "白色" in text else "未说明",
            "print_colors": 1 if "单色" in text else 0,
        },
        "constraints": constraints,
    }
