"""Run-end deterministic grading: metadata.eval + no Inspector recompute."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentharness.contracts import ApprovalMode, RunRequest, RunStatus
from agentharness.eval.graders import CompositeGrader
from agentharness.eval.runner import build_trajectory


def _meta(run: dict[str, Any]) -> dict[str, Any]:
    raw = run.get("metadata_json") or "{}"
    data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    assert isinstance(data, dict)
    return data


@pytest.mark.asyncio
async def test_run_end_grade_pass(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]hello world",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"status": "completed", "contains": ["hello"]}},
        )
    )
    assert result.status == RunStatus.completed
    row = harness.get_run(result.run_id)
    assert row is not None
    meta = _meta(row)
    ev = meta.get("eval")
    assert isinstance(ev, dict)
    assert ev["schema_version"] == 1
    assert ev["passed"] is True
    assert ev["score"] == 1.0
    assert ev["grader"] == "composite"
    assert ev["reasons"] == []
    assert "graded_at" in ev


@pytest.mark.asyncio
async def test_run_end_grade_fail_reasons(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]hello world",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={
                "eval_assert": {
                    "status": "completed",
                    "contains": ["definitely-missing-token-xyz"],
                }
            },
        )
    )
    assert result.status == RunStatus.completed  # fail score must not change terminal status
    row = harness.get_run(result.run_id)
    assert row is not None
    ev = _meta(row)["eval"]
    assert ev["passed"] is False
    assert ev["score"] < 1.0
    assert ev["reasons"]
    assert any("missing substring" in r for r in ev["reasons"])


@pytest.mark.asyncio
async def test_run_end_no_assert_skips_eval(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]plain",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"note": "no eval"},
        )
    )
    assert result.status == RunStatus.completed
    meta = _meta(harness.get_run(result.run_id) or {})
    assert "eval" not in meta
    assert meta.get("note") == "no eval"


@pytest.mark.asyncio
async def test_run_end_grade_idempotent_overwrite(harness) -> None:
    req = RunRequest(
        message="[fake:text]hello",
        provider="fake",
        approval=ApprovalMode.never,
        metadata={"eval_assert": {"status": "completed", "contains": ["hello"]}},
    )
    result = await harness.run(req)
    first = _meta(harness.get_run(result.run_id) or {})["eval"]
    first_at = first["graded_at"]

    # Second grade overwrites the same key rather than stacking entries.
    harness._maybe_grade_run(req, result, latency_s=0.01)
    second = _meta(harness.get_run(result.run_id) or {})["eval"]
    assert isinstance(second, dict)
    assert second["schema_version"] == 1
    assert second["passed"] is True
    # graded_at may equal if same second, but structure is a single object
    assert "eval" in _meta(harness.get_run(result.run_id) or {})
    assert not isinstance(_meta(harness.get_run(result.run_id) or {})["eval"], list)
    assert second["graded_at"] >= first_at


@pytest.mark.asyncio
async def test_run_end_grade_redacts_secrets_in_reasons(harness) -> None:
    secret = "sk-" + ("A" * 24)
    result = await harness.run(
        RunRequest(
            message="[fake:text]hello",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"contains": [secret]}},
        )
    )
    assert result.status == RunStatus.completed
    ev = _meta(harness.get_run(result.run_id) or {})["eval"]
    blob = json.dumps(ev, ensure_ascii=False)
    assert secret not in blob
    assert "REDACTED" in blob
    assert any("missing substring" in r for r in ev["reasons"])


@pytest.mark.asyncio
async def test_get_run_path_does_not_regrade(harness, monkeypatch) -> None:
    """Readonly get_run / messages must not invoke CompositeGrader (no live recompute)."""
    result = await harness.run(
        RunRequest(
            message="[fake:text]hello",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"contains": ["hello"]}},
        )
    )
    calls = {"n": 0}
    original = CompositeGrader.grade

    def spy(self, case, traj):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, case, traj)

    monkeypatch.setattr(CompositeGrader, "grade", spy)
    row = harness.get_run(result.run_id)
    assert row is not None
    _ = harness.get_run_messages(result.run_id)
    assert calls["n"] == 0
    assert "eval" in _meta(row)


@pytest.mark.asyncio
async def test_build_trajectory_matches_run_result(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]trajectory-check",
            provider="fake",
            approval=ApprovalMode.never,
        )
    )
    traj = build_trajectory(harness, result, latency_s=0.123)
    assert traj.status == "completed"
    assert "trajectory-check" in (traj.output or "") or traj.output is not None
    assert traj.latency_s == 0.123
    assert isinstance(traj.tools_ordered, list)
    assert isinstance(traj.messages, list)


@pytest.mark.asyncio
async def test_invalid_eval_assert_does_not_break_run(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]ok",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": "not-a-spec"},
        )
    )
    assert result.status == RunStatus.completed
    meta = _meta(harness.get_run(result.run_id) or {})
    assert "eval" not in meta


@pytest.mark.asyncio
async def test_manual_grade_run_reuses_stored_assertion(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]manual-grade",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"contains": ["manual-grade"]}},
        )
    )
    first = harness.grade_run(result.run_id)
    second = harness.grade_run(result.run_id)
    assert first["passed"] is True
    assert second["passed"] is True
    stored = _meta(harness.get_run(result.run_id) or {})
    assert isinstance(stored["eval"], dict)
    assert stored["eval"]["schema_version"] == 1


@pytest.mark.asyncio
async def test_manual_grade_without_assert_uses_run_health(harness) -> None:
    result = await harness.run(
        RunRequest(
            message="[fake:text]no-rule",
            provider="fake",
            approval=ApprovalMode.never,
        )
    )
    evaluation = harness.grade_run(result.run_id)
    assert evaluation["passed"] is True
    assert evaluation["mode"] == "deterministic"
    assert evaluation["assertion_summary"]["status"] is None
    assert evaluation["evaluation_report"]["mode"] == "health_only"
    assert evaluation["score"] is None
    with pytest.raises(KeyError):
        harness.grade_run("does-not-exist")
