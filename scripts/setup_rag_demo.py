"""Create a small RAG demo dataset for UI verification (offline, fake-free).

Creates in a fresh data directory:
  - five approved historical requests (rag_chunks, Chinese demo suppliers),
  - one analyzed request with matching specs (history references present).

Usage:
    uv run python scripts/setup_rag_demo.py --data-dir output/rag-ui-data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentharness.contracts import RunStatus
from agentharness.harness import Harness
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("output/rag-ui-data"))
    parser.add_argument("--force", action="store_true", help="overwrite existing data dir")
    args = parser.parse_args()
    if args.data_dir.exists() and not args.force:
        print(f"数据目录已存在：{args.data_dir}（使用 --force 重建）")
        return
    if args.data_dir.exists():
        import shutil

        shutil.rmtree(args.data_dir)

    truth = load_frozen_truth()
    harness = Harness(data_dir=args.data_dir)
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

        # Matching request -> history references shown.
        matching = service.create_request(_request_body(truth))
        _import_two(service, str(matching["id"]), truth)
        run_id = "rag-demo-match-run"
        harness.storage.create_run(
            run_id=run_id,
            session_id=str(matching["session_id"]),
            root_run_id=run_id,
            status=RunStatus.completed,
        )
        result = service.execute_analysis_pipeline(str(matching["id"]), run_id=run_id)
        harness.storage.update_run(run_id, finished=True)
        assert result["knowledge_references"], "matching request should see history"

        print(f"数据目录：{args.data_dir}")
        print(f"历史已成交请求数：{len(history_ids)}（供应商已应用中文演示名）")
        print(f"匹配请求（应显示历史参考，可展开 top-5）：{matching['id']} ({matching['reference']})")
        print(f"索引 chunk 数：{harness.storage.rag.count_chunks()}")
    finally:
        harness.close()


if __name__ == "__main__":
    sys.exit(main())
