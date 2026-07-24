from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentharness.contracts import ApprovalMode, EventEnvelope, Message, MessageRole, RunRequest
from agentharness.eval.contracts import AgentTrace
from agentharness.trace import TraceProjector


@pytest.mark.asyncio
async def test_projects_real_run_into_complete_canonical_trace(
    harness, workspace: Path
) -> None:
    result = await harness.run(
        RunRequest(
            message='[fake:tools]read_file\n{"path":"a.txt"}',
            provider="fake",
            cwd=str(workspace),
            approval=ApprovalMode.auto,
        )
    )

    trace = TraceProjector(harness.storage, redactor=harness.redactor).project(result.run_id)

    assert isinstance(trace, AgentTrace)
    assert trace.schema_version == 2
    assert trace.run_id == result.run_id
    assert trace.completeness == "complete"
    assert trace.status == "completed"
    assert trace.provider == "fake"
    assert trace.messages[0].role == MessageRole.user
    assert trace.final_output
    assert trace.usage.model_turns == 2
    assert trace.versions.prompt_fingerprint
    assert trace.versions.tool_schema_fingerprints["read_file"]

    model_spans = [span for span in trace.spans if span.kind == "model"]
    tool_spans = [span for span in trace.spans if span.kind == "tool"]
    assert len(model_spans) == 2
    assert len(tool_spans) == 1
    tool = tool_spans[0]
    assert tool.parent_span_id == model_spans[0].span_id
    assert tool.tool_name == "read_file"
    assert tool.tool_arguments == {"path": "a.txt"}
    assert tool.tool_result is not None
    assert tool.tool_result.is_error is False
    assert "alpha" in tool.tool_result.content
    assert tool.duration_ms is not None
    assert tool.event_ids


def test_projection_sorts_events_and_marks_legacy_missing_boundaries_partial(tmp_path: Path) -> None:
    from agentharness.storage.sqlite import Storage

    storage = Storage(tmp_path / "data")
    try:
        storage.create_session("s")
        storage.create_run(
            run_id="r",
            session_id="s",
            root_run_id="r",
            provider="fake",
            model="m",
        )
        now = datetime.now(UTC)
        events = [
            EventEnvelope(
                schema_version=0,
                event_id="late",
                global_seq=2,
                run_seq=2,
                session_id="s",
                root_run_id="r",
                run_id="r",
                span_id="model-1",
                type="model_turn_end",
                timestamp=now + timedelta(seconds=1),
                payload={"step": 0, "usage": {"input_tokens": 1, "output_tokens": 1}},
            ),
            EventEnvelope(
                schema_version=0,
                event_id="early",
                global_seq=1,
                run_seq=1,
                session_id="s",
                root_run_id="r",
                run_id="r",
                span_id="model-1",
                type="model_turn_start",
                timestamp=now,
                payload={"step": 0},
            ),
        ]
        trace = TraceProjector.from_records(
            run={
                "id": "r",
                "session_id": "s",
                "root_run_id": "r",
                "status": "interrupted",
                "provider": "fake",
                "model": "m",
                "created_at": now.isoformat(),
                "finished_at": (now + timedelta(seconds=1)).isoformat(),
                "usage_json": "{}",
                "metadata_json": "{}",
            },
            messages=[Message(role=MessageRole.user, content="hello")],
            events=events,
            redactor=storage.redactor,
        )
        assert trace.completeness == "partial"
        assert "legacy_event_schema" in trace.partial_reasons
        assert "missing_run_started" in trace.partial_reasons
        assert "missing_terminal_event" in trace.partial_reasons
        assert trace.spans[0].event_ids == ["early", "late"]
        assert trace.spans[0].status == "interrupted"
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_projection_redacts_secrets_and_persists_content_addressed_artifact(
    harness,
) -> None:
    secret = "sk-" + ("Z" * 30)
    result = await harness.run(
        RunRequest(message=f"[fake:text]token {secret}", provider="fake")
    )
    projector = TraceProjector(harness.storage, redactor=harness.redactor)
    trace = projector.project(result.run_id)
    artifact = projector.persist(trace)

    payload = trace.model_dump_json()
    stored = harness.storage.get_artifact(artifact.artifact_id)
    assert secret not in payload
    assert "REDACTED" in payload
    assert stored is not None
    assert stored["sha256"] == artifact.sha256
    assert harness.storage.artifacts.get_text(artifact.sha256) is not None


def test_projection_preserves_orphan_parent_reference_as_partial(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    event = EventEnvelope(
        schema_version=2,
        event_id="tool-start",
        global_seq=1,
        run_seq=1,
        session_id="s",
        root_run_id="r",
        run_id="r",
        span_id="tool",
        parent_span_id="missing-model",
        type="span_start",
        timestamp=now,
        payload={"kind": "tool", "name": "read_file", "tool_call_id": "call"},
    )
    trace = TraceProjector.from_records(
        run={
            "id": "r",
            "session_id": "s",
            "root_run_id": "r",
            "status": "interrupted",
            "created_at": now.isoformat(),
            "usage_json": "{}",
            "metadata_json": "{}",
        },
        messages=[],
        events=[event],
    )
    assert trace.spans[0].parent_span_id == "missing-model"
    assert "orphan_parent_span" in trace.partial_reasons
