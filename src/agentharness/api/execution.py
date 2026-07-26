"""Web execution control plane for background runs and human approvals.

The Web UI is a first-class caller of :class:`Harness`, not a wrapper around the
CLI. This module deliberately exposes a narrow request model: callers choose a
configured workspace while the OpenAI provider, parent/root ids, internal
metadata and arbitrary host paths remain server-owned.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    BudgetConfig,
    RunRequest,
    ToolRecoveryDecision,
    new_id,
)
from agentharness.harness import Harness

logger = logging.getLogger(__name__)


class CreateRunBody(BaseModel):
    """Public, intentionally narrow Web run request."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=200_000)
    session_id: str | None = Field(default=None, max_length=128)
    system: str | None = Field(default=None, max_length=50_000)
    provider: Literal["openai"] | None = Field(
        default=None,
        description="Compatibility field; OpenAI is the only supported value.",
    )
    model: str | None = Field(default=None, max_length=200)
    approval: ApprovalMode = ApprovalMode.ask
    workspace_id: str = Field(default="default", max_length=64)
    cwd: str | None = Field(
        default=None,
        max_length=2_000,
        description="Relative subdirectory inside the selected configured workspace.",
    )
    allow_write: bool = False

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("model", "system", "cwd", mode="before")
    @classmethod
    def blank_optional_strings_are_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ResumeRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str | None = Field(default=None, max_length=200_000)


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["deny", "allow_once", "allow_run"]
    invocation_id: str = Field(min_length=1, max_length=128)
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ToolRecoveryDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ToolRecoveryDecision
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PendingApprovalBroker:
    """Bridge an engine approval awaitable to an explicit Web decision endpoint."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[ApprovalRequest, asyncio.Future[ApprovalDecision]]] = {}
        self._closed = False

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        if self._closed:
            return ApprovalDecision.deny
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[request.id] = (request, future)
        try:
            return await future
        finally:
            self._pending.pop(request.id, None)

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRequest:
        pending = self._pending.get(approval_id)
        if pending is None:
            raise KeyError(approval_id)
        request, future = pending
        if request.requires_confirmation and decision == ApprovalDecision.allow_run:
            raise ValueError("this action requires a one-time decision")
        if future.done():
            raise RuntimeError("approval has already been resolved")
        future.set_result(decision)
        return request

    def request(self, approval_id: str) -> ApprovalRequest | None:
        pending = self._pending.get(approval_id)
        return pending[0] if pending else None

    def close(self) -> None:
        self._closed = True
        for _request, future in list(self._pending.values()):
            if not future.done():
                future.set_result(ApprovalDecision.deny)


class WebRunSupervisor:
    """Own background run tasks for one FastAPI process."""

    def __init__(
        self,
        harness: Harness,
        *,
        workspace_roots: list[str | Path] | None = None,
        execution_enabled: bool = True,
    ) -> None:
        self.harness = harness
        self.execution_enabled = execution_enabled
        roots = workspace_roots or [Path.cwd()]
        self.workspace_roots = {
            "default" if index == 0 else f"workspace-{index + 1}": Path(root)
            .expanduser()
            .resolve()
            for index, root in enumerate(roots)
        }
        self.approvals = PendingApprovalBroker()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._previous_approval_callback = harness.engine.approval_callback
        if execution_enabled:
            harness.set_approval_callback(self.approvals)

    def describe(self) -> dict[str, Any]:
        adapter = self.harness.providers.get("openai")
        providers = [
            {
                "name": "openai",
                "configured": bool(
                    adapter is not None and getattr(adapter, "api_key", True)
                ),
                "default_model": getattr(adapter, "default_model", None),
            }
        ]
        tools = []
        for name, tool in self.harness.tools.items():
            spec = tool.spec
            tools.append(
                {
                    "name": name,
                    "description": spec.description,
                    "effect": spec.effect.value,
                    "version": spec.version,
                    "timeout_s": spec.timeout_s,
                    "replay_policy": (
                        spec.replay_policy.value if spec.replay_policy else None
                    ),
                }
            )
        workspaces = [
            {"id": workspace_id, "name": root.name or workspace_id}
            for workspace_id, root in self.workspace_roots.items()
        ]
        return {
            "execution_enabled": self.execution_enabled,
            "default_provider": "openai",
            "providers": providers,
            "tools": tools,
            "workspaces": workspaces,
            "defaults": {
                "approval": ApprovalMode.ask.value,
                "allow_write": False,
            },
        }

    def _require_enabled(self) -> None:
        if not self.execution_enabled:
            raise PermissionError("Web execution is disabled for this server")

    def _resolve_cwd(self, workspace_id: str, relative: str | None) -> Path:
        root = self.workspace_roots.get(workspace_id)
        if root is None:
            raise ValueError("unknown workspace")
        candidate = Path(relative or ".")
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise ValueError("cwd must be relative to the configured workspace")
        resolved = (root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("cwd escapes the configured workspace")
        if not resolved.is_dir():
            raise ValueError("cwd does not exist or is not a directory")
        return resolved

    async def start(self, body: CreateRunBody) -> dict[str, Any]:
        self._require_enabled()
        if "openai" not in self.harness.providers:
            raise RuntimeError("OpenAI provider is unavailable")
        cwd = self._resolve_cwd(body.workspace_id, body.cwd)
        session_id = body.session_id or new_id()
        run_id = new_id()
        request = RunRequest(
            message=body.message,
            session_id=session_id,
            system=body.system,
            provider="openai",
            model=body.model,
            approval=body.approval,
            budget=BudgetConfig(
                max_steps=30,
                max_wall_time_s=600,
                max_tokens=100_000,
                max_context_tokens=64_000,
                max_output_length=200_000,
                max_delegate_depth=2,
                max_concurrent_children=2,
            ),
            cwd=str(cwd),
            allow_write=body.allow_write,
            metadata={"source": "web"},
        )
        task = asyncio.create_task(
            self.harness.run(request, run_id=run_id),
            name=f"agentharness-web-run-{run_id[:12]}",
        )
        self._track(run_id, task)
        await self._wait_until_visible(run_id, task)
        return {
            "run_id": run_id,
            "session_id": session_id,
            "status": "accepted",
            "run": self.harness.get_run(run_id),
        }

    async def resume(self, run_id: str, body: ResumeRunBody) -> dict[str, Any]:
        self._require_enabled()
        run = self.harness.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run_id in self._tasks and not self._tasks[run_id].done():
            raise RuntimeError("run is already active")
        task = asyncio.create_task(
            self.harness.resume(run_id, input=body.input),
            name=f"agentharness-web-resume-{run_id[:12]}",
        )
        self._track(run_id, task)
        await asyncio.sleep(0)
        if task.done():
            task.result()
        current = self.harness.get_run(run_id) or run
        return {
            "run_id": run_id,
            "session_id": current["session_id"],
            "status": "accepted",
            "run": current,
        }

    async def cancel(self, run_id: str) -> dict[str, Any]:
        self._require_enabled()
        await self.harness.cancel(run_id)
        return {"run_id": run_id, "run": self.harness.get_run(run_id)}

    def decide(self, approval_id: str, body: ApprovalDecisionBody) -> dict[str, Any]:
        self._require_enabled()
        decision = ApprovalDecision(body.decision)
        pending = self.approvals.request(approval_id)
        if pending is None:
            raise KeyError(approval_id)
        if pending.requires_confirmation and decision == ApprovalDecision.allow_run:
            raise ValueError("this action requires a one-time decision")
        if (
            pending.invocation_id != body.invocation_id
            or pending.arguments_sha256 != body.arguments_sha256
        ):
            raise RuntimeError("approval parameters do not match the pending invocation")
        if not self.harness.storage.resolve_approval(
            approval_id,
            decision.value,
            invocation_id=body.invocation_id,
            arguments_sha256=body.arguments_sha256,
        ):
            raise RuntimeError("approval has already been resolved or expired")
        request = self.approvals.resolve(approval_id, decision)
        return {
            "approval_id": approval_id,
            "run_id": request.run_id,
            "decision": decision.value,
        }

    def resolve_tool_recovery(
        self,
        invocation_id: str,
        body: ToolRecoveryDecisionBody,
    ) -> dict[str, Any]:
        self._require_enabled()
        invocation = self.harness.resolve_indeterminate_tool(
            invocation_id,
            body.decision,
            arguments_sha256=body.arguments_sha256,
        )
        return {
            "invocation_id": invocation.id,
            "run_id": invocation.run_id,
            "decision": body.decision.value,
            "invocation": invocation.model_dump(mode="json"),
        }

    def _track(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[run_id] = task

        def finished(done: asyncio.Task[Any]) -> None:
            if self._tasks.get(run_id) is done:
                self._tasks.pop(run_id, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - persist failure, keep server alive
                logger.exception("Web run task failed: %s", run_id)

        task.add_done_callback(finished)

    async def _wait_until_visible(
        self, run_id: str, task: asyncio.Task[Any]
    ) -> None:
        for _ in range(50):
            if self.harness.get_run(run_id) is not None:
                return
            if task.done():
                task.result()
                break
            await asyncio.sleep(0.01)
        if self.harness.get_run(run_id) is None:
            raise RuntimeError("run failed before it was persisted")

    async def aclose(self) -> None:
        self.approvals.close()
        active = list(self._tasks.items())
        for run_id, task in active:
            if task.done():
                continue
            with suppress(Exception):
                await self.harness.interrupt(run_id, "server_shutdown")
            task.cancel()
        if active:
            await asyncio.gather(*(task for _run_id, task in active), return_exceptions=True)
        self._tasks.clear()
        if self.harness.engine.approval_callback is self.approvals:
            self.harness.set_approval_callback(self._previous_approval_callback)


__all__ = [
    "ApprovalDecisionBody",
    "CreateRunBody",
    "ResumeRunBody",
    "ToolRecoveryDecisionBody",
    "WebRunSupervisor",
]
