"""Token-protected command surface used by the Java procurement control plane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agentharness.api.reporting import build_run_report
from agentharness.contracts import (
    BudgetConfig,
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
from agentharness.procurement.evaluation import evaluate_frozen_cases
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


class AgentCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    operation_type: Literal[
        "start_conversation",
        "import_quote",
        "resume_run",
        "analyze",
        "approve_decision",
        "create_structured",
        "reopen_task",
    ]
    aggregate_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    expected_task_version: int = Field(ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]


class InternalAgentCommands:
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
        self.token = os.environ.get(
            "AGENT_INTERNAL_TOKEN", "development-only-change-me"
        )
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
        try:
            existing = self.harness.storage.internal_operations.accept(
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
            return self._envelope(
                body.operation_id, "failed", None, str(existing.get("error") or "failed")
            )
        try:
            result = await self._dispatch(body)
        except Exception as exc:  # noqa: BLE001 - failure must be durable for idempotency
            self.harness.storage.internal_operations.fail(body.operation_id, str(exc))
            return self._envelope(body.operation_id, "failed", None, str(exc))
        self.harness.storage.internal_operations.complete(body.operation_id, result)
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
        if body.operation_type in {"create_structured", "reopen_task"}:
            return await self._bind_new_task(body)
        raise ValueError(f"unsupported operation type: {body.operation_type}")

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
        else:
            result = self._run_result(existing_run)
        self._require_paused(result, RunStatus.require_human, "初始采购资料复核")
        requirement_payload = self._latest_tool_payload(
            result.run_id, "procurement_capture_requirement"
        )
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

    async def _import_quote(self, body: AgentCommandBody) -> dict[str, Any]:
        context = await self._java_json(f"/internal/v1/tasks/{body.aggregate_id}/context")
        run_id = str(context.get("analysis_run_id") or "")
        if not run_id or self.harness.storage.get_run(run_id) is None:
            raise ValueError("Java task is not bound to an Agent run")
        self._ensure_run_provider(run_id)
        self.harness.storage.merge_run_metadata(
            run_id,
            {
                "procurement_stage": "import_quote",
                "procurement_pending_attachments": [dict(body.payload)],
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
        if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict):
            raise ValueError("采购 Agent 未返回新增报价的解析结果")
        return {"quote": quotes[0], "run_id": run_id, "status": result.status.value}

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
        report = build_run_report(self.harness, run_id) or {}
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
                f"{message}\n仅调用 procurement_request_review。"
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
        return next(
            (
                run
                for run in self.harness.storage.list_runs(limit=500)
                if (run.get("metadata") or {}).get("operation_id") == operation_id
            ),
            None,
        )

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
        return InternalAgentCommands._envelope(
            operation_id,
            str(value["status"]),
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


def _default_model_config() -> dict[str, Any]:
    return {
        "provider": os.environ.get("AGENTHARNESS_PROCUREMENT_PROVIDER", "openai"),
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
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    return _read_model_config(data_dir)
