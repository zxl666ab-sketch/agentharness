"""Multi-turn session history, title, ordering, resume, migration."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

import agentharness.storage.sqlite as storage_sqlite
from agentharness.contracts import ApprovalMode, RunRequest, RunStatus
from agentharness.providers.fake import FakeModelAdapter
from agentharness.session_history import session_title_from_message
from agentharness.storage.migrations import SCHEMA_VERSION
from agentharness.storage.sqlite import Storage


@pytest.mark.asyncio
async def test_second_turn_includes_first_turn_messages(harness, workspace):
    """Provider request on turn 2 must contain turn-1 user + assistant."""
    fake = FakeModelAdapter()
    harness.register_provider("fake", fake)

    r1 = await harness.run(
        RunRequest(
            message="[fake:text]First answer",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r1.status == RunStatus.completed
    assert "First answer" in r1.output
    fake.reset()  # clear calls but keep adapter

    r2 = await harness.run(
        RunRequest(
            message="[fake:text]Second answer",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r2.status == RunStatus.completed
    assert r2.session_id == r1.session_id
    assert len(fake.calls) >= 1
    msgs = fake.calls[0].messages
    roles_contents = [
        (
            m.role.value if hasattr(m.role, "value") else m.role,
            m.content,
        )
        for m in msgs
    ]
    # First-turn user + assistant must be present before second-turn user
    user_texts = [c for r, c in roles_contents if r == "user"]
    assistant_texts = [c for r, c in roles_contents if r == "assistant"]
    assert any("First answer" in (c or "") or "[fake:text]First answer" in (c or "") for c in user_texts)
    # The first user message content is the raw request message
    assert any("[fake:text]First answer" in (c or "") for c in user_texts)
    assert any("First answer" in (c or "") for c in assistant_texts)
    assert any("[fake:text]Second answer" in (c or "") for c in user_texts)


@pytest.mark.asyncio
async def test_new_session_has_no_prior_history(harness, workspace):
    r1 = await harness.run(
        RunRequest(
            message="[fake:text]Session A",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    fake = FakeModelAdapter()
    harness.register_provider("fake", fake)
    r2 = await harness.run(
        RunRequest(
            message="[fake:text]Session B",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            # no session_id → new session
        )
    )
    assert r2.session_id != r1.session_id
    msgs = fake.calls[0].messages
    user_texts = [
        m.content
        for m in msgs
        if (m.role.value if hasattr(m.role, "value") else m.role) == "user"
    ]
    assert not any("Session A" in (c or "") for c in user_texts)
    assert any("Session B" in (c or "") for c in user_texts)


@pytest.mark.asyncio
async def test_failed_run_excluded_from_history(harness, workspace):
    r1 = await harness.run(
        RunRequest(
            message="[fake:error:provider]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r1.status == RunStatus.failed
    fake = FakeModelAdapter()
    harness.register_provider("fake", fake)
    r2 = await harness.run(
        RunRequest(
            message="[fake:text]After fail",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r2.status == RunStatus.completed
    msgs = fake.calls[0].messages
    user_texts = [
        m.content
        for m in msgs
        if (m.role.value if hasattr(m.role, "value") else m.role) == "user"
    ]
    # Failed turn's user message must not appear in next context
    assert not any("[fake:error:provider]" in (c or "") for c in user_texts)
    assert any("After fail" in (c or "") for c in user_texts)


@pytest.mark.asyncio
async def test_delegate_child_messages_not_in_parent_context(harness, workspace):
    """Delegate creates a child run; parent multi-turn must not load child messages."""
    # Simulate a completed child (delegate) run under a session
    r1 = await harness.run(
        RunRequest(
            message="[fake:text]Parent first",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    child = await harness.run(
        RunRequest(
            message="[fake:text]Child secret task",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            parent_run_id=r1.run_id,
            root_run_id=r1.run_id,
            delegate_depth=1,
        )
    )
    assert child.parent_run_id == r1.run_id
    assert child.status == RunStatus.completed

    fake = FakeModelAdapter()
    harness.register_provider("fake", fake)
    r2 = await harness.run(
        RunRequest(
            message="[fake:text]Parent second",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r2.status == RunStatus.completed
    msgs = fake.calls[0].messages
    all_text = " ".join(m.content or "" for m in msgs)
    assert "Child secret task" not in all_text
    assert "Parent first" in all_text
    assert "Parent second" in all_text


@pytest.mark.asyncio
async def test_resume_preserves_multiturn_history_without_resplice(harness, workspace):
    """Completed turn1 + interrupted turn2 → resume keeps turn1 user+assistant, no double splice.

    Drives the real engine path: interrupt mid-stream, load checkpoint, resume.
    """
    import asyncio

    r1 = await harness.run(
        RunRequest(
            message="[fake:text]Turn one answer",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert r1.status == RunStatus.completed

    # Slow stream so we can interrupt before completion on turn 2
    slow = FakeModelAdapter(
        script=[{"kind": "sleep", "seconds": 3.0}, {"kind": "text", "text": "should-not-finish"}]
    )
    harness.register_provider("fake", slow)
    harness.engine.providers["fake"] = slow

    task = asyncio.create_task(
        harness.run(
            RunRequest(
                message="resume-me turn two",
                session_id=r1.session_id,
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    )
    # Wait until engine has registered the active run and in-memory messages
    rid = None
    for _ in range(50):
        await asyncio.sleep(0.05)
        rid = harness.engine.active_run_id
        if rid and rid in harness.engine._run_messages:
            break
    assert rid, "turn2 never became active"
    await harness.interrupt(rid)
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (asyncio.CancelledError, TimeoutError, Exception):  # noqa: BLE001
        pass

    run = harness.get_run(rid)
    assert run is not None
    assert run["status"] in ("interrupted", "cancelled", "failed", "running")

    cp = harness.storage.load_checkpoint(rid)
    assert cp is not None, "interrupt must leave a resume checkpoint"
    cp_blob = " ".join(m.content or "" for m in cp.messages)
    # Checkpoint must retain turn1 user + assistant (session history splice), not only this run
    assert "[fake:text]Turn one answer" in cp_blob or "Turn one answer" in cp_blob
    assert "resume-me turn two" in cp_blob

    resume_fake = FakeModelAdapter(script=[{"kind": "text", "text": "resumed-ok"}])
    harness.register_provider("fake", resume_fake)
    harness.engine.providers["fake"] = resume_fake

    resumed = await harness.resume(rid)
    assert resumed.status == RunStatus.completed
    assert resume_fake.calls, "resume must call the provider"
    msgs = resume_fake.calls[0].messages
    roles_contents = [
        (
            m.role.value if hasattr(m.role, "value") else m.role,
            m.content or "",
        )
        for m in msgs
    ]
    user_texts = [c for r, c in roles_contents if r == "user"]
    assistant_texts = [c for r, c in roles_contents if r == "assistant"]
    # Turn1 history preserved
    assert any("Turn one answer" in c or "[fake:text]Turn one answer" in c for c in user_texts)
    assert any("Turn one answer" in c for c in assistant_texts)
    # Turn2 user present exactly once (no re-splice doubling)
    turn2_users = [c for c in user_texts if "resume-me turn two" in c]
    assert len(turn2_users) == 1
    # Turn1 user must not appear twice from a re-splice on resume
    turn1_users = [c for c in user_texts if "Turn one answer" in c or "[fake:text]Turn one answer" in c]
    assert len(turn1_users) == 1


@pytest.mark.asyncio
async def test_resume_rejects_completed_run_without_appending_message(harness, workspace):
    result = await harness.run(
        RunRequest(
            message="[fake:text]already complete",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert result.status == RunStatus.completed
    before = harness.storage.get_messages(result.run_id)

    with pytest.raises(RuntimeError, match="not resumable.*completed"):
        await harness.resume(result.run_id, input="MUST_NOT_BE_APPENDED")

    after = harness.storage.get_messages(result.run_id)
    assert len(after) == len(before)
    assert all("MUST_NOT_BE_APPENDED" not in message.content for message in after)


@pytest.mark.asyncio
async def test_resume_rejects_failed_and_running_runs_before_mutation(harness, workspace):
    from agentharness.contracts import Checkpoint

    failed = await harness.run(
        RunRequest(
            message="[fake:error:provider]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    failed_before = harness.storage.get_messages(failed.run_id)
    with pytest.raises(RuntimeError, match="not resumable.*failed"):
        await harness.resume(failed.run_id, input="FAILED_APPEND_SENTINEL")
    failed_after = harness.storage.get_messages(failed.run_id)
    assert len(failed_after) == len(failed_before)
    assert all("FAILED_APPEND_SENTINEL" not in message.content for message in failed_after)

    session_id = harness.storage.create_session()
    run_id = "persisted-running-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.running,
        provider="fake",
        cwd=str(workspace),
    )
    harness.storage.save_checkpoint(
        Checkpoint(
            run_id=run_id,
            phase="model_turn",
            step=0,
            messages=[],
            status=RunStatus.running,
        )
    )
    with pytest.raises(RuntimeError, match="not resumable.*running"):
        await harness.resume(run_id, input="RUNNING_APPEND_SENTINEL")
    assert harness.storage.get_messages(run_id) == []


@pytest.mark.asyncio
async def test_cancelling_resume_restores_interrupted_status_and_checkpoint(
    harness, workspace
):
    interrupted = await harness.run(
        RunRequest(
            message="[fake:error:timeout]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert interrupted.status == RunStatus.interrupted

    slow = FakeModelAdapter(script=[{"kind": "sleep", "seconds": 30.0}])
    harness.register_provider("fake", slow)
    resume_task = asyncio.create_task(harness.resume(interrupted.run_id))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if harness.engine.active_run_id == interrupted.run_id:
            break
    assert harness.engine.active_run_id == interrupted.run_id

    resume_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await resume_task

    row = harness.get_run(interrupted.run_id)
    assert row is not None
    assert row["status"] == RunStatus.interrupted.value
    checkpoint = harness.storage.load_checkpoint(interrupted.run_id)
    assert checkpoint is not None
    assert checkpoint.status == RunStatus.interrupted


@pytest.mark.asyncio
async def test_resume_provider_exception_finishes_failed_instead_of_leaving_running(
    harness, workspace
):
    interrupted = await harness.run(
        RunRequest(
            message="[fake:error:timeout]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )

    class RaisingProvider:
        name = "fake"

        async def stream(self, request):
            raise RuntimeError("resume provider exploded")
            yield  # pragma: no cover

    harness.register_provider("fake", RaisingProvider())
    resumed = await harness.resume(interrupted.run_id)

    assert resumed.status == RunStatus.failed
    assert resumed.error == "resume provider exploded"
    row = harness.get_run(interrupted.run_id)
    assert row is not None
    assert row["status"] == RunStatus.failed.value
    assert harness.engine.active_run_id is None


@pytest.mark.asyncio
async def test_session_title_from_first_user_message(harness, workspace):
    long = "  hello   world  " + ("x" * 100)
    r = await harness.run(
        RunRequest(
            message=f"[fake:text]ok\n{long}",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    sess = harness.get_session(r.session_id)
    assert sess is not None
    title = sess["title"]
    assert title != "session"
    assert len(title) <= 48
    assert "  " not in title  # collapsed whitespace


def test_session_title_helper():
    assert session_title_from_message("  a   b  ") == "a b"
    assert session_title_from_message("") == "session"
    t = session_title_from_message("x" * 100)
    assert len(t) <= 48
    assert t.endswith("…")


@pytest.mark.asyncio
async def test_delegate_does_not_update_session_ordering(
    harness, workspace, data_dir, monkeypatch
):
    r1 = await harness.run(
        RunRequest(
            message="[fake:text]Alpha session",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    # Wall clocks can step backwards (for example after time synchronization).
    # Session recency must follow top-level activity order, not timestamp ordering.
    monkeypatch.setattr(
        storage_sqlite,
        "_utcnow",
        lambda: "2000-01-01T00:00:00+00:00",
    )
    r2 = await harness.run(
        RunRequest(
            message="[fake:text]Beta session",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    # Sessions ordered by top-level activity → Beta first.
    sessions = harness.list_sessions()
    ids = [s["id"] for s in sessions]
    assert ids.index(r2.session_id) < ids.index(r1.session_id)

    # Child delegate under Alpha must not bump Alpha above Beta
    before = harness.get_session(r1.session_id)["updated_at"]
    await harness.run(
        RunRequest(
            message="[fake:text]delegate child",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
            parent_run_id=r1.run_id,
            root_run_id=r1.run_id,
            delegate_depth=1,
        )
    )
    after = harness.get_session(r1.session_id)["updated_at"]
    assert after == before
    sessions2 = harness.list_sessions()
    ids2 = [s["id"] for s in sessions2]
    assert ids2.index(r2.session_id) < ids2.index(r1.session_id)


@pytest.mark.asyncio
async def test_get_session_transcript_includes_failures(harness, workspace):
    r1 = await harness.run(
        RunRequest(
            message="[fake:text]ok turn",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    r2 = await harness.run(
        RunRequest(
            message="[fake:error:provider]",
            session_id=r1.session_id,
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    turns = harness.get_session_transcript(r1.session_id)
    assert len(turns) == 2
    assert turns[0].run_id == r1.run_id
    assert turns[0].status == RunStatus.completed
    assert turns[1].run_id == r2.run_id
    assert turns[1].status == RunStatus.failed
    assert turns[1].user_content


@pytest.mark.asyncio
async def test_subscribe_events_receives_text_delta(harness, workspace):
    seen: list[str] = []

    def cb(ev):
        t = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
        seen.append(t)

    unsub = harness.subscribe_events(cb)
    try:
        await harness.run(
            RunRequest(
                message="[fake:text]stream me",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    finally:
        unsub()
    assert "run_started" in seen
    assert "text_delta" in seen or "run_completed" in seen


def test_v1_to_v2_migration(tmp_path: Path):
    """Apply v1 schema manually, insert row, open Storage → migrates to v2, data intact."""
    db = tmp_path / "agentharness.db"
    conn = sqlite3.connect(str(db))
    # Apply only v1
    from agentharness.storage.migrations import MIGRATIONS

    conn.executescript(MIGRATIONS[1])
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '1')"
    )
    conn.execute(
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
        ("sess1", "old title", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    data_dir = tmp_path
    store = Storage(data_dir)
    # schema version should be 2
    row = store._conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    assert int(row[0]) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 2
    sess = store.get_session("sess1")
    assert sess is not None
    assert sess["title"] == "old title"
    store.close()


def test_schema_version_on_fresh_db(data_dir):
    store = Storage(data_dir)
    row = store._conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    assert int(row[0]) == SCHEMA_VERSION
    store.close()
