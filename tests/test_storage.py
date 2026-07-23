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
