from __future__ import annotations

from datetime import date

from agentharness.procurement.costing import _canonical_material as costing_material
from agentharness.rag.chunking import canonical_material as rag_material
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


def test_retriever_normalizes_material_and_color_aliases(data_dir) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(data_dir)
    try:
        # Chunk uses Chinese/alias values; query uses canonical codes.
        storage.rag.upsert_chunk(
            _chunk(
                "2" * 63 + "2",
                specifications={
                    "width_mm": "250",
                    "length_mm": "350",
                    "thickness_um": "60",
                    "material": "聚乙烯",
                    "color": "白",
                    "print_colors": 1,
                },
                decision_at="2026-07-10T00:00:00+00:00",
            )
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(
            request=_request(
                specifications={
                    "width_mm": "250",
                    "length_mm": "350",
                    "thickness_um": "60",
                    "material": "PE",
                    "color": "白色",
                    "print_colors": 1,
                }
            ),
            now=date(2026, 7, 15),
        )
        assert len(results) == 1
        assert results[0]["chunk_sha256"] == "2" * 63 + "2"
        assert results[0]["spec_match"]["material"] is True
        assert results[0]["spec_match"]["color"] is True
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


def test_rag_material_canonical_matches_costing_word_boundary() -> None:
    """RAG canonical material must agree with costing identity checks.

    The old substring matcher turned ``pet``/``PET膜`` into PE and ``apple``
    into PP, so a PET history chunk could be injected as a “similar PE” ref.
    """
    cases = (
        "PE", "聚乙烯", "polyethylene", "PE泡沫", "PE胶袋",
        "PVC", "聚氯乙烯", "polyvinyl chloride",
        "PP", "聚丙烯", "polypropylene",
        "PET", "PET膜", "聚对苯二甲酸乙二醇酯",
        "PLA", "聚乳酸",
        "per", "pet", "apple", "pete", "未注明",
        "铜版纸", "coated paper", "art paper", "不干胶铜版纸",
        "BOPP", "bopp", "双向拉伸聚丙烯", "BOPP 基材，水性丙烯酸胶",
    )
    for value in cases:
        assert rag_material(value) == costing_material(value), value


def test_retriever_keeps_older_perfect_spec_match_above_new_keyword_only_hits(
    data_dir,
) -> None:  # type: ignore[no-untyped-def]
    """Regression: keyword_bonus=1 tied with a 6/6 spec score, so 20 newer
    keyword-only hits (older decision_at tie-break) pushed the one perfect
    historical match out of the top-20 coarse window."""
    storage = Storage(data_dir)
    try:
        for idx in range(20):
            storage.rag.upsert_chunk(
                _chunk(
                    f"{idx:064d}",
                    item_name="快递袋",
                    specifications={
                        "width_mm": "500",
                        "length_mm": "600",
                        "thickness_um": "40",
                        "material": "PP",
                        "color": "黑色",
                        "print_colors": 2,
                    },
                    decision_at=f"2026-07-{idx + 1:02d}T00:00:00+00:00",
                )
            )
        perfect_sha = "p" * 64
        storage.rag.upsert_chunk(
            _chunk(perfect_sha, decision_at="2026-06-01T00:00:00+00:00")
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert any(item["chunk_sha256"] == perfect_sha for item in results)
        assert results[0]["chunk_sha256"] == perfect_sha
    finally:
        storage.close()


def test_retriever_pages_structured_scan_past_hard_cap(
    data_dir, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Regression: structured recall used one list_chunks(limit=100_000) call,
    so chunks older than the newest 100k rows were silently invisible. The
    retriever must page with offsets until the index is fully scanned."""
    from agentharness.rag import retriever as retriever_module

    monkeypatch.setattr(retriever_module, "_STRUCTURED_SCAN_PAGE_SIZE", 20)
    chunks: list[dict] = []
    for idx in range(119):
        chunks.append(
            _chunk(
                f"{idx:064d}",
                item_name="快递袋",
                specifications={
                    "width_mm": "500",
                    "length_mm": "600",
                    "thickness_um": "40",
                    "material": "PP",
                    "color": "黑色",
                    "print_colors": 2,
                },
                decision_at=f"2026-07-{idx % 28 + 1:02d}T00:00:00+00:00",
            )
        )
    perfect_sha = "z" * 64
    chunks.append(_chunk(perfect_sha, decision_at="2026-06-01T00:00:00+00:00"))

    class _CappedRag:
        """Mimics the old single-call LIMIT cap: one oversized call only sees
        the first ``cap`` rows, while paged calls see everything."""

        def __init__(self, rows: list[dict], cap: int = 60) -> None:
            self._rows = rows
            self._cap = cap
            self.calls: list[tuple[int, int]] = []

        def list_chunks(self, *, limit: int = 1000, offset: int = 0) -> list[dict]:
            self.calls.append((limit, offset))
            if limit >= 100_000:
                return self._rows[offset : min(offset + limit, self._cap)]
            return self._rows[offset : offset + limit]

        def fts_search(self, query: str, *, limit: int = 100) -> list[dict]:
            return []

    class _FakeStorage:
        def __init__(self, rag: _CappedRag) -> None:
            self.rag = rag

    fake = _CappedRag(chunks, cap=60)
    retriever = Retriever(_FakeStorage(fake))
    results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
    assert any(item["chunk_sha256"] == perfect_sha for item in results)
    assert max(offset for _limit, offset in fake.calls) >= 100


def test_retriever_excludes_other_item_history_from_references(data_dir) -> None:  # type: ignore[no-untyped-def]
    """Regression: carton history must never be returned as a reference for a
    PE express-bag request, even when a weak spec dimension (print_colors)
    matches; a small index must not fill the top-5 with unrelated items."""
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk("1" * 64, item_name="PE白色快递袋", decision_at="2026-07-02T00:00:00+00:00")
        )
        storage.rag.upsert_chunk(
            _chunk("2" * 64, item_name="PE白色快递袋", decision_at="2026-07-01T00:00:00+00:00")
        )
        for idx, sha in enumerate(("3" * 64, "4" * 64, "5" * 64), start=3):
            storage.rag.upsert_chunk(
                _chunk(
                    sha,
                    item_name="五层瓦楞纸箱",
                    specifications={
                        "width_mm": "400",
                        "length_mm": "300",
                        "thickness_um": "5000",
                        "material": "瓦楞纸",
                        "color": "牛皮色",
                        "print_colors": 1,
                    },
                    decision_at=f"2026-07-{idx:02d}T00:00:00+00:00",
                )
            )
        retriever = Retriever(storage)
        results = retriever.retrieve(request=_request(), now=date(2026, 7, 15))
        assert [item["chunk_sha256"] for item in results] == ["1" * 64, "2" * 64]
        assert all(item["item_name"] == "PE白色快递袋" for item in results)
    finally:
        storage.close()


def test_retriever_label_request_excludes_bag_and_carton_history(data_dir) -> None:  # type: ignore[no-untyped-def]
    """Regression: once labels are canonicalizable, a thermal-label request
    must not retrieve unrelated mailer/carton history even on weak spec hits."""
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(
            _chunk("1" * 64, item_name="PE白色快递袋", decision_at="2026-07-02T00:00:00+00:00")
        )
        storage.rag.upsert_chunk(
            _chunk("2" * 64, item_name="五层瓦楞纸箱", decision_at="2026-07-01T00:00:00+00:00")
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(
            request=_request(
                item_name="热敏不干胶标签",
                specifications={
                    "width_mm": "100",
                    "length_mm": "150",
                    "thickness_um": "80",
                    "material": "铜版纸",
                    "color": "白色",
                    "print_colors": 1,
                },
            ),
            now=date(2026, 7, 15),
        )
        assert results == []
    finally:
        storage.close()


def test_retriever_label_history_is_retrieved_for_label_request(data_dir) -> None:  # type: ignore[no-untyped-def]
    """A canonicalizable label history chunk is a valid reference for a
    thermal-label request while mailer/carton chunks stay excluded."""
    storage = Storage(data_dir)
    try:
        label_sha = "l" * 64
        storage.rag.upsert_chunk(
            _chunk(
                label_sha,
                item_name="热敏不干胶标签",
                specifications={
                    "width_mm": "100",
                    "length_mm": "150",
                    "thickness_um": "80",
                    "material": "铜版纸",
                    "color": "白色",
                    "print_colors": 1,
                },
                decision_at="2026-07-03T00:00:00+00:00",
            )
        )
        storage.rag.upsert_chunk(
            _chunk("1" * 64, item_name="PE白色快递袋", decision_at="2026-07-02T00:00:00+00:00")
        )
        storage.rag.upsert_chunk(
            _chunk("2" * 64, item_name="五层瓦楞纸箱", decision_at="2026-07-01T00:00:00+00:00")
        )
        retriever = Retriever(storage)
        results = retriever.retrieve(
            request=_request(
                item_name="热敏不干胶标签",
                specifications={
                    "width_mm": "100",
                    "length_mm": "150",
                    "thickness_um": "80",
                    "material": "铜版纸",
                    "color": "白色",
                    "print_colors": 1,
                },
            ),
            now=date(2026, 7, 15),
        )
        assert [item["chunk_sha256"] for item in results] == [label_sha]
    finally:
        storage.close()
