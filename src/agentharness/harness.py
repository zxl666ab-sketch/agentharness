"""Public Harness API — run / resume / cancel / register_tool / readonly queries."""

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
    ToolSpec,
)
from agentharness.engine.runtime import ApprovalCallback, RunEngine
from agentharness.providers.anthropic_adapter import AnthropicMessagesAdapter
from agentharness.providers.fake import FakeModelAdapter
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter
from agentharness.security.egress import EgressPolicy, default_policy
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage
from agentharness.tools import create_default_tools
from agentharness.tools.mcp_tool import MCPBridge

EventCallback = Callable[[EventEnvelope], None]


class Harness:
    """Top-level agent harness facade."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        redactor: Redactor | None = None,
        approval_callback: ApprovalCallback | None = None,
        providers: dict[str, Any] | None = None,
        tools: dict[str, Any] | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".agentharness"
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or default_redactor
        # Default-secure egress policy shared by every outbound tool (http/browser/MCP).
        # Trusted hosts/CIDRs come only from injected config, never from model arguments.
        self.egress_policy = egress_policy or default_policy()
        self.storage = Storage(self.data_dir, redactor=self.redactor)
        self.mcp_bridge = MCPBridge(redactor=self.redactor, policy=self.egress_policy)
        self._process_registry: dict[str, list[Any]] = {}
        self.tools: dict[str, Any] = tools or create_default_tools(
            process_registry=self._process_registry,
            mcp_bridge=self.mcp_bridge,
            egress_policy=self.egress_policy,
        )
        self.providers: dict[str, Any] = providers or {
            "fake": FakeModelAdapter(),
            "openai": OpenAIResponsesAdapter(),
            "anthropic": AnthropicMessagesAdapter(),
        }
        self.engine = RunEngine(
            self.storage,
            self.providers,
            self.tools,
            redactor=self.redactor,
            approval_callback=approval_callback,
            harness=self,
        )
        # Share process registry with engine for cancel
        self.engine._active_processes = self._process_registry
        self._event_subs: list[EventCallback] = []
        self._event_subs_lock = threading.RLock()
        self._closed = False

    def set_approval_callback(self, cb: ApprovalCallback | None) -> None:
        self.engine.approval_callback = cb

    def register_tool(self, tool: Any) -> None:
        spec: ToolSpec = tool.spec
        self.tools[spec.name] = tool

    def register_provider(self, name: str, adapter: Any) -> None:
        self.providers[name] = adapter
        self.engine.providers = self.providers

    async def run(self, request: RunRequest) -> RunResult:
        return await self.engine.run(request)

    async def resume(self, run_id: str, input: str | None = None) -> RunResult:
        return await self.engine.resume(run_id, input=input)

    async def cancel(self, run_id: str) -> None:
        await self.engine.cancel(run_id)

    async def interrupt(self, run_id: str, reason: str = "interrupted") -> None:
        await self.engine.interrupt(run_id, reason)

    # -- readonly queries ---------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.storage.get_run(run_id)

    def list_runs(
        self, session_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self.storage.list_runs(session_id=session_id, limit=limit, offset=offset)

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List sessions with latest top-level run status for the observer UI."""
        sessions = self.storage.list_sessions(limit=limit)
        return [self._enrich_session(s) for s in sessions]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        sess = self.storage.get_session(session_id)
        if not sess:
            return None
        return self._enrich_session(sess)

    def _enrich_session(self, sess: dict[str, Any]) -> dict[str, Any]:
        """Attach latest top-level run status / id / error for UI left column.

        list_sessions already resolves these in one SQL query. For get_session (a
        bare session row without the join), fall back to a single scoped list_runs.
        """
        out = dict(sess)
        sid = out.get("id")
        if not sid:
            return out
        if "latest_status" in out or "latest_run_id" in out:
            # Already enriched by list_sessions' join — normalize missing keys.
            out.setdefault("latest_status", None)
            out.setdefault("latest_run_id", None)
            out.setdefault("latest_error", None)
            return out
        runs = self.storage.list_runs(session_id=sid, limit=50)
        top = [r for r in runs if not r.get("parent_run_id")]
        latest = top[0] if top else None  # list_runs is DESC by created_at
        if latest:
            out["latest_status"] = latest.get("status")
            out["latest_run_id"] = latest.get("id")
            out["latest_error"] = latest.get("error")
        return out

    def get_events(
        self,
        run_id: str | None = None,
        after_global_seq: int = 0,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        return self.storage.get_events(
            run_id=run_id, after_global_seq=after_global_seq, limit=limit
        )

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.get_run_tree(run_id)

    def get_run_messages(self, run_id: str) -> list[Message]:
        return self.storage.get_messages(run_id)

    def get_checkpoint(self, run_id: str) -> Checkpoint | None:
        return self.storage.load_checkpoint(run_id)

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.list_approvals(run_id)

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.storage.get_artifact(artifact_id)

    def get_session_transcript(self, session_id: str) -> list[ConversationTurn]:
        """Return all top-level turns for a session, including failed turns.

        Ordered by run created_at ascending. Delegate child runs are excluded.
        """
        runs = self.storage.list_top_level_runs(session_id)
        turns: list[ConversationTurn] = []
        for run in runs:
            messages = self.storage.get_messages(run["id"])
            user_content = ""
            assistant_parts: list[str] = []
            for m in messages:
                role = m.role.value if hasattr(m.role, "value") else str(m.role)
                if role == MessageRole.user.value and not user_content:
                    user_content = m.content or ""
                elif role == MessageRole.assistant.value:
                    if m.content:
                        assistant_parts.append(m.content)
            # Prefer stored output_summary when assistant messages empty
            assistant_content = "".join(assistant_parts)
            if not assistant_content and run.get("output_summary"):
                assistant_content = run["output_summary"] or ""
            status_raw = run.get("status") or RunStatus.pending.value
            try:
                status: RunStatus | str = RunStatus(status_raw)
            except ValueError:
                status = status_raw
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
        """Subscribe to ordered redacted events (including text_delta).

        Returns an unsubscribe callable. Events are already redacted by storage.
        """
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
            subs = list(self._event_subs)
        for ev in events:
            for cb in subs:
                try:
                    cb(ev)
                except Exception:  # noqa: BLE001
                    pass

    def doctor(self) -> dict[str, Any]:
        packaged_web = Path(__file__).resolve().parent / "web_dist" / "index.html"
        source_web = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
        web_ready = packaged_web.is_file() or source_web.is_file()
        browser_runtime = "missing"
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                if Path(playwright.chromium.executable_path).is_file():
                    browser_runtime = "ready"
        except Exception:  # noqa: BLE001
            browser_runtime = "missing"
        return {
            "data_dir": str(self.data_dir),
            "db": str(self.storage.db_path),
            "db_exists": self.storage.db_path.exists(),
            "sqlite_integrity": self.storage.integrity_check(),
            "schema_version": self.storage.schema_version(),
            "web_build": "ready" if web_ready else "missing",
            "browser_runtime": browser_runtime,
            "providers": list(self.providers.keys()),
            "tools": list(self.tools.keys()),
            "sessions": len(self.list_sessions()),
            "runs": len(self.list_runs()),
            "max_global_seq": self.storage.max_global_seq(),
        }

    async def aclose(self) -> None:
        """Close async tools and storage on their owning event loop."""
        if self._closed:
            return
        errors: list[Exception] = []
        for active_run_id in list(self.engine._active_run_ids):
            try:
                await self.engine.interrupt(active_run_id, "harness_shutdown")
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
        try:
            await self.mcp_bridge.close_all()
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
        if self.engine._active_run_ids or any(self._process_registry.values()):
            return True
        if self.mcp_bridge._sessions:
            return True
        for tool in self.tools.values():
            if getattr(tool, "_playwright", None) is not None:
                return True
            if getattr(tool, "_browsers", None):
                return True
        for provider in self.providers.values():
            if getattr(provider, "_client", None) is not None:
                return True
        return False

    def close(self) -> None:
        """Synchronous close for callers without live async resources."""
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
