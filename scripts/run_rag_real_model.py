"""Budget-constrained real-model validation for stage-6 RAG.

Runs one live-provider procurement task against a data directory pre-seeded
with 5 approved history chunks, then verifies:
  - comparison page data contains knowledge_references,
  - agent recommendation/reply mentions the history reference,
  - approval completes,
  - run_id / model turns / tokens / estimated cost are recorded.

Requires a configured live provider and pricing (max_cost_usd enforced).
Usage:
    uv run python scripts/run_rag_real_model.py --data-dir output/rag-live-data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.api.execution import PendingApprovalBroker
from agentharness.config import load_project_env
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import parse_quote
from agentharness.procurement.service import ProcurementService

MESSAGE = (
    "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
    "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。"
    "请比较附件报价并推荐供应商。"
)


def _seed_history(service: ProcurementService, truth: dict[str, Any]) -> None:
    for index in range(5):
        request = service.create_request(
            {
                "title": "华东仓快递袋询价",
                "category": "ecommerce_packaging",
                "item_name": truth["request"]["item_name"],
                "quantity": truth["request"]["quantity"],
                "unit": truth["request"]["unit"],
                "specifications": truth["request"]["specifications"],
                "constraints": truth["request"]["constraints"],
            }
        )
        for case in truth["quotes"][:2]:
            document = build_case_document(case)
            service.import_quote(
                str(request["id"]),
                filename=case["filename"],
                data=document,
                extracted=parse_quote(case["filename"], document),
            )
        run_id = f"live-history-{index}"
        service.harness.storage.create_run(
            run_id=run_id,
            session_id=str(request["session_id"]),
            root_run_id=run_id,
        )
        snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=str(snapshot["result"]["recommended_quote_id"]),
            run_id=run_id,
            approval_id=f"live-history-approval-{index}",
            note=None,
            actor="跑批",
        )


async def run(data_dir: Path) -> dict[str, Any]:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, approval_broker=broker)
    result: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat()}
    started = time.monotonic()
    try:
        _seed_history(service, truth)
        result["history_chunks"] = harness.storage.rag.count_chunks()
        attachments = [
            (case["filename"], build_case_document(case))
            for case in truth["quotes"][:2]
        ]
        accepted = await agent.start(message=MESSAGE, attachments=attachments)
        run_id = accepted["run_id"]
        result["run_id"] = run_id
        task = agent._tasks[run_id]  # noqa: SLF001 - live evidence

        async def approve_when_requested() -> None:
            # Resolve the engine approval the moment the broker sees it
            # (automated stand-in for the buyer clicking 确认选定).
            from agentharness.contracts import ApprovalDecision

            while not task.done():
                for row in reversed(harness.list_approvals(run_id)):
                    if row["tool_name"] != "procurement_approve_supplier":
                        continue
                    pending = broker.request(str(row["id"]))
                    if pending is not None:
                        broker.resolve(str(row["id"]), ApprovalDecision.allow_once)
                await asyncio.sleep(0.05)

        approver = asyncio.create_task(approve_when_requested())
        try:
            run_result = await asyncio.wait_for(task, timeout=300)
        finally:
            approver.cancel()
        run = harness.get_run(run_id)
        request = service.get_request(accepted["purchase_request_id"])
        comparison = request.get("comparison")
        references = request.get("knowledge_references") or []
        result["status"] = run_result.status.value
        result["error"] = run_result.error
        result["analysis_success"] = bool(
            run_result.status.value in {"completed", "require_human"}
            or comparison is not None
        )
        result["comparison_produced"] = comparison is not None
        result["knowledge_references_count"] = len(references)
        result["knowledge_reference_ids"] = [item["chunk_id"] for item in references[:5]]
        messages = harness.get_run_messages(run_id)
        result["tool_result_has_references"] = any(
            "knowledge_references" in (message.content or "")
            for message in messages
            if message.role.value == "tool"
        )
        result["model_reply_mentions_history"] = any(
            "历史成交参考" in (message.content or "")
            for message in messages
            if message.role.value == "assistant" and message.content
        )
        usage = json.loads(run.get("usage_json") or "{}")
        result["model_turns"] = usage.get("model_turns", 0)
        result["tokens"] = {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        result["estimated_cost_usd"] = usage.get("estimated_cost_usd")
        result["approval_status"] = request.get("decision", {}).get("decision") if request.get("decision") else None
        result["approval_success"] = result["approval_status"] == "approved"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = str(exc)[:1000]
        result["analysis_success"] = False
        result["approval_success"] = False
    finally:
        result["duration_s"] = round(time.monotonic() - started, 3)
        result["finished_at"] = datetime.now(UTC).isoformat()
        broker.close()
        await agent.aclose()
        await harness.aclose()
    return result


def main() -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("output/rag-live-data"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.data_dir.exists() and not args.force:
        print(f"数据目录已存在：{args.data_dir}（使用 --force 重建）")
        return 1
    if args.data_dir.exists():
        shutil.rmtree(args.data_dir)
    result = asyncio.run(run(args.data_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("approval_success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
