"""Asynchronous cancellation token and task lifecycle management."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


class TaskCancelledException(Exception):
    """Raised when an asynchronous agent workflow task is requested to cancel."""


TaskCancelledError = TaskCancelledException


class CancellationToken:
    """Cooperative cancellation token for async Agent workflows and tool executions."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._is_cancelled = False
        self._cancel_reason: str | None = None
        self._callbacks: list[Callable[[], Any]] = []

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    @property
    def cancel_reason(self) -> str | None:
        return self._cancel_reason

    def cancel(self, reason: str = "User cancelled") -> None:
        if self._is_cancelled:
            return
        self._is_cancelled = True
        self._cancel_reason = reason
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    def register_callback(self, callback: Callable[[], Any]) -> None:
        if self._is_cancelled:
            callback()
        else:
            self._callbacks.append(callback)

    def throw_if_cancelled(self) -> None:
        if self._is_cancelled:
            raise TaskCancelledException(f"Task {self.task_id} was cancelled: {self._cancel_reason}")


class SagaCompensationManager:
    """Manages rollback and cleanup actions when workflow fails or is cancelled."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._compensations: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []

    def register(self, name: str, compensation_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register a compensating step to run in reverse order upon failure."""
        self._compensations.append((name, compensation_fn))

    async def execute_compensations(self) -> list[str]:
        """Execute all registered compensations in LIFO (reverse) order."""
        executed: list[str] = []
        for name, fn in reversed(self._compensations):
            try:
                await fn()
                executed.append(name)
            except Exception:
                # Log but continue remaining compensations
                pass
        return executed
