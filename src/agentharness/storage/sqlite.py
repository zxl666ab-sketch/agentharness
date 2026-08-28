"""SQLite WAL storage facade — delegates to per-domain repositories.

The connection, its writer lock and per-thread readers live in
:class:`StorageCore`; each domain (runs, events, messages, checkpoints, tool
invocations, approvals, leases, artifact index, maintenance) owns its
SQL in a dedicated repo module. This class is the stable public surface: every
method keeps its original signature and delegates 1:1.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    Checkpoint,
    EventEnvelope,
    Message,
    RunStatus,
    ToolInvocationRecord,
    Usage,
)
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.approvals import ApprovalRepo
from agentharness.storage.artifact_index import ArtifactIndexRepo
from agentharness.storage.artifacts import ArtifactStore
from agentharness.storage.checkpoints import CheckpointRepo
from agentharness.storage.core import StorageCore
from agentharness.storage.events import EventRepo
from agentharness.storage.internal_operations import InternalOperationRepo
from agentharness.storage.leases import LeaseRepo
from agentharness.storage.maintenance import MaintenanceOps
from agentharness.storage.messages import MessageRepo
from agentharness.storage.runs import RunRepo
from agentharness.storage.sessions import SessionRepo
from agentharness.storage.tool_invocations import ToolInvocationRepo


class Storage:
    """Thread-safe SQLite store with WAL. Single-writer transactions for status+events."""

    def __init__(
        self,
        data_dir: Path | str,
        redactor: Redactor | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "agentharness.db"
        self.redactor = redactor or default_redactor
        self.artifacts = ArtifactStore(self.data_dir / "artifacts", redactor=self.redactor)
        self._core = StorageCore(self.db_path)
        # Internal aliases kept for tests and diagnostics that reach past the facade.
        self._lock = self._core.lock
        self._conn = self._core.conn
        self._reader = self._core.reader
        self.events = EventRepo(self._core, self.redactor)
        self.internal_operations = InternalOperationRepo(self._core)
        self.leases = LeaseRepo(self._core, events=self.events)
        self.sessions = SessionRepo(self._core, self.redactor)
        self.runs = RunRepo(self._core, self.redactor, events=self.events)
        self.messages = MessageRepo(self._core, self.redactor)
        self.checkpoints = CheckpointRepo(self._core, self.redactor)
        self.tool_invocations = ToolInvocationRepo(self._core, self.redactor)
        self.approvals = ApprovalRepo(self._core, self.redactor)
        self.artifact_index = ArtifactIndexRepo(self._core, self.redactor)
        self.maintenance = MaintenanceOps(self._core, artifacts=self.artifacts)

    def close(self) -> None:
        self._core.close()

    def integrity_check(self) -> str:
        return self._core.integrity_check()

    def schema_version(self) -> int:
        return self._core.schema_version()

    def transaction(self):  # type: ignore[no-untyped-def]
        return self._core.transaction()

    # -- run ownership / lifecycle ----------------------------------------

    def acquire_run_lease(self, run_id: str, owner_id: str, *, ttl_s: float) -> bool:
        return self.leases.acquire_run_lease(run_id, owner_id, ttl_s=ttl_s)

    def heartbeat_run_lease(self, run_id: str, owner_id: str, *, ttl_s: float) -> bool:
        return self.leases.heartbeat_run_lease(run_id, owner_id, ttl_s=ttl_s)

    def release_run_lease(self, run_id: str, owner_id: str) -> None:
        self.leases.release_run_lease(run_id, owner_id)

    def recover_expired_run_leases(self) -> list[str]:
        return self.leases.recover_expired_run_leases()

    def pin_run(self, run_id: str, note: str | None = None) -> None:
        self.runs.pin_run(run_id, note)

    def unpin_run(self, run_id: str) -> bool:
        return self.runs.unpin_run(run_id)

    def list_pins(self) -> list[dict[str, Any]]:
        return self.runs.list_pins()

    # -- sessions -----------------------------------------------------------

    def create_session(self, session_id: str | None = None, title: str | None = None) -> str:
        return self.sessions.create_session(session_id, title)

    def session_exists(self, session_id: str) -> bool:
        return self.sessions.session_exists(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        touch: bool = False,
    ) -> None:
        self.sessions.update_session(session_id, title=title, touch=touch)

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.sessions.list_sessions(limit)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get_session(session_id)

    def list_top_level_runs(self, session_id: str) -> list[dict[str, Any]]:
        return self.sessions.list_top_level_runs(session_id)

    def list_completed_top_level_runs(self, session_id: str) -> list[dict[str, Any]]:
        return self.sessions.list_completed_top_level_runs(session_id)

    def get_session_history_messages(self, session_id: str) -> list[Message]:
        """Full messages from all completed top-level runs, ordered by run then seq.

        Excludes failed/cancelled/interrupted and any run with non-null parent_run_id.
        """
        from agentharness.session_history import assemble_session_history_messages

        runs = self.list_completed_top_level_runs(session_id)
        messages_by_run: dict[str, list[Message]] = {}
        for run in runs:
            messages_by_run[run["id"]] = self.get_messages(run["id"])
        return assemble_session_history_messages(runs, messages_by_run)

    # -- runs ---------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        root_run_id: str,
        parent_run_id: str | None = None,
        status: RunStatus = RunStatus.pending,
        provider: str | None = None,
        model: str | None = None,
        approval: str | None = None,
        cwd: str | None = None,
        delegate_depth: int = 0,
        allow_write: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.runs.create_run(
            run_id=run_id,
            session_id=session_id,
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            status=status,
            provider=provider,
            model=model,
            approval=approval,
            cwd=cwd,
            delegate_depth=delegate_depth,
            allow_write=allow_write,
            metadata=metadata,
        )

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        error: str | None = None,
        output_summary: str | None = None,
        usage: Usage | None = None,
        steps: int | None = None,
        finished: bool = False,
        clear_error: bool = False,
        clear_finished_at: bool = False,
        events: list[EventEnvelope] | None = None,
    ) -> list[EventEnvelope]:
        return self.runs.update_run(
            run_id,
            status=status,
            error=error,
            output_summary=output_summary,
            usage=usage,
            steps=steps,
            finished=finished,
            clear_error=clear_error,
            clear_finished_at=clear_finished_at,
            events=events,
        )

    def merge_run_metadata(self, run_id: str, patch: dict[str, Any]) -> None:
        self.runs.merge_run_metadata(run_id, patch)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get_run(run_id)

    def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.runs.list_runs(session_id, limit, offset)

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        return self.runs.get_run_tree(run_id)

    def request_stop(self, run_id: str, mode: str) -> None:
        self.runs.request_stop(run_id, mode)

    def get_stop_request(self, run_id: str) -> str | None:
        return self.runs.get_stop_request(run_id)

    def clear_stop_request(self, run_id: str) -> None:
        self.runs.clear_stop_request(run_id)

    # -- events -------------------------------------------------------------

    def append_events(self, events: list[EventEnvelope]) -> list[EventEnvelope]:
        return self.events.append_events(events)

    def get_events(
        self,
        run_id: str | None = None,
        after_global_seq: int = 0,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        return self.events.get_events(run_id, after_global_seq, limit)

    def get_context_manifests(self, run_id: str) -> list[dict[str, Any]]:
        return self.events.get_context_manifests(run_id)

    def iter_events_after(self, after_global_seq: int = 0) -> Iterator[EventEnvelope]:
        return self.events.iter_events_after(after_global_seq)

    def max_global_seq(self) -> int:
        return self.events.max_global_seq()

    def max_event_seq(self) -> int:
        return self.events.max_event_seq()

    def global_seq_watermark(self) -> int:
        return self.events.global_seq_watermark()

    def bump_global_seq(self, seq: int) -> int:
        """Persist the durable event high-water mark (MAX upsert); returns stored value."""
        return self.events.bump_global_seq(seq)

    def explain_query_plan(self, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
        return self.events.explain_query_plan(sql, params)

    # -- messages -----------------------------------------------------------

    def save_message(self, run_id: str, session_id: str, message: Message, seq: int) -> None:
        self.messages.save_message(run_id, session_id, message, seq)

    def delete_messages(self, run_id: str, message_ids: list[str]) -> int:
        return self.messages.delete_messages(run_id, message_ids)

    def get_messages(self, run_id: str) -> list[Message]:
        return self.messages.get_messages(run_id)

    # -- checkpoints --------------------------------------------------------

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoints.save_checkpoint(checkpoint)

    def load_checkpoint(self, run_id: str) -> Checkpoint | None:
        return self.checkpoints.load_checkpoint(run_id)

    # -- tool invocations ---------------------------------------------------

    def save_tool_invocation(self, invocation: ToolInvocationRecord) -> None:
        self.tool_invocations.save_tool_invocation(invocation)

    def resolve_indeterminate_tool_invocation(
        self,
        invocation: ToolInvocationRecord,
        *,
        expected_arguments_sha256: str,
    ) -> bool:
        return self.tool_invocations.resolve_indeterminate_tool_invocation(
            invocation,
            expected_arguments_sha256=expected_arguments_sha256,
        )

    def get_tool_invocation(self, invocation_id: str) -> ToolInvocationRecord | None:
        return self.tool_invocations.get_tool_invocation(invocation_id)

    def list_tool_invocations(self, run_id: str) -> list[ToolInvocationRecord]:
        return self.tool_invocations.list_tool_invocations(run_id)

    def start_tool_attempt(self, invocation_id: str, attempt: int) -> str:
        return self.tool_invocations.start_tool_attempt(invocation_id, attempt)

    def finish_tool_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        duration_ms: float,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> None:
        self.tool_invocations.finish_tool_attempt(
            attempt_id,
            status=status,
            duration_ms=duration_ms,
            error_code=error_code,
            error_category=error_category,
        )

    def finish_running_tool_attempts(
        self,
        invocation_id: str,
        *,
        status: str,
        error_code: str,
        error_category: str,
    ) -> int:
        return self.tool_invocations.finish_running_tool_attempts(
            invocation_id,
            status=status,
            error_code=error_code,
            error_category=error_category,
        )

    def list_tool_attempts(self, invocation_id: str) -> list[dict[str, Any]]:
        return self.tool_invocations.list_tool_attempts(invocation_id)

    # -- approvals ----------------------------------------------------------

    def save_approval(self, approval: dict[str, Any]) -> None:
        self.approvals.save_approval(approval)

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        *,
        invocation_id: str | None = None,
        arguments_sha256: str | None = None,
    ) -> bool:
        return self.approvals.resolve_approval(
            approval_id,
            decision,
            invocation_id=invocation_id,
            arguments_sha256=arguments_sha256,
        )

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self.approvals.list_approvals(run_id)

    # -- artifacts meta -----------------------------------------------------

    def register_artifact(self, meta: dict[str, Any]) -> str:
        return self.artifact_index.register_artifact(meta)

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.artifact_index.get_artifact(artifact_id)

    def get_artifact_by_sha(self, sha: str) -> dict[str, Any] | None:
        return self.artifact_index.get_artifact_by_sha(sha)

    # -- explicit maintenance ---------------------------------------------

    def maintenance_stats(self) -> dict[str, Any]:
        return self.maintenance.maintenance_stats()

    def plan_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        return self.maintenance.plan_gc(older_than_days=older_than_days)

    def apply_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        return self.maintenance.apply_gc(older_than_days=older_than_days)

    def compact(self) -> dict[str, int]:
        return self.maintenance.compact()
