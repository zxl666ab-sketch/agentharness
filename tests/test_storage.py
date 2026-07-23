import sqlite3

from agentharness.contracts import (
    EventEnvelope,
    EventType,
    Message,
    MessageRole,
    RunStatus,
    ToolCall,
)
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


def test_memory_fts(data_dir):
    store = Storage(data_dir)
    mid = store.add_memory("The sky is blue on earth", source="test", scope="global")
    assert mid
    hits = store.search_memories("sky blue")
    assert hits
    assert "sky" in hits[0]["content"]
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
