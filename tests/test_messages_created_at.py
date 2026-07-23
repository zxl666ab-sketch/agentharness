from pathlib import Path
from datetime import datetime, timezone

from agentharness.contracts import Message, MessageRole, RunStatus
from agentharness.storage.sqlite import Storage


def test_get_messages_preserves_created_at(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    try:
        sid = storage.create_session("t")
        run_id = "run-created-at"
        storage.create_run(
            run_id=run_id,
            session_id=sid,
            root_run_id=run_id,
            status=RunStatus.completed,
            provider="fake",
            model="m",
            approval="auto",
            cwd=str(tmp_path),
        )
        stamped = datetime(2026, 7, 23, 4, 36, 27, tzinfo=timezone.utc)
        storage.save_message(
            run_id,
            sid,
            Message(role=MessageRole.user, content="hello", created_at=stamped),
            seq=0,
        )
        messages = storage.get_messages(run_id)
        assert len(messages) == 1
        assert messages[0].created_at is not None
        assert messages[0].created_at.year == 2026
        assert messages[0].created_at.month == 7
        assert messages[0].created_at.day == 23
        assert messages[0].created_at.hour == 4
    finally:
        storage.close()
