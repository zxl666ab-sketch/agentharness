"""`build_run_report` must cover the whole event log, not one 10k window (P-L16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.api import reporting
from agentharness.contracts import EventEnvelope, EventType, RunStatus
from agentharness.storage.sqlite import Storage


def _seed_run(storage: Storage, run_id: str, event_count: int) -> None:
    session_id = storage.create_session(title="report")
    storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=RunStatus.completed,
    )
    storage.append_events(
        [
            EventEnvelope(
                session_id=session_id,
                root_run_id=run_id,
                run_id=run_id,
                type=EventType.run_status,
                payload={"index": index},
            )
            for index in range(event_count)
        ]
    )


def test_report_pages_beyond_one_event_window(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "_EVENT_PAGE_SIZE", 4)
    storage = Storage(data_dir)
    try:
        _seed_run(storage, "paged-run", 11)
    finally:
        storage.close()

    from agentharness.harness import Harness

    runtime = Harness(data_dir=data_dir)
    try:
        report = reporting.build_run_report(runtime, "paged-run")
        assert report is not None
        assert report["source"]["event_count"] == 11
        assert report["source"]["events_truncated"] is False
        assert [event["payload"]["index"] for event in report["events"]] == list(range(11))
        # 证据指纹覆盖全集：翻两倍页也必须是同一个哈希
        monkeypatch.setattr(reporting, "_EVENT_PAGE_SIZE", 1)
        replayed = reporting.build_run_report(runtime, "paged-run")
        assert replayed is not None
        assert replayed["evidence_sha256"] == report["evidence_sha256"]
    finally:
        runtime.close()


def test_report_flags_a_hard_capped_event_set(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reporting, "_EVENT_PAGE_SIZE", 2)
    monkeypatch.setattr(reporting, "_EVENT_HARD_CAP", 6)
    storage = Storage(data_dir)
    try:
        _seed_run(storage, "capped-run", 9)
    finally:
        storage.close()

    from agentharness.harness import Harness

    runtime = Harness(data_dir=data_dir)
    try:
        report = reporting.build_run_report(runtime, "capped-run")
        assert report is not None
        assert report["source"]["event_count"] == 6
        assert report["source"]["events_truncated"] is True
    finally:
        runtime.close()
