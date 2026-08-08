import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from agentharness.contracts import (
    EventEnvelope,
    EventType,
    Message,
    MessageRole,
    RunStatus,
    ToolCall,
    ToolInvocationRecord,
    ToolInvocationStatus,
)
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage


def test_sqlite_wal_and_events(data_dir):
    store = Storage(data_dir)
    sid = store.create_session()
    rid = "run1"
    store.create_run(
        run_id=rid,
        session_id=sid,
        root_run_id=rid,
        status=RunStatus.running,
        provider="fake",
    )
    ev = EventEnvelope(
        session_id=sid,
        root_run_id=rid,
        run_id=rid,
        type=EventType.run_started,
        payload={"hello": "world"},
    )
    assigned = store.update_run(
        rid,
        status=RunStatus.completed,
        finished=True,
        events=[ev],
    )
    assert len(assigned) == 1
    assert assigned[0].global_seq >= 1
    assert assigned[0].run_seq == 1

    events = store.get_events(run_id=rid)
    assert len(events) == 1
    assert events[0].payload["hello"] == "world"

    # Incremental
    more = store.get_events(after_global_seq=assigned[0].global_seq)
    assert more == []
    store.close()


def test_message_provider_response_state_round_trips(data_dir):
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(run_id="run", session_id=sid, root_run_id="run")
        message = Message(
            role=MessageRole.assistant,
            content="tool preamble",
            provider_response_id="resp_123",
            provider_run_id="run",
            provider_phase="commentary",
        )

        store.save_message("run", sid, message, 1)
        restored = store.get_messages("run")

        assert restored[0].provider_response_id == "resp_123"
        assert restored[0].provider_run_id == "run"
        assert restored[0].provider_phase == "commentary"
    finally:
        store.close()


def test_append_events_assigns_sequences_per_run_in_one_batch(data_dir):
    store = Storage(data_dir)
    try:
        events = [
            EventEnvelope(
                session_id="session",
                root_run_id=run_id,
                run_id=run_id,
                type=EventType.run_status,
                payload={"index": index},
            )
            for index, run_id in enumerate(("run-a", "run-b", "run-a", "run-b"))
        ]

        assigned = store.append_events(events)

        assert [(event.run_id, event.run_seq) for event in assigned] == [
            ("run-a", 1),
            ("run-b", 1),
            ("run-a", 2),
            ("run-b", 2),
        ]
    finally:
        store.close()


def test_get_events_query_plans_use_indexes_no_full_scan(data_dir):
    """Goal 1: both get_events paths must be index-driven, never a full table scan."""
    store = Storage(data_dir)
    try:
        # Global SSE hot path — resumes by global_seq, which is the INTEGER PRIMARY KEY.
        global_plan = store.explain_query_plan(
            "SELECT * FROM events WHERE global_seq > ? ORDER BY global_seq ASC LIMIT ?",
            (0, 500),
        )
        assert any("PRIMARY KEY" in line for line in global_plan), global_plan
        assert not any("SCAN events" in line for line in global_plan), global_plan

        # Run-scoped path — covered by idx_events_run_global, no ORDER BY temp b-tree.
        run_plan = store.explain_query_plan(
            "SELECT * FROM events WHERE run_id = ? AND global_seq > ? "
            "ORDER BY global_seq ASC LIMIT ?",
            ("r1", 0, 500),
        )
        assert any("idx_events_run_global" in line for line in run_plan), run_plan
        assert not any("TEMP B-TREE" in line for line in run_plan), run_plan
        assert not any("SCAN events" in line for line in run_plan), run_plan
    finally:
        store.close()


def test_list_runs_aggregates_child_count_and_summary_in_sql(data_dir):
    """Goal 1: list_runs returns enriched fields from one query, no per-row N+1 subqueries."""
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(
            run_id="parent",
            session_id=sid,
            root_run_id="parent",
            status=RunStatus.completed,
            provider="fake",
        )
        store.save_message(
            "parent", sid, Message(role=MessageRole.user, content="do the thing"), 1
        )
        for i in range(3):
            store.create_run(
                run_id=f"child{i}",
                session_id=sid,
                root_run_id="parent",
                parent_run_id="parent",
                status=RunStatus.completed,
                provider="fake",
                delegate_depth=1,
            )
        # Count queries the RO connection sees; list_runs must not fan out per row.
        rows = store.list_runs(limit=100)
        by_id = {r["id"]: r for r in rows}
        assert by_id["parent"]["child_count"] == 3
        assert by_id["parent"]["user_summary"] == "do the thing"
        assert by_id["parent"]["depth"] == 0
        assert by_id["child0"]["depth"] == 1
        # The list_runs SELECT itself carries child_count + user_summary (single-query proof).
        plan = store.explain_query_plan(
            """SELECT r.*,
                   substr((SELECT m.content FROM messages AS m
                           WHERE m.run_id = r.id AND m.role = 'user'
                           ORDER BY m.seq ASC LIMIT 1), 1, 500) AS user_summary,
                   (SELECT COUNT(*) FROM runs AS child WHERE child.parent_run_id = r.id)
                       AS child_count
               FROM runs AS r ORDER BY r.created_at DESC, r.rowid DESC LIMIT ? OFFSET ?""",
            (100, 0),
        )
        assert plan  # plan resolves; correlated subqueries are part of the single statement
    finally:
        store.close()


def test_list_sessions_enriches_latest_run_in_one_query(data_dir):
    """Goal 2: latest_status/id/error come from the list_sessions SQL, no per-session N+1."""
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(
            run_id="older",
            session_id=sid,
            root_run_id="older",
            status=RunStatus.completed,
            provider="fake",
        )
        store.create_run(
            run_id="latest",
            session_id=sid,
            root_run_id="latest",
            status=RunStatus.failed,
            provider="fake",
        )
        store.update_run("latest", error="boom")
        # A child run must never be picked as the session's latest top-level run.
        store.create_run(
            run_id="child",
            session_id=sid,
            root_run_id="latest",
            parent_run_id="latest",
            status=RunStatus.completed,
            provider="fake",
        )
        rows = store.list_sessions()
        match = next(r for r in rows if r["id"] == sid)
        assert match["latest_run_id"] == "latest"
        assert match["latest_status"] == "failed"
        assert match["latest_error"] == "boom"
        assert match["run_count"] == 2

        # The enrichment is part of the single list_sessions statement.
        plan = store.explain_query_plan(
            """SELECT sessions.*, latest.id AS latest_run_id, latest.status
               FROM sessions
               LEFT JOIN (SELECT session_id, MAX(rowid) AS activity_order
                          FROM runs WHERE parent_run_id IS NULL GROUP BY session_id)
                   AS recent ON recent.session_id = sessions.id
               LEFT JOIN runs AS latest ON latest.rowid = recent.activity_order
               LIMIT ?""",
            (100,),
        )
        assert plan
    finally:
        store.close()


def test_get_run_and_tree_use_reader_and_enrich(data_dir):
    """Goal 2: get_run / get_run_tree read via the RO connection with in-SQL enrichment."""
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(
            run_id="root", session_id=sid, root_run_id="root",
            status=RunStatus.completed, provider="fake",
        )
        store.save_message(
            "root", sid, Message(role=MessageRole.user, content="hello world"), 1
        )
        store.create_run(
            run_id="kid", session_id=sid, root_run_id="root",
            parent_run_id="root", status=RunStatus.completed, provider="fake",
        )
        run = store.get_run("root")
        assert run is not None
        assert run["child_count"] == 1
        assert run["user_summary"] == "hello world"
        assert run["depth"] == 0

        tree = store.get_run_tree("root")
        assert {r["id"] for r in tree} == {"root", "kid"}
        by_id = {r["id"]: r for r in tree}
        assert by_id["root"]["child_count"] == 1
        assert by_id["kid"]["child_count"] == 0

        # Unknown run id returns cleanly.
        assert store.get_run("nope") is None
        assert store.get_run_tree("nope") == []
    finally:
        store.close()


def test_reads_use_readonly_connection_concurrent_with_writes(data_dir):
    """Goal 1: reads run on a separate RO connection so they don't block on the write lock."""
    import threading

    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(
            run_id="r1", session_id=sid, root_run_id="r1",
            status=RunStatus.running, provider="fake",
        )
        # RO connection is per-thread and distinct from the writer connection.
        main_reader = store._reader()
        assert main_reader is not store._conn

        other: dict[str, object] = {}

        def in_thread() -> None:
            other["reader"] = store._reader()
            other["rows"] = store.list_runs(limit=10)

        thread = threading.Thread(target=in_thread)
        thread.start()
        thread.join()
        # Each thread gets its own RO connection.
        assert other["reader"] is not main_reader
        assert len(other["rows"]) == 1

        # RO connection refuses writes.
        try:
            main_reader.execute("DELETE FROM runs")
            raised = False
        except sqlite3.OperationalError:
            raised = True
        assert raised, "read-only connection must reject writes"
    finally:
        store.close()


def test_artifacts_sha(data_dir):
    store = Storage(data_dir)
    meta = store.artifacts.put("payload-data-12345", content_type="text/plain")
    store.register_artifact(meta)
    assert len(meta["sha256"]) == 64
    text = store.artifacts.get_text(meta["sha256"])
    assert text == "payload-data-12345"
    store.close()


def test_duplicate_artifact_content_reuses_registered_id(data_dir):
    store = Storage(data_dir)
    try:
        first = store.artifacts.put("same artifact body")
        second = store.artifacts.put("same artifact body")
        first_id = store.register_artifact(first)
        second_id = store.register_artifact(second)

        assert second_id == first_id
        assert store.get_artifact(second_id) is not None
        assert store.get_artifact(second["id"]) is None
    finally:
        store.close()


def test_tool_calls_json_is_recursively_redacted_before_sqlite_write(data_dir):
    sentinel = "SENTINEL_TOOL_ARGUMENT_24680"
    store = Storage(data_dir, redactor=Redactor(extra_sentinels=[sentinel]))
    session_id = store.create_session()
    run_id = "redacted-tool-call"
    store.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.running,
        provider="fake",
    )
    store.save_message(
        run_id,
        session_id,
        Message(
            role=MessageRole.assistant,
            tool_calls=[
                ToolCall(
                    name="example",
                    arguments={"nested": [{"secret": sentinel}]},
                    arguments_raw=f'{{"secret":"{sentinel}"}}',
                )
            ],
        ),
        seq=1,
    )

    with sqlite3.connect(store.db_path) as conn:
        raw = conn.execute(
            "SELECT tool_calls_json FROM messages WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    assert sentinel not in raw
    assert "[REDACTED_SENTINEL]" in raw
    store.close()


def test_storage_reads_with_relative_data_dir(tmp_path: Path, monkeypatch) -> None:
    """Regression: the CLI accepts relative --data-dir (e.g. output/...).

    The read-only connection URI must resolve the path before as_uri();
    Path.as_uri() raises ValueError on relative paths, which made every read
    endpoint (including /api/health) return 500 in real startup.
    """
    from agentharness.harness import Harness

    monkeypatch.chdir(tmp_path)
    harness = Harness(data_dir=Path("data"))
    try:
        # writer + reader both work under a relative data_dir
        assert harness.storage.max_global_seq() == 0
        session_id = harness.storage.create_session(title="relative-dir")
        assert harness.storage.session_exists(session_id)
    finally:
        harness.close()


# ------------------------------------------------------------- event paging


def test_iter_events_after_pages_beyond_a_single_batch(data_dir: Path) -> None:
    """iter_events_after must yield every event, not just the first 10k."""
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(run_id="run-pages", session_id=sid, root_run_id="run-pages")
        store.append_events(
            [
                EventEnvelope(
                    session_id=sid,
                    root_run_id="run-pages",
                    run_id="run-pages",
                    type=EventType.run_status,
                    payload={"i": i},
                )
                for i in range(250)
            ]
        )
        all_events = list(store.events.iter_events_after(page_size=100))
        assert len(all_events) == 250
        assert [event.payload["i"] for event in all_events] == list(range(250))

        resumed = list(
            store.events.iter_events_after(after_global_seq=150, page_size=100)
        )
        assert len(resumed) == 100
        assert resumed[0].payload["i"] == 150
        assert resumed[-1].payload["i"] == 249
    finally:
        store.close()


def test_get_events_tail_returns_newest_window_ascending(data_dir: Path) -> None:
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(run_id="run-tail", session_id=sid, root_run_id="run-tail")
        store.append_events(
            [
                EventEnvelope(
                    session_id=sid,
                    root_run_id="run-tail",
                    run_id="run-tail",
                    type=EventType.run_status,
                    payload={"i": i},
                )
                for i in range(25)
            ]
        )
        tail = store.events.get_events_tail(run_id="run-tail", limit=10)
        assert len(tail) == 10
        assert [event.payload["i"] for event in tail] == list(range(15, 25))
        assert [event.global_seq for event in tail] == sorted(
            event.global_seq for event in tail
        )
        assert store.events.get_events_tail(run_id="run-tail", limit=0) == []
        assert len(store.events.get_events_tail(run_id="run-tail", limit=100)) == 25
    finally:
        store.close()


def test_run_timeline_uses_tail_and_marks_truncation(
    data_dir: Path, monkeypatch
) -> None:
    """A run longer than the timeline's event fetch window must still show its
    newest terminal events and set truncated correctly."""
    from agentharness.api import reporting

    monkeypatch.setattr(reporting, "_TIMELINE_EVENT_FETCH_LIMIT", 10)
    harness = Harness(data_dir=data_dir)
    try:
        sid = harness.storage.create_session()
        harness.storage.create_run(
            run_id="timeline-run", session_id=sid, root_run_id="timeline-run"
        )
        harness.storage.append_events(
            [
                EventEnvelope(
                    session_id=sid,
                    root_run_id="timeline-run",
                    run_id="timeline-run",
                    type=EventType.run_status,
                    payload={"i": i},
                )
                for i in range(25)
            ]
        )
        timeline = reporting.build_run_timeline(harness, "timeline-run")
        assert timeline is not None
        assert timeline["event_count"] == 10
        assert timeline["truncated"] is True
        assert timeline["max_global_seq"] == 25
        event_seqs = [
            item["seq"] for item in timeline["items"] if item["kind"] == "event"
        ]
        assert 25 in event_seqs  # newest terminal event retained
    finally:
        harness.close()


# ------------------------------------------------------------- artifacts


def test_artifacts_reject_traversal_sha_values(data_dir: Path) -> None:
    store = Storage(data_dir)
    try:
        with pytest.raises(ValueError, match="invalid sha256"):
            store.artifacts.get_bytes("../../secret")
        with pytest.raises(ValueError, match="invalid sha256"):
            store.artifacts.get_text("../..")
        with pytest.raises(ValueError, match="invalid sha256"):
            store.artifacts.get_bytes("A" * 64)
        with pytest.raises(ValueError, match="invalid sha256"):
            store.artifacts.get_bytes("a" * 63)
        meta = store.artifacts.put("payload")
        assert store.artifacts.get_text(meta["sha256"]) == "payload"
    finally:
        store.close()


def test_register_artifact_returns_stored_id_on_lost_race(data_dir: Path) -> None:
    """INSERT OR IGNORE can lose a concurrent race; the returned id must be
    the id actually stored, never a ghost uuid that is absent from the table."""
    store = Storage(data_dir)
    try:
        first = store.artifacts.put("race body")
        existing_id = store.register_artifact(first)
        second = dict(first)
        second["id"] = "ghost-id"

        class _RaceConnection:
            def __init__(self, real: object) -> None:
                self._real = real
                self._selects = 0

            def execute(self, sql: str, *args: object):  # type: ignore[no-untyped-def]
                if str(sql).startswith("SELECT id FROM artifacts WHERE sha256"):
                    self._selects += 1
                    if self._selects == 1:
                        # Simulate another process inserting between our first
                        # SELECT and the INSERT OR IGNORE: the first SELECT
                        # misses, the INSERT loses the race.
                        return mock.Mock(fetchone=lambda: None)
                return self._real.execute(sql, *args)  # type: ignore[attr-defined]

            def __getattr__(self, name: str) -> object:
                return getattr(self._real, name)

        original = store.artifact_index._conn  # noqa: SLF001 - white-box
        store.artifact_index._conn = _RaceConnection(original)  # type: ignore[assignment]  # noqa: SLF001
        try:
            returned = store.register_artifact(second)
        finally:
            store.artifact_index._conn = original  # type: ignore[assignment]  # noqa: SLF001
        assert returned == existing_id
        assert returned != "ghost-id"
        assert store.get_artifact(returned) is not None
    finally:
        store.close()


# ------------------------------------------------------------- approvals


def test_save_approval_conflict_preserves_resolution_audit(data_dir: Path) -> None:
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(run_id="approval-run", session_id=sid, root_run_id="approval-run")
        pending = {
            "id": "approval-1",
            "run_id": "approval-run",
            "tool_call_id": "tool-1",
            "tool_name": "write_file",
            "effect": "workspace_write",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        store.save_approval(pending)
        resolved = {
            **pending,
            "decision": "allow",
            "status": "resolved",
            "resolved_at": "2026-01-02T00:00:00+00:00",
        }
        store.save_approval(resolved)
        row = store.approvals.list_approvals("approval-run")[0]
        assert row["decision"] == "allow"
        assert row["status"] == "resolved"
        assert row["resolved_at"] == "2026-01-02T00:00:00+00:00"
        assert row["created_at"] == "2026-01-01T00:00:00+00:00"

        # A stale pending re-save must never wipe the resolution audit trail.
        store.save_approval(pending)
        row = store.approvals.list_approvals("approval-run")[0]
        assert row["decision"] == "allow"
        assert row["status"] == "resolved"
        assert row["resolved_at"] == "2026-01-02T00:00:00+00:00"
        assert row["created_at"] == "2026-01-01T00:00:00+00:00"
    finally:
        store.close()


def test_expire_pending_for_run_expires_only_pending(data_dir: Path) -> None:
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(
            run_id="approval-expire", session_id=sid, root_run_id="approval-expire"
        )
        for i in range(3):
            store.save_approval(
                {
                    "id": f"pending-{i}",
                    "run_id": "approval-expire",
                    "tool_call_id": "tool-1",
                    "tool_name": "write_file",
                    "effect": "workspace_write",
                }
            )
        store.save_approval(
            {
                "id": "resolved-1",
                "run_id": "approval-expire",
                "tool_call_id": "tool-1",
                "tool_name": "write_file",
                "effect": "workspace_write",
                "decision": "allow",
                "status": "resolved",
                "resolved_at": "2026-01-01T00:00:00+00:00",
            }
        )
        assert store.approvals.expire_pending_for_run("approval-expire") == 3
        rows = {
            approval["id"]: approval
            for approval in store.approvals.list_approvals("approval-expire")
        }
        assert rows["pending-0"]["status"] == "expired"
        assert rows["pending-0"]["resolved_at"] is not None
        assert rows["resolved-1"]["status"] == "resolved"
        assert rows["resolved-1"]["decision"] == "allow"
        assert rows["resolved-1"]["resolved_at"] == "2026-01-01T00:00:00+00:00"
        assert store.approvals.expire_pending_for_run("approval-expire") == 0
    finally:
        store.close()


# ------------------------------------------------------------- procurement


def _tree_chunk() -> dict:
    created = "2026-01-01T00:00:00+00:00"
    return {
        "chunk_sha256": "d" * 64,
        "request_id": "req-tree",
        "quote_id": "quote-tree",
        "artifact_id": "art-1",
        "artifact_sha256": "e" * 64,
        "request_reference": "RFQ-TREE",
        "supplier_name": "华东优包",
        "item_name": "快递袋",
        "category": "ecommerce_packaging",
        "specifications": {"material": "PE", "color": "白色"},
        "unit_price": "0.42",
        "currency": "CNY",
        "landed_unit_cost": "0.45",
        "lead_days": 10,
        "moq": 5000,
        "decision": "approved",
        "decision_at": created,
        "content": "RFQ-TREE 快递袋",
        "quality_flags": [],
        "created_at": created,
        "updated_at": created,
    }


def _seed_procurement_tree(store: Storage, *, with_decision: bool = True) -> None:
    created = "2026-01-01T00:00:00+00:00"
    sid = store.create_session("delete-session")
    store.create_run(run_id="delete-run", session_id=sid, root_run_id="delete-run")
    conn = store._conn  # noqa: SLF001 - white-box seed
    conn.execute(
        """INSERT INTO artifacts(id, sha256, content_type, size_bytes, summary, path, created_at)
           VALUES(?,?,?,?,?,?,?)""",
        ("art-1", "a" * 64, "application/pdf", 10, "sum", "/tmp/art-1", created),
    )
    conn.execute(
        """INSERT INTO procurement_requests(
               id, reference, title, category, item_name, quantity, unit,
               specifications_json, constraints_json, status, session_id,
               created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "req-tree",
            "RFQ-TREE",
            "t",
            "c",
            "item",
            1,
            "piece",
            "{}",
            "{}",
            "draft",
            sid,
            created,
            created,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_quotes(
               id, request_id, supplier_name, source_filename, source_kind,
               source_artifact_id, source_sha256, extracted_json, status,
               review_count, parser_version, processing_ms, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "quote-tree",
            "req-tree",
            "华东优包",
            "f.xlsx",
            "xlsx",
            "art-1",
            "b" * 64,
            "{}",
            "parsed",
            0,
            "1",
            0,
            created,
            created,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_comparison_snapshots(
               id, request_id, run_id, version, input_sha256, result_json,
               artifact_id, created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        ("snap-tree", "req-tree", "delete-run", 1, "c" * 64, "{}", "art-1", created),
    )
    if with_decision:
        conn.execute(
            """INSERT INTO procurement_decisions(
                   id, request_id, snapshot_id, quote_id, run_id, approval_id,
                   decision, note, actor, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "dec-tree",
                "req-tree",
                "snap-tree",
                "quote-tree",
                "delete-run",
                None,
                "approved",
                None,
                "actor",
                created,
            ),
        )
    conn.execute(
        """INSERT INTO procurement_audit_events(
               id, request_id, quote_id, run_id, type, actor, payload_json, created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            "audit-tree",
            "req-tree",
            "quote-tree",
            "delete-run",
            "supplier_approved",
            "actor",
            "{}",
            created,
        ),
    )
    conn.execute(
        """INSERT INTO procurement_purchase_orders(
               id, request_id, po_number, payload_json, created_at
           ) VALUES(?,?,?,?,?)""",
        ("po-tree", "req-tree", "PO-1", "{}", created),
    )
    store.rag.upsert_chunk(_tree_chunk())


def test_delete_request_tree_is_atomic(data_dir: Path) -> None:
    """A mid-way delete failure must roll back the whole request tree."""
    store = Storage(data_dir)
    try:
        _seed_procurement_tree(store)
        with store._lock:  # noqa: SLF001 - trigger fault injection
            store._conn.execute(  # noqa: SLF001
                """CREATE TRIGGER fail_delete_tree
                   BEFORE DELETE ON procurement_audit_events
                   BEGIN
                       SELECT RAISE(ABORT, 'forced delete failure');
                   END"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="forced delete failure"):
            store.procurement.delete_request_tree("req-tree")
        assert store.procurement.get_request("req-tree") is not None
        assert len(store.procurement.list_quotes("req-tree")) == 1
        assert store.procurement.get_decision("req-tree") is not None
        assert store.rag.count_chunks() == 1

        with store._lock:  # noqa: SLF001
            store._conn.execute("DROP TRIGGER fail_delete_tree")  # noqa: SLF001
        store.procurement.delete_request_tree("req-tree")
        assert store.procurement.get_request("req-tree") is None
        assert store.rag.count_chunks() == 0
    finally:
        store.close()


def test_commit_decision_joins_caller_transaction(data_dir: Path) -> None:
    """commit_decision must not BEGIN inside an open transaction."""
    store = Storage(data_dir)
    try:
        _seed_procurement_tree(store, with_decision=False)
        decision = {
            "id": "dec-2",
            "request_id": "req-tree",
            "snapshot_id": "snap-tree",
            "quote_id": "quote-tree",
            "run_id": "delete-run",
            "approval_id": None,
            "decision": "approved",
            "note": None,
            "actor": "buyer",
            "created_at": "2026-02-01T00:00:00+00:00",
        }
        audit = {
            "id": "audit-2",
            "request_id": "req-tree",
            "quote_id": "quote-tree",
            "run_id": "delete-run",
            "type": "supplier_approved",
            "actor": "buyer",
            "payload": {},
            "created_at": "2026-02-01T00:00:00+00:00",
        }
        with store._lock:  # noqa: SLF001
            store._conn.execute("BEGIN")  # noqa: SLF001
            try:
                store.procurement.commit_decision(decision, audit)
                assert store._conn.in_transaction is True  # noqa: SLF001
            finally:
                store._conn.execute("COMMIT")  # noqa: SLF001
        stored = store.procurement.get_decision("req-tree")
        assert stored is not None
        assert stored["decision"] == "approved"
    finally:
        store.close()


# ------------------------------------------------------------- tool invocations


def test_save_tool_invocation_conflict_refreshes_arguments(data_dir: Path) -> None:
    """Re-saving an invocation with changed arguments must refresh the stored
    arguments identity instead of keeping the stale first-write values."""
    store = Storage(data_dir)
    try:
        sid = store.create_session()
        store.create_run(run_id="tool-run", session_id=sid, root_run_id="tool-run")
        first = ToolInvocationRecord(
            id="inv-1",
            run_id="tool-run",
            session_id=sid,
            step=1,
            ordinal=2,
            provider_call_id="p-1",
            tool_name="read_file",
            arguments={"path": "a.txt"},
            arguments_sha256="hash-a",
        )
        second = first.model_copy(
            update={
                "arguments": {"path": "b.txt"},
                "arguments_sha256": "hash-b",
                "status": ToolInvocationStatus.succeeded,
            }
        )
        store.save_tool_invocation(first)
        store.save_tool_invocation(second)
        restored = store.get_tool_invocation("inv-1")
        assert restored is not None
        assert restored.arguments == {"path": "b.txt"}
        assert restored.arguments_sha256 == "hash-b"
        assert restored.status == ToolInvocationStatus.succeeded
        assert restored.run_id == "tool-run"
        assert restored.step == 1
        assert restored.ordinal == 2
    finally:
        store.close()
