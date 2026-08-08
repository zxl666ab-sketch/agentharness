"""Live stage evidence: convergence+reasons, independent review, approval->PO,
and budget behavior on the real model (deepseek-v4-flash via OpenAI gateway).

Run: python scripts/verify_live_stage_evidence.py
Requires configured .env key + procurement pricing env vars (max_cost_usd etc).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.api.execution import PendingApprovalBroker
from agentharness.api.reporting import build_run_report
from agentharness.config import load_project_env
from agentharness.contracts import BudgetConfig, EventType
from agentharness.harness import Harness
from agentharness.procurement.agent import (
    ProcurementAgent,
    ProcurementRunProfile,
    procurement_run_profile_from_env,
)
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementService

DEFAULT_OUTPUT = Path("output/procurement-evaluation")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def _run_stage_evidence(
    budget: dict[str, Any],
    run_profile: ProcurementRunProfile,
) -> dict[str, Any]:
    truth = load_frozen_truth()
    cases = truth["quotes"][:2]
    message = (
        "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
        "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
        "厚度公差3微米。请比较附件报价并推荐供应商。"
    )
    tmp = tempfile.mkdtemp(prefix="live-stage-evidence-")
    root = Path(tmp)
    harness = Harness(data_dir=root / "data")
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, approval_broker=broker, run_profile=run_profile)
    agent.ai_review_enabled = True
    agent.review_provider = "openai"
    agent.review_model = None
    result: dict[str, Any] = {"budget": budget, "started_at": datetime.now(UTC).isoformat()}
    try:
        accepted = await agent.start(
            message=message,
            attachments=[(case["filename"], build_case_document(case)) for case in cases],
        )
        run_id = accepted["run_id"]
        request_id = accepted["purchase_request_id"]
        run_result = await agent._tasks[run_id]
        request = service.get_request(request_id)
        comparison = request.get("comparison")
        result["run_id"] = run_id
        result["request_id"] = request_id
        result["analysis_status"] = run_result.status.value
        result["analysis_error"] = run_result.error
        result["comparison_produced"] = comparison is not None
        if comparison is not None:
            snapshot = comparison
            detail = await asyncio.wait_for(
                agent.approve(
                    request_id,
                    snapshot_id=snapshot["id"],
                    input_sha256=snapshot["input_sha256"],
                    quote_id=snapshot["result"]["recommended_quote_id"],
                    note="真实模型阶段验证",
                    actor="阶段验证",
                ),
                timeout=240,
            )
            result["approval_status"] = detail.get("status")
            if detail.get("status") == "approved":
                po = service.purchase_order(request_id)
                result["purchase_order"] = {
                    "po_number": po["po_number"],
                    "supplier_name": po["supplier_name"],
                    "quantity": po["quantity"],
                    "total_amount_base": po["total_amount_base"],
                    "currency": po["currency"],
                    "snapshot_id": po["snapshot_id"],
                    "approval_id": po["approval_id"],
                    "evidence_sha256": po["evidence_sha256"],
                }
                result["purchase_order_csv_ok"] = bool(
                    service.purchase_order_csv(request_id)[1]
                )
            audit = service.audit_report(request_id)
            result["ai_review"] = [
                event
                for event in audit["audit_events"]
                if event["type"] == "ai_review"
            ]
        run_report = build_run_report(harness, run_id)
        result["convergence"] = (run_report or {}).get("convergence")
        events = harness.get_events(run_id=run_id, limit=10_000)
        result["provider_retries"] = [
            {"error_kind": event.payload.get("error_kind"), "attempt": event.payload.get("attempt")}
            for event in events
            if event.type == EventType.provider_retry
        ]
        result["budget_warnings"] = [
            event.payload for event in events if event.type == EventType.budget_warning
        ]
        usage = json.loads(harness.get_run(run_id)["usage_json"] or "{}")
        result["usage"] = {
            "model_turns": usage.get("model_turns"),
            "total_tokens": usage.get("total_tokens"),
            "estimated_cost_usd": usage.get("estimated_cost_usd"),
        }
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:500]
    finally:
        result["finished_at"] = datetime.now(UTC).isoformat()
        await agent.aclose()
        await harness.aclose()
    return result


async def main(args: argparse.Namespace) -> int:
    base = procurement_run_profile_from_env()
    if (
        base.pricing.input_per_million_usd is None
        or base.pricing.output_per_million_usd is None
        or base.budget.max_cost_usd is None
    ):
        print(
            "错误：真实模型阶段证据必须配置输入/输出单价与费用上限 "
            "（AGENTHARNESS_PROCUREMENT_INPUT/OUTPUT_PER_MILLION_USD、"
            "AGENTHARNESS_PROCUREMENT_MAX_COST_USD）。",
            file=sys.stderr,
        )
        return 2
    # CLI 预算必须真正进入 Run 的 BudgetConfig，而不是只写进结果文件。
    run_profile = ProcurementRunProfile(
        provider=base.provider,
        model=base.model,
        pricing=base.pricing,
        budget=BudgetConfig(
            max_cost_usd=args.max_cost_usd,
            max_tokens=args.max_tokens,
            max_steps=args.max_steps,
            max_wall_time_s=args.max_wall_time_s,
        ),
        reasoning_effort=base.reasoning_effort,
        base_url=base.base_url,
        api_mode=base.api_mode,
        api_key=base.api_key,
    )
    result = await _run_stage_evidence({}, run_profile)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "note": (
            "真实模型（deepseek-v4-flash，OpenAI 兼容网关）单场景阶段证据："
            "收敛指标/理由、独立评审、审批→PO 导出、预算行为。非公开可复现，"
            "与确定性冻结评测分层呈现。"
        ),
        "result": result,
    }
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"live-stage-evidence-{stamp}.json"
    _write_json(path, payload)
    print(f"报告：{path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 失败或未完成审批必须以非零退出，脚本调用方才能感知。
    if result.get("error") or result.get("approval_status") != "approved":
        return 1
    return 0


def main_entry() -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="真实模型阶段证据验证")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-cost-usd", type=float, default=0.15)
    parser.add_argument("--max-tokens", type=int, default=30000)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-wall-time-s", type=float, default=180)
    args = parser.parse_args()
    return asyncio.run(main(args))


if __name__ == "__main__":
    raise SystemExit(main_entry())
