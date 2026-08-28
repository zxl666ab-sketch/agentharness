from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from agentharness.contracts import (
    Checkpoint,
    EffectKind,
    Message,
    MessageRole,
    ReplayPolicy,
    RunRequest,
    RunStatus,
    ToolContentPart,
    ToolInvocationRecord,
    ToolInvocationStatus,
    ToolResult,
)
from agentharness.engine.lease import LeaseManager
from agentharness.storage.sqlite import Storage
from tests.fake_provider import create_test_harness


def _create_run(storage: Storage, run_id: str, status: RunStatus) -> None:
    session_id = storage.create_session(f"session-{run_id}")
    storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        status=status,
    )


def test_active_lease_cannot_be_stolen_or_recovered(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        _create_run(storage, "active-run", RunStatus.running)
        assert storage.acquire_run_lease("active-run", "owner-a", ttl_s=60)
        assert not storage.acquire_run_lease("active-run", "owner-b", ttl_s=60)
        assert storage.recover_expired_run_leases() == []
        assert storage.get_run("active-run")["status"] == "running"  # type: ignore[index]
    finally:
        storage.close()


def test_only_expired_lease_becomes_process_lost_and_resumable(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        _create_run(storage, "lost-run", RunStatus.running)
        storage.save_checkpoint(
            Checkpoint(
                run_id="lost-run",
                phase="model_turn",
                step=0,
                messages=[Message(role=MessageRole.user, content="recover me")],
                status=RunStatus.running,
            )
        )
        assert storage.acquire_run_lease("lost-run", "dead-owner", ttl_s=-1)
    finally:
        storage.close()

    harness = create_test_harness(data_dir=data_dir)
    try:
        assert harness.recovered_run_ids == ["lost-run"]
        run = harness.get_run("lost-run")
        assert run is not None
        assert run["status"] == "interrupted"
        assert run["error"] == "process_lost"
        checkpoint = harness.storage.load_checkpoint("lost-run")
        assert checkpoint is not None
        assert checkpoint.status == RunStatus.interrupted
        events = harness.get_events(run_id="lost-run", limit=100)
        assert any(
            str(event.type) == "run_interrupted"
            and event.payload.get("reason") == "process_lost"
            for event in events
        )
    finally:
        harness.close()


def test_process_loss_marks_nonreplayable_attempt_indeterminate(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        _create_run(storage, "unsafe-run", RunStatus.running)
        storage.save_tool_invocation(
            ToolInvocationRecord(
                id="unsafe-invocation",
                run_id="unsafe-run",
                session_id="session-unsafe-run",
                step=0,
                ordinal=0,
                provider_call_id="unsafe-call",
                tool_name="shell",
                status=ToolInvocationStatus.running,
                effect=EffectKind.destructive,
                replay_policy=ReplayPolicy.never,
                attempt_count=1,
            )
        )
        storage.start_tool_attempt("unsafe-invocation", 1)
        assert storage.acquire_run_lease("unsafe-run", "dead-owner", ttl_s=-1)

        assert storage.recover_expired_run_leases() == ["unsafe-run"]
        invocation = storage.get_tool_invocation("unsafe-invocation")
        attempts = storage.list_tool_attempts("unsafe-invocation")
        assert invocation is not None
        assert invocation.status == ToolInvocationStatus.indeterminate
        assert attempts[0]["status"] == "indeterminate"
        assert attempts[0]["error_code"] == "outcome_indeterminate"
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_normal_run_releases_lease(data_dir: Path, workspace: Path) -> None:
    harness = create_test_harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(message="[fake:text]done", provider="fake", cwd=str(workspace))
        )
        assert result.status == RunStatus.completed
        assert harness.maintenance_stats()["run_leases"] == 0
    finally:
        await harness.aclose()


def test_gc_is_dry_run_by_default_and_preserves_pins(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        _create_run(storage, "delete-me", RunStatus.completed)
        _create_run(storage, "keep-me", RunStatus.completed)
        storage.pin_run("keep-me", "portfolio evidence")

        referenced = storage.artifacts.put("referenced artifact")
        referenced_id = storage.register_artifact(referenced)
        storage.merge_run_metadata("keep-me", {"artifact_id": referenced_id})
        orphan = storage.artifacts.put("orphan artifact")
        orphan_id = storage.register_artifact(orphan)

        tool_artifact = storage.artifacts.put("tool-only artifact")
        tool_artifact_id = storage.register_artifact(tool_artifact)
        storage.save_tool_invocation(
            ToolInvocationRecord(
                id="keep-invocation",
                run_id="keep-me",
                session_id="session-keep-me",
                step=0,
                ordinal=0,
                provider_call_id="keep-call",
                tool_name="read_file",
                status=ToolInvocationStatus.succeeded,
                effect=EffectKind.workspace_read,
                replay_policy=ReplayPolicy.safe,
                result=ToolResult(
                    tool_call_id="keep-call",
                    invocation_id="keep-invocation",
                    name="read_file",
                    content="stored as artifact",
                    artifact_id=tool_artifact_id,
                    parts=[
                        ToolContentPart(
                            type="resource", artifact_id=tool_artifact_id
                        )
                    ],
                ),
            )
        )
        storage.save_tool_invocation(
            ToolInvocationRecord(
                id="delete-invocation",
                run_id="delete-me",
                session_id="session-delete-me",
                step=0,
                ordinal=0,
                provider_call_id="delete-call",
                tool_name="read_file",
                status=ToolInvocationStatus.succeeded,
                effect=EffectKind.workspace_read,
                replay_policy=ReplayPolicy.safe,
            )
        )
        attempt_id = storage.start_tool_attempt("delete-invocation", 1)
        storage.finish_tool_attempt(attempt_id, status="succeeded", duration_ms=1)

        plan = storage.plan_gc(older_than_days=0)
        assert plan["dry_run"] is True
        assert "delete-me" in plan["run_ids"]
        assert "keep-me" not in plan["run_ids"]
        assert orphan_id in plan["orphan_artifact_ids"]
        assert tool_artifact_id not in plan["orphan_artifact_ids"]
        assert storage.get_run("delete-me") is not None
        assert storage.get_artifact(orphan_id) is not None

        applied = storage.apply_gc(older_than_days=0)
        assert applied["dry_run"] is False
        assert storage.get_run("delete-me") is None
        assert storage.get_run("keep-me") is not None
        assert storage.get_artifact(referenced_id) is not None
        assert storage.get_artifact(tool_artifact_id) is not None
        assert storage.get_artifact(orphan_id) is None
        assert not Path(orphan["path"]).exists()
        assert storage.maintenance_stats()["tool_invocations"] == 1
        assert storage.maintenance_stats()["tool_attempts"] == 0
    finally:
        storage.close()


class _FlakyLeaseStorage:
    """Minimal stand-in for Storage: heartbeat renewals fail on demand."""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def heartbeat_run_lease(self, run_id: str, owner_id: str, *, ttl_s: float) -> bool:
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return bool(outcome)


@pytest.mark.asyncio
async def test_heartbeat_tolerates_transient_failures_then_reports_lost() -> None:
    """P-H3: one `database is locked` used to kill the loop silently.

    A dead heartbeat means the TTL expires while the engine keeps writing —
    exactly the double-write the lease exists to prevent — so the loop must
    survive a few failures and then call ``on_lost`` instead of vanishing.
    """
    lock_error = sqlite3.OperationalError("database is locked")
    storage = _FlakyLeaseStorage([lock_error, lock_error, True, lock_error, lock_error])
    manager = LeaseManager(storage, owner_id="owner", ttl_s=5.0, heartbeat_s=0.5)  # type: ignore[arg-type]
    lost: list[str] = []

    task = asyncio.create_task(manager._heartbeat_loop("run-1", lost.append))
    # 前两次失败 + 一次成功 = 1.5s：此时循环必须还活着（旧实现早已异常退出）
    await asyncio.sleep(1.4)
    assert not task.done(), "两次瞬时失败不该结束心跳循环"
    await asyncio.wait_for(task, timeout=4.0)

    assert lost == ["run-1"], "连续 3 次失败后必须上报租约丢失"
    assert storage.calls == 6


@pytest.mark.asyncio
async def test_heartbeat_success_resets_the_failure_budget() -> None:
    storage = _FlakyLeaseStorage(
        [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            True,
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            True,
        ]
    )
    manager = LeaseManager(storage, owner_id="owner", ttl_s=5.0, heartbeat_s=0.5)  # type: ignore[arg-type]
    lost: list[str] = []

    task = asyncio.create_task(manager._heartbeat_loop("run-2", lost.append))
    await asyncio.sleep(2.6)  # 5 个心跳周期：失败被一次成功重置，未达 3 连败
    assert lost == []
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_gc_skips_runs_pinned_after_planning(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P-L15: the plan is a candidate list, not an authorization to delete."""
    storage = Storage(data_dir)
    try:
        _create_run(storage, "race-run", RunStatus.completed)
        # Age it deterministically: the GC cutoff is `now`, so a row written in
        # the same clock tick as the plan would not be a candidate at all.
        with storage.transaction():
            storage._conn.execute(  # noqa: SLF001 - deterministic GC fixture
                "UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00', "
                "updated_at = '2020-01-01T00:00:00+00:00' WHERE id = 'race-run'"
            )
        ops = storage.maintenance
        original = ops._gc_candidates_unlocked
        calls = {"n": 0}

        def racing(cutoff: str) -> list[str]:
            calls["n"] += 1
            # 规划阶段看到它；事务内复查时它已经被钉住/续租
            return original(cutoff) if calls["n"] == 1 else []

        monkeypatch.setattr(ops, "_gc_candidates_unlocked", racing)
        applied = storage.apply_gc(older_than_days=0)
        assert calls["n"] >= 2
        assert applied["deleted_runs"] == 0
        assert applied["skipped_run_ids"] == ["race-run"]
        assert storage.get_run("race-run") is not None
    finally:
        storage.close()


def test_compact_refuses_active_lease_then_succeeds(data_dir: Path) -> None:
    storage = Storage(data_dir)
    try:
        _create_run(storage, "active", RunStatus.running)
        assert storage.acquire_run_lease("active", "owner", ttl_s=60)
        with pytest.raises(RuntimeError, match="active run leases"):
            storage.compact()
        storage.release_run_lease("active", "owner")
        result = storage.compact()
        assert result["before_bytes"] >= 0
        assert result["after_bytes"] >= 0
    finally:
        storage.close()
