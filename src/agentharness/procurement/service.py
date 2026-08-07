"""Application service for the complete procurement sourcing workflow."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, date, datetime
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
)
from agentharness.rag.chunking import build_chunk
from agentharness.rag.reference import (
    EXPANDED_TOP_K,
    INJECTED_TOP_K,
    KNOWLEDGE_INJECTION_MAX_CHARS,
    injected_text,
    sanitize_reference,
)
from agentharness.rag.retriever import Retriever

MAX_QUOTES_PER_REQUEST = 50
DEFAULT_SIZE_TOLERANCE_MM = Decimal("2")
DEFAULT_THICKNESS_TOLERANCE_UM = Decimal("3")


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
            raw_specs["length_mm"],
            "长度",
            exclusive_minimum=Decimal("0"),
            # 卷材（胶带/缠绕膜/气泡膜/珍珠棉等）按毫米填写长度时可达数十万甚至上百万
            # 毫米（1 km = 1,000,000 mm）。上限 10,000,000 mm（10 km）覆盖常见卷材，
            # 避免把合法卷材长度误当成超出限制而拒绝整单。
            maximum=Decimal("10000000"),
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
            raw_constraints.get("size_tolerance_mm", DEFAULT_SIZE_TOLERANCE_MM),
            "尺寸公差",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        ),
        "thickness_tolerance_um": _domain_decimal(
            raw_constraints.get(
                "thickness_tolerance_um", DEFAULT_THICKNESS_TOLERANCE_UM
            ),
            "厚度公差",
            minimum=Decimal("0"),
            maximum=Decimal("5000"),
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
        "title": title,
        "category": category,
        "item_name": item_name,
        "quantity": quantity,
        "unit": unit,
        "specifications": specifications,
        "constraints": constraints,
    }


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> date:
    return datetime.now(UTC).date()


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
        validated = _validated_requirement(payload)
        request_id = new_id()
        now = _utcnow()
        reference = f"RFQ-{now[:10].replace('-', '')}-{request_id[:6].upper()}"
        title = validated["title"]
        session_id = self.storage.create_session(title=f"{reference} {title}")
        request = {
            "id": request_id,
            "reference": reference,
            "title": title,
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

    def list_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        requests = self.repo.list_requests(limit)
        request_ids = [str(item["id"]) for item in requests]
        quotes = self.repo.list_quotes_for_requests(request_ids)
        decisions = self.repo.list_decisions_for_requests(request_ids)
        return [
            self._summary(
                item,
                quotes=quotes[str(item["id"])],
                decision=decisions.get(str(item["id"])),
            )
            for item in requests
        ]

    def get_request(self, request_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        quotes = [self._enrich_quote(item) for item in self.repo.list_quotes(request_id)]
        snapshot = (
            self.repo.get_snapshot(str(request["current_snapshot_id"]))
            if request.get("current_snapshot_id")
            else None
        )
        return {
            **request,
            "attachments": self._staged_attachments(request_id),
            "quotes": quotes,
            "quote_count": len(quotes),
            "unresolved_field_count": sum(len(item["review_fields"]) for item in quotes),
            "comparison": snapshot,
            "decision": self.repo.get_decision(request_id),
            "knowledge_references": self._latest_knowledge_references(request_id),
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
            if self.repo.get_decision(request_id) is not None:
                raise ProcurementError("已形成审批结论的采购需求不可再修改")
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
        self._editable_request(request_id)
        if self.repo.get_decision(request_id) is not None:
            raise ProcurementError("已形成审批结论的采购需求不可再修改")
        validated = _validated_requirement(payload)
        self.repo.update_request(
            request_id,
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

    def parse_staged_quotes(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        self._editable_request(request_id)
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
            extracted = parse_quote(
                str(attachment["filename"]), data, time_budget_s=10.0
            )
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
                "field_label": FIELD_META[field]["label"],
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
            quote["id"] for quote in quotes if fields_requiring_review(quote["extracted"])
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
        supplier_names = list(dict.fromkeys(str(item["supplier_name"]) for item in current_quotes))
        records_by_supplier = {supplier_name: [] for supplier_name in supplier_names}
        for record in self.repo.list_supplier_decision_history(
            exclude_request_id=request_id,
            supplier_names=supplier_names,
        ):
            records_by_supplier[str(record["supplier_name"])].append(
                {
                    "request_reference": record["request_reference"],
                    "decision_at": record["decision_at"],
                    "decision": record["decision"],
                }
            )
        history: list[dict[str, Any]] = []
        for current in current_quotes:
            supplier_name = str(current["supplier_name"])
            approved_records = records_by_supplier[supplier_name]
            history.append(
                {
                    "quote_id": current["id"],
                    "supplier_name": supplier_name,
                    "approved_purchase_count": len(approved_records),
                    "records": approved_records[:5],
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
                "field_label": FIELD_META[field]["label"],
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
        knowledge_references = self._knowledge_references(request_id, run_id=run_id)
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
            {
                "name": "knowledge",
                "status": "completed",
                "references": len(knowledge_references),
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
            },
        )
        return {
            "status": "completed",
            "request_id": request_id,
            "snapshot": snapshot,
            "verification": verification,
            "selection": selection,
            "stages": stages,
            "knowledge_references": knowledge_references,
        }

    def compare_for_agent(self, request_id: str, *, run_id: str) -> dict[str, Any]:
        request = self._editable_request(request_id)
        quotes = self.repo.list_quotes(request_id)
        if len(quotes) < 2:
            raise ProcurementError("至少上传 2 家供应商报价后才能比价")
        unresolved = [
            quote["id"] for quote in quotes if fields_requiring_review(quote["extracted"])
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
        artifact_payload = {
            "schema_version": 1,
            "kind": "procurement_comparison",
            "request_id": request_id,
            "snapshot_id": snapshot_id,
            "input_sha256": input_hash,
            "input": canonical_analysis_input(
                request,
                quotes,
                analysis_as_of=analysis_as_of,
            ),
            "result": result,
        }
        artifact = self.storage.artifacts.put_json(
            artifact_payload,
            summary=f"{request['reference']} 确定性比价快照",
        )
        with self.storage.transaction():
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
        quote_id: str,
        run_id: str,
        approval_id: str,
        note: str | None,
        actor: str,
    ) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.get("approved_quote_id") or str(request.get("status") or "") in {
            "approved",
            "no_award",
        }:
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
        decision = {
            "id": new_id(),
            "request_id": request_id,
            "snapshot_id": snapshot_id,
            "quote_id": quote_id,
            "run_id": run_id,
            "approval_id": approval_id,
            "decision": "approved",
            "note": (note or "").strip() or None,
            "actor": actor,
            "created_at": _utcnow(),
        }
        approved_quote = next(
            (item for item in quotes if item["id"] == quote_id),
            None,
        )
        rag_chunks: list[dict[str, Any]] = []
        if approved_quote is not None:
            rag_chunks.append(
                build_chunk(
                    request=request,
                    quote=approved_quote,
                    decision=decision,
                    snapshot_result=snapshot["result"],
                )
            )
        self.repo.commit_decision(
            decision,
            {
                "id": new_id(),
                "request_id": request_id,
                "quote_id": quote_id,
                "run_id": run_id,
                "type": "supplier_approved",
                "actor": actor,
                "payload": {
                "decision_id": decision["id"],
                "snapshot_id": snapshot_id,
                "input_sha256": input_sha256,
                "approval_id": approval_id,
                "supplier_name": selected["supplier_name"],
                "landed_total_base": selected["cost"]["landed_total_base"],
                "note": decision["note"],
                },
            },
            rag_chunks=rag_chunks,
        )
        return self.get_request(request_id)

    def record_no_award(
        self,
        request_id: str,
        *,
        snapshot_id: str,
        input_sha256: str,
        note: str | None,
        actor: str,
    ) -> dict[str, Any]:
        request = self.get_request(request_id)
        if request.get("decision") is not None:
            raise ProcurementError("该采购需求已经形成审批结论")
        if request.get("current_snapshot_id") != snapshot_id:
            raise ProcurementError("比价快照已失效，请重新分析")
        snapshot = self.repo.get_snapshot(snapshot_id)
        run_id = str(request.get("analysis_run_id") or "")
        if snapshot is None or snapshot["request_id"] != request_id or not run_id:
            raise ProcurementError("比价快照不属于当前采购任务")
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
        current = compare_quotes(request, quotes, analysis_as_of=analysis_as_of)
        if int(snapshot["result"].get("eligible_count") or 0) != 0:
            raise ProcurementError("仍有满足全部硬性条件的报价，不能提交无合格报价结论")
        if int(current.get("eligible_count") or 0) != 0:
            raise ProcurementError("当前报价中已有合格供应商，请重新分析")
        decision = {
            "id": new_id(),
            "request_id": request_id,
            "snapshot_id": snapshot_id,
            "quote_id": None,
            "run_id": run_id,
            "approval_id": None,
            "decision": "no_award",
            "note": (note or "").strip() or "全部报价未通过硬性条件，确认本轮不选定供应商。",
            "actor": actor,
            "created_at": _utcnow(),
        }
        self.repo.commit_decision(
            decision,
            {
                "id": new_id(),
                "request_id": request_id,
                "quote_id": None,
                "run_id": run_id,
                "type": "procurement_no_award",
                "actor": actor,
                "payload": {
                    "decision_id": decision["id"],
                    "snapshot_id": snapshot_id,
                    "input_sha256": input_sha256,
                    "excluded_count": snapshot["result"].get("excluded_count", 0),
                    "note": decision["note"],
                },
            },
        )
        return self.get_request(request_id)

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
            "requires_reanalysis": (
                str(detail.get("status") or "") == "ready"
                and detail.get("current_snapshot_id") is None
            ),
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
        review_fields = fields_requiring_review(extracted)
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
            if self.repo.get_decision(request_id) is not None:
                raise ProcurementError("已形成审批结论的采购需求不可再修改")
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
        return self._enrich_quote(stored)

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
        if field not in FIELD_META:
            raise ProcurementError("不支持的报价字段")
        coerced = coerce_field_value(field, value)
        if coerced is None and FIELD_META[field]["required"]:
            raise ProcurementError(f"{FIELD_META[field]['label']} 不能为空")
        extracted = dict(quote["extracted"])
        fields = dict(extracted.get("fields", {}))
        old = dict(fields.get(field, {}))
        now = _utcnow()
        source = old.get("source") or {
            "document_kind": quote["source_kind"],
            "locator": "manual",
            "excerpt": "",
            "method": "manual",
        }
        fields[field] = {
            **old,
            "value": coerced,
            "original_value": old.get("original_value", old.get("value")),
            "confidence": 1.0,
            "status": "corrected",
            "source": source,
            "correction": {"actor": actor, "corrected_at": now},
        }
        extracted["fields"] = fields
        review_fields = fields_requiring_review(extracted)
        supplier = str(fields.get("supplier_name", {}).get("value") or quote["supplier_name"])
        with self.storage.transaction():
            if self.repo.get_decision(request_id) is not None:
                raise ProcurementError("已形成审批结论的采购需求不可再修改")
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
        return self._enrich_quote(stored)



    def create_demo_request(self, *, actor: str = "演示员") -> dict[str, Any]:
        """Create and stage a frozen demo conversation (one-click demo task)."""
        from agentharness.procurement.evaluation import build_case_document, load_frozen_truth

        truth = load_frozen_truth()
        cases = truth["quotes"][:2]
        attachments = [
            (case["filename"], build_case_document(case)) for case in cases
        ]
        request = self.create_conversation(
            (
                "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
                "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
                "厚度公差3微米。请比较附件报价并推荐供应商。"
            ),
            attachments,
            actor=actor,
        )
        self._audit(
            request["id"],
            "demo_request",
            actor=actor,
            payload={"kind": "frozen-express-bag", "quote_count": len(cases)},
        )
        return self.get_request(request["id"])

    def clean_demo_requests(self) -> dict[str, int]:
        """Remove demo requests (created through the one-click demo task).

        Requests whose analysis run is still active (pending/running/
        waiting_approval) are skipped so cleanup can never delete a task that
        is mid-approval; the caller sees how many were skipped.
        """
        removed = 0
        skipped = 0
        for summary in self.list_requests():
            request_id = str(summary["id"])
            events = self.repo.list_audit_events(request_id)
            if not any(event["type"] == "demo_request" for event in events):
                continue
            run_id = str(summary.get("analysis_run_id") or "")
            if run_id:
                run = self.harness.get_run(run_id)
                if run is not None and str(run.get("status") or "") in {
                    "pending",
                    "running",
                    "waiting_approval",
                }:
                    skipped += 1
                    continue
            self.repo.delete_request_tree(request_id)
            removed += 1
        return {"removed": removed, "skipped": skipped}

    def purchase_order(self, request_id: str) -> dict[str, Any]:

        """Build (and persist once) the purchase order after approval."""
        existing = self.repo.get_purchase_order(request_id)
        if existing is not None:
            return existing
        request = self.get_request(request_id)
        decision = request.get("decision")
        if decision is None or decision.get("quote_id") is None:
            raise ProcurementError("采购任务尚未形成正式审批结论，无法生成订单")
        comparison = request.get("comparison")
        if comparison is None:
            raise ProcurementError("缺少比价快照，无法生成订单")
        result = comparison.get("result") or {}
        quote = next(
            (
                item
                for item in result.get("quotes") or []
                if item.get("quote_id") == decision.get("quote_id")
            ),
            None,
        )
        if quote is None:
            raise ProcurementError("审批所选报价不在当前快照中")
        cost = quote.get("cost") or {}
        reference = request.get("reference") or request_id[:8]
        order = {
            "id": new_id(),
            "po_number": f"PO-{reference}",
            "request_id": request_id,
            "reference": request.get("reference"),
            "title": request.get("title"),
            "item_name": request.get("item_name"),
            "quantity": request.get("quantity"),
            "unit": request.get("unit"),
            "supplier_name": quote.get("supplier_name"),
            "quote_id": quote.get("quote_id"),
            "currency": result.get("base_currency") or comparison.get("base_currency"),
            "unit_price_base": cost.get("landed_unit_base"),
            "total_amount_base": cost.get("landed_total_base"),
            "snapshot_id": comparison.get("id"),
            "snapshot_version": comparison.get("version"),
            "input_sha256": comparison.get("input_sha256"),
            "approval_id": decision.get("approval_id"),
            "decision_id": decision.get("id"),
            "created_at": datetime.now(UTC).isoformat(),
        }
        canonical = json.dumps(
            order, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        order["evidence_sha256"] = hashlib.sha256(canonical).hexdigest()
        self.repo.save_purchase_order(order)
        self._audit(
            request_id,
            "purchase_order_created",
            actor="系统",
            payload={
                "po_number": order["po_number"],
                "evidence_sha256": order["evidence_sha256"],
                "snapshot_id": comparison.get("id"),
            },
        )
        return order

    def purchase_order_csv(self, request_id: str) -> tuple[str, str]:
        """Return (filename, UTF-8 BOM CSV content) for the purchase order."""
        order = self.purchase_order(request_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "采购订单号", "需求单号", "标题", "物料", "数量", "单位",
            "供应商", "单价（本位币）", "总金额（本位币）", "币种",
            "快照ID", "快照版本", "审批ID", "创建时间", "证据SHA-256",
        ])
        writer.writerow([
            order["po_number"],
            order.get("reference") or "",
            order.get("title") or "",
            order.get("item_name") or "",
            order.get("quantity") if order.get("quantity") is not None else "",
            order.get("unit") or "",
            order.get("supplier_name") or "",
            order.get("unit_price_base") if order.get("unit_price_base") is not None else "",
            order.get("total_amount_base") if order.get("total_amount_base") is not None else "",
            order.get("currency") or "",
            order.get("snapshot_id") or "",
            order.get("snapshot_version") if order.get("snapshot_version") is not None else "",
            order.get("approval_id") or "",
            order.get("created_at") or "",
            order.get("evidence_sha256") or "",
        ])
        filename = f'{order["po_number"]}.csv'
        return filename, "\ufeff" + buffer.getvalue()

    def audit_report(self, request_id: str) -> dict[str, Any]:
        request = self.get_request(request_id)
        events = self.repo.list_audit_events(request_id)
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

    def _summary(
        self,
        request: dict[str, Any],
        *,
        quotes: list[dict[str, Any]],
        decision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            **request,
            "quote_count": len(quotes),
            "unresolved_field_count": sum(
                len(fields_requiring_review(item["extracted"])) for item in quotes
            ),
            "decision": decision,
        }

    def staged_attachment_count(self, request_id: str) -> int:
        """Number of quote attachments staged for this request (conversation flow)."""
        return len(self._staged_attachments(request_id))

    def _staged_attachments(self, request_id: str) -> list[dict[str, Any]]:
        staged = [
            dict(event["payload"])
            for event in self.repo.list_audit_events(request_id)
            if event["type"] == "attachment_staged"
        ]
        if not staged:
            return []
        imported_hashes = {
            str(quote["source_sha256"]) for quote in self.repo.list_quotes(request_id)
        }
        return [
            item
            for item in staged
            if str(item.get("sha256") or "") not in imported_hashes
        ]

    def _enrich_quote(self, quote: dict[str, Any]) -> dict[str, Any]:
        return {**quote, "review_fields": fields_requiring_review(quote["extracted"])}

    def _editable_request(self, request_id: str) -> dict[str, Any]:
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if self.repo.get_decision(request_id) is not None:
            raise ProcurementError("已形成审批结论的采购需求不可再修改")
        return request

    def _knowledge_references(
        self,
        request_id: str,
        *,
        run_id: str,
    ) -> list[dict[str, Any]]:
        """Retrieve + sanitize top-5 history, inject top-3, audit, assert budget."""
        request = self.repo.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        retriever = Retriever(self.storage)
        chunks = retriever.retrieve(
            request=request,
            limit=EXPANDED_TOP_K,
            adopted_counts=self._knowledge_adopted_counts(),
        )
        references = [
            sanitize_reference(chunk, self.storage.redactor) for chunk in chunks
        ]
        injected = injected_text(references, top_k=INJECTED_TOP_K)
        if len(injected) > KNOWLEDGE_INJECTION_MAX_CHARS:
            raise RuntimeError("knowledge injection exceeds token budget")
        self._audit(
            request_id,
            "knowledge_retrieved",
            actor="system",
            run_id=run_id,
            payload={
                "count": len(references),
                "injected_count": min(INJECTED_TOP_K, len(references)),
                "references": references,
            },
        )
        return references

    def _latest_knowledge_references(self, request_id: str) -> list[dict[str, Any]]:
        for event in reversed(self.repo.list_audit_events(request_id)):
            if event["type"] == "knowledge_retrieved":
                payload = event.get("payload", {})
                references = payload.get("references")
                if isinstance(references, list):
                    return references
        return []

    def _knowledge_adopted_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.repo.list_knowledge_feedback_events():
            if event["type"] != "knowledge_reference_adopted":
                continue
            chunk_sha = str(event.get("payload", {}).get("chunk_id") or "")
            chunk = self.storage.rag.get_chunk(chunk_sha) if chunk_sha else None
            if chunk is None:
                continue
            supplier = str(chunk.get("supplier_name") or "")
            counts[supplier] = counts.get(supplier, 0) + 1
        return counts

    def record_knowledge_feedback(
        self,
        request_id: str,
        *,
        chunk_id: str,
        action: str,
        actor: str = "采购员",
    ) -> dict[str, Any]:
        """Record viewed/adopted feedback (chunk_id + action only)."""
        if self.repo.get_request(request_id) is None:
            raise KeyError(request_id)
        if action not in {"viewed", "adopted"}:
            raise ProcurementError("反馈动作只支持 viewed 或 adopted")
        if not chunk_id or len(chunk_id) != 64:
            raise ProcurementError("历史成交参考 ID 无效")
        if self.storage.rag.get_chunk(chunk_id) is None:
            raise ProcurementError("历史成交参考不存在")
        self._audit(
            request_id,
            f"knowledge_reference_{action}",
            actor=actor,
            payload={"chunk_id": chunk_id, "action": action},
        )
        return {"ok": True, "request_id": request_id, "chunk_id": chunk_id, "action": action}

    def _sync_rag_chunk_for_quote(
        self,
        request: dict[str, Any],
        quote: dict[str, Any],
    ) -> None:
        """Keep approved knowledge chunks consistent with business facts.

        Intentionally not wired into correct_field (post-approval edits are
        rejected by the editable guard); kept as a tested utility for future
        flows that may rebuild knowledge chunks after a business-fact change.
        """
        quote_id = str(quote["id"])
        self.storage.rag.delete_chunks_for_quote(quote_id)
        decision = self.repo.get_decision(str(request["id"]))
        if decision is None or str(decision.get("decision")) != "approved":
            return
        if str(decision.get("quote_id")) != quote_id:
            return
        snapshot = self.repo.get_snapshot(str(decision["snapshot_id"]))
        if snapshot is None:
            return
        chunk = build_chunk(
            request=request,
            quote=quote,
            decision=decision,
            snapshot_result=snapshot["result"],
        )
        self.storage.rag.upsert_chunk(chunk)

    def _refresh_request_state(
        self,
        request: dict[str, Any],
        *,
        invalidate_snapshot: bool,
        pending_quotes: list[dict[str, Any]] | None = None,
    ) -> None:
        if invalidate_snapshot:
            self._record_snapshot_invalidation(request, reason="quote_changed")
        quotes_by_id: dict[str, dict[str, Any]] = {
            str(item["id"]): item for item in self.repo.list_quotes(request["id"])
        }
        for item in pending_quotes or []:
            # A pending quote can be an updated copy of an already-committed
            # quote (human correction). The pending copy must win so the stale
            # version's review fields are not counted twice and the request
            # status can advance from "review" back to "ready".
            quotes_by_id[str(item["id"])] = item
        quotes = list(quotes_by_id.values())
        unresolved = sum(len(fields_requiring_review(item["extracted"])) for item in quotes)
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
