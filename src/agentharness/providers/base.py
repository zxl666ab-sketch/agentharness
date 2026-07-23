"""Provider adapter base helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agentharness.contracts import ModelRequest, ModelStreamItem


class BaseModelAdapter:
    name: str = "base"

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise NotImplementedError
