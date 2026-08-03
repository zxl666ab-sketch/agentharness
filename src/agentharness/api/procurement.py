"""Narrow Web API for procurement requests, quote review, comparison, and approval."""

from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentharness.procurement.agent import ProcurementAgent
from agentharness.procurement.costing import RULESET_VERSION
from agentharness.procurement.evaluation import evaluate_frozen_cases
from agentharness.procurement.parsing import (
    MAX_FILE_BYTES,
    PARSER_VERSION,
    QuoteParseError,
    parse_quote,
)
from agentharness.procurement.service import (
    MAX_QUOTES_PER_REQUEST,
    ProcurementError,
    ProcurementService,
)

MAX_CONVERSATION_UPLOAD_BYTES = 20 * 1024 * 1024


class PackagingSpecifications(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width_mm: Decimal = Field(gt=0, le=10_000)
    length_mm: Decimal = Field(gt=0, le=10_000)
    thickness_um: Decimal = Field(gt=0, le=5_000)
    material: str = Field(default="PE", min_length=1, max_length=100)
    color: str = Field(default="白色", min_length=1, max_length=100)
    print_colors: int = Field(default=0, ge=0, le=12)


class ProcurementConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str = Field(default="CNY", min_length=3, max_length=3)
    fx_rates: dict[str, Decimal] = Field(default_factory=lambda: {"CNY": Decimal("1")})
    max_lead_days: int = Field(default=15, ge=1, le=365)
    invoice_required: bool = True
    size_tolerance_mm: Decimal = Field(default=Decimal("2"), ge=0, le=100)
    thickness_tolerance_um: Decimal = Field(default=Decimal("3"), ge=0, le=100)
    max_landed_unit_cost: Decimal | None = Field(default=None, gt=0)
    destination: str = Field(default="", max_length=300)
    required_delivery_date: date | None = None

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("fx_rates")
    @classmethod
    def validate_fx_rates(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if not value or len(value) > 20:
            raise ValueError("汇率表必须包含 1 至 20 个币种")
        normalized: dict[str, Decimal] = {}
        for currency, rate in value.items():
            code = str(currency).upper()
            if len(code) != 3 or rate <= 0:
                raise ValueError("币种代码必须为 3 个字母，且汇率必须大于 0")
            normalized[code] = rate
        return normalized

    @model_validator(mode="after")
    def base_currency_is_identity(self) -> ProcurementConstraints:
        if self.fx_rates.get(self.base_currency) != Decimal("1"):
            raise ValueError("本位币汇率必须等于 1")
        return self


class CreateProcurementRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    category: Literal["ecommerce_packaging"] = "ecommerce_packaging"
    item_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=100_000_000)
    unit: Literal["piece"] = "piece"
    specifications: PackagingSpecifications
    constraints: ProcurementConstraints

    @field_validator("title", "item_name")
    @classmethod
    def non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("内容不能为空")
        return cleaned


class ImportQuoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=7_100_000)

    def decode(self) -> bytes:
        try:
            data = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProcurementError("报价文件内容不是有效 Base64") from exc
        if len(data) > MAX_FILE_BYTES:
            raise ProcurementError(f"报价文件不得超过 {MAX_FILE_BYTES // 1024 // 1024} MB")
        return data


class ConversationAttachment(ImportQuoteBody):
    pass


class StartProcurementConversationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)
    attachments: list[ConversationAttachment] = Field(
        min_length=2, max_length=MAX_QUOTES_PER_REQUEST
    )
    actor: str = Field(default="采购员", min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("采购目标不能为空")
        return cleaned

    def decoded_attachments(self) -> list[tuple[str, bytes]]:
        decoded = [(item.filename, item.decode()) for item in self.attachments]
        if sum(len(data) for _filename, data in decoded) > MAX_CONVERSATION_UPLOAD_BYTES:
            raise ProcurementError("单次上传的报价文件总计不得超过 20 MB")
        return decoded


class CorrectQuoteFieldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=100)
    value: str | int | float | bool | None
    actor: str = Field(default="采购员", min_length=1, max_length=100)


class ResumeProcurementConversationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("补充信息不能为空")
        return cleaned


class ApproveSupplierBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote_id: str = Field(min_length=1, max_length=128)
    confirmed: bool
    note: str | None = Field(default=None, max_length=2_000)
    actor: str = Field(default="采购员", min_length=1, max_length=100)


class ProcurementModelConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["procurement_fake", "openai"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=1_000)
    api_mode: Literal["auto", "chat", "responses"] = "auto"
    reasoning_effort: Literal[
        "auto", "none", "minimal", "low", "medium", "high", "max"
    ] = "auto"
    input_price_per_million_usd: float | None = Field(default=None, ge=0, le=1_000)
    output_price_per_million_usd: float | None = Field(default=None, ge=0, le=1_000)
    cached_input_price_per_million_usd: float | None = Field(
        default=None, ge=0, le=1_000
    )
    max_cost_usd: float | None = Field(default=None, ge=0, le=100)

    @field_validator("model")
    @classmethod
    def model_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("模型名称不能为空")
        return cleaned

    @field_validator("base_url", "api_key")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


def procurement_router(service: ProcurementService, agent: ProcurementAgent) -> APIRouter:
    router = APIRouter(prefix="/api/procurement", tags=["procurement"])

    @router.post("/conversations", status_code=202)
    async def start_conversation(
        body: StartProcurementConversationBody,
    ) -> dict[str, str]:
        try:
            return await agent.start(
                message=body.message,
                attachments=body.decoded_attachments(),
                actor=body.actor,
            )
        except (ProcurementError, QuoteParseError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/meta")
    async def meta() -> dict[str, Any]:
        return {
            "category": "ecommerce_packaging",
            "parser_version": PARSER_VERSION,
            "ruleset_version": RULESET_VERSION,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_conversation_upload_bytes": MAX_CONVERSATION_UPLOAD_BYTES,
            "max_quotes_per_request": MAX_QUOTES_PER_REQUEST,
            "allowed_extensions": [".xlsx", ".pdf"],
            "field_meta": service.field_meta,
        }

    @router.get("/config")
    async def config() -> dict[str, Any]:
        return agent.model_config()

    @router.post("/config")
    async def update_config(body: ProcurementModelConfigBody) -> dict[str, Any]:
        try:
            return await agent.configure_model(**body.model_dump())
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/requests")
    async def requests(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service.list_requests(limit)

    @router.post("/requests", status_code=201)
    async def create_request(body: CreateProcurementRequestBody) -> dict[str, Any]:
        return service.create_request(body.model_dump(mode="json"))

    @router.get("/requests/{request_id}")
    async def request_detail(request_id: str) -> dict[str, Any]:
        try:
            return service.get_request(request_id)
        except KeyError:
            raise HTTPException(404, "未找到采购需求") from None

    @router.post("/requests/{request_id}/resume", status_code=202)
    async def resume_conversation(
        request_id: str,
        body: ResumeProcurementConversationBody,
    ) -> dict[str, str]:
        try:
            return await agent.resume(request_id, message=body.message)
        except KeyError:
            raise HTTPException(404, "未找到采购需求或运行") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/quotes", status_code=201)
    async def import_quote(request_id: str, body: ImportQuoteBody) -> dict[str, Any]:
        try:
            data = body.decode()
            extracted = await asyncio.wait_for(
                asyncio.to_thread(parse_quote, body.filename, data),
                timeout=10,
            )
            return await asyncio.to_thread(
                service.import_quote,
                request_id,
                filename=body.filename,
                data=data,
                extracted=extracted,
            )
        except TimeoutError:
            raise HTTPException(408, "报价解析超过 10 秒，请检查文件后重试") from None
        except KeyError:
            raise HTTPException(404, "未找到采购需求") from None
        except (ProcurementError, QuoteParseError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/requests/{request_id}/quotes/{quote_id}/corrections")
    async def correct_quote(
        request_id: str,
        quote_id: str,
        body: CorrectQuoteFieldBody,
    ) -> dict[str, Any]:
        try:
            return service.correct_field(
                request_id,
                quote_id,
                field=body.field,
                value=body.value,
                actor=body.actor,
            )
        except KeyError:
            raise HTTPException(404, "未找到采购需求或报价") from None
        except ProcurementError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/analyze", status_code=202)
    async def analyze(request_id: str) -> dict[str, str]:
        try:
            return await agent.start_existing(request_id)
        except KeyError:
            raise HTTPException(404, "未找到采购需求") from None
        except (ProcurementError, RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/decision")
    async def approve(request_id: str, body: ApproveSupplierBody) -> dict[str, Any]:
        if not body.confirmed:
            raise HTTPException(409, "正式选定供应商必须人工确认")
        try:
            return await agent.approve(
                request_id,
                snapshot_id=body.snapshot_id,
                input_sha256=body.input_sha256,
                quote_id=body.quote_id,
                note=body.note,
                actor=body.actor,
            )
        except KeyError:
            raise HTTPException(404, "未找到采购需求、比价快照或报价") from None
        except (ProcurementError, RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/requests/{request_id}/report")
    async def audit_report(request_id: str) -> dict[str, Any]:
        try:
            return service.audit_report(request_id)
        except KeyError:
            raise HTTPException(404, "未找到采购需求") from None

    @router.get("/evaluation")
    async def evaluation() -> dict[str, Any]:
        return evaluate_frozen_cases()

    return router


__all__ = [
    "ApproveSupplierBody",
    "CreateProcurementRequestBody",
    "CorrectQuoteFieldBody",
    "ConversationAttachment",
    "ImportQuoteBody",
    "MAX_CONVERSATION_UPLOAD_BYTES",
    "ProcurementModelConfigBody",
    "ResumeProcurementConversationBody",
    "StartProcurementConversationBody",
    "procurement_router",
]
