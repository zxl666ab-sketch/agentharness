"""Create a small RAG demo dataset for UI verification (offline, fake-free).

Creates in a fresh data directory:
  - five approved historical requests (rag_chunks, Chinese demo suppliers),
  - one analyzed request with matching specs (history references present).

Usage:
    uv run python scripts/setup_rag_demo.py --data-dir output/rag-ui-data
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from agentharness.contracts import RunStatus
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent, _fake_run_profile
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import parse_quote
from agentharness.procurement.service import ProcurementService


def _request_body(truth: dict, **overrides: object) -> dict:
    request = dict(truth["request"])
    specs = dict(request["specifications"])
    specs.update(overrides.pop("specifications", {}) or {})
    return {
        "title": "华东仓快递袋询价",
        "category": "ecommerce_packaging",
        "item_name": request["item_name"],
        "quantity": request["quantity"],
        "unit": request["unit"],
        "specifications": specs,
        "constraints": request["constraints"],
    }


def _import_two(service: ProcurementService, request_id: str, truth: dict) -> None:
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        quote = service.import_quote(
            request_id,
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
        # 应用演示中文供应商名（华东优包 / 沪上包装…），保持界面全中文。
        service.correct_field(
            request_id,
            str(quote["id"]),
            field="supplier_name",
            value=case["demo_supplier_name"],
            actor="演示员",
        )


async def _analyze_matching_async(
    service: ProcurementService,
    request_id: str,
) -> str:
    """Run one real fake-provider agent analysis so the run ends resumable.

    Ends at require_human (analysis completed, awaiting buyer selection) with a
    persisted checkpoint — the same state a real analyzed request has, so the
    UI approval resume path (提交供应商审批) works on the demo data.
    """
    agent = ProcurementAgent(service.harness, service, run_profile=_fake_run_profile())
    try:
        accepted = await agent.start_existing(request_id)
        run_id = accepted["run_id"]
        await asyncio.wait_for(agent._tasks[run_id], timeout=120)
        return run_id
    finally:
        await agent.aclose()


async def _build_demo_async(data_dir: Path, *, force: bool) -> dict[str, Any]:
    """Create the RAG demo dataset in ``data_dir`` and return its key facts."""
    if data_dir.exists() and not force:
        raise FileExistsError(f"数据目录已存在：{data_dir}（使用 force=True 重建）")
    if data_dir.exists():
        import shutil

        shutil.rmtree(data_dir)

    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    try:
        # Five historical approved requests -> five rag_chunks (expand top-5).
        history_ids: list[str] = []
        for index in range(5):
            history = service.create_request(_request_body(truth))
            _import_two(service, str(history["id"]), truth)
            run_id = f"rag-demo-history-run-{index}"
            harness.storage.create_run(
                run_id=run_id,
                session_id=str(history["session_id"]),
                root_run_id=run_id,
                status=RunStatus.completed,
            )
            snapshot = service.compare_for_agent(str(history["id"]), run_id=run_id)
            service.approve_supplier_from_agent(
                str(history["id"]),
                snapshot_id=str(snapshot["id"]),
                input_sha256=str(snapshot["input_sha256"]),
                quote_id=str(snapshot["result"]["recommended_quote_id"]),
                run_id=run_id,
                approval_id=f"rag-demo-approval-{index}",
                note=f"演示历史成交 {index + 1}",
                actor="演示员",
            )
            harness.storage.update_run(run_id, finished=True)
            history_ids.append(str(history["id"]))

        # Matching request -> history references shown. Run through the real
        # fake-provider agent so the run is require_human + checkpointed
        # (resumable by the approval flow), not a fabricated completed run.
        matching = service.create_request(_request_body(truth))
        _import_two(service, str(matching["id"]), truth)
        matching_run_id = await _analyze_matching_async(service, str(matching["id"]))
        detail = service.get_request(str(matching["id"]))
        assert detail["knowledge_references"], "matching request should see history"

        run = harness.get_run(matching_run_id)
        assert run is not None and run["status"] == RunStatus.require_human.value, (
            "matching run should be require_human (awaiting buyer selection)"
        )
        assert harness.storage.load_checkpoint(matching_run_id) is not None, (
            "matching run should carry a resume checkpoint"
        )

        return {
            "data_dir": str(data_dir),
            "history_ids": history_ids,
            "matching_id": str(matching["id"]),
            "matching_reference": str(matching["reference"]),
            "matching_run_id": matching_run_id,
            "chunk_count": harness.storage.rag.count_chunks(),
        }
    finally:
        harness.close()


async def build_demo_async(data_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Async entry used by tests that already run inside an event loop."""
    return await _build_demo_async(data_dir, force=force)


def build_demo(data_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Create the RAG demo dataset in ``data_dir`` and return its key facts."""
    return asyncio.run(_build_demo_async(data_dir, force=force))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("output/rag-ui-data"))
    parser.add_argument("--force", action="store_true", help="overwrite existing data dir")
    args = parser.parse_args()
    if args.data_dir.exists() and not args.force:
        print(f"数据目录已存在：{args.data_dir}（使用 --force 重建）")
        return
    result = build_demo(args.data_dir, force=True)
    print(f"数据目录：{result['data_dir']}")
    print(f"历史已成交请求数：{len(result['history_ids'])}（供应商已应用中文演示名）")
    print(f"匹配请求（应显示历史参考，可展开 top-5）：{result['matching_id']} ({result['matching_reference']})")
    print(f"匹配运行：{result['matching_run_id']}（status=require_human，带检查点，可审批恢复）")
    print(f"索引 chunk 数：{result['chunk_count']}")


if __name__ == "__main__":
    sys.exit(main())
