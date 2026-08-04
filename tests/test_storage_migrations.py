from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agentharness.storage.migrations import MIGRATIONS, SCHEMA_VERSION, apply_migrations
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


def test_failed_migration_rolls_back_ddl_and_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _database_at_version(tmp_path / "rollback.db", 5)
    try:
        with monkeypatch.context() as patch:
            patch.setitem(
                MIGRATIONS,
                6,
                """
                ALTER TABLE memories ADD COLUMN partial_column TEXT;
                SELECT * FROM table_that_does_not_exist;
                """,
            )
            with pytest.raises(sqlite3.OperationalError):
                apply_migrations(conn)

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert "partial_column" not in columns
        assert version == ("5",)

        assert apply_migrations(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_v5_memory_data_survives_v6_forward_migration(tmp_path: Path) -> None:
    created_at = "2026-01-02T03:04:05+00:00"
    conn = _database_at_version(tmp_path / "agentharness.db", 5)
    conn.execute(
        """INSERT INTO memories(
               id, content, source, scope, created_at, last_used_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            "historical-memory",
            "historical migration fact",
            "v5",
            "global",
            created_at,
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO memories_fts(rowid, content, source, scope)
           SELECT rowid, content, source, scope FROM memories WHERE id = ?""",
        ("historical-memory",),
    )
    conn.commit()
    conn.close()

    storage = Storage(tmp_path)
    try:
        row = storage.get_memory("historical-memory")
        assert storage.schema_version() == SCHEMA_VERSION
        assert row is not None
        assert row["content"] == "historical migration fact"
        assert row["content_hash"]
        assert row["updated_at"] == created_at
        assert row["expires_at"] is None
        assert row["use_count"] == 0
        assert storage.search_memories("historical migration", scopes=["global"])
    finally:
        storage.close()


def test_v6_approvals_gain_confirmation_flag(tmp_path: Path) -> None:
    created_at = "2026-01-02T03:04:05+00:00"
    conn = _database_at_version(tmp_path / "agentharness.db", 6)
    conn.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
        ("session", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO runs(
               id, session_id, root_run_id, status, created_at, updated_at
           ) VALUES(?,?,?,?,?,?)""",
        ("run", "session", "run", "waiting_approval", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO approvals(
               id, run_id, tool_call_id, tool_name, effect, created_at
           ) VALUES(?,?,?,?,?,?)""",
        ("approval", "run", "tool", "write_file", "workspace_write", created_at),
    )
    conn.commit()
    conn.close()

    storage = Storage(tmp_path)
    try:
        approvals = storage.list_approvals("run")
        assert storage.schema_version() == SCHEMA_VERSION
        assert approvals[0]["requires_confirmation"] is False
    finally:
        storage.close()


def test_v7_gains_durable_tool_execution_tables(tmp_path: Path) -> None:
    conn = _database_at_version(tmp_path / "agentharness.db", 7)
    conn.close()

    storage = Storage(tmp_path)
    try:
        tables = {
            row[0]
            for row in storage._conn.execute(  # noqa: SLF001 - migration evidence
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        message_columns = {
            row[1]
            for row in storage._conn.execute("PRAGMA table_info(messages)").fetchall()  # noqa: SLF001
        }
        approval_columns = {
            row[1]
            for row in storage._conn.execute("PRAGMA table_info(approvals)").fetchall()  # noqa: SLF001
        }
        assert storage.schema_version() == SCHEMA_VERSION
        assert {"tool_invocations", "tool_attempts"} <= tables
        assert "tool_result_json" in message_columns
        assert {"invocation_id", "arguments_sha256", "approval_scope", "status"} <= approval_columns
    finally:
        storage.close()


def test_v8_gains_procurement_workflow_tables(tmp_path: Path) -> None:
    conn = _database_at_version(tmp_path / "agentharness.db", 8)
    conn.close()

    storage = Storage(tmp_path)
    try:
        tables = {
            row[0]
            for row in storage._conn.execute(  # noqa: SLF001 - migration evidence
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert storage.schema_version() == SCHEMA_VERSION
        assert {
            "procurement_requests",
            "procurement_quotes",
            "procurement_comparison_snapshots",
            "procurement_decisions",
            "procurement_audit_events",
        } <= tables
    finally:
        storage.close()


def test_v10_procurement_decisions_preserve_v1_rows_and_allow_no_award(
    tmp_path: Path,
) -> None:
    created_at = "2026-01-02T03:04:05+00:00"
    conn = _database_at_version(tmp_path / "agentharness.db", 9)
    conn.execute(
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
        ("proc-session", "历史采购", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO runs(
               id, session_id, root_run_id, status, created_at, updated_at
           ) VALUES(?,?,?,?,?,?)""",
        ("proc-run", "proc-session", "proc-run", "completed", created_at, created_at),
    )
    conn.execute(
        """INSERT INTO artifacts(
               id, sha256, content_type, size_bytes, summary, path, created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        ("proc-artifact", "a" * 64, "application/json", 2, "snapshot", "snapshot.json", created_at),
    )
    conn.execute(
        """INSERT INTO procurement_requests(
               id, reference, title, category, item_name, quantity, unit,
               specifications_json, constraints_json, status, session_id,
               analysis_run_id, current_snapshot_id, approved_quote_id,
               created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "proc-request",
            "RFQ-LEGACY-001",
            "历史采购",
            "ecommerce_packaging",
            "快递袋",
            10,
            "piece",
            "{}",
            "{}",
            "approved",
            "proc-session",
            "proc-run",
            "proc-snapshot",
            "proc-quote",
            created_at,
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_quotes(
               id, request_id, supplier_name, source_filename, source_kind,
               source_artifact_id, source_sha256, extracted_json, status,
               review_count, parser_version, processing_ms, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "proc-quote",
            "proc-request",
            "历史供应商",
            "legacy.xlsx",
            "xlsx",
            "proc-artifact",
            "b" * 64,
            "{}",
            "ready",
            0,
            "legacy-parser",
            0,
            created_at,
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_comparison_snapshots(
               id, request_id, run_id, version, input_sha256, result_json,
               artifact_id, created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            "proc-snapshot",
            "proc-request",
            "proc-run",
            1,
            "c" * 64,
            '{"schema_version":1,"quotes":[]}',
            "proc-artifact",
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_decisions(
               id, request_id, snapshot_id, quote_id, run_id, approval_id,
               decision, note, actor, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "proc-decision",
            "proc-request",
            "proc-snapshot",
            "proc-quote",
            "proc-run",
            "legacy-approval",
            "approved",
            "历史决策",
            "采购员",
            created_at,
        ),
    )
    conn.commit()
    conn.close()

    storage = Storage(tmp_path)
    try:
        decision_columns = {
            row[1]: row[3]
            for row in storage._conn.execute(  # noqa: SLF001 - migration evidence
                "PRAGMA table_info(procurement_decisions)"
            ).fetchall()
        }
        assert storage.schema_version() == SCHEMA_VERSION
        assert decision_columns["quote_id"] == 0
        legacy = storage.procurement.get_decision("proc-request")
        assert legacy is not None
        assert legacy["decision"] == "approved"
        assert legacy["quote_id"] == "proc-quote"
        snapshot = storage.procurement.get_snapshot("proc-snapshot")
        assert snapshot is not None
        assert snapshot["result"]["schema_version"] == 1

        # The v10 table accepts a final no-award decision without a quote.
        storage._conn.execute("PRAGMA foreign_keys=OFF")  # noqa: SLF001
        storage._conn.execute(
            """INSERT INTO procurement_decisions(
                   id, request_id, snapshot_id, quote_id, run_id, approval_id,
                   decision, note, actor, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "proc-no-award",
                "proc-request-no-award",
                "proc-snapshot",
                None,
                "proc-run",
                "no-award-approval",
                "no_award",
                "本轮无合格报价",
                "采购员",
                created_at,
            ),
        )
        no_award = storage._conn.execute(  # noqa: SLF001
            "SELECT quote_id, decision FROM procurement_decisions WHERE id = ?",
            ("proc-no-award",),
        ).fetchone()
        assert no_award[0] is None
        assert no_award[1] == "no_award"
    finally:
        storage.close()


def test_legacy_checkpoint_invocation_id_is_stable_across_loads(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    try:
        session_id = storage.create_session("session")
        storage.create_run(
            run_id="legacy-run",
            session_id=session_id,
            root_run_id="legacy-run",
        )
        payload = {
            "run_id": "legacy-run",
            "phase": "tool_batch",
            "step": 3,
            "messages": [],
            "pending_tool_calls": [
                {
                    "id": "provider-call",
                    "name": "read_file",
                    "arguments": {"path": "README.md"},
                    "ordinal": 0,
                }
            ],
            "status": "interrupted",
        }
        storage._conn.execute(  # noqa: SLF001 - inject a pre-v8 serialized checkpoint
            """INSERT INTO checkpoints(run_id, phase, step, data_json, created_at)
               VALUES(?,?,?,?,?)""",
            ("legacy-run", "tool_batch", 3, json.dumps(payload), "2026-01-01T00:00:00+00:00"),
        )

        first = storage.load_checkpoint("legacy-run")
        second = storage.load_checkpoint("legacy-run")
        assert first is not None and second is not None
        first_id = first.pending_tool_calls[0].invocation_id
        assert first_id == second.pending_tool_calls[0].invocation_id
        assert len(first_id) == 32
    finally:
        storage.close()
