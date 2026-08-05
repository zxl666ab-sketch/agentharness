"""Lifecycle glue for procurement runs that await a human supplier decision.

The procurement API owns run creation and resumption through
``ProcurementAgent``.  This module intentionally contains no generic Web run
request, workspace, shell-verification, cancellation, or recovery surface.
It only bridges the Harness approval callback to the procurement workflow and
cleans that callback up when the FastAPI process stops.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentharness.contracts import ApprovalDecision, ApprovalRequest
from agentharness.harness import Harness


class PendingApprovalBroker:
    """Bridge an engine approval awaitable to the procurement approval flow."""

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
    """Attach the procurement approval broker for one FastAPI process.

    ``workspace_roots`` remains accepted by ``create_app`` for launch-command
    compatibility, but procurement runs do not expose host workspace tools and
    therefore never use it.
    """

    def __init__(
        self,
        harness: Harness,
        *,
        workspace_roots: list[str | Path] | None = None,
        execution_enabled: bool = True,
    ) -> None:
        self.harness = harness
        self.execution_enabled = execution_enabled
        self.approvals = PendingApprovalBroker()
        self._previous_approval_callback = harness.engine.approval_callback
        # Keep the accepted server argument without retaining an unused generic
        # workspace registry or re-exposing arbitrary local paths.
        _ = workspace_roots
        if execution_enabled:
            harness.set_approval_callback(self.approvals)

    async def aclose(self) -> None:
        self.approvals.close()
        if self.harness.engine.approval_callback is self.approvals:
            self.harness.set_approval_callback(self._previous_approval_callback)


__all__ = ["PendingApprovalBroker", "WebRunSupervisor"]
