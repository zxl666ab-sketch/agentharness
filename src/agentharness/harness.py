"""Small public facade for the production Agent Runtime."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    Checkpoint,
    ConversationTurn,
    EventEnvelope,
    Message,
    MessageRole,
    RunRequest,
    RunResult,
    RunStatus,
    ToolRecoveryDecision,
    ToolSpec,
)
from agentharness.engine.runtime import ApprovalCallback, RunEngine
from agentharness.engine.tool_execution import validate_tool_spec
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage

EventCallback = Callable[[EventEnvelope], None]


class Harness:
    """Top-level runtime facade used directly by the Web control plane."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        redactor: Redactor | None = None,
        approval_callback: ApprovalCallback | None = None,
        providers: dict[str, Any] | None = None,
        tools: dict[str, Any] | None = None,
        lease_owner_id: str | None = None,
        lease_ttl_s: float = 60.0,
        lease_heartbeat_s: float = 10.0,
    ) -> None:
        self.data_dir = Path(data_dir or Path.home() / ".agentharness").expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or default_redactor
        self.storage = Storage(self.data_dir, redactor=self.redactor)
        self.recovered_run_ids = self.storage.recover_expired_run_leases()
        self.tools: dict[str, Any] = dict(tools or {})
        for tool in self.tools.values():
            validate_tool_spec(tool.spec)
        self.providers: dict[str, Any] = providers or {
            "openai": OpenAIResponsesAdapter(),
        }
        self._event_subs: list[EventCallback] = []
        self._event_subs_lock = threading.RLock()
        self.engine = RunEngine(
            self.storage,
            self.providers,
            self.tools,
            redactor=self.redactor,
            approval_callback=approval_callback,
            on_events=self._notify_events,
            lease_owner_id=lease_owner_id,
            lease_ttl_s=lease_ttl_s,
            lease_heartbeat_s=lease_heartbeat_s,
        )
        self._closed = False

    def set_approval_callback(self, callback: ApprovalCallback | None) -> None:
        self.engine.approval_callback = callback

    def register_tool(self, tool: Any) -> None:
        spec: ToolSpec = tool.spec
        validate_tool_spec(spec)
        self.tools[spec.name] = tool

    def register_provider(self, name: str, adapter: Any) -> None:
        self.providers[name] = adapter
        self.engine.providers = self.providers

    async def run(
        self, request: RunRequest, *, run_id: str | None = None
    ) -> RunResult:
        return await self.engine.run(request, run_id=run_id)

    async def resume(self, run_id: str, input: str | None = None) -> RunResult:
        return await self.engine.resume(run_id, input=input)

    def child_run_ids(self, run_id: str) -> list[str]:
        """Child run ids spawned by a run (delegate concurrency accounting)."""
        return self.engine.child_run_ids(run_id)

    def resolve_indeterminate_tool(
        self,
        invocation_id: str,
        decision: ToolRecoveryDecision,
        *,
        arguments_sha256: str,
    ):
        return self.engine.resolve_indeterminate_tool(
            invocation_id,
            decision,
            arguments_sha256=arguments_sha256,
        )

    async def cancel(self, run_id: str) -> None:
        await self.engine.cancel(run_id)

    async def interrupt(self, run_id: str, reason: str = "interrupted") -> None:
        await self.engine.interrupt(run_id, reason)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.storage.get_run(run_id)

    def list_runs(
        self, session_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self.storage.list_runs(session_id=session_id, limit=limit, offset=offset)

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        return [self._enrich_session(item) for item in self.storage.list_sessions(limit=limit)]

    def resolve_session_id(self, value: str, *, limit: int = 1000) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("session id is required")
        matches = [
            str(session["id"])
            for session in self.storage.list_sessions(limit=limit)
            if str(session.get("id", "")) == value
            or str(session.get("id", "")).startswith(value)
        ]
        unique = list(dict.fromkeys(matches))
        if len(unique) == 1:
            return unique[0]
        if not unique:
            raise KeyError(f"Session not found: {value}")
        raise ValueError(f"Session prefix is ambiguous: {value}")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session = self.storage.get_session(session_id)
        return self._enrich_session(session) if session else None

    def _enrich_session(self, session: dict[str, Any]) -> dict[str, Any]:
        result = dict(session)
        session_id = result.get("id")
        if not session_id:
            return result
        if "latest_status" in result or "latest_run_id" in result:
            result.setdefault("latest_status", None)
            result.setdefault("latest_run_id", None)
            result.setdefault("latest_error", None)
            return result
        runs = self.storage.list_runs(session_id=session_id, limit=50)
        latest = next((run for run in runs if not run.get("parent_run_id")), None)
        if latest:
            result["latest_status"] = latest.get("status")
            result["latest_run_id"] = latest.get("id")
            result["latest_error"] = latest.get("error")
        return result

    def get_events(
        self,
        run_id: str | None = None,
        after_global_seq: int = 0,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        return self.storage.get_events(
            run_id=run_id,
            after_global_seq=after_global_seq,
            limit=limit,
        )

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.get_run_tree(run_id)

    def get_run_messages(self, run_id: str) -> list[Message]:
        return self.storage.get_messages(run_id)

    def get_context_manifests(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.get_context_manifests(run_id)

    def get_checkpoint(self, run_id: str) -> Checkpoint | None:
        return self.storage.load_checkpoint(run_id)

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.list_approvals(run_id)

    def list_tool_invocations(self, run_id: str):  # type: ignore[no-untyped-def]
        return self.storage.list_tool_invocations(run_id)

    def get_tool_invocation(self, invocation_id: str):  # type: ignore[no-untyped-def]
        return self.storage.get_tool_invocation(invocation_id)

    def list_tool_attempts(self, invocation_id: str) -> list[dict[str, Any]]:
        return self.storage.list_tool_attempts(invocation_id)

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.storage.get_artifact(artifact_id)

    def pin_run(self, run_id: str, note: str | None = None) -> None:
        self.storage.pin_run(run_id, note)

    def unpin_run(self, run_id: str) -> bool:
        return self.storage.unpin_run(run_id)

    def maintenance_stats(self) -> dict[str, Any]:
        return self.storage.maintenance_stats()

    def plan_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        return self.storage.plan_gc(older_than_days=older_than_days)

    def apply_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        return self.storage.apply_gc(older_than_days=older_than_days)

    def compact_storage(self) -> dict[str, int]:
        return self.storage.compact()

    def get_session_transcript(self, session_id: str) -> list[ConversationTurn]:
        turns: list[ConversationTurn] = []
        for run in self.storage.list_top_level_runs(session_id):
            user_content = ""
            assistant_parts: list[str] = []
            for message in self.storage.get_messages(run["id"]):
                if message.role == MessageRole.user and not user_content:
                    user_content = message.content or ""
                elif (
                    message.role == MessageRole.assistant
                    and message.content
                    and not message.tool_calls
                ):
                    assistant_parts.append(message.content)
            assistant_content = "".join(assistant_parts) or str(
                run.get("output_summary") or ""
            )
            status_raw = run.get("status") or RunStatus.pending.value
            try:
                status: RunStatus | str = RunStatus(status_raw)
            except ValueError:
                status = str(status_raw)
            turns.append(
                ConversationTurn(
                    run_id=run["id"],
                    session_id=session_id,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    status=status,
                    error=run.get("error"),
                    provider=run.get("provider"),
                    model=run.get("model"),
                    started_at=run.get("created_at"),
                    finished_at=run.get("finished_at"),
                )
            )
        return turns

    def subscribe_events(self, callback: EventCallback) -> Callable[[], None]:
        with self._event_subs_lock:
            self._event_subs.append(callback)

        def unsubscribe() -> None:
            with self._event_subs_lock:
                try:
                    self._event_subs.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _notify_events(self, events: list[EventEnvelope]) -> None:
        with self._event_subs_lock:
            subscribers = list(self._event_subs)
        for event in events:
            for callback in subscribers:
                try:
                    callback(event)
                except Exception:  # noqa: BLE001 - one observer cannot break a run
                    pass

    def doctor(self) -> dict[str, Any]:
        source_web = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
        return {
            "data_dir": str(self.data_dir),
            "db": str(self.storage.db_path),
            "db_exists": self.storage.db_path.exists(),
            "sqlite_integrity": self.storage.integrity_check(),
            "schema_version": self.storage.schema_version(),
            "web_build": "ready" if source_web.is_file() else "external-java-control-plane",
            "providers": list(self.providers),
            "tools": list(self.tools),
            "sessions": len(self.list_sessions()),
            "runs": len(self.list_runs()),
            "max_global_seq": self.storage.max_global_seq(),
            "recovered_process_lost_runs": len(self.recovered_run_ids),
        }

    async def aclose(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        for run_id in list(self.engine._active_run_ids):
            try:
                await self.engine.interrupt(run_id, "harness_shutdown")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        seen: set[int] = set()
        for tool in self.tools.values():
            if id(tool) in seen:
                continue
            seen.add(id(tool))
            close_all = getattr(tool, "close_all", None)
            if callable(close_all):
                try:
                    result = close_all()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        seen.clear()
        for provider in self.providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            closer = getattr(provider, "aclose", None) or getattr(provider, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
        with self._event_subs_lock:
            self._event_subs.clear()
        try:
            self.storage.close()
            self._closed = True
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        if errors:
            raise ExceptionGroup("Harness cleanup failed", errors)

    def _has_open_async_resources(self) -> bool:
        if self.engine._active_run_ids:
            return True
        return any(getattr(provider, "_client", None) is not None for provider in self.providers.values())

    def close(self) -> None:
        if self._closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        if self._has_open_async_resources():
            raise RuntimeError("Harness has live async resources; use `await harness.aclose()`")
        with self._event_subs_lock:
            self._event_subs.clear()
        self.storage.close()
        self._closed = True
