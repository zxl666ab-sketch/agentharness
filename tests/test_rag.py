from __future__ import annotations

import sqlite3
from pathlib import Path

from agentharness.storage.migrations import MIGRATIONS, SCHEMA_VERSION
from agentharness.storage.sqlite import Storage


def _database_at_version(path: Path, version: int) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    for migration_version in range(1, version + 1):
        conn.executescript(MIGRATIONS[migration_version])
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(migration_version),),
        )
        conn.commit()
    return conn


def _legacy_row(conn: sqlite3.Connection) -> None:
    created_at = "2026-07-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
        ("session", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO runs(
               id, session_id, root_run_id, status, created_at, updated_at
           ) VALUES(?,?,?,?,?,?)""",
        ("run", "session", "run", "passed", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO procurement_requests(
               id, reference, title, category, item_name, quantity, unit,
               specifications_json, constraints_json, status, session_id,
               created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-request",
            "RFQ-20260701-LEGACY",
            "快递袋采购",
            "ecommerce_packaging",
            "快递袋",
            10000,
            "piece",
            '{"width_mm":"250","length_mm":"350","thickness_um":"60",'
            '"material":"PE","color":"白色","print_colors":1}',
            '{"base_currency":"CNY","fx_rates":{"CNY":"1"},"max_lead_days":15,'
            '"invoice_required":true}',
            "approved",
            "session",
            created_at,
            created_at,
        ),
    )
    conn.commit()
    conn.close()


def test_v14_upgrade_gains_rag_index_and_preserves_legacy_data(
    tmp_path: Path,
) -> None:
    db = tmp_path / "agentharness.db"
    conn = _database_at_version(db, 14)
    _legacy_row(conn)

    storage = Storage(tmp_path)
    try:
        assert storage.schema_version() == SCHEMA_VERSION == 15
        tables = {
            row[0]
            for row in storage._conn.execute(  # noqa: SLF001 - migration evidence
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "rag_chunks" in tables
        assert "rag_chunks_fts" in tables
        legacy = storage.procurement.get_request("legacy-request")
        assert legacy is not None
        assert legacy["reference"] == "RFQ-20260701-LEGACY"
        assert legacy["specifications"]["material"] == "PE"
        assert storage.rag.count_chunks() == 0
    finally:
        storage.close()


def _chunk(**overrides: object) -> dict[str, object]:
    base = {
        "chunk_sha256": "a" * 64,
        "request_id": "req-1",
        "quote_id": "quote-1",
        "artifact_id": "art-1",
        "artifact_sha256": "b" * 64,
        "request_reference": "RFQ-20260701-AAAAAA",
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
        "decision_at": "2026-07-27T00:00:00+00:00",
        "content": "快递袋 250x350mm PE 白色 单色 0.42 CNY 到货单价 0.4521",
        "quality_flags": [],
        "created_at": "2026-07-27T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_rag_repo_upsert_get_delete_roundtrip(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(_chunk())
        assert storage.rag.count_chunks() == 1
        stored = storage.rag.get_chunk("a" * 64)
        assert stored is not None
        assert stored["request_reference"] == "RFQ-20260701-AAAAAA"
        assert stored["specifications"]["material"] == "PE"
        assert stored["quality_flags"] == []

        # Idempotent rebuild: same chunk_sha256 replaces without duplication.
        storage.rag.upsert_chunk(_chunk(unit_price="0.45"))
        assert storage.rag.count_chunks() == 1
        assert storage.rag.get_chunk("a" * 64)["unit_price"] == "0.45"

        storage.rag.upsert_chunk(_chunk(chunk_sha256="c" * 64, quote_id="quote-2"))
        assert storage.rag.count_chunks() == 2
        assert len(storage.rag.list_chunks_by_quote("quote-1")) == 1
        assert storage.rag.delete_chunks_for_quote("quote-1") == 1
        assert storage.rag.count_chunks() == 1
        assert storage.rag.delete_chunk("c" * 64) is True
        assert storage.rag.count_chunks() == 0
    finally:
        storage.close()


def test_rag_repo_fts_and_like_fallback(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(_chunk())
        storage.rag.upsert_chunk(
            _chunk(
                chunk_sha256="d" * 64,
                request_reference="RFQ-20260702-BBBBBB",
                supplier_name="星河包装",
                item_name="垃圾袋",
                content="垃圾袋 500x600mm PE 黑色 0.30 CNY",
                specifications={
                    "width_mm": "500",
                    "length_mm": "600",
                    "thickness_um": "40",
                    "material": "PE",
                    "color": "黑色",
                    "print_colors": 0,
                },
            )
        )
        hits = storage.rag.fts_search("快递袋", limit=10)
        assert len(hits) == 1
        assert hits[0]["item_name"] == "快递袋"
        hits = storage.rag.fts_search("华东优包", limit=10)
        assert len(hits) == 1
        # Content keyword recall.
        hits = storage.rag.fts_search("单色", limit=10)
        assert any(item["chunk_sha256"] == "a" * 64 for item in hits)
        # Non-matching query returns empty (LIKE fallback also empty).
        assert storage.rag.fts_search("不存在的物料", limit=10) == []
    finally:
        storage.close()


def test_rag_repo_delete_by_request(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        storage.rag.upsert_chunk(_chunk())
        storage.rag.upsert_chunk(_chunk(chunk_sha256="e" * 64, quote_id="quote-2"))
        assert storage.rag.delete_chunks_for_request("req-1") == 2
        assert storage.rag.count_chunks() == 0
    finally:
        storage.close()


