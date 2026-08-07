from __future__ import annotations

from datetime import date

from agentharness.rag.retriever import Retriever
from agentharness.storage.sqlite import Storage


def _request(**overrides: object) -> dict:
    base = {
        "id": "current-request",
        "item_name": "快递袋",
        "specifications": {
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "material": "PE",
            "color": "白色",
            "print_colors": 1,
        },
        "constraints": {
            "size_tolerance_mm": "2",
            "thickness_tolerance_um": "3",
        },
    }
    base.update(overrides)
    return base


def _chunk(sha: str, **overrides: object) -> dict:
    base = {
        "chunk_sha256": sha,
        "request_id": f"req-{sha[:6]}",
        "quote_id": f"quote-{sha[:6]}",
        "artifact_id": f"art-{sha[:6]}",
        "artifact_sha256": "f" * 64,
        "request_reference": f"RFQ-20260701-{sha[:6].upper()}",
        "supplier_name": "华东优包",
        "item_name": "快递袋",
        "category": "ecommerce_packaging",
        "specifications": {
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "material": "PE",
            "color": "白色",
            "print_colors": 1,
        },
        "unit_price": "0.42",
        "currency": "CNY",
        "landed_unit_cost": "0.4521",
        "lead_days": 10,
        "moq": 5000,
        "decision": "approved",
        "decision_at": "2026-07-01T00:00:00+00:00",
        "content": "快递袋 250x350mm PE 白色 单色 0.42 CNY",
        "quality_flags": [],
        "created_at": "2026-07-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_retriever_hybrid_recall_and_top5(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        for idx, sha in enumerate(["1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64]):
            storage.rag.upsert_chunk(
                _chunk(sha, unit_price=f"0.{idx}0", decision_at=f"2026-07-0{idx+1}T00:00:00+00:00")
            )
        # A spec-mismatched chunk (different material) must be excluded.
        storage.rag.upsert_chunk(
            _chunk(
                "6" * 64,
                item_name="垃圾袋",
                specifications={
                    "width_mm": "500",
                    "length_mm": "600",
                    "thickness_um": "40",
                    "material": "PE",
                    "color": "黑色",
                    "print_colors": 0,
                },
                content="垃圾袋 500x600mm PE 黑色 0.30 CNY",
            )
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert len(results) == 5
        assert all(item["chunk_sha256"] != "6" * 64 for item in results)
        assert all("score" in item for item in results)
        assert all("specification_summary" in item for item in results)
        assert all(item["spec_match"]["material"] for item in results)
        # Newer decision first on equal scores (time decay is monotonic).
        assert results[0]["decision_at"] >= results[-1]["decision_at"]
    finally:
        storage.close()


def test_retriever_excludes_current_request_and_spec_mismatch(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk("7" * 64, request_id="current-request", request_reference="RFQ-CURRENT")
        )
        storage.rag.upsert_chunk(
            _chunk(
                "8" * 64,
                specifications={
                    "width_mm": "500",
                    "length_mm": "600",
                    "thickness_um": "40",
                    "material": "PP",
                    "color": "黑色",
                    "print_colors": 2,
                },
            )
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert results == []
    finally:
        storage.close()


def test_retriever_quality_flag_reranks_low_confidence_below_clean(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk(
                "9" * 64,
                quality_flags=["low_confidence"],
                decision_at="2026-07-10T00:00:00+00:00",
            )
        )
        storage.rag.upsert_chunk(
            _chunk(
                "a" * 64,
                quality_flags=[],
                decision_at="2026-07-09T00:00:00+00:00",
            )
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert len(results) == 2
        assert results[0]["chunk_sha256"] == "a" * 64
        assert results[1]["chunk_sha256"] == "9" * 64
    finally:
        storage.close()


def test_retriever_reputation_boost(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk("b" * 64, supplier_name="华东优包", decision_at="2026-07-10T00:00:00+00:00")
        )
        storage.rag.upsert_chunk(
            _chunk("c" * 64, supplier_name="星河包装", decision_at="2026-07-10T00:00:00+00:00")
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(
            request=_request(),
            now=date(2026, 7, 15),
            adopted_counts={"华东优包": 5, "星河包装": 0},
        )
        assert results[0]["chunk_sha256"] == "b" * 64
        assert results[1]["chunk_sha256"] == "c" * 64
    finally:
        storage.close()


def test_retriever_is_deterministic(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        for idx, sha in enumerate(["d" * 64, "e" * 64, "f" * 64]):
            storage.rag.upsert_chunk(
                _chunk(sha, unit_price=f"0.{idx}0", decision_at=f"2026-07-0{idx+1}T00:00:00+00:00")
            )
        retriever = Retriever(storage)
        first = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        second = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert [item["chunk_sha256"] for item in first] == [
            item["chunk_sha256"] for item in second
        ]
        assert [item["score"] for item in first] == [item["score"] for item in second]
    finally:
        storage.close()


def test_retriever_missing_fields_are_penalized(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk(
                "1" * 63 + "0",
                specifications={
                    "width_mm": "250",
                    "length_mm": "350",
                    "thickness_um": "60",
                    "material": "PE",
                    "color": "白色",
                    "print_colors": 1,
                },
                decision_at="2026-07-10T00:00:00+00:00",
            )
        )
        storage.rag.upsert_chunk(
            _chunk(
                "1" * 63 + "1",
                specifications={
                    "width_mm": "250",
                    "material": "PE",
                    "color": "白色",
                    "print_colors": 1,
                },
                decision_at="2026-07-10T00:00:00+00:00",
            )
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert len(results) == 2
        assert results[0]["chunk_sha256"] == "1" * 63 + "0"
        assert results[1]["chunk_sha256"] == "1" * 63 + "1"
    finally:
        storage.close()
