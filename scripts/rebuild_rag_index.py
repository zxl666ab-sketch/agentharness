"""Idempotent full rebuild of the historical-price RAG index (offline).

Rebuilds ``rag_chunks`` from all formally approved procurement decisions in a
data directory. The rebuild is deterministic: zero model calls, pure storage,
and running it twice yields the same chunk set (deduplicated by
``chunk_sha256``). Existing chunks for a request are removed before the
request is re-indexed so stale business facts never survive a rebuild.

Usage:
    uv run python scripts/rebuild_rag_index.py --data-dir output/dev-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentharness.rag.chunking import build_chunk
from agentharness.storage.sqlite import Storage


def rebuild(data_dir: Path) -> dict[str, int]:
    storage = Storage(data_dir)
    try:
        requests = storage.procurement.list_requests(limit=100_000)
        indexed = 0
        skipped = 0
        for request in requests:
            request_id = str(request["id"])
            decision = storage.procurement.get_decision(request_id)
            storage.rag.delete_chunks_for_request(request_id)
            if decision is None or str(decision.get("decision")) != "approved":
                skipped += 1
                continue
            snapshot = storage.procurement.get_snapshot(str(decision["snapshot_id"]))
            if snapshot is None:
                skipped += 1
                continue
            quote = next(
                (
                    item
                    for item in storage.procurement.list_quotes(request_id)
                    if item["id"] == decision.get("quote_id")
                ),
                None,
            )
            if quote is None:
                skipped += 1
                continue
            storage.rag.upsert_chunk(
                build_chunk(
                    request=request,
                    quote=quote,
                    decision=decision,
                    snapshot_result=snapshot["result"],
                )
            )
            indexed += 1
        return {"indexed": indexed, "skipped": skipped, "total": len(requests)}
    finally:
        storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("output/dev-run"),
        help="Runtime data directory (default: output/dev-run)",
    )
    args = parser.parse_args()
    result = rebuild(args.data_dir)
    print(
        f"RAG 索引重建完成：indexed={result['indexed']} "
        f"skipped={result['skipped']} total={result['total']} "
        f"(0 模型调用，按 chunk_sha256 去重)"
    )


if __name__ == "__main__":
    sys.exit(main())
