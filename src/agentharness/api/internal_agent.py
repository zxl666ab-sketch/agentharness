"""Token-protected command surface used by the Java procurement control plane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agentharness.api.reporting import build_run_report
from agentharness.config import resolve_internal_token
from agentharness.contracts import (
    BudgetConfig,
    MessageRole,
    PricingConfig,
    RunRequest,
    RunStatus,
    ToolResult,
)
from agentharness.harness import Harness
from agentharness.procurement.agent_tools import (
    PROCUREMENT_AGENT_SYSTEM_PROMPT,
    PROCUREMENT_TOOL_NAMES,
    DeterministicProcurementAdapter,
    ProcurementAgentTools,
)
from agentharness.procurement.contract_drafting import build_contract_draft
from agentharness.procurement.evaluation import evaluate_frozen_cases
from agentharness.procurement.invoice_parsing import build_diff_explanation, parse_invoice
from agentharness.procurement.semantic_cache import SemanticCache
from agentharness.providers.gateway import GatewayAdapter
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_object(value: Any) -> dict[str, Any]:
    """Decode a persisted `*_json` column into a dict (never raises)."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("采购模型价格配置必须是非负数字") from exc
    if result < 0:
        raise ValueError("采购模型价格配置必须是非负数字")
    return result


def _render_interaction_answer(answer: Any, note: str = "") -> str:
    labels = {
        "quantity": "采购数量",
        "unit": "采购单位",
        "size": "尺寸",
        "thickness": "厚度",
        "max_lead_days": "最长交期",
        "invoice_required": "是否开票",
        "clarification": "补充说明",
    }
    if isinstance(answer, dict):
        lines: list[str] = []
        quantity = answer.get("quantity")
        unit = str(answer.get("unit") or "").strip()
        if quantity is not None and str(quantity).strip():
            lines.append(f"采购数量：{quantity}{f' {unit}' if unit else ''}")
        for key, value in answer.items():
            if key in {"quantity", "unit"}:
                continue
            if key == "max_lead_days":
                lead_days = str(value).strip()
                if lead_days and not re.search(r"(?:天|日)$", lead_days):
                    lead_days += " 天"
                lines.append(f"最长交期：{lead_days}")
                continue
            lines.append(f"{labels.get(str(key), str(key))}：{value}")
        size = str(answer.get("size") or "").strip()
        size_match = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?",
            size,
            re.I,
        )
        if size_match:
            lines.extend(
                (
                    f"宽度：{size_match.group(1)} mm",
                    f"长度：{size_match.group(2)} mm",
                )
            )
    elif isinstance(answer, list):
        lines = ["选择：" + "、".join(str(item) for item in answer)]
    else:
        lines = [str(answer)]
    if note:
        lines.append("补充说明：" + note)
    return "\n".join(lines)


def _merge_requirement_answer(source: str, answer: str) -> str:
    merged = source.strip()
    if not re.search(r"(?:^|\n)\s*物料\s*[:：]", merged):
        first = re.split(r"[，,；;。.!！?？\n]", merged, maxsplit=1)[0]
        item = re.sub(r"^(?:请|麻烦)?\s*(?:帮我|为我)?\s*(?:采购|购买|需要)\s*(?:一批|一些)?\s*", "", first).strip()
        if item and not re.fullmatch(r"[\d,.]+(?:个|件|套|箱|卷|张)?", item):
            merged += f"\n物料：{item}"
    return f"{merged}\n{answer.strip()}".strip()


class AgentCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    operation_type: Literal[
        "start_conversation",
        "import_quote",
        "resume_run",
        "human_interaction_answer",
        "analyze",
        "approve_decision",
        "create_structured",
        "reopen_task",
        "parse_invoice",
        "explain_invoice_diff",
        "draft_contract",
    ]
    aggregate_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    expected_task_version: int = Field(ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]


class InternalAgentCommands:
    # Structured procurement stages have a deterministic tool graph.  In
    # hybrid mode the AgentHarness Run and every tool/audit event are still
    # preserved, but the graph is planned by the in-process Agent adapter
    # instead of paying for a remote model round-trip at every edge.
    _INTERNAL_PLANNER_STAGES = frozenset({
        "capture",
        "import_quote",
        "comparison",
        "decision",
        "bind",
    })

    def __init__(
        self,
        harness: Harness,
        *,
        fetch_context: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        fetch_artifact: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> None:
        self.harness = harness
        self.java_base_url = os.environ.get(
            "PROCUREMENT_INTERNAL_BASE_URL", "http://127.0.0.1:8741"
        ).rstrip("/")
        self.token = resolve_internal_token()
        self._fetch_context = fetch_context or self._http_json
        self._fetch_artifact = fetch_artifact or self._http_bytes
        # P2-3 语义缓存：Redis 可用则启用（AGENT_REDIS_URL），否则 no-op
        self.semantic_cache = SemanticCache.from_env()
        self.procurement_tools = ProcurementAgentTools(
            harness.storage,
            fetch_context=lambda path: self._java_json(path),
            fetch_artifact=lambda path: self._java_bytes(path),
            semantic_cache=self.semantic_cache,
        )
        for tool in self.procurement_tools.tools.values():
            harness.register_tool(tool)
        if "procurement_internal" not in harness.providers:
            harness.register_provider("procurement_internal", DeterministicProcurementAdapter())

    async def execute(self, body: AgentCommandBody) -> dict[str, Any]:
        if _canonical_sha256(body.payload) != body.payload_sha256:
            raise HTTPException(409, "payload SHA-256 does not match canonical payload")
        operations = self.harness.storage.internal_operations
        try:
            existing = operations.accept(
                operation_id=body.operation_id,
                payload_sha256=body.payload_sha256,
                operation_type=body.operation_type,
                aggregate_id=body.aggregate_id,
            )
        except ValueError as exc:
            raise HTTPException(409, "operation id was used with a different payload") from exc
        if existing["status"] == "completed":
            return self._envelope(body.operation_id, "completed", existing["result"])
        if existing["status"] == "failed":
            if body.operation_type != "human_interaction_answer" or not operations.reopen_failed(
                body.operation_id, body.payload_sha256
            ):
                return self._envelope(
                    body.operation_id, "failed", None, str(existing.get("error") or "failed")
                )
        # P-M4: claim by compare-and-set, so two deliveries of one operation can
        # never run their side effects twice (the old check only short-circuited
        # terminal states and let in-flight replays through).
        if operations.claim(body.operation_id) is None:
            current = operations.get(body.operation_id) or {}
            status = str(current.get("status") or "")
            if status == "completed":
                return self._envelope(body.operation_id, "completed", current.get("result"))
            if status == "failed":
                return self._envelope(
                    body.operation_id, "failed", None, str(current.get("error") or "failed")
                )
            return self._envelope(
                body.operation_id,
                "failed",
                None,
                "duplicate_command: this operation is already being executed",
            )
        try:
            result = await self._dispatch(body)
        except Exception as exc:  # noqa: BLE001 - failure must be durable for idempotency
            # 异常文本可能带凭据：落库/回传前统一过 redactor（P-M9）。
            error = self.harness.redactor.redact_text(str(exc)) or exc.__class__.__name__
            operations.fail(body.operation_id, error)
            return self._envelope(body.operation_id, "failed", None, error)
        # 统一回写点：把本次 run 绑定到 operation（幂等重放走索引直达）
        dispatch_run_id = (
            str(result.get("run_id") or "") if isinstance(result, dict) else ""
        )
        if dispatch_run_id:
            operations.set_run_id(body.operation_id, dispatch_run_id)
        operations.complete(body.operation_id, result)
        return self._envelope(body.operation_id, "completed", result)

    async def _dispatch(self, body: AgentCommandBody) -> dict[str, Any]:
        if body.operation_type == "start_conversation":
            return await self._start_conversation(body)
        if body.operation_type == "import_quote":
            return await self._import_quote(body)
        if body.operation_type == "analyze":
            return await self._analyze(body)
        if body.operation_type == "approve_decision":
            return await self._approve(body)
        if body.operation_type == "resume_run":
            return await self._resume(body)
        if body.operation_type == "human_interaction_answer":
            return await self._answer_interaction(body)
        if body.operation_type in {"create_structured", "reopen_task"}:
            return await self._bind_new_task(body)
        if body.operation_type == "parse_invoice":
            return await self._parse_invoice(body)
        if body.operation_type == "explain_invoice_diff":
            return await self._explain_invoice_diff(body)
        if body.operation_type == "draft_contract":
            return self._draft_contract(body)
        raise ValueError(f"unsupported operation type: {body.operation_type}")

    def _draft_contract(self, body: AgentCommandBody) -> dict[str, Any]:
        """P3-2 合同草拟（模式 B 模板 + 条款库软提示）：金额/交期/供应商只来自注入的定标结果。"""
        return build_contract_draft(body.payload)

    async def _parse_invoice(self, body: AgentCommandBody) -> dict[str, Any]:
        """P3-1：确定性发票字段抽取（Java 侧三单匹配只消费 invoice 键）。"""
        artifact_id = str(body.payload.get("artifact_id") or "")
        filename = str(body.payload.get("filename") or "")
        expected_sha = str(body.payload.get("sha256") or "")
        if not artifact_id.startswith("jb") or not filename:
            raise ValueError("invalid Java invoice artifact reference")
        content = await self._java_bytes(f"/internal/v1/artifacts/{artifact_id}/raw")
        if expected_sha and hashlib.sha256(content).hexdigest() != expected_sha:
            raise ValueError("invoice artifact SHA-256 mismatch")
        parsed = await asyncio.to_thread(parse_invoice, filename, content)
        invoice = parsed.get("invoice") or {}
        if not str(invoice.get("invoice_no") or "").strip() or invoice.get("total_amount") is None:
            raise ValueError("invoice parse missing required fields (invoice_no / total_amount)")
        # parser_version 位于 invoice 对象内层（与 Java applyParseResult 的读取位置一致）
        return {"invoice": invoice, "processing_ms": parsed.get("processing_ms")}

    async def _explain_invoice_diff(self, body: AgentCommandBody) -> dict[str, Any]:
        """P3-1 模式 C：Java 结构化差异 → 自然语言原因与处理建议（数值只来自注入的 diffs，
        满足「解释中每个数字必须存在于结构化差异」的评测硬校验）。"""
        diffs = body.payload.get("diffs")
        if not isinstance(diffs, list) or not diffs:
            raise ValueError("explain_invoice_diff requires structured diffs")
        return {"explanation": build_diff_explanation(diffs)}

    async def _start_conversation(self, body: AgentCommandBody) -> dict[str, Any]:
        message_text = str(body.payload.get("message") or "").strip()
        if not message_text:
            raise ValueError("conversation message is blank")
        existing_run = self._run_for_operation(body.operation_id)
        if existing_run is None:
            request = self._new_procurement_request(
                body,
                message=message_text,
                stage="capture",
                pending_attachments=list(body.payload.get("attachments") or []),
                source_message=message_text,
            )
            result = await self.harness.run(request)
            if self._should_fallback_initial_capture(result):
                fallback_request = request.model_copy(
                    update={
                        "session_id": result.session_id,
                        "provider": "procurement_internal",
                        "model": "deterministic-procurement",
                        "reasoning_effort": None,
                        "budget": request.budget.model_copy(
                            update={"max_cost_usd": None}
                        ),
                        "pricing": PricingConfig(),
                        "metadata": {
                            **request.metadata,
                            "procurement_fallback_from_run_id": result.run_id,
                            "procurement_fallback_reason": result.error,
                        },
                    }
                )
                result = await self.harness.run(fallback_request)
        else:
            result = self._run_result(existing_run)
        self._require_paused(result, RunStatus.require_human, "初始采购资料复核")
        requirement_payload = self._latest_tool_payload(
            result.run_id, "procurement_capture_requirement"
        )
        interaction = requirement_payload.get("interaction")
        if isinstance(interaction, dict):
            return {
                "session_id": result.session_id,
                "run_id": result.run_id,
                "status": result.status.value,
                "interaction": interaction,
            }
        quote_payload = self._latest_tool_payload(
            result.run_id, "procurement_parse_uploaded_quotes"
        )
        requirement = quote_payload.get("requirement") or requirement_payload.get("requirement")
        if not isinstance(requirement, dict):
            raise ValueError("采购 Agent 未返回可验证的结构化需求")
        quotes = quote_payload.get("quotes")
        if not isinstance(quotes, list):
            raise ValueError("采购 Agent 未返回报价解析结果")
        return {
            "session_id": result.session_id,
            "run_id": result.run_id,
            "requirement": requirement,
            "quotes": quotes,
        }

    async def _answer_interaction(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        interaction_id = str(body.payload.get("interaction_id") or "")
        interaction = next(
            (
                dict(item)
                for item in context.get("interactions") or []
                if isinstance(item, dict) and item.get("interaction_id") == interaction_id
            ),
            None,
        )
        if interaction is None:
            raise ValueError("Java human interaction does not exist")
        if interaction.get("status") != "ANSWERED":
            raise ValueError("human interaction is not ready to resume")
        if int(interaction.get("generation") or -1) != body.generation:
            raise ValueError("human interaction generation is stale")
        if int(context.get("generation") or -1) != body.generation:
            raise ValueError("Java task generation is stale")
        if int(context.get("task_version") or -1) != body.expected_task_version:
            raise ValueError("Java task version is stale")
        for key in ("run_id", "checkpoint_id"):
            expected = str(interaction.get(key) or "")
            supplied = str(body.payload.get(key) or "")
            if expected != supplied:
                raise ValueError(f"human interaction {key} binding is invalid")
        answer = interaction.get("answer")
        if answer != body.payload.get("answer"):
            raise ValueError("human interaction answer does not match Java state")
        note = str(interaction.get("note") or "").strip()
        answer_text = _render_interaction_answer(answer, note)
        selected_artifact_ids = {str(item) for item in interaction.get("artifact_ids") or []}
        authorized_artifacts = [
            dict(item)
            for item in context.get("authorized_artifacts") or []
            if isinstance(item, dict)
        ]
        supplemental_attachments = [
            item
            for item in authorized_artifacts
            if str(item.get("artifact_id") or "") in selected_artifact_ids
        ]
        if len(supplemental_attachments) != len(selected_artifact_ids):
            raise ValueError("human interaction contains an unauthorized artifact")
        source_message = str(context.get("source_message") or "").strip()
        combined_source = _merge_requirement_answer(source_message, answer_text)
        run_id = str(interaction.get("run_id") or context.get("analysis_run_id") or "")
        run = self.harness.storage.get_run(run_id) if run_id else None
        checkpoint = self.harness.storage.load_checkpoint(run_id) if run is not None else None
        rebuilt = run is None or checkpoint is None
        if rebuilt:
            existing = self._run_for_operation(body.operation_id)
            if existing is None:
                result = await self.harness.run(
                    self._new_procurement_request(
                        body,
                        message=combined_source,
                        stage="capture",
                        pending_attachments=[
                            dict(item)
                            for item in context.get("attachments") or []
                            if isinstance(item, dict)
                        ] + supplemental_attachments,
                        source_message=combined_source,
                    )
                )
            else:
                result = self._run_result(existing)
            run_id = result.run_id
        else:
            self._ensure_run_provider(run_id)
            resume_input = (
                "采购员已回答当前问题。以下回答来自 Java 持久化交互，并已通过 Schema 校验：\n"
                f"{answer_text}\n继续当前采购资料解析；仍缺关键字段时再次结构化提问。"
            )
            persisted_messages = self.harness.storage.get_messages(run_id)
            answer_message = next(
                (
                    message
                    for message in reversed(persisted_messages)
                    if message.role == MessageRole.user
                    and message.content == resume_input
                ),
                None,
            )
            answer_message_exists = answer_message is not None
            answer_in_checkpoint = answer_message is not None and any(
                message.id == answer_message.id for message in checkpoint.messages
            )
            self.harness.storage.merge_run_metadata(
                run_id,
                {
                    "procurement_stage": "capture",
                    "procurement_source_message": combined_source,
                    "procurement_interaction_id": interaction_id,
                    "procurement_interaction_operation_id": body.operation_id,
                    **(
                        {"procurement_pending_attachments": supplemental_attachments}
                        if supplemental_attachments
                        else {}
                    ),
                },
            )
            if answer_message_exists and not answer_in_checkpoint:
                self.harness.storage.save_checkpoint(
                    checkpoint.model_copy(
                        update={
                            "messages": [*checkpoint.messages, answer_message],
                        }
                    )
                )
            if answer_in_checkpoint and RunStatus(str(run["status"])) == RunStatus.require_human:
                result = self._run_result(run)
            else:
                result = await self.harness.resume(
                    run_id, input=None if answer_message_exists else resume_input
                )
        self._require_paused(result, RunStatus.require_human, "人工回答恢复")
        requirement_payload = self._latest_tool_payload(
            result.run_id, "procurement_capture_requirement"
        )
        next_interaction = requirement_payload.get("interaction")
        response: dict[str, Any] = {
            "interaction_id": interaction_id,
            "session_id": result.session_id,
            "run_id": result.run_id,
            "status": result.status.value,
            "rebuilt": rebuilt,
        }
        if isinstance(next_interaction, dict):
            response["interaction"] = next_interaction
            return response
        quote_payload = self._latest_tool_payload(
            result.run_id, "procurement_parse_uploaded_quotes"
        )
        requirement = quote_payload.get("requirement") or requirement_payload.get("requirement")
        quotes = quote_payload.get("quotes")
        if not isinstance(requirement, dict) or not isinstance(quotes, list):
            raise ValueError("resumed procurement run did not return structured inputs")
        response["requirement"] = requirement
        response["quotes"] = quotes
        return response

    @staticmethod
    def _should_fallback_initial_capture(result: Any) -> bool:
        if result.status != RunStatus.failed:
            return False
        error = str(result.error or "").casefold()
        return any(
            marker in error
            for marker in (
                "arguments are invalid json",
                "max_tool_calls_per_turn exceeded",
                "provider ended before tool",
                "tool call completed without a name",
                "tool call id changed during streaming",
            )
        )

    async def _import_quote(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        run_id = str(context.get("analysis_run_id") or "")
        if not run_id or self.harness.storage.get_run(run_id) is None:
            raise ValueError("Java task is not bound to an Agent run")
        raw_attachments = body.payload.get("attachments")
        if raw_attachments is None:
            # Backward-compatible single-file command payload.
            attachments = [dict(body.payload)]
        elif isinstance(raw_attachments, list):
            attachments = [dict(item) for item in raw_attachments if isinstance(item, dict)]
        else:
            raise ValueError("quote import attachments must be a list")
        if not attachments or any(
            not str(item.get("artifact_id") or "").strip()
            or not str(item.get("filename") or "").strip()
            for item in attachments
        ):
            raise ValueError("quote import attachment is invalid")
        self._ensure_run_provider(run_id)
        self.harness.storage.merge_run_metadata(
            run_id,
            {
                "procurement_stage": "import_quote",
                # One command owns the entire selected batch.  The parser then
                # applies its bounded gather, preserving the attachment order.
                "procurement_pending_attachments": attachments,
                "procurement_operation_id": body.operation_id,
            },
        )
        result = await self.harness.resume(
            run_id,
            input="阶段：新增报价解析。仅按采购工具顺序解析新报价并请求人工复核。",
        )
        self._require_paused(result, RunStatus.require_human, "报价解析复核")
        payload = self._latest_tool_payload(run_id, "procurement_parse_uploaded_quotes")
        quotes = payload.get("quotes")
        expected_artifact_ids = [str(item["artifact_id"]) for item in attachments]
        if (
            not isinstance(quotes, list)
            or len(quotes) != len(expected_artifact_ids)
            or any(not isinstance(quote, dict) for quote in quotes)
            or [str(quote.get("artifact_id") or "") for quote in quotes] != expected_artifact_ids
        ):
            raise ValueError("采购 Agent 未返回新增报价的解析结果")
        response: dict[str, Any] = {
            "quotes": quotes,
            "run_id": run_id,
            "status": result.status.value,
        }
        # Keep the existing single-file result contract available to callers
        # that have not switched to the batch endpoint yet.
        if len(quotes) == 1:
            response["quote"] = quotes[0]
        return response

    async def _analyze(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        run_id = str(context.get("analysis_run_id") or "")
        if not run_id or self.harness.storage.get_run(run_id) is None:
            raise ValueError("Java task is not bound to an Agent run")
        self._ensure_run_provider(run_id)
        self.harness.storage.merge_run_metadata(
            run_id,
            {
                "procurement_stage": "comparison",
                "procurement_analysis_operation_id": body.operation_id,
            },
        )
        result = await self.harness.resume(
            run_id,
            input="阶段：确定性比价。只调用 procurement_request_comparison。",
        )
        self._require_paused(result, RunStatus.waiting_approval, "确定性比价")
        return {"run_id": run_id, "status": result.status.value}

    async def _approve(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        expected = next(
            (
                dict(item)
                for item in context.get("pending_decisions") or []
                if isinstance(item, dict) and item.get("operation_id") == body.operation_id
            ),
            None,
        )
        if expected is None:
            raise ValueError("Java pending decision does not exist")
        if expected.get("status") == "stale":
            raise ValueError("stale_approval: approval evidence is stale")
        binding_keys = (
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
        binding = {key: body.payload.get(key) for key in binding_keys}
        if binding != {key: expected.get(key) for key in binding_keys}:
            raise ValueError("approval binding does not match Java authoritative state")
        if int(context.get("task_version", -1)) != body.expected_task_version:
            raise ValueError("approval task version is stale")
        run_id = str(binding["run_id"])
        if self.harness.storage.get_run(run_id) is None:
            raise ValueError("approval run was not found")
        self._ensure_run_provider(run_id)
        self.harness.storage.merge_run_metadata(
            run_id,
            {
                "procurement_stage": "decision",
                "procurement_pending_decision": binding,
                "procurement_decision_operation_id": body.operation_id,
            },
        )
        result = await self.harness.resume(
            run_id,
            input="阶段：正式采购决定。只调用 procurement_record_decision_evidence。",
        )
        if result.status != RunStatus.completed:
            raise ValueError("采购 Agent 未在正式决定证据后完成 Run")
        evidence_payload = self._latest_tool_payload(
            run_id, "procurement_record_decision_evidence"
        )
        approval = evidence_payload.get("approval")
        if not isinstance(approval, dict):
            raise ValueError("采购 Agent 未返回正式决定证据")
        approval_id = str(approval.get("id") or "")
        if len(approval_id) != 32:
            raise ValueError("采购 Agent 返回的正式决定审批 ID 无效")
        arguments_sha256 = _canonical_sha256(binding)
        if approval.get("arguments_sha256") != arguments_sha256:
            raise ValueError("采购 Agent 返回的正式决定证据哈希无效")
        resolved_at = str(approval.get("created_at") or "")
        if not resolved_at:
            raise ValueError("采购 Agent 返回的正式决定审批时间无效")
        self.harness.storage.save_approval(
            {
                "id": approval_id,
                "run_id": run_id,
                "tool_call_id": str(binding["pending_decision_id"]),
                "tool_name": str(binding["tool_name"]),
                "effect": "external_write",
                "arguments_summary": "Java 控制面已确认正式采购决定",
                "requires_confirmation": True,
                "decision": "allow_once",
                "created_at": resolved_at,
                "resolved_at": resolved_at,
                "invocation_id": body.operation_id,
                "tool_version": "1",
                "arguments_sha256": arguments_sha256,
                "approval_scope": (
                    f"procurement:{body.aggregate_id}:"
                    f"{binding['snapshot_id']}:{binding['quote_id']}"
                ),
                "status": "resolved",
            }
        )
        # P-M6: build_run_report pulls the whole event log and per-artifact rows
        # (seconds on a long run) — keep it off the event loop, same as
        # `api/server.py` does for the report endpoint.
        report = await asyncio.to_thread(build_run_report, self.harness, run_id) or {}
        return {
            "run_id": run_id,
            "approval": approval,
            "runtime_report": report,
            "runtime_evidence_sha256": str(
                report.get("evidence_sha256") or _canonical_sha256(report)
            ),
        }

    async def _resume(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        run_id = str(context.get("analysis_run_id") or "")
        if not run_id or self.harness.storage.get_run(run_id) is None:
            raise ValueError("run cannot be resumed")
        self._ensure_run_provider(run_id)
        message = str(body.payload.get("message") or "").strip()
        self.harness.storage.merge_run_metadata(
            run_id,
            {
                "procurement_stage": "review",
                "procurement_resume_operation_id": body.operation_id,
            },
        )
        result = await self.harness.resume(
            run_id,
            input=(
                "阶段：复核恢复。采购员补充的信息如下：\n"
                f"{message}\n"
                "请先用一两句中文直接回应采购员（确认收到、说明当前卡点或下一步），"
                "回复面向采购员、不要提及任何工具名称，然后仅调用 procurement_request_review。"
            ),
        )
        self._require_paused(result, RunStatus.require_human, "采购资料复核")
        return {"run_id": run_id, "status": result.status.value}

    async def _bind_new_task(self, body: AgentCommandBody) -> dict[str, Any]:
        existing_run = self._run_for_operation(body.operation_id)
        if existing_run is not None:
            return {
                "session_id": str(existing_run["session_id"]),
                "run_id": str(existing_run["id"]),
                "status": str(existing_run["status"]),
            }
        result = await self.harness.run(
            self._new_procurement_request(
                body,
                message="阶段：采购任务绑定。只调用 procurement_request_review。",
                stage="bind",
                pending_attachments=[],
                source_message="",
            )
        )
        self._require_paused(result, RunStatus.require_human, "采购任务绑定")
        return {
            "session_id": result.session_id,
            "run_id": result.run_id,
            "status": result.status.value,
        }

    def _new_procurement_request(
        self,
        body: AgentCommandBody,
        *,
        message: str,
        stage: str,
        pending_attachments: list[dict[str, Any]],
        source_message: str,
    ) -> RunRequest:
        config = _load_model_config(self.harness.data_dir)
        planner_mode = str(config.get("planner_mode") or "model").strip().lower()
        if planner_mode not in {"model", "hybrid"}:
            raise ValueError("采购 Agent planner_mode 必须是 model 或 hybrid")
        if planner_mode == "hybrid" and stage in self._INTERNAL_PLANNER_STAGES:
            provider, model = "procurement_internal", "deterministic-procurement"
        else:
            provider, model = self._configure_provider(config)
        max_cost = _optional_float(config.get("max_cost_usd"))
        pricing = PricingConfig(
            input_per_million_usd=_optional_float(
                config.get("input_price_per_million_usd")
            ),
            output_per_million_usd=_optional_float(
                config.get("output_price_per_million_usd")
            ),
            cached_input_per_million_usd=_optional_float(
                config.get("cached_input_price_per_million_usd")
            ),
        )
        if max_cost is not None and not pricing.known:
            raise ValueError("设置单次模型成本上限时必须同时配置输入和输出价格")
        return RunRequest(
            message=message,
            provider=provider,
            model=model,
            reasoning_effort=(
                None
                if str(config.get("reasoning_effort") or "auto") == "auto"
                else str(config.get("reasoning_effort"))
            ),
            system=PROCUREMENT_AGENT_SYSTEM_PROMPT,
            allow_write=False,
            tools=PROCUREMENT_TOOL_NAMES,
            budget=BudgetConfig(
                max_steps=16,
                max_tool_calls_per_turn=1,
                max_concurrent_tools=1,
                max_cost_usd=max_cost,
            ),
            pricing=pricing,
            metadata={
                "purchase_request_id": body.aggregate_id,
                "operation_id": body.operation_id,
                "source": "java_control_plane",
                # Some OpenAI-compatible providers ignore parallel_tool_calls=false
                # and stream multiple tool calls in one assistant turn. Procurement
                # stages are deliberately sequential, so keep only the first call
                # instead of failing the whole durable operation.
                "truncate_excess_tool_calls_per_turn": True,
                "generation": body.generation,
                "procurement_stage": stage,
                "procurement_source_message": source_message,
                "procurement_pending_attachments": pending_attachments,
            },
        )

    def _configure_provider(self, config: dict[str, Any]) -> tuple[str, str]:
        provider = str(config.get("provider") or "").strip()
        if provider == "procurement_fake":
            return "procurement_internal", "deterministic-procurement"
        if provider != "openai":
            raise ValueError("采购模型 Provider 配置无效")
        api_key = str(config.get("api_key") or "").strip()
        model = str(config.get("model") or "").strip()
        if not api_key or not model:
            raise ValueError("采购模型未配置 API Key 或模型名称")
        adapter = self.harness.providers.get("openai")
        # P2-1：默认适配器已被 LLM 网关包裹，比较内层适配器类型
        inner = adapter.inner if isinstance(adapter, GatewayAdapter) else adapter
        if isinstance(inner, OpenAIResponsesAdapter):
            self.harness.register_provider(
                "procurement_openai",
                OpenAIResponsesAdapter(
                    api_key=api_key,
                    base_url=str(config.get("base_url") or "").strip() or None,
                    default_model=model,
                    api_mode=str(config.get("api_mode") or "auto"),
                    use_env=False,
                ),
            )
            return "procurement_openai", model
        if adapter is None or not hasattr(adapter, "stream"):
            raise ValueError("采购模型 Provider 不可用")
        return "openai", model

    def _ensure_run_provider(self, run_id: str) -> None:
        run = self.harness.storage.get_run(run_id)
        if run is None:
            raise ValueError("run was not found")
        provider = str(run.get("provider") or "")
        if provider in self.harness.providers:
            return
        if provider == "procurement_openai":
            restored, _ = self._configure_provider(
                _load_model_config(self.harness.data_dir)
            )
            if restored == provider and provider in self.harness.providers:
                return
        raise ValueError(
            f"run {run_id} uses unavailable provider {provider!r}; "
            "restore the original provider configuration before resuming"
        )

    @staticmethod
    def _require_paused(result: Any, expected: RunStatus, operation: str) -> None:
        if result.status != expected:
            detail = result.error or result.output or result.status.value
            raise ValueError(f"{operation}未进入预期状态：{detail}")

    def _latest_tool_payload(self, run_id: str, tool_name: str) -> dict[str, Any]:
        for invocation in reversed(self.harness.storage.list_tool_invocations(run_id)):
            if invocation.tool_name != tool_name or invocation.result is None:
                continue
            content = invocation.result.content
            spill = next(
                (
                    part
                    for part in invocation.result.parts
                    if part.type == "resource"
                    and part.text == "Full tool result stored as artifact"
                    and part.artifact_id
                ),
                None,
            )
            if spill is not None:
                artifact = self.harness.storage.get_artifact(str(spill.artifact_id))
                raw = (
                    self.harness.storage.artifacts.get_text(str(artifact["sha256"]))
                    if artifact is not None
                    else None
                )
                if raw is None:
                    raise ValueError(f"{tool_name} durable result artifact is missing")
                try:
                    full_result = ToolResult.model_validate_json(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"{tool_name} durable result artifact is invalid"
                    ) from exc
                if (
                    full_result.name != invocation.result.name
                    or full_result.tool_call_id != invocation.result.tool_call_id
                ):
                    raise ValueError(f"{tool_name} durable result artifact is mismatched")
                content = full_result.content
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{tool_name} returned invalid JSON evidence") from exc
            if isinstance(payload, dict):
                return payload
        raise ValueError(f"{tool_name} did not produce durable evidence")

    def _run_for_operation(self, operation_id: str) -> dict[str, Any] | None:
        # 索引优先：internal_operations.run_id 直达（O(1)）；旧数据回退全量扫描
        # 并回填 run_id，避免 list_runs(limit=500) 之外的历史 operation 永远查不到。
        operation = self.harness.storage.internal_operations.get(operation_id)
        indexed_run_id = str(operation.get("run_id") or "") if operation else ""
        if indexed_run_id:
            run = self.harness.storage.get_run(indexed_run_id)
            if run is not None:
                return run
        for run in self.harness.storage.list_runs(limit=500):
            # P-M5: 行里只有 `metadata_json`（`_decorate_run_observability` 只补
            # actor/depth/child_count/user_summary），读不存在的 `metadata` 键
            # 会让这条崩溃恢复回退永远匹配不上。
            metadata = _json_object(run.get("metadata_json"))
            if metadata.get("operation_id") == operation_id:
                self.harness.storage.internal_operations.set_run_id(
                    operation_id, str(run["id"])
                )
                return run
        return None

    def _run_result(self, run: dict[str, Any]):  # type: ignore[no-untyped-def]
        from agentharness.contracts import RunResult, Usage

        usage = json.loads(str(run.get("usage_json") or "{}"))
        metadata = json.loads(str(run.get("metadata_json") or "{}"))
        return RunResult(
            run_id=str(run["id"]),
            session_id=str(run["session_id"]),
            status=RunStatus(str(run["status"])),
            output=str(run.get("output_summary") or ""),
            usage=Usage.model_validate(usage),
            steps=int(run.get("steps") or 0),
            error=run.get("error"),
            parent_run_id=run.get("parent_run_id"),
            root_run_id=run.get("root_run_id"),
            metadata=metadata,
        )

    async def _java_json(self, path: str) -> dict[str, Any]:
        return await self._fetch_context(path)

    async def _java_bytes(self, path: str) -> bytes:
        return await self._fetch_artifact(path)

    async def _http_json(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                self.java_base_url + path,
                headers={"X-Agent-Internal-Token": self.token},
            )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Java internal API returned a non-object")
        return value

    async def _http_bytes(self, path: str) -> bytes:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self.java_base_url + path,
                headers={"X-Agent-Internal-Token": self.token},
            )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _envelope(
        operation_id: str,
        status: str,
        result: dict[str, Any] | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "status": status,
            "result": result,
            "error": error,
        }


def internal_agent_router(harness: Harness) -> APIRouter:
    router = APIRouter(prefix="/internal/v1", tags=["internal-agent"])
    commands = InternalAgentCommands(harness)

    @router.post("/commands")
    async def execute_command(
        body: AgentCommandBody,
        operation_id: str = Header(alias="X-Operation-Id"),
        payload_sha256: str = Header(alias="X-Payload-SHA256"),
    ) -> dict[str, Any]:
        if operation_id != body.operation_id or payload_sha256 != body.payload_sha256:
            raise HTTPException(409, "operation headers do not match request body")
        return await commands.execute(body)

    @router.get("/operations/{operation_id}")
    async def operation(operation_id: str) -> dict[str, Any]:
        value = harness.storage.internal_operations.get(operation_id)
        if value is None:
            raise HTTPException(404, "operation not found")
        status = str(value["status"])
        if status == "executing":
            # `executing` is the internal claim state (P-M4); the documented
            # wire vocabulary is [accepted, completed, failed], and callers poll
            # until it leaves the non-terminal bucket.
            status = "accepted"
        return InternalAgentCommands._envelope(
            operation_id,
            status,
            value.get("result"),
            value.get("error"),
        )

    @router.get("/config")
    async def get_config() -> dict[str, Any]:
        return _read_model_config(harness.data_dir)

    @router.post("/config")
    async def update_config(body: dict[str, Any]) -> dict[str, Any]:
        return _write_model_config(harness.data_dir, body)

    @router.get("/evaluation")
    async def evaluation() -> dict[str, Any]:
        return await asyncio.to_thread(evaluate_frozen_cases)

    return router


def _model_config_path(data_dir: Path) -> Path:
    return data_dir / "procurement-model-config.json"


def _env_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def _looks_like_api_key(value: str) -> bool:
    """Key-shaped: long enough to be a real credential, or an `sk-` prefixed one.

    P-M9: 只有「与 env 完全相同」才脱敏是不够的——用户手输的自定义 key、旧格式
    残留的明文 key 同样会落盘（Windows 上 chmod 600 还是不生效）。任何看起来像
    密钥的值都按 env 引用形态存储。
    """
    if not value:
        return False
    return value.startswith("sk-") or len(value) >= 20


def _sanitize_for_disk(config: dict[str, Any]) -> dict[str, Any]:
    """落盘前脱敏：密钥形状的 api_key 一律只存 env 引用标记，绝不写明文。

    旧格式（文件里直接存 key 字符串）仍可读取；下次写入时自动归一化为引用形态，
    真实 key 需要回到 `.env`（`OPENAI_API_KEY`）里配置。
    """
    stored = dict(config)
    api_key = str(stored.get("api_key") or "").strip()
    if api_key and (api_key == _env_api_key() or _looks_like_api_key(api_key)):
        stored["api_key"] = None
        stored["api_key_from_env"] = True
    else:
        stored["api_key"] = api_key or None
        stored["api_key_from_env"] = False
    return stored


def _default_model_config() -> dict[str, Any]:
    return {
        "provider": os.environ.get("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai"),
        "planner_mode": os.environ.get(
            "AGENTHARNESS_PROCUREMENT_PLANNER_MODE", "model"
        ),
        "model": (
            os.environ.get("AGENTHARNESS_PROCUREMENT_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-5.4"
        ),
        "base_url": os.environ.get("OPENAI_BASE_URL") or None,
        "api_mode": os.environ.get("AGENTHARNESS_PROCUREMENT_API_MODE", "auto"),
        "reasoning_effort": os.environ.get(
            "AGENTHARNESS_PROCUREMENT_REASONING_EFFORT", "auto"
        ),
        "api_key": os.environ.get("OPENAI_API_KEY") or None,
        "input_price_per_million_usd": None,
        "output_price_per_million_usd": None,
        "cached_input_price_per_million_usd": None,
        "max_cost_usd": None,
    }


def _load_model_config(data_dir: Path) -> dict[str, Any]:
    config = _default_model_config()
    try:
        value = json.loads(_model_config_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    if isinstance(value, dict):
        config.update(value)
    # 引用形态回填：文件只存标记，运行时从 env 取回真实 key（行为与旧格式一致）
    if (
        not str(config.get("api_key") or "").strip()
        and config.get("api_key_from_env")
    ):
        env_key = _env_api_key()
        if env_key:
            config["api_key"] = env_key
    return config


def _read_model_config(data_dir: Path) -> dict[str, Any]:
    config = _load_model_config(data_dir)
    key = str(config.pop("api_key", "") or "")
    return {
        **config,
        "api_key_configured": bool(key),
        "api_key_preview": f"{key[:3]}…{key[-2:]}" if len(key) >= 6 else None,
    }


def _write_model_config(data_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "provider",
        "planner_mode",
        "model",
        "base_url",
        "api_mode",
        "reasoning_effort",
        "api_key",
        "input_price_per_million_usd",
        "output_price_per_million_usd",
        "cached_input_price_per_million_usd",
        "max_cost_usd",
    }
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(422, f"unknown model config fields: {sorted(unknown)}")
    if body.get("provider") not in {"procurement_fake", "openai"}:
        raise HTTPException(422, "provider must be procurement_fake or openai")
    if body.get("planner_mode", "model") not in {"model", "hybrid"}:
        raise HTTPException(422, "planner_mode must be model or hybrid")
    if not str(body.get("model") or "").strip():
        raise HTTPException(422, "model must not be blank")
    for key in (
        "input_price_per_million_usd",
        "output_price_per_million_usd",
        "cached_input_price_per_million_usd",
        "max_cost_usd",
    ):
        try:
            _optional_float(body.get(key))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    current = _default_model_config()
    path = _model_config_path(data_dir)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        stored = {}
    if isinstance(stored, dict):
        current.update(stored)
    current.update({key: value for key, value in body.items() if key in allowed})
    if not body.get("api_key"):
        current["api_key"] = current.get("api_key")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_sanitize_for_disk(current), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    try:
        # 明文密钥绝不落盘；即便存了用户显式输入的自定义 key，也收紧文件权限
        path.chmod(0o600)
    except OSError:
        pass  # Windows FAT 等文件系统不支持 POSIX 权限，忽略
    return _read_model_config(data_dir)
