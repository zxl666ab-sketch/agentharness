"""Automated pilot: measure RAG pipeline overhead (NOT a human trial).

Runs the deterministic procurement pipeline twice with the fake-free service:
one data dir without history (no RAG references) and one with 5 approved
history chunks (RAG references injected), and reports wall-clock deltas.

This is a machine pre-run for the 1.8 human trial device; it does NOT measure
human decision time and must never be presented as 提效 evidence.
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from agentharness.harness import Harness
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import parse_quote
from agentharness.procurement.service import ProcurementService


def _request_body(truth: dict) -> dict:
    request = truth["request"]
    return {
        "title": "华东仓快递袋询价",
        "category": "ecommerce_packaging",
        "item_name": request["item_name"],
        "quantity": request["quantity"],
        "unit": request["unit"],
        "specifications": request["specifications"],
        "constraints": request["constraints"],
    }


def _import_two(service: ProcurementService, request_id: str, truth: dict) -> None:
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            request_id,
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )


def _run(data_dir: Path, *, with_history: bool, force: bool = False) -> dict:
    if data_dir.exists() and not force:
        raise SystemExit(f"数据目录已存在：{data_dir}（使用 --force 重建）")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    try:
        if with_history:
            for index in range(5):
                history = service.create_request(_request_body(truth))
                _import_two(service, str(history["id"]), truth)
                run_id = f"pilot-history-{index}"
                harness.storage.create_run(
                    run_id=run_id,
                    session_id=str(history["session_id"]),
                    root_run_id=run_id,
                )
                snapshot = service.compare_for_agent(str(history["id"]), run_id=run_id)
                service.approve_supplier_from_agent(
                    str(history["id"]),
                    snapshot_id=str(snapshot["id"]),
                    input_sha256=str(snapshot["input_sha256"]),
                    quote_id=str(snapshot["result"]["recommended_quote_id"]),
                    run_id=run_id,
                    approval_id=f"pilot-approval-{index}",
                    note=None,
                    actor="演示员",
                )
        request = service.create_request(_request_body(truth))
        _import_two(service, str(request["id"]), truth)
        run_id = "pilot-run"
        harness.storage.create_run(
            run_id=run_id,
            session_id=str(request["session_id"]),
            root_run_id=run_id,
        )
        started = time.perf_counter()
        result = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "with_history": with_history,
            "chunks": harness.storage.rag.count_chunks(),
            "references": len(result["knowledge_references"]),
            "pipeline_ms": elapsed_ms,
            "input_sha256": result["snapshot"]["input_sha256"],
        }
    finally:
        harness.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("output/rag-pilot"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="允许删除已存在的数据目录")
    args = parser.parse_args()
    baseline = []
    with_rag = []
    for _ in range(args.runs):
        baseline.append(_run(args.root / "baseline", with_history=False, force=args.force))
        with_rag.append(_run(args.root / "with-history", with_history=True, force=args.force))
    baseline_avg = sum(item["pipeline_ms"] for item in baseline) / len(baseline)
    with_rag_avg = sum(item["pipeline_ms"] for item in with_rag) / len(with_rag)
    print(f"无历史（无 RAG）管线耗时均值：{baseline_avg:.2f} ms（{len(baseline)} 次）")
    print(f"有历史（带 RAG）管线耗时均值：{with_rag_avg:.2f} ms（{len(with_rag)} 次）")
    print(f"RAG 检索/注入增量：{with_rag_avg - baseline_avg:+.2f} ms（约 {(with_rag_avg / baseline_avg - 1) * 100:+.1f}%）")
    print("说明：这是自动化预跑（机器耗时），不是真人对照，不构成提效证据。")


if __name__ == "__main__":
    main()
