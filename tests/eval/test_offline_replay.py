from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.eval.contracts import EvaluationPolicy, ReplaySnapshot
from agentharness.eval.replay import OfflineReplay, SnapshotStore


@pytest.mark.asyncio
async def test_snapshot_is_immutable_redacted_and_content_addressed(
    harness, workspace: Path
) -> None:
    secret = "sk-" + ("R" * 30)
    result = await harness.run(
        RunRequest(
            message=f'[fake:tools]read_file\n{{"path":"a.txt","token":"{secret}"}}',
            provider="fake",
            model="fake-v1",
            cwd=str(workspace),
            approval=ApprovalMode.auto,
            metadata={"seed": 7, "temperature": 0.2, "provider_parameters": {"top_p": 0.9}},
        )
    )
    store = SnapshotStore(harness.storage, redactor=harness.redactor)
    snapshot, artifact = store.capture(result.run_id, evaluation_policy_version="policy-v1")

    assert snapshot.schema_version == 2
    assert snapshot.snapshot_id == artifact.sha256
    assert snapshot.provider == "fake"
    assert snapshot.model == "fake-v1"
    assert snapshot.prompt_fingerprint
    assert snapshot.tool_schema_fingerprints["read_file"]
    assert snapshot.runtime_config_fingerprint
    assert snapshot.evaluation_policy_version == "policy-v1"
    assert snapshot.seed == 7
    assert snapshot.temperature == 0.2
    assert snapshot.provider_parameters == {"top_p": 0.9}
    assert secret not in snapshot.model_dump_json()
    assert "REDACTED" in snapshot.model_dump_json()

    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"provider": "mutated"}, deep=True).provider = "other"  # type: ignore[misc]

    loaded = store.load(snapshot.snapshot_id)
    assert loaded == snapshot


@pytest.mark.asyncio
async def test_same_snapshot_replays_multiple_policy_versions_without_side_effects(
    harness, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]offline answer",
            provider="fake",
            cwd=str(workspace),
        )
    )
    snapshot, _artifact = SnapshotStore(harness.storage).capture(result.run_id)
    before_files = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
    provider_calls = len(harness.providers["fake"].calls)

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("offline replay attempted a forbidden side effect")

    monkeypatch.setattr(harness, "run", forbidden)
    monkeypatch.setattr("httpx.request", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)

    replay = OfflineReplay()
    policy_v1 = EvaluationPolicy(
        policy_id="p",
        version="1",
        output_contains=["offline"],
    )
    policy_v2 = EvaluationPolicy(
        policy_id="p",
        version="2",
        output_contains=["missing"],
    )
    first = replay.evaluate(snapshot, policy_v1)
    second = replay.evaluate(snapshot, policy_v2)
    again = replay.evaluate(snapshot, policy_v1)

    assert first.passed is True
    assert second.passed is False
    assert first.model_dump_json() == again.model_dump_json(exclude={"report_id", "evaluated_at"}) or (
        first.checks == again.checks and first.score == again.score and first.passed == again.passed
    )
    assert first.trace_id == second.trace_id == snapshot.trace.trace_id
    assert first.policy_version == "1"
    assert second.policy_version == "2"
    assert len(harness.providers["fake"].calls) == provider_calls
    after_files = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*"))
    assert after_files == before_files


def test_snapshot_json_load_rejects_tampering(tmp_path: Path) -> None:
    store = SnapshotStore.from_directory(tmp_path)
    raw = ReplaySnapshot.model_validate(
        {
            "snapshot_id": "bad",
            "trace": {
                "trace_id": "t",
                "run_id": "r",
                "status": "completed",
                "completeness": "complete",
            },
        }
    )
    path = tmp_path / "bad.json"
    path.write_text(raw.model_dump_json(), encoding="utf-8")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["trace"]["final_output"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        store.load("bad")


def test_offline_replay_is_explicitly_not_rerun() -> None:
    assert OfflineReplay.guarantee == "deterministic policy evaluation of an immutable trace"
    assert OfflineReplay.guarantee != "deterministic model execution"
