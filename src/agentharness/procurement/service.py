"""Application service for the complete procurement sourcing workflow."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from agentharness.contracts import new_id
from agentharness.harness import Harness
from agentharness.procurement.costing import (
    analysis_input_sha256,
    canonical_analysis_input,
    compare_quotes,
)
from agentharness.procurement.parsing import (
    FIELD_META,
    MAX_FILE_BYTES,
    PARSER_VERSION,
    coerce_field_value,
    fields_requiring_review,
    parse_quote,
    requirement_quote_field_candidates,
)

MAX_QUOTES_PER_REQUEST = 50
MAX_REQUIREMENT_SPECIFICATIONS = 100
MAX_REQUIREMENT_KEY_LENGTH = 80
MAX_REQUIREMENT_LABEL_LENGTH = 120
MAX_REQUIREMENT_TEXT_LENGTH = 2_000
V2_REQUIRED_QUOTE_FIELDS = {
    "supplier_name",
    "item_description",
    "currency",
    "unit_price",
    "price_basis",
    "tax_rate",
    "tax_included",
    "shipping_included",
    "moq",
    "lead_time_days",
    "supports_invoice",
}
QUOTE_REVIEW_THRESHOLD = 0.80


class ProcurementError(ValueError):
    pass


def _domain_decimal(
    value: Any,
    label: str,
    *,
    minimum: Decimal | None = None,
    exclusive_minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> str:
    if isinstance(value, bool):
        raise ProcurementError(f"{label} 不是有效数值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProcurementError(f"{label} 不是有效数值") from exc
    if not result.is_finite():
        raise ProcurementError(f"{label} 必须是有限数值")
    if len(result.as_tuple().digits) > 60 or abs(result.as_tuple().exponent) > 1_000:
        raise ProcurementError(f"{label} 精度或数量级超出安全范围")
    if minimum is not None and result < minimum:
        raise ProcurementError(f"{label} 不得小于 {minimum}")
    if exclusive_minimum is not None and result <= exclusive_minimum:
        raise ProcurementError(f"{label} 必须大于 {exclusive_minimum}")
    if maximum is not None and result > maximum:
        raise ProcurementError(f"{label} 不得大于 {maximum}")
    normalized = result.normalize()
    return format(normalized, "f") if normalized != 0 else "0"


def _domain_integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    number = _domain_decimal(value, label)
    result = Decimal(number)
    if result != result.to_integral_value():
        raise ProcurementError(f"{label} 必须是整数")
    integer = int(result)
    if not minimum <= integer <= maximum:
        raise ProcurementError(f"{label} 必须在 {minimum} 至 {maximum} 之间")
    return integer


def _validated_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    raw_schema_version = payload.get("schema_version", 1)
    try:
        schema_version = int(raw_schema_version)
    except (TypeError, ValueError) as exc:
        raise ProcurementError("采购需求 schema_version 无效") from exc
    if schema_version == 2:
        return _validated_requirement_v2(payload)
    if schema_version != 1:
        raise ProcurementError("不支持的采购需求 schema_version")
    return _validated_requirement_v1(payload)


def _validated_requirement_v1(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"title", "item_name", "quantity", "unit", "specifications", "constraints"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ProcurementError("采购需求缺少字段：" + ", ".join(missing))

    title = str(payload["title"]).strip()
    item_name = str(payload["item_name"]).strip()
    if not title or len(title) > 200 or not item_name or len(item_name) > 200:
        raise ProcurementError("采购标题和物料名称必须为 1 至 200 个字符")
    category = str(payload.get("category") or "ecommerce_packaging")
    unit = str(payload["unit"])
    if category != "ecommerce_packaging" or unit != "piece":
        raise ProcurementError("当前采购域仅支持 ecommerce_packaging 和 piece")
    quantity = _domain_integer(payload["quantity"], "采购数量", minimum=1, maximum=100_000_000)

    raw_specs = payload["specifications"]
    if not isinstance(raw_specs, dict):
        raise ProcurementError("采购规格必须是对象")
    required_specs = {
        "width_mm",
        "length_mm",
        "thickness_um",
        "material",
        "color",
        "print_colors",
    }
    missing_specs = sorted(required_specs - raw_specs.keys())
    if missing_specs:
        raise ProcurementError("采购规格缺少字段：" + ", ".join(missing_specs))
    material = str(raw_specs["material"]).strip()
    color = str(raw_specs["color"]).strip()
    if not material or len(material) > 100 or not color or len(color) > 100:
        raise ProcurementError("材质和颜色必须为 1 至 100 个字符")
    specifications = {
        "width_mm": _domain_decimal(
            raw_specs["width_mm"], "宽度", exclusive_minimum=Decimal("0"), maximum=Decimal("10000")
        ),
        "length_mm": _domain_decimal(
            raw_specs["length_mm"], "长度", exclusive_minimum=Decimal("0"), maximum=Decimal("10000")
        ),
        "thickness_um": _domain_decimal(
            raw_specs["thickness_um"],
            "厚度",
            exclusive_minimum=Decimal("0"),
            maximum=Decimal("5000"),
        ),
        "material": material,
        "color": color,
        "print_colors": _domain_integer(
            raw_specs["print_colors"], "印刷色数", minimum=0, maximum=12
        ),
    }

    raw_constraints = payload["constraints"]
    if not isinstance(raw_constraints, dict):
        raise ProcurementError("采购约束必须是对象")
    required_constraints = {
        "base_currency",
        "fx_rates",
        "max_lead_days",
        "invoice_required",
        "size_tolerance_mm",
        "thickness_tolerance_um",
    }
    missing_constraints = sorted(required_constraints - raw_constraints.keys())
    if missing_constraints:
        raise ProcurementError("采购约束缺少字段：" + ", ".join(missing_constraints))
    base_currency = str(raw_constraints["base_currency"]).strip().upper()
    if re.fullmatch(r"[A-Z]{3}", base_currency) is None:
        raise ProcurementError("本位币必须是 3 位字母代码")
    raw_fx_rates = raw_constraints["fx_rates"]
    if not isinstance(raw_fx_rates, dict) or not 1 <= len(raw_fx_rates) <= 20:
        raise ProcurementError("汇率表必须包含 1 至 20 个币种")
    fx_rates: dict[str, str] = {}
    for raw_currency, raw_rate in raw_fx_rates.items():
        currency = str(raw_currency).strip().upper()
        # Models and users commonly write a quote currency pair (for example
        # ``USD/CNY``) even though the domain stores rates keyed by the foreign
        # currency. Only accept the unambiguous ``foreign/base`` form; never
        # silently invert a rate with the base currency in the numerator.
        pair = re.fullmatch(r"([A-Z]{3})/([A-Z]{3})", currency)
        if pair and pair.group(2) == base_currency:
            currency = pair.group(1)
        if re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ProcurementError("汇率币种必须是 3 位字母代码")
        if currency in fx_rates:
            raise ProcurementError(f"汇率币种重复：{currency}")
        fx_rates[currency] = _domain_decimal(
            raw_rate,
            f"{currency} 汇率",
            exclusive_minimum=Decimal("0"),
        )
    if base_currency in fx_rates and fx_rates[base_currency] != "1":
        raise ProcurementError("本位币汇率必须等于 1")
    fx_rates[base_currency] = "1"
    invoice_required = raw_constraints["invoice_required"]
    if not isinstance(invoice_required, bool):
        raise ProcurementError("发票要求必须是布尔值")
    constraints: dict[str, Any] = {
        "base_currency": base_currency,
        "fx_rates": fx_rates,
        "max_lead_days": _domain_integer(
            raw_constraints["max_lead_days"], "最长交期", minimum=1, maximum=365
        ),
        "invoice_required": invoice_required,
        "size_tolerance_mm": _domain_decimal(
            raw_constraints["size_tolerance_mm"],
            "尺寸公差",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        ),
        "thickness_tolerance_um": _domain_decimal(
            raw_constraints["thickness_tolerance_um"],
            "厚度公差",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        ),
    }
    max_unit_cost = raw_constraints.get("max_landed_unit_cost")
    if max_unit_cost not in (None, ""):
        constraints["max_landed_unit_cost"] = _domain_decimal(
            max_unit_cost,
            "到货单价上限",
            exclusive_minimum=Decimal("0"),
        )
    destination = str(raw_constraints.get("destination") or "").strip()
    if len(destination) > 300:
        raise ProcurementError("送货地点不得超过 300 个字符")
    constraints["destination"] = destination
    required_delivery_date = raw_constraints.get("required_delivery_date")
    if required_delivery_date not in (None, ""):
        try:
            constraints["required_delivery_date"] = date.fromisoformat(
                str(required_delivery_date)
            ).isoformat()
        except ValueError as exc:
            raise ProcurementError("要求到货日期格式无效") from exc

    return {
        "schema_version": 1,
        "title": title,
        "category": category,
        "item_name": item_name,
        "quantity": quantity,
        "unit": unit,
        "specifications": specifications,
        "constraints": constraints,
    }


def _validated_requirement_v2(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"title", "item_name", "quantity", "unit", "specifications", "constraints"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ProcurementError("采购需求缺少字段：" + ", ".join(missing))

    title = str(payload["title"]).strip()
    item_name = str(payload["item_name"]).strip()
    category = str(payload.get("category") or "general").strip()
    unit = str(payload["unit"]).strip()
    if not title or len(title) > 200 or not item_name or len(item_name) > 200:
        raise ProcurementError("采购标题和物料名称必须为 1 至 200 个字符")
    if not category or len(category) > 100:
        raise ProcurementError("采购品类必须为 1 至 100 个字符")
    if not unit or len(unit) > 50:
        raise ProcurementError("采购单位必须为 1 至 50 个字符")
    quantity = _domain_decimal(
        payload["quantity"],
        "采购数量",
        exclusive_minimum=Decimal("0"),
    )

    raw_specs = payload["specifications"]
    if not isinstance(raw_specs, dict):
        raise ProcurementError("采购规格必须是对象")
    if len(raw_specs) > MAX_REQUIREMENT_SPECIFICATIONS:
        raise ProcurementError(f"采购规格最多 {MAX_REQUIREMENT_SPECIFICATIONS} 项")
    specifications: dict[str, dict[str, Any]] = {}
    allowed_types = {"number", "text", "boolean"}
    allowed_matches = {"exact", "tolerance", "range", "gte", "lte"}
    allowed_priorities = {"hard", "preference"}
    for raw_key, raw_spec in raw_specs.items():
        key = str(raw_key).strip()
        if not key or len(key) > MAX_REQUIREMENT_KEY_LENGTH:
            raise ProcurementError("规格名称不能为空且不得超过 80 个字符")
        if not isinstance(raw_spec, dict):
            raise ProcurementError(f"规格 {key} 必须是对象")
        kind = str(raw_spec.get("type") or "text").strip().lower()
        match = str(raw_spec.get("match") or "exact").strip().lower()
        priority = str(raw_spec.get("priority") or "hard").strip().lower()
        label = str(raw_spec.get("label") or key).strip()
        if kind not in allowed_types:
            raise ProcurementError(f"规格 {key} 类型不受支持")
        if match not in allowed_matches:
            raise ProcurementError(f"规格 {key} 匹配方式不受支持")
        if priority not in allowed_priorities:
            raise ProcurementError(f"规格 {key} 优先级不受支持")
        if len(label) > MAX_REQUIREMENT_LABEL_LENGTH:
            raise ProcurementError(f"规格 {key} 标签过长")
        if kind != "number" and match != "exact":
            raise ProcurementError(f"规格 {key} 只有数值类型支持范围匹配")

        normalized: dict[str, Any] = {
            "label": label,
            "type": kind,
            "match": match,
            "priority": priority,
        }
        if kind == "number":
            raw_unit = str(raw_spec.get("unit") or "").strip()
            if len(raw_unit) > 40:
                raise ProcurementError(f"规格 {key} 单位过长")
            if not raw_unit:
                raise ProcurementError(f"数值规格 {key} 必须填写单位")
            normalized["unit"] = raw_unit
            if match == "range":
                minimum = _domain_decimal(raw_spec.get("min"), f"规格 {key} 最小值")
                maximum = _domain_decimal(raw_spec.get("max"), f"规格 {key} 最大值")
                if Decimal(minimum) > Decimal(maximum):
                    raise ProcurementError(f"规格 {key} 范围无效")
                normalized["min"] = minimum
                normalized["max"] = maximum
            else:
                normalized["value"] = _domain_decimal(
                    raw_spec.get("value"), f"规格 {key} 数值"
                )
                if match == "tolerance":
                    normalized["tolerance"] = _domain_decimal(
                        raw_spec.get("tolerance"),
                        f"规格 {key} 公差",
                        minimum=Decimal("0"),
                    )
        elif kind == "text":
            value = str(raw_spec.get("value") or "").strip()
            if not value or len(value) > MAX_REQUIREMENT_TEXT_LENGTH:
                raise ProcurementError(f"规格 {key} 文本值不能为空且不得超过 {MAX_REQUIREMENT_TEXT_LENGTH} 个字符")
            normalized["value"] = value
        else:
            value = raw_spec.get("value")
            if not isinstance(value, bool):
                raise ProcurementError(f"布尔规格 {key} 必须填写 true 或 false")
            normalized["value"] = value
        specifications[key] = normalized

    raw_constraints = payload["constraints"]
    if not isinstance(raw_constraints, dict):
        raise ProcurementError("采购约束必须是对象")
    base_currency = str(raw_constraints.get("base_currency") or "CNY").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", base_currency) is None:
        raise ProcurementError("本位币必须是 3 位字母代码")
    raw_fx_rates = raw_constraints.get("fx_rates") or {base_currency: "1"}
    if not isinstance(raw_fx_rates, dict) or not 1 <= len(raw_fx_rates) <= 20:
        raise ProcurementError("汇率表必须包含 1 至 20 个币种")
    fx_rates: dict[str, str] = {}
    for raw_currency, raw_rate in raw_fx_rates.items():
        currency = str(raw_currency).strip().upper()
        pair = re.fullmatch(r"([A-Z]{3})/([A-Z]{3})", currency)
        if pair and pair.group(2) == base_currency:
            currency = pair.group(1)
        if re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ProcurementError("汇率币种必须是 3 位字母代码")
        if currency in fx_rates:
            raise ProcurementError(f"汇率币种重复：{currency}")
        fx_rates[currency] = _domain_decimal(
            raw_rate,
            f"{currency} 汇率",
            exclusive_minimum=Decimal("0"),
        )
    if base_currency in fx_rates and fx_rates[base_currency] != "1":
        raise ProcurementError("本位币汇率必须等于 1")
    fx_rates[base_currency] = "1"
    invoice_required = raw_constraints.get("invoice_required", True)
    if not isinstance(invoice_required, bool):
        raise ProcurementError("发票要求必须是布尔值")
    constraints: dict[str, Any] = {
        "base_currency": base_currency,
        "fx_rates": fx_rates,
        "max_lead_days": _domain_integer(
            raw_constraints.get("max_lead_days", 365),
            "最长交期",
            minimum=1,
            maximum=3650,
        ),
        "invoice_required": invoice_required,
        "destination": str(raw_constraints.get("destination") or "").strip(),
    }
    if len(constraints["destination"]) > 300:
        raise ProcurementError("送货地点不得超过 300 个字符")
    max_unit_cost = raw_constraints.get("max_landed_unit_cost")
    if max_unit_cost not in (None, ""):
        constraints["max_landed_unit_cost"] = _domain_decimal(
            max_unit_cost,
            "到货单价上限",
            exclusive_minimum=Decimal("0"),
        )
    required_delivery_date = raw_constraints.get("required_delivery_date")
    if required_delivery_date not in (None, ""):
        try:
            constraints["required_delivery_date"] = date.fromisoformat(
                str(required_delivery_date)
            ).isoformat()
        except ValueError as exc:
            raise ProcurementError("要求到货日期格式无效") from exc

    return {
        "schema_version": 2,
        "title": title,
        "category": category,
        "item_name": item_name,
        "quantity": quantity,
        "unit": unit,
        "specifications": specifications,
        "constraints": constraints,
    }


def _quote_review_fields(
    request: dict[str, Any], extracted: dict[str, Any]
) -> list[str]:
    schema_version = int(request.get("schema_version") or 1)
    if schema_version < 2:
        return fields_requiring_review(extracted)
    fields = extracted.get("fields") if isinstance(extracted, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    required = list(V2_REQUIRED_QUOTE_FIELDS)
    if fields.get("shipping_included", {}).get("value") is False:
        required.append("shipping_fee")
    unresolved: list[str] = []
    for field in required:
        entry = fields.get(field) or {}
        if (
            not isinstance(entry, dict)
            or entry.get("value") is None
            or entry.get("status") == "needs_review"
            or float(entry.get("confidence", 0)) < QUOTE_REVIEW_THRESHOLD
        ):
            unresolved.append(field)
    raw_specs = extracted.get("specifications") if isinstance(extracted, dict) else None
    raw_specs = raw_specs if isinstance(raw_specs, dict) else {}

    def is_ready(entry: Any) -> bool:
        if not isinstance(entry, dict) or entry.get("value") is None:
            return False
        if entry.get("status") == "needs_review":
            return False
        try:
            return float(entry.get("confidence", 0)) >= QUOTE_REVIEW_THRESHOLD
        except (TypeError, ValueError):
            return False

    def label_key(value: Any) -> str:
        text = str(value or "").strip().casefold().replace("μ", "u").replace("µ", "u")
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)

    def labels_match(left: Any, right: Any) -> bool:
        left_key = label_key(left)
        right_key = label_key(right)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        units = ("mm", "毫米", "cm", "厘米", "um", "微米", "层", "色")
        return any(
            left_key == right_key + unit or right_key == left_key + unit
            for unit in units
        )

    for key, expected in (request.get("specifications") or {}).items():
        if not isinstance(expected, dict) or expected.get("priority", "hard") != "hard":
            continue
        entry = raw_specs.get(key) or fields.get(key)
        if entry is None:
            expected_label = expected.get("label") or key
            entry = next(
                (
                    candidate
                    for candidate in raw_specs.values()
                    if isinstance(candidate, dict)
                    and labels_match(candidate.get("label"), expected_label)
                ),
                None,
            )
        if entry is not None:
            entries = [entry]
        else:
            candidates = requirement_quote_field_candidates(
                key,
                expected.get("label") or key,
            )
            entries = [fields.get(candidate) for candidate in candidates]
        if not entries or any(not is_ready(candidate) for candidate in entries):
            unresolved.append(str(key))
    return list(dict.fromkeys(unresolved))


def _effective_request_status(
    request: dict[str, Any],
    *,
    quote_count: int,
    unresolved_field_count: int,
) -> str:
    status = str(request.get("status") or "")
    if status == "review" and unresolved_field_count == 0:
        return "ready" if quote_count >= 2 else "collecting"
    return status


def _review_field_label(request: dict[str, Any], field: str) -> str:
    fixed = FIELD_META.get(field)
    if fixed is not None:
        return str(fixed["label"])
    specification = (request.get("specifications") or {}).get(field)
    if isinstance(specification, dict):
        return str(specification.get("label") or field)
    return str(field)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> date:
    return datetime.now(UTC).date()


def _date_from_timestamp(value: Any) -> date:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return _today()


def _ensure_required_delivery_date(
    constraints: dict[str, Any],
    *,
    base_date: date,
) -> dict[str, Any]:
    if constraints.get("required_delivery_date"):
        return constraints
    lead_days = int(constraints["max_lead_days"])
    return {
        **constraints,
        "required_delivery_date": (base_date + timedelta(days=lead_days)).isoformat(),
    }


def _validated_attachment(filename: str, data: bytes) -> dict[str, Any]:
    if not filename or Path(filename).name != filename:
        raise ProcurementError("文件名无效")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".pdf"}:
        raise ProcurementError("仅支持 .xlsx 和文本型 .pdf 报价")
    if not data:
        raise ProcurementError("报价文件为空")
    if len(data) > MAX_FILE_BYTES:
        raise ProcurementError(f"报价文件不得超过 {MAX_FILE_BYTES // 1024 // 1024} MB")
    return {
        "filename": filename,
        "data": data,
        "suffix": suffix,
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if suffix == ".xlsx"
            else "application/pdf"
        ),
    }


def _new_draft_request(message: str) -> dict[str, Any]:
    request_id = new_id()
    now = _utcnow()
    reference = f"RFQ-{now[:10].replace('-', '')}-{request_id[:6].upper()}"
    title = " ".join(message.strip().split())[:80] or "采购询价"
    return {
        "id": request_id,
        "reference": reference,
        "title": title,
        "category": "ecommerce_packaging",
        "item_name": "待识别",
        "quantity": 1,
        "unit": "piece",
        "specifications": {},
        "constraints": {},
        "status": "draft",
        "session_id": new_id(),
        "created_at": now,
    }


def _canonical_sha256(value: Any) -> str:
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class ProcurementService:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.storage = harness.storage
        self.repo = harness.storage.procurement

    @property
    def field_meta(self) -> dict[str, dict[str, Any]]:
        return FIELD_META

    def create_draft(self, message: str, *, actor: str = "采购员") -> dict[str, Any]:
        request = _new_draft_request(message)
        request_id = str(request["id"])
        self.storage.create_session(
            session_id=str(request["session_id"]),
            title=f"{request['reference']} {request['title']}",
        )
        self.repo.create_request(request)
        self._audit(
            request_id,
            "request_created_from_conversation",
            actor=actor,
            payload={"reference": request["reference"], "message_length": len(message)},
        )
        return self.get_request(request_id)

    def create_conversation(
        self,
        message: str,
        attachments: list[tuple[str, bytes]],
        *,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        validated = self.validate_attachment_batch(attachments)
        prepared = [
            {
                **item,
                "artifact": self.storage.artifacts.put(
                    item["data"],
                    content_type=str(item["content_type"]),
                    summary=f"采购报价原件：{item['filename']}",
                ),
            }
            for item in validated
        ]
        request = _new_draft_request(message)
        request_id = str(request["id"])
        with self.storage.transaction():
            self.storage.create_session(
                session_id=str(request["session_id"]),
                title=f"{request['reference']} {request['title']}",
            )
            self.repo.create_request(request)
            self._audit(
                request_id,
                "request_created_from_conversation",
                actor=actor,
                payload={
                    "reference": request["reference"],
                    "message_length": len(message),
                },
            )
            for item in prepared:
                artifact_id = self.storage.register_artifact(item["artifact"])
                attachment = {
                    "filename": item["filename"],
                    "artifact_id": artifact_id,
                    "sha256": item["sha256"],
                    "content_type": item["content_type"],
                    "size_bytes": len(item["data"]),
                }
                self._audit(
                    request_id,
                    "attachment_staged",
                    actor=actor,
                    payload=attachment,
                )
        return self.get_request(request_id)

    def create_request(self, payload: dict[str, Any], *, actor: str = "采购员") -> dict[str, Any]:
        now = _utcnow()
        validated = _validated_requirement(payload)
        validated["constraints"] = _ensure_required_delivery_date(
            validated["constraints"],
            base_date=_date_from_timestamp(now),
        )
        request_id = new_id()
        reference = f"RFQ-{now[:10].replace('-', '')}-{request_id[:6].upper()}"
        title = validated["title"]
        session_id = self.storage.create_session(title=f"{reference} {title}")
        request = {
            "id": request_id,
            "reference": reference,
            "title": title,
            "schema_version": validated["schema_version"],
            "category": validated["category"],
            "item_name": validated["item_name"],
            "quantity": validated["quantity"],
            "unit": validated["unit"],
            "specifications": validated["specifications"],
            "constraints": validated["constraints"],
            "status": "collecting",
            "session_id": session_id,
            "created_at": now,
        }
        self.repo.create_request(request)
        self._audit(
            request_id,
            "request_created",
            actor=actor,
            payload={
                "reference": reference,
                "quantity": request["quantity"],
                "specifications": request["specifications"],
                "constraints": request["constraints"],
            },
        )
        return self.get_request(request_id)

    def reopen_request(
        self,
        request_id: str,
        *,
        copy_quotes: bool = False,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        source = self.repo.get_request(request_id)
        if source is None:
            raise KeyError(request_id)
        if source.get("status") not in {"approved", "no_award"}:
            raise ProcurementError("只有已批准或已流标的任务可以复制重开")
        if self.repo.get_decision(request_id) is None:
            raise ProcurementError("采购任务缺少最终决策，不能复制重开")
        now = _utcnow()
        new_request_id = new_id()
        reference = f"RFQ-{now[:10].replace('-', '')}-{new_request_id[:6].upper()}"
        session_id = self.storage.create_session(
            title=f"{reference} {source['title']}（重新询价）"
        )
        new_request = {
            "id": new_request_id,
            "reference": reference,
            "title": f"{source['title']}（重新询价）",
            "schema_version": int(source.get("schema_version") or 1),
            "category": source["category"],
            "item_name": source["item_name"],
            "quantity": source["quantity"],
            "unit": source["unit"],
            "specifications": source["specifications"],
            "constraints": source["constraints"],
            "status": "collecting",
            "session_id": session_id,
            "created_at": now,
        }
        source_quotes = self.repo.list_quotes(request_id) if copy_quotes else []
        with self.storage.transaction():
            self.repo.create_request(new_request)
            for source_quote in source_quotes:
                copied_quote = {
                    **source_quote,
                    "id": new_id(),
                    "request_id": new_request_id,
                    "created_at": now,
                    "updated_at": now,
                }
                self.repo.create_quote(copied_quote)
            self._audit(
                new_request_id,
                "request_reopened",
                actor=actor,
                payload={
                    "source_request_id": request_id,
                    "copy_quotes": copy_quotes,
                    "copied_quote_count": len(source_quotes),
                },
            )
            self._audit(
                request_id,
                "request_reopened_as_new_task",
                actor=actor,
                payload={"new_request_id": new_request_id, "copy_quotes": copy_quotes},
            )
            if source_quotes:
                self._refresh_request_state(new_request, invalidate_snapshot=False)
        return self.get_request(new_request_id)

    def list_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        return [self._summary(item) for item in self.repo.list_requests(limit)]

    def delete_request(self, request_id: str) -> dict[str, Any]:
        request = self.get_request(request_id)
        run_id = str(request.get("analysis_run_id") or "")
        if run_id:
            run = self.harness.get_run(run_id)
            status = str((run or {}).get("status") or "")
            if status in {"pending", "running", "waiting_approval"}:
                raise ProcurementError("采购 Agent 正在运行，请等待运行结束后再删除")
        if not self.repo.delete_request(request_id):
            raise KeyError(request_id)
        return {
            "request_id": request_id,
            "reference": request["reference"],
            "deleted": True,
        }

    def get_request(self, request_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        quotes = [
            self._enrich_quote(request, item)
            for item in self.repo.list_quotes(request_id)
        ]
        unresolved_field_count = sum(len(item["review_fields"]) for item in quotes)
        snapshot = (
            self.repo.get_snapshot(str(request["current_snapshot_id"]))
            if request.get("current_snapshot_id")
            else None
        )
        return {
            **request,
            "status": _effective_request_status(
                request,
                quote_count=len(quotes),
                unresolved_field_count=unresolved_field_count,
            ),
            "attachments": self._staged_attachments(request_id),
            "quotes": quotes,
            "quote_count": len(quotes),
            "unresolved_field_count": unresolved_field_count,
            "comparison": snapshot,
            "decision": self.repo.get_decision(request_id),
        }

    def validate_attachment_batch(
        self,
        attachments: list[tuple[str, bytes]],
    ) -> list[dict[str, Any]]:
        if not 2 <= len(attachments) <= MAX_QUOTES_PER_REQUEST:
            raise ProcurementError(f"每个采购任务最多上传 {MAX_QUOTES_PER_REQUEST} 份报价")
        validated = [_validated_attachment(filename, data) for filename, data in attachments]
        hashes = [item["sha256"] for item in validated]
        if len(set(hashes)) != len(hashes):
            raise ProcurementError("同一报价文件已上传")
        return validated

    def stage_attachment(
        self,
        request_id: str,
        *,
        filename: str,
        data: bytes,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        self._editable_request(request_id)
        validated = _validated_attachment(filename, data)
        source_sha = str(validated["sha256"])
        staged = self._staged_attachments(request_id)
        if len(staged) >= MAX_QUOTES_PER_REQUEST:
            raise ProcurementError(f"每个采购任务最多上传 {MAX_QUOTES_PER_REQUEST} 份报价")
        if any(item["sha256"] == source_sha for item in staged):
            raise ProcurementError("同一报价文件已上传")
        content_type = str(validated["content_type"])
        meta = self.storage.artifacts.put(
            data,
            content_type=content_type,
            summary=f"采购报价原件：{filename}",
        )
        with self.storage.transaction():
            artifact_id = self.storage.register_artifact(meta)
            attachment = {
                "filename": filename,
                "artifact_id": artifact_id,
                "sha256": source_sha,
                "content_type": content_type,
                "size_bytes": len(data),
            }
            self._audit(
                request_id,
                "attachment_staged",
                actor=actor,
                payload=attachment,
            )
        return attachment

    def bind_run(self, request_id: str, *, run_id: str, actor: str = "agent") -> None:
        if self.repo.get_request(request_id) is None:
            raise KeyError(request_id)
        self.repo.update_request(request_id, analysis_run_id=run_id)
        self._audit(
            request_id,
            "agent_run_started",
            actor=actor,
            run_id=run_id,
            payload={"run_id": run_id},
        )

    def capture_requirement(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        request = self._editable_request(request_id)
        validated = _validated_requirement(payload)
        validated["constraints"] = _ensure_required_delivery_date(
            validated["constraints"],
            base_date=_date_from_timestamp(request.get("created_at")),
        )
        self.repo.update_request(
            request_id,
            schema_version=validated["schema_version"],
            title=validated["title"],
            item_name=validated["item_name"],
            quantity=validated["quantity"],
            unit=validated["unit"],
            specifications=validated["specifications"],
            constraints=validated["constraints"],
            status="collecting",
        )
        self._audit(
            request_id,
            "requirement_captured_by_agent",
            actor="agent",
            run_id=run_id,
            payload={
                "title": validated["title"],
                "item_name": validated["item_name"],
                "quantity": validated["quantity"],
                "specifications": validated["specifications"],
                "constraints": validated["constraints"],
            },
        )
        return self.get_request(request_id)

    def replace_requirement(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        """Persist a human-confirmed requirement as one auditable correction."""

        request = self._editable_request(request_id)
        validated = _validated_requirement(payload)
        validated["constraints"] = _ensure_required_delivery_date(
            validated["constraints"],
            base_date=_date_from_timestamp(request.get("created_at")),
        )
        before = {
            key: request.get(key)
            for key in (
                "schema_version",
                "title",
                "item_name",
                "quantity",
                "unit",
                "specifications",
                "constraints",
            )
        }
        after = {
            key: validated[key]
            for key in (
                "schema_version",
                "title",
                "item_name",
                "quantity",
                "unit",
                "specifications",
                "constraints",
            )
        }
        changed_fields = [
            key for key in after if before.get(key) != after.get(key)
        ]
        with self.storage.transaction():
            self.repo.update_request(request_id, **after)
            self._audit(
                request_id,
                "requirement_corrected" if changed_fields else "requirement_confirmed",
                actor=actor,
                payload={
                    "changed_fields": changed_fields,
                    "before": before,
                    "after": after,
                },
            )
            self._refresh_request_state(
                request,
                invalidate_snapshot=bool(changed_fields),
            )
        return self.get_request(request_id)

    def parse_staged_quotes(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        request = self._editable_request(request_id)
        existing_hashes = {
            str(quote["source_sha256"]) for quote in self.repo.list_quotes(request_id)
        }
        imported: list[dict[str, Any]] = []
        for attachment in self._staged_attachments(request_id):
            if attachment["sha256"] in existing_hashes:
                continue
            data = self.storage.artifacts.get_bytes(str(attachment["sha256"]))
            if data is None:
                raise ProcurementError(f"报价原件不可用：{attachment['filename']}")
            extracted = parse_quote(str(attachment["filename"]), data)
            quote = self.import_quote(
                request_id,
                filename=str(attachment["filename"]),
                data=data,
                extracted=extracted,
                actor="agent",
            )
            imported.append(quote)
            existing_hashes.add(str(attachment["sha256"]))
        detail = self.get_request(request_id)
        gaps = [
            {
                "quote_id": quote["id"],
                "filename": quote["source_filename"],
                "field": field,
                "field_label": _review_field_label(request, field),
            }
            for quote in detail["quotes"]
            for field in quote["review_fields"]
        ]
        self._audit(
            request_id,
            "quotes_parsed_by_agent",
            actor="agent",
            run_id=run_id,
            payload={"imported_quote_ids": [item["id"] for item in imported], "gaps": gaps},
        )
        return {
            "request_id": request_id,
            "quote_count": detail["quote_count"],
            "imported_quote_ids": [item["id"] for item in imported],
            "review_gaps": gaps,
        }

    def record_clarification(
        self,
        request_id: str,
        *,
        fields: list[str],
        question: str,
        run_id: str,
    ) -> None:
        if self.repo.get_request(request_id) is None:
            raise KeyError(request_id)
        self._audit(
            request_id,
            "clarification_requested",
            actor="agent",
            run_id=run_id,
            payload={"fields": fields, "question": question},
        )

    def correct_quote_from_agent(
        self,
        request_id: str,
        quote_id: str,
        *,
        field: str,
        value: Any,
        run_id: str,
    ) -> dict[str, Any]:
        del request_id, quote_id, field, value, run_id
        raise ProcurementError("报价事实只能由采购员通过人工复核接口修正")

    def match_materials(self, request_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        quotes = self.repo.list_quotes(request_id)
        unresolved = [
            quote["id"]
            for quote in quotes
            if _quote_review_fields(request, quote["extracted"])
        ]
        if unresolved:
            raise ProcurementError("报价仍有低置信度字段，不能执行物料匹配")
        result = compare_quotes(request, quotes, analysis_as_of=_today())
        return {
            "request_id": request_id,
            "matches": [
                {
                    "quote_id": item["quote_id"],
                    "supplier_name": item["supplier_name"],
                    "passed": item["match"]["passed"],
                    "checks": item["match"]["spec_checks"],
                }
                for item in result["quotes"]
            ],
        }

    def supplier_history(self, request_id: str) -> dict[str, Any]:
        if self.repo.get_request(request_id) is None:
            raise KeyError(request_id)
        current_quotes = self.repo.list_quotes(request_id)
        history: list[dict[str, Any]] = []
        for current in current_quotes:
            supplier_name = str(current["supplier_name"])
            approved_records: list[dict[str, Any]] = []
            for request in self.repo.list_requests(limit=500):
                if request["id"] == request_id:
                    continue
                decision = self.repo.get_decision(str(request["id"]))
                if (
                    not decision
                    or decision.get("decision") != "approved"
                    or not decision.get("quote_id")
                ):
                    continue
                selected = self.repo.get_quote(str(decision["quote_id"]))
                if selected and str(selected["supplier_name"]) == supplier_name:
                    approved_records.append(
                        {
                            "request_reference": request["reference"],
                            "decision_at": decision["created_at"],
                            "decision": decision["decision"],
                        }
                    )
            history.append(
                {
                    "quote_id": current["id"],
                    "supplier_name": supplier_name,
                    "approved_purchase_count": len(approved_records),
                    "records": approved_records[-5:],
                    "evidence": "本地历史采购决策" if approved_records else "暂无本地历史记录",
                }
            )
        return {"request_id": request_id, "suppliers": history}

    def execute_analysis_pipeline(
        self,
        request_id: str,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        detail = self.get_request(request_id)
        parsed: dict[str, Any] | None = None
        if detail["quote_count"] == 0:
            parsed = self.parse_staged_quotes(request_id, run_id=run_id)
            detail = self.get_request(request_id)

        review_gaps = [
            {
                "quote_id": quote["id"],
                "filename": quote["source_filename"],
                "field": field,
                "field_label": _review_field_label(detail, field),
            }
            for quote in detail["quotes"]
            for field in quote["review_fields"]
        ]
        if review_gaps:
            labels = "、".join(
                f"{item['filename']} 的{item['field_label']}" for item in review_gaps
            )
            question = f"检测到待复核字段：{labels}。请在报价与复核面板提交准确值后继续。"
            self.record_clarification(
                request_id,
                fields=list(dict.fromkeys(str(item["field"]) for item in review_gaps)),
                question=question,
                run_id=run_id,
            )
            return {
                "status": "needs_review",
                "request_id": request_id,
                "review_gaps": review_gaps,
                "question": question,
                "parsed_quote_count": int((parsed or {}).get("quote_count") or 0),
            }

        matches = self.match_materials(request_id)
        history = self.supplier_history(request_id)
        snapshot = self.compare_for_agent(request_id, run_id=run_id)
        verification = self.verify_agent_result(request_id, run_id=run_id)
        selection = self.request_supplier_selection(request_id, run_id=run_id)
        stages = [
            {
                "name": "parse_quotes",
                "status": "completed" if parsed is not None else "reused",
                "quote_count": detail["quote_count"],
            },
            {
                "name": "match_materials",
                "status": "completed",
                "passed": sum(1 for item in matches["matches"] if item["passed"]),
                "total": len(matches["matches"]),
            },
            {
                "name": "supplier_history",
                "status": "completed",
                "suppliers": len(history["suppliers"]),
            },
            {
                "name": "compare_quotes",
                "status": "completed",
                "snapshot_id": snapshot["id"],
            },
            {
                "name": "verify_result",
                "status": "completed",
                "verified": verification["verified"],
            },
            {
                "name": "request_selection",
                "status": "completed",
                "eligible_count": len(selection["eligible_quotes"]),
            },
        ]
        self._audit(
            request_id,
            "deterministic_pipeline_completed",
            actor="system",
            run_id=run_id,
            payload={
                "stages": stages,
                "snapshot_id": snapshot["id"],
                "input_sha256": snapshot["input_sha256"],
                "recommended_quote_id": selection["recommended_quote_id"],
                "supplier_history": history,
            },
        )
        return {
            "status": "completed",
            "request_id": request_id,
            "snapshot": snapshot,
            "verification": verification,
            "selection": selection,
            "supplier_history": history,
            "stages": stages,
        }

    def compare_for_agent(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        request = self._editable_request(request_id)
        quotes = self.repo.list_quotes(request_id)
        if len(quotes) < 2:
            raise ProcurementError("至少上传 2 家供应商报价后才能比价")
        unresolved = [
            quote["id"]
            for quote in quotes
            if _quote_review_fields(request, quote["extracted"])
        ]
        if unresolved:
            raise ProcurementError("报价仍有低置信度字段，不能执行确定性比价")
        analysis_as_of = _today()
        result = compare_quotes(request, quotes, analysis_as_of=analysis_as_of)
        input_hash = analysis_input_sha256(
            request,
            quotes,
            analysis_as_of=analysis_as_of,
        )
        snapshot_id = new_id()
        with self.storage.transaction():
            current_request = self.repo.get_request(request_id)
            if current_request is None:
                raise KeyError(request_id)
            if current_request.get("approved_quote_id"):
                raise ProcurementError("已批准的采购需求不可再修改")
            current_quotes = self.repo.list_quotes(request_id)
            current_hash = analysis_input_sha256(
                current_request,
                current_quotes,
                analysis_as_of=analysis_as_of,
            )
            if current_hash != input_hash:
                raise ProcurementError("报价或采购需求在分析期间发生变化，请重新分析")

            artifact_payload = {
                "schema_version": 1,
                "kind": "procurement_comparison",
                "request_id": request_id,
                "snapshot_id": snapshot_id,
                "input_sha256": input_hash,
                "input": canonical_analysis_input(
                    current_request,
                    current_quotes,
                    analysis_as_of=analysis_as_of,
                ),
                "result": result,
            }
            artifact = self.storage.artifacts.put_json(
                artifact_payload,
                summary=f"{current_request['reference']} 确定性比价快照",
            )
            artifact_id = self.storage.register_artifact(artifact)
            snapshot = self.repo.create_snapshot(
                {
                    "id": snapshot_id,
                    "request_id": request_id,
                    "run_id": run_id,
                    "input_sha256": input_hash,
                    "result": result,
                    "artifact_id": artifact_id,
                }
            )
            self.repo.update_request(
                request_id,
                status="analyzed",
                analysis_run_id=run_id,
                current_snapshot_id=snapshot_id,
            )
            self._audit(
                request_id,
                "comparison_created_by_agent",
                actor="agent",
                run_id=run_id,
                payload={
                    "snapshot_id": snapshot_id,
                    "version": snapshot["version"],
                    "input_sha256": input_hash,
                    "artifact_id": artifact_id,
                    "recommended_quote_id": result["recommended_quote_id"],
                },
            )
        return snapshot

    def verify_agent_result(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        snapshot_id = request.get("current_snapshot_id")
        snapshot = self.repo.get_snapshot(str(snapshot_id)) if snapshot_id else None
        if snapshot is None or str(snapshot["run_id"]) != run_id:
            raise ProcurementError("当前运行没有有效比价快照")
        quotes = self.repo.list_quotes(request_id)
        analysis_as_of = snapshot["result"].get("analysis_as_of")
        if not analysis_as_of:
            raise ProcurementError("比价快照缺少分析基准日期")
        current_hash = analysis_input_sha256(
            request,
            quotes,
            analysis_as_of=analysis_as_of,
        )
        if current_hash != snapshot["input_sha256"]:
            raise ProcurementError("报价或采购需求已变化，比价快照失效")
        recomputed = compare_quotes(
            request,
            quotes,
            analysis_as_of=analysis_as_of,
        )
        if _canonical_sha256(recomputed) != _canonical_sha256(snapshot["result"]):
            raise ProcurementError("比价快照无法通过确定性复算")
        return {
            "request_id": request_id,
            "run_id": run_id,
            "snapshot_id": snapshot["id"],
            "input_sha256": current_hash,
            "verified": True,
            "recommended_quote_id": snapshot["result"]["recommended_quote_id"],
            "recommendation_explanation": snapshot["result"]["recommendation_explanation"],
        }

    def request_supplier_selection(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        detail = self.get_request(request_id)
        snapshot = detail.get("comparison")
        if snapshot is None or str(snapshot["run_id"]) != run_id:
            raise ProcurementError("当前运行没有可供审批的比价快照")
        eligible = [
            {
                "quote_id": item["quote_id"],
                "supplier_name": item["supplier_name"],
                "landed_total_base": item["cost"]["landed_total_base"],
            }
            for item in snapshot["result"]["quotes"]
            if item["eligible"]
        ]
        payload = {
            "snapshot_id": snapshot["id"],
            "input_sha256": snapshot["input_sha256"],
            "recommended_quote_id": snapshot["result"]["recommended_quote_id"],
            "recommendation_explanation": snapshot["result"]["recommendation_explanation"],
            "eligible_quotes": eligible,
        }
        self._audit(
            request_id,
            "supplier_selection_requested",
            actor="agent",
            run_id=run_id,
            payload=payload,
        )
        return payload

    def approve_supplier_from_agent(
        self,
        request_id: str,
        *,
        snapshot_id: str,
        input_sha256: str,
        quote_id: str | None,
        decision: str = "approved",
        run_id: str,
        approval_id: str,
        note: str | None,
        actor: str,
    ) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if decision not in {"approved", "no_award"}:
            raise ProcurementError("不支持的采购决策")
        if request.get("approved_quote_id") or request.get("status") in {
            "approved",
            "no_award",
        } or self.repo.get_decision(request_id) is not None:
            raise ProcurementError("该采购需求已经完成供应商审批")
        if request.get("current_snapshot_id") != snapshot_id:
            raise ProcurementError("比价快照已失效，请重新分析")
        snapshot = self.repo.get_snapshot(snapshot_id)
        if (
            snapshot is None
            or snapshot["request_id"] != request_id
            or snapshot["run_id"] != run_id
        ):
            raise ProcurementError("比价快照不属于当前 Agent 运行")
        quotes = self.repo.list_quotes(request_id)
        analysis_as_of = snapshot["result"].get("analysis_as_of")
        if not analysis_as_of:
            raise ProcurementError("比价快照缺少分析基准日期")
        current_hash = analysis_input_sha256(
            request,
            quotes,
            analysis_as_of=analysis_as_of,
        )
        if snapshot["input_sha256"] != input_sha256 or current_hash != input_sha256:
            raise ProcurementError("报价或采购需求已变化，比价快照失效")
        selected = next(
            (
                item
                for item in snapshot["result"].get("quotes", [])
                if item.get("quote_id") == quote_id
            ),
            None,
        )
        if decision == "approved":
            if selected is None or not selected.get("eligible"):
                raise ProcurementError("只能选定通过全部硬性条件的报价")
            approval_comparison = compare_quotes(
                request,
                quotes,
                analysis_as_of=_today(),
            )
            current_selected = next(
                (
                    item
                    for item in approval_comparison["quotes"]
                    if item.get("quote_id") == quote_id
                ),
                None,
            )
            if current_selected is None or not current_selected.get("eligible"):
                raise ProcurementError("所选报价在审批日已不再满足硬性条件，请重新分析")
        else:
            if quote_id is not None or note is None or not note.strip():
                raise ProcurementError("流标必须填写原因且不能选择供应商")
            current_comparison = compare_quotes(
                request,
                quotes,
                analysis_as_of=analysis_as_of,
            )
            if current_comparison["eligible_count"] != 0:
                raise ProcurementError("当前仍有合格报价，不能流标")
        decision = {
            "id": new_id(),
            "request_id": request_id,
            "snapshot_id": snapshot_id,
            "quote_id": quote_id,
            "run_id": run_id,
            "approval_id": approval_id,
            "decision": decision,
            "note": (note or "").strip() or None,
            "actor": actor,
            "created_at": _utcnow(),
        }
        self.repo.commit_decision(
            decision,
            {
                "id": new_id(),
                "request_id": request_id,
                "quote_id": quote_id,
                "run_id": run_id,
                "type": "supplier_approved" if decision["decision"] == "approved" else "supplier_no_award",
                "actor": actor,
                "payload": {
                "decision_id": decision["id"],
                "snapshot_id": snapshot_id,
                "input_sha256": input_sha256,
                "approval_id": approval_id,
                "supplier_name": selected["supplier_name"] if selected else None,
                "landed_total_base": selected["cost"]["landed_total_base"] if selected else None,
                "note": decision["note"],
                "decision": decision["decision"],
                },
            },
        )
        if decision["decision"] == "approved":
            self.ensure_execution_artifacts(request_id)
        return self.get_request(request_id)

    @staticmethod
    def _artifact_text(value: Any) -> str:
        return str(value if value not in (None, "") else "-").replace("\r", " ").replace("\n", " ")

    def ensure_execution_artifacts(self, request_id: str) -> list[dict[str, Any]]:
        """Create idempotent, human-editable execution drafts after approval."""

        existing = next(
            (
                event["payload"].get("artifacts")
                for event in self.repo.list_audit_events(request_id)
                if event["type"] == "execution_artifacts_created"
                and isinstance(event["payload"].get("artifacts"), list)
            ),
            None,
        )
        if existing is not None:
            return list(existing)

        detail = self.get_request(request_id)
        decision = detail.get("decision")
        snapshot = detail.get("comparison")
        if decision is None or decision.get("decision") != "approved" or snapshot is None:
            return []
        selected = next(
            (
                item
                for item in snapshot["result"].get("quotes", [])
                if item.get("quote_id") == decision.get("quote_id")
            ),
            None,
        )
        if selected is None:
            raise ProcurementError("审批结果缺少已选报价，无法生成执行草稿")

        request = detail
        specs = request.get("specifications") or {}
        constraints = request.get("constraints") or {}
        commercial = selected.get("commercial") or {}
        cost = selected.get("cost") or {}
        supplier = self._artifact_text(selected.get("supplier_name"))
        reference = self._artifact_text(request.get("reference"))
        item_name = self._artifact_text(request.get("item_name"))
        quantity = self._artifact_text(request.get("quantity"))
        currency = self._artifact_text(cost.get("base_currency"))
        landed_total = self._artifact_text(cost.get("landed_total_base"))
        landed_unit = self._artifact_text(cost.get("landed_unit_base"))
        lead_days = self._artifact_text(commercial.get("lead_time_days"))
        destination = self._artifact_text(constraints.get("destination"))
        invoice = "需要开票" if constraints.get("invoice_required") else "不要求开票"
        specification_text = (
            f"{self._artifact_text(specs.get('width_mm'))} × "
            f"{self._artifact_text(specs.get('length_mm'))} mm；"
            f"厚度 {self._artifact_text(specs.get('thickness_um'))} µm；"
            f"材质 {self._artifact_text(specs.get('material'))}；"
            f"颜色 {self._artifact_text(specs.get('color'))}；"
            f"印刷色数 {self._artifact_text(specs.get('print_colors'))}"
        )
        source_quote = next(
            (quote for quote in detail["quotes"] if quote["id"] == decision["quote_id"]),
            None,
        )
        source_filename = self._artifact_text(
            source_quote.get("source_filename") if source_quote else None
        )
        source_sha = self._artifact_text(
            source_quote.get("source_sha256") if source_quote else None
        )
        order_text = "\n".join(
            [
                "# 采购订单草稿",
                "",
                f"- 采购编号：{reference}",
                f"- 供应商：{supplier}",
                f"- 物料：{item_name}",
                f"- 规格：{specification_text}",
                f"- 数量：{quantity} 个",
                f"- 预计交期：{lead_days} 天",
                f"- 送货地点：{destination}",
                f"- 到货单价：{landed_unit} {currency}",
                f"- 到货总成本：{landed_total} {currency}",
                f"- 发票要求：{invoice}",
                "",
                "> 状态：待供应商确认；本文件由已批准的采购决策生成，不等同于正式 ERP 订单。",
                f"> 报价原件：{source_filename}（SHA-256：{source_sha}）",
            ]
        )
        email_text = "\n".join(
            [
                f"主题：{reference} 采购订单确认｜{item_name}",
                "",
                f"尊敬的 {supplier}：",
                "",
                f"您好，现请贵司确认以下采购安排：{quantity} 个 {item_name}（{specification_text}）。",
                f"到货单价 {landed_unit} {currency}，预计 {lead_days} 天内送达 {destination}，{invoice}。",
                "",
                "请确认规格、数量、价格、交期和送货地点无误，并回复可执行的交付日期。",
                "",
                "谢谢。",
                "采购部",
            ]
        )
        order_meta = self.storage.artifacts.put(
            order_text,
            content_type="text/plain",
            summary=f"{reference} 采购订单草稿",
        )
        email_meta = self.storage.artifacts.put(
            email_text,
            content_type="text/plain",
            summary=f"{reference} 供应商确认邮件草稿",
        )
        artifacts: list[dict[str, Any]] = []
        for kind, meta, filename in (
            ("purchase_order_draft", order_meta, f"{reference}-采购订单草稿.txt"),
            ("supplier_confirmation_email", email_meta, f"{reference}-供应商确认邮件.txt"),
        ):
            artifacts.append(
                {
                    "kind": kind,
                    "artifact_id": self.storage.register_artifact(meta),
                    "sha256": meta["sha256"],
                    "filename": filename,
                    "content_type": meta["content_type"],
                    "summary": meta["summary"],
                }
            )
        with self.storage.transaction():
            self._audit(
                request_id,
                "execution_artifacts_created",
                actor="system",
                run_id=str(decision.get("run_id") or "") or None,
                payload={"artifacts": artifacts, "quote_id": decision["quote_id"]},
            )
        return artifacts

    def agent_state(self, request_id: str) -> dict[str, Any]:
        detail = self.get_request(request_id)
        comparison = detail.get("comparison")
        unresolved_quotes = [
            quote for quote in detail["quotes"] if quote["review_fields"]
        ]
        return {
            "request": {
                key: detail[key]
                for key in (
                    "id",
                    "reference",
                    "title",
                    "item_name",
                    "quantity",
                    "unit",
                    "specifications",
                    "constraints",
                    "status",
                    "session_id",
                    "analysis_run_id",
                    "current_snapshot_id",
                )
            },
            "attachment_count": len(detail["attachments"]),
            "quote_count": detail["quote_count"],
            "unresolved_quote_count": len(unresolved_quotes),
            "quote_scope": "unresolved_only",
            "quotes": [
                {
                    "id": quote["id"],
                    "source_filename": quote["source_filename"],
                    "supplier_name": quote["supplier_name"],
                    "review_fields": quote["review_fields"],
                }
                for quote in unresolved_quotes
            ],
            "comparison": (
                {
                    "id": comparison["id"],
                    "run_id": comparison["run_id"],
                    "input_sha256": comparison["input_sha256"],
                    "recommended_quote_id": comparison["result"]["recommended_quote_id"],
                }
                if comparison
                else None
            ),
            "decision": detail["decision"],
        }

    def import_quote(
        self,
        request_id: str,
        *,
        filename: str,
        data: bytes,
        extracted: dict[str, Any] | None = None,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        request = self._editable_request(request_id)
        quotes = self.repo.list_quotes(request_id)
        if len(quotes) >= MAX_QUOTES_PER_REQUEST:
            raise ProcurementError(f"每个采购任务最多上传 {MAX_QUOTES_PER_REQUEST} 份报价")
        extracted = extracted or parse_quote(filename, data)
        source_sha = hashlib.sha256(data).hexdigest()
        if any(
            quote["source_sha256"] == source_sha
            for quote in quotes
        ):
            raise ProcurementError("同一报价文件已上传")
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if Path(filename).suffix.lower() == ".xlsx"
            else "application/pdf"
        )
        meta = self.storage.artifacts.put(
            data,
            content_type=content_type,
            summary=f"采购报价原件：{filename}",
        )
        review_fields = _quote_review_fields(request, extracted)
        supplier = str(
            extracted.get("fields", {}).get("supplier_name", {}).get("value")
            or Path(filename).stem
        )
        quote_id = new_id()
        quote = {
            "id": quote_id,
            "request_id": request_id,
            "supplier_name": supplier,
            "source_filename": filename,
            "source_kind": Path(filename).suffix.lower().removeprefix("."),
            "source_artifact_id": "",
            "source_sha256": source_sha,
            "extracted": extracted,
            "status": "needs_review" if review_fields else "ready",
            "review_count": len(review_fields),
            "parser_version": PARSER_VERSION,
            "processing_ms": extracted.get("processing_ms", 0),
        }
        with self.storage.transaction():
            artifact_id = self.storage.register_artifact(meta)
            quote["source_artifact_id"] = artifact_id
            self.repo.create_quote(quote)
            self._audit(
                request_id,
                "quote_imported",
                quote_id=quote_id,
                actor=actor,
                payload={
                    "filename": filename,
                    "source_sha256": source_sha,
                    "artifact_id": artifact_id,
                    "parser_version": PARSER_VERSION,
                    "review_fields": review_fields,
                },
            )
            self._refresh_request_state(
                request,
                invalidate_snapshot=True,
                pending_quotes=[quote],
            )
        stored = self.repo.get_quote(quote_id)
        if stored is None:
            raise RuntimeError("报价写入后不可见")
        return self._enrich_quote(request, stored)

    def correct_field(
        self,
        request_id: str,
        quote_id: str,
        *,
        field: str,
        value: Any,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        request = self._editable_request(request_id)
        quote = self.repo.get_quote(quote_id)
        if quote is None or quote["request_id"] != request_id:
            raise KeyError(quote_id)
        extracted = dict(quote["extracted"])
        fields = dict(extracted.get("fields", {}))
        dynamic_definition = (
            request.get("specifications", {}).get(field)
            if int(request.get("schema_version") or 1) >= 2
            else None
        )
        if field in FIELD_META:
            coerced = coerce_field_value(field, value)
            if coerced is None and FIELD_META[field]["required"]:
                raise ProcurementError(f"{FIELD_META[field]['label']} 不能为空")
            target = fields
            old = dict(fields.get(field, {}))
        elif isinstance(dynamic_definition, dict):
            kind = str(dynamic_definition.get("type") or "text")
            if kind == "number":
                coerced = _domain_decimal(value, f"{dynamic_definition.get('label') or field} 数值")
            elif kind == "boolean":
                if not isinstance(value, bool):
                    raise ProcurementError(f"{dynamic_definition.get('label') or field} 必须是布尔值")
                coerced = value
            else:
                coerced = str(value or "").strip()
                if not coerced:
                    raise ProcurementError(f"{dynamic_definition.get('label') or field} 不能为空")
            target = dict(extracted.get("specifications", {}))
            old = dict(target.get(field, {}))
        else:
            raise ProcurementError("不支持的报价字段")
        now = _utcnow()
        source = old.get("source") or {
            "document_kind": quote["source_kind"],
            "locator": "manual",
            "excerpt": "",
            "method": "manual",
        }
        updated_entry = {
            **old,
            "value": coerced,
            "original_value": old.get("original_value", old.get("value")),
            "confidence": 1.0,
            "status": "corrected",
            "source": source,
            "correction": {"actor": actor, "corrected_at": now},
        }
        target[field] = updated_entry
        if target is fields:
            extracted["fields"] = fields
        else:
            extracted["specifications"] = target
        review_fields = _quote_review_fields(request, extracted)
        supplier = str(fields.get("supplier_name", {}).get("value") or quote["supplier_name"])
        with self.storage.transaction():
            self.repo.update_quote(
                quote_id,
                extracted=extracted,
                supplier_name=supplier,
                status="needs_review" if review_fields else "ready",
                review_count=len(review_fields),
            )
            self._audit(
                request_id,
                "field_corrected",
                quote_id=quote_id,
                actor=actor,
                payload={
                    "field": field,
                    "old_value": old.get("value"),
                    "new_value": coerced,
                    "source": source,
                },
            )
            self._refresh_request_state(
                request,
                invalidate_snapshot=True,
                pending_quotes=[{**quote, "extracted": extracted}],
            )
        stored = self.repo.get_quote(quote_id)
        if stored is None:
            raise RuntimeError("报价修正后不可见")
        return self._enrich_quote(request, stored)



    def audit_report(self, request_id: str) -> dict[str, Any]:
        request = self.get_request(request_id)
        self.ensure_execution_artifacts(request_id)
        events = self.repo.list_audit_events(request_id)
        execution_artifacts = next(
            (
                event["payload"].get("artifacts")
                for event in events
                if event["type"] == "execution_artifacts_created"
                and isinstance(event["payload"].get("artifacts"), list)
            ),
            [],
        )
        supplier_history = next(
            (
                event["payload"].get("supplier_history")
                for event in events
                if event["type"] == "deterministic_pipeline_completed"
                and isinstance(event["payload"].get("supplier_history"), dict)
            ),
            {"request_id": request_id, "suppliers": []},
        )
        report = {
            "schema_version": 1,
            "request": {
                key: value
                for key, value in request.items()
                if key not in {"quotes", "comparison", "decision"}
            },
            "quotes": request["quotes"],
            "comparison": request["comparison"],
            "decision": request["decision"],
            "execution_artifacts": execution_artifacts,
            "supplier_history": supplier_history,
            "audit_events": events,
            "runtime": {
                "session_id": request["session_id"],
                "run_id": request.get("analysis_run_id"),
                "checkpoint_endpoint": (
                    f"/api/runs/{request['analysis_run_id']}/checkpoint"
                    if request.get("analysis_run_id")
                    else None
                ),
                "report_endpoint": (
                    f"/api/runs/{request['analysis_run_id']}/report"
                    if request.get("analysis_run_id")
                    else None
                ),
            },
        }
        report["evidence_sha256"] = _canonical_sha256(report)
        return report

    def _summary(self, request: dict[str, Any]) -> dict[str, Any]:
        quotes = self.repo.list_quotes(request["id"])
        unresolved_field_count = sum(
            len(_quote_review_fields(request, item["extracted"])) for item in quotes
        )
        return {
            **request,
            "status": _effective_request_status(
                request,
                quote_count=len(quotes),
                unresolved_field_count=unresolved_field_count,
            ),
            "quote_count": len(quotes),
            "unresolved_field_count": unresolved_field_count,
            "decision": self.repo.get_decision(request["id"]),
        }

    def _staged_attachments(self, request_id: str) -> list[dict[str, Any]]:
        return [
            dict(event["payload"])
            for event in self.repo.list_audit_events(request_id)
            if event["type"] == "attachment_staged"
        ]

    def _enrich_quote(
        self, request: dict[str, Any], quote: dict[str, Any]
    ) -> dict[str, Any]:
        review_fields = _quote_review_fields(request, quote["extracted"])
        return {
            **quote,
            "status": "needs_review" if review_fields else "ready",
            "review_count": len(review_fields),
            "review_fields": review_fields,
        }

    def _editable_request(self, request_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.get("approved_quote_id") or request.get("status") in {
            "approved",
            "no_award",
        } or self.repo.get_decision(request_id) is not None:
            raise ProcurementError("已批准的采购需求不可再修改")
        return request

    def _refresh_request_state(
        self,
        request: dict[str, Any],
        *,
        invalidate_snapshot: bool,
        pending_quotes: list[dict[str, Any]] | None = None,
    ) -> None:
        if invalidate_snapshot:
            self._record_snapshot_invalidation(request, reason="quote_changed")
        quotes_by_id = {
            str(quote["id"]): quote for quote in self.repo.list_quotes(request["id"])
        }
        for quote in pending_quotes or []:
            quotes_by_id[str(quote["id"])] = quote
        quotes = list(quotes_by_id.values())
        unresolved = sum(
            len(_quote_review_fields(request, item["extracted"])) for item in quotes
        )
        if unresolved:
            status = "review"
        elif len(quotes) < 2:
            status = "collecting"
        else:
            status = "ready"
        changes: dict[str, Any] = {"status": status}
        if invalidate_snapshot:
            changes["current_snapshot_id"] = None
        self.repo.update_request(request["id"], **changes)

    def _record_snapshot_invalidation(
        self,
        request: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        snapshot_id = request.get("current_snapshot_id")
        if not snapshot_id:
            return
        self._audit(
            request["id"],
            "comparison_superseded",
            run_id=str(request.get("analysis_run_id") or "") or None,
            actor="system",
            payload={"reason": reason, "snapshot_id": snapshot_id},
        )

    def _audit(
        self,
        request_id: str,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any],
        quote_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.repo.add_audit_event(
            {
                "id": new_id(),
                "request_id": request_id,
                "quote_id": quote_id,
                "run_id": run_id,
                "type": event_type,
                "actor": actor,
                "payload": payload,
            }
        )

__all__ = ["MAX_QUOTES_PER_REQUEST", "ProcurementError", "ProcurementService"]
