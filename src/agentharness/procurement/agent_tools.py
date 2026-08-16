"""Procurement-only tools and the offline adapter used by the purchase workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agentharness.contracts import (
    EffectKind,
    Message,
    MessageRole,
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    ToolContext,
    ToolResult,
    Usage,
    new_id,
)
from agentharness.procurement.parsing import fields_requiring_review, parse_quote
from agentharness.procurement.requirements import (
    RequirementModelError,
    _validate_model_requirement,
    extract_requirement,
)
from agentharness.storage.sqlite import Storage
from agentharness.tools.base import FunctionTool

PROCUREMENT_TOOL_NAMES = [
    "procurement_capture_requirement",
    "procurement_parse_uploaded_quotes",
    "procurement_request_review",
    "procurement_request_comparison",
    "procurement_record_decision_evidence",
]

DEFAULT_QUOTE_FX_RATES = {"USD": "7.2"}

PROCUREMENT_AGENT_SYSTEM_PROMPT = """你是采购工作台中的受限采购 Agent。

你只能调用已提供的采购工具，绝不能直接给出采购决定、修改业务数据、访问网络、文件系统、浏览器或其他工具。每个阶段都必须调用要求的采购工具，不能用普通文本结束任务。

工作顺序：
1. 初始会话：先调用 procurement_capture_requirement，再调用 procurement_parse_uploaded_quotes，最后调用 procurement_request_review。
2. 新报价阶段：调用 procurement_parse_uploaded_quotes，再调用 procurement_request_review。
3. 复核恢复阶段：调用 procurement_request_review，让 Java 业务真源决定是否仍需人工输入。
4. 比价阶段：只调用 procurement_request_comparison。它只请求 Java 执行确定性比价，不能推荐或批准供应商。
5. 正式决定阶段：只调用 procurement_record_decision_evidence。只有它确认 Java 已保存正式决定后，Run 才能结束。

需求结构化时，procurement_capture_requirement 的 requirement 参数必须是完整、准确的 JSON 采购需求。不要编造报价、汇率、供应商或审批信息。"""

JsonFetcher = Callable[[str], Awaitable[dict[str, Any]]]
BytesFetcher = Callable[[str], Awaitable[bytes]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _json_content(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProcurementAgentTools:
    """Tool set with read-only access to the Java procurement control plane.

    The tools never write Java business state.  Their results are returned by
    the internal command handler, then Java validates and persists the change.
    """

    def __init__(
        self,
        storage: Storage,
        *,
        fetch_context: JsonFetcher,
        fetch_artifact: BytesFetcher,
    ) -> None:
        self.storage = storage
        self.fetch_context = fetch_context
        self.fetch_artifact = fetch_artifact
        self.tools = {
            "procurement_capture_requirement": FunctionTool(
                "procurement_capture_requirement",
                "Validate a structured procurement requirement from the current buyer message.",
                {
                    "type": "object",
                    "properties": {
                        "requirement": {
                            "type": "object",
                            "description": "The complete structured requirement. Omit only for the deterministic offline adapter.",
                        }
                    },
                    "additionalProperties": False,
                },
                EffectKind.pure,
                self.capture_requirement,
            ),
            "procurement_parse_uploaded_quotes": FunctionTool(
                "procurement_parse_uploaded_quotes",
                "Parse only Java-owned uploaded quote artifacts referenced by this procurement Run.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                EffectKind.pure,
                self.parse_uploaded_quotes,
            ),
            "procurement_request_review": FunctionTool(
                "procurement_request_review",
                "Pause this procurement Run for persisted requirement or quote review in the Java control plane.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                EffectKind.pure,
                self.request_review,
            ),
            "procurement_request_comparison": FunctionTool(
                "procurement_request_comparison",
                "Request deterministic comparison from Java after every review gate is satisfied.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                EffectKind.pure,
                self.request_comparison,
            ),
            "procurement_record_decision_evidence": FunctionTool(
                "procurement_record_decision_evidence",
                "Record evidence of a formal Java procurement decision and finish the Run.",
                {"type": "object", "properties": {}, "additionalProperties": False},
                EffectKind.pure,
                self.record_decision_evidence,
            ),
        }

    def _run_metadata(self, run_id: str) -> dict[str, Any]:
        row = self.storage.get_run(run_id)
        if row is None:
            raise ValueError("procurement run was not found")
        try:
            value = json.loads(str(row.get("metadata_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("procurement run metadata is invalid") from exc
        if not isinstance(value, dict):
            raise ValueError("procurement run metadata is invalid")
        return value

    @staticmethod
    def _task_id(metadata: dict[str, Any]) -> str:
        task_id = str(metadata.get("purchase_request_id") or "")
        if len(task_id) != 32:
            raise ValueError("procurement run is missing its Java task binding")
        return task_id

    async def capture_requirement(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult:
        metadata = self._run_metadata(ctx.run_id)
        proposed = arguments.get("requirement")
        if proposed is None:
            message = str(metadata.get("procurement_source_message") or "").strip()
            if not message:
                raise ValueError("procurement source message is missing")
            requirement = extract_requirement([Message(role=MessageRole.user, content=message)])
            source = "deterministic_offline_adapter"
        else:
            try:
                requirement = _validate_model_requirement(proposed)
                source = "model_tool_call"
            except RequirementModelError as exc:
                message = str(metadata.get("procurement_source_message") or "").strip()
                if not message:
                    raise
                requirement = extract_requirement(
                    [Message(role=MessageRole.user, content=message)]
                )
                source = "deterministic_validation_fallback"
                metadata["procurement_model_requirement_error"] = str(exc)
        self.storage.merge_run_metadata(
            ctx.run_id,
            {
                "procurement_requirement": requirement,
                "procurement_requirement_source": source,
                **(
                    {
                        "procurement_model_requirement_error": metadata[
                            "procurement_model_requirement_error"
                        ]
                    }
                    if "procurement_model_requirement_error" in metadata
                    else {}
                ),
            },
        )
        return ToolResult(
            tool_call_id="",
            name="procurement_capture_requirement",
            content=_json_content({"requirement": requirement, "source": source}),
        )

    async def parse_uploaded_quotes(
        self, ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        metadata = self._run_metadata(ctx.run_id)
        raw_attachments = metadata.get("procurement_pending_attachments") or []
        if not isinstance(raw_attachments, list):
            raise ValueError("procurement pending attachments are invalid")
        quotes = [await self._parse_attachment(dict(raw)) for raw in raw_attachments]
        requirement = metadata.get("procurement_requirement")
        prefilled_rates: list[str] = []
        if isinstance(requirement, dict):
            prefilled_rates = self._prefill_quote_fx_rates(requirement, quotes)
        self.storage.merge_run_metadata(
            ctx.run_id,
            {
                "procurement_pending_attachments": [],
                "procurement_last_parsed_artifact_ids": [item["artifact_id"] for item in quotes],
                **(
                    {"procurement_requirement": requirement}
                    if isinstance(requirement, dict)
                    else {}
                ),
            },
        )
        return ToolResult(
            tool_call_id="",
            name="procurement_parse_uploaded_quotes",
            content=_json_content(
                {
                    "quotes": quotes,
                    "requirement": requirement if isinstance(requirement, dict) else None,
                    "prefilled_fx_rates": prefilled_rates,
                }
            ),
        )

    @staticmethod
    def _prefill_quote_fx_rates(
        requirement: dict[str, Any], quotes: list[dict[str, Any]]
    ) -> list[str]:
        constraints = requirement.get("constraints")
        if not isinstance(constraints, dict):
            raise ValueError("procurement requirement is missing constraints")
        rates = constraints.get("fx_rates")
        if not isinstance(rates, dict):
            rates = {}
        normalized = {
            str(currency).upper(): str(rate)
            for currency, rate in rates.items()
            if str(currency).strip()
        }
        normalized.setdefault("CNY", "1")
        prefilled: list[str] = []
        for quote in quotes:
            fields = (quote.get("extracted") or {}).get("fields") or {}
            currency = str((fields.get("currency") or {}).get("value") or "").upper()
            rate = DEFAULT_QUOTE_FX_RATES.get(currency)
            if rate is not None and currency not in normalized:
                normalized[currency] = rate
                prefilled.append(f"{currency}/CNY={rate}")
        constraints["fx_rates"] = normalized
        requirement["constraints"] = constraints
        return prefilled

    async def _parse_attachment(self, attachment: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(attachment.get("artifact_id") or "")
        filename = str(attachment.get("filename") or "")
        if not artifact_id.startswith("jb") or not filename:
            raise ValueError("invalid Java business artifact reference")
        content = await self.fetch_artifact(f"/internal/v1/artifacts/{artifact_id}/raw")
        expected_sha = str(attachment.get("sha256") or "")
        if expected_sha and hashlib.sha256(content).hexdigest() != expected_sha:
            raise ValueError("business artifact SHA-256 mismatch")
        extracted = await asyncio.to_thread(parse_quote, filename, content)
        review_fields = fields_requiring_review(extracted)
        extracted = {**extracted, "review_fields": review_fields}
        supplier = str(
            ((extracted.get("fields") or {}).get("supplier_name") or {}).get("value")
            or filename.rsplit(".", 1)[0]
        )
        return {
            "artifact_id": artifact_id,
            "supplier_name": supplier,
            "status": "needs_review" if review_fields else "ready",
            "parser_version": str(extracted.get("parser_version") or "packaging-quote-v3"),
            "processing_ms": str(extracted.get("processing_ms") or "0"),
            "extracted": extracted,
        }

    async def request_review(
        self, ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        metadata = self._run_metadata(ctx.run_id)
        stage = str(metadata.get("procurement_stage") or "capture")
        if stage in {"capture", "import_quote", "bind"}:
            reason = "请在采购系统中保存人工确认的需求，并逐项复核所有待确认报价字段。"
            detail: dict[str, Any] = {"requirement_confirmed": False, "unresolved_field_count": None}
        else:
            context = await self.fetch_context(
                f"/internal/v1/tasks/{self._task_id(metadata)}/context"
            )
            confirmed = bool(context.get("requirement_confirmed"))
            unresolved = int(context.get("unresolved_field_count") or 0)
            detail = {
                "requirement_confirmed": confirmed,
                "unresolved_field_count": unresolved,
            }
            if not confirmed:
                reason = "采购需求尚未由采购员保存确认。"
            elif unresolved:
                reason = f"仍有 {unresolved} 个报价字段需要人工复核。"
            else:
                reason = "采购输入已复核。请在采购系统中发起确定性比价。"
        self.storage.merge_run_metadata(ctx.run_id, {"procurement_stage": "review"})
        return ToolResult(
            tool_call_id="",
            name="procurement_request_review",
            content=_json_content({"review": detail}),
            pause_status="require_human",
            pause_reason=reason,
        )

    async def request_comparison(
        self, ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        metadata = self._run_metadata(ctx.run_id)
        context = await self.fetch_context(
            f"/internal/v1/tasks/{self._task_id(metadata)}/context"
        )
        if str(context.get("analysis_run_id") or "") != ctx.run_id:
            raise ValueError("Java task is not bound to this procurement Run")
        if not bool(context.get("requirement_confirmed")):
            return ToolResult(
                tool_call_id="",
                name="procurement_request_comparison",
                content=_json_content({"comparison": "blocked", "reason": "requirement_review_required"}),
                pause_status="require_human",
                pause_reason="采购需求尚未由采购员保存确认。",
            )
        unresolved = int(context.get("unresolved_field_count") or 0)
        if unresolved:
            return ToolResult(
                tool_call_id="",
                name="procurement_request_comparison",
                content=_json_content({"comparison": "blocked", "unresolved_field_count": unresolved}),
                pause_status="require_human",
                pause_reason=f"仍有 {unresolved} 个报价字段需要人工复核。",
            )
        self.storage.merge_run_metadata(ctx.run_id, {"procurement_stage": "approval"})
        return ToolResult(
            tool_call_id="",
            name="procurement_request_comparison",
            content=_json_content({"comparison_requested": True, "run_id": ctx.run_id}),
            pause_status="waiting_approval",
            pause_reason="Java 正在执行确定性比价，等待采购员作出正式决定。",
        )

    async def record_decision_evidence(
        self, ctx: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        metadata = self._run_metadata(ctx.run_id)
        binding = metadata.get("procurement_pending_decision")
        if not isinstance(binding, dict):
            raise ValueError("formal Java decision evidence is missing")
        context = await self.fetch_context(
            f"/internal/v1/tasks/{self._task_id(metadata)}/context"
        )
        expected = next(
            (
                dict(item)
                for item in context.get("pending_decisions") or []
                if isinstance(item, dict)
                and item.get("pending_decision_id") == binding.get("pending_decision_id")
            ),
            None,
        )
        keys = (
            "pending_decision_id",
            "run_id",
            "tool_name",
            "task_version",
            "snapshot_id",
            "input_sha256",
            "business_decision",
            "quote_id",
            "note_hash",
        )
        if expected is None or {key: expected.get(key) for key in keys} != {
            key: binding.get(key) for key in keys
        }:
            raise ValueError("formal decision no longer matches Java authoritative state")
        if str(expected.get("status") or "") != "pending":
            raise ValueError("formal decision is no longer pending")
        arguments_sha256 = hashlib.sha256(_canonical_bytes({key: binding.get(key) for key in keys})).hexdigest()
        evidence = {
            "id": hashlib.sha256(
                _canonical_bytes({"binding": binding, "tool_call_id": ctx.metadata.get("tool_call_id")})
            ).hexdigest()[:32],
            **{key: binding.get(key) for key in keys},
            "arguments_sha256": arguments_sha256,
            "decision": "formal_java_confirmation",
            "confirmation_source": "java_control_plane",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.storage.merge_run_metadata(
            ctx.run_id,
            {"procurement_stage": "terminal", "procurement_decision_evidence": evidence},
        )
        return ToolResult(
            tool_call_id="",
            name="procurement_record_decision_evidence",
            content=_json_content({"approval": evidence}),
            final_output="已记录 Java 采购控制面中的正式决定证据。",
        )


class DeterministicProcurementAdapter:
    """Offline adapter that follows the same tool loop as a real model.

    It exists only for the configured ``procurement_fake`` mode.  It does not
    bypass RunEngine or invoke business code directly.
    """

    name = "procurement_internal"

    async def stream(self, request: ModelRequest):
        stage = str(request.metadata.get("procurement_stage") or "capture")
        available = {tool.name for tool in request.tools}
        current_tools = self._current_turn_tool_names(request)
        tool_name = self._next_tool(stage, current_tools)
        if tool_name not in available:
            yield ModelStreamItem(
                type=StreamItemType.error,
                error=f"required procurement tool is unavailable: {tool_name}",
                error_kind="provider",
            )
            return
        tool_call_id = new_id()
        yield ModelStreamItem(
            type=StreamItemType.tool_call_start,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        yield ModelStreamItem(
            type=StreamItemType.tool_call_end,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments={},
        )
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=0, output_tokens=0, total_tokens=0),
        )
        yield ModelStreamItem(type=StreamItemType.done)

    @staticmethod
    def _current_turn_tool_names(request: ModelRequest) -> set[str]:
        latest_user = max(
            (
                index
                for index, message in enumerate(request.messages)
                if message.role == MessageRole.user
            ),
            default=-1,
        )
        return {
            str(message.name)
            for message in request.messages[latest_user + 1 :]
            if message.role == MessageRole.tool and message.name
        }

    @staticmethod
    def _next_tool(stage: str, current_tools: set[str]) -> str:
        if stage == "capture":
            if "procurement_capture_requirement" not in current_tools:
                return "procurement_capture_requirement"
            if "procurement_parse_uploaded_quotes" not in current_tools:
                return "procurement_parse_uploaded_quotes"
            return "procurement_request_review"
        if stage == "import_quote":
            return (
                "procurement_parse_uploaded_quotes"
                if "procurement_parse_uploaded_quotes" not in current_tools
                else "procurement_request_review"
            )
        if stage == "comparison":
            return "procurement_request_comparison"
        if stage == "decision":
            return "procurement_record_decision_evidence"
        return "procurement_request_review"


__all__ = [
    "DeterministicProcurementAdapter",
    "PROCUREMENT_AGENT_SYSTEM_PROMPT",
    "PROCUREMENT_TOOL_NAMES",
    "ProcurementAgentTools",
]
