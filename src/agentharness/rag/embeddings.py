"""Pluggable embedding interface (locked design: NOT enabled by default).

Stage-6 retrieval is hybrid FTS5 + structured-spec matching and does not use
embeddings. This module exists only as the allowed pluggable seam; the default
:class:`NoopEmbedder` returns ``None`` so no vector data is ever produced or
queried unless an explicit future implementation is configured.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    """Protocol for an optional embedding implementation."""

    def embed(self, text: str) -> list[float] | None:
        """Return a vector for ``text`` or ``None`` when embeddings are disabled."""
        ...


class NoopEmbedder:
    """Default embedder: embeddings are disabled (returns None)."""

    def embed(self, text: str) -> list[float] | None:  # noqa: ARG001 - interface seam
        return None


default_embedder: Embedder = NoopEmbedder()

__all__ = ["Embedder", "NoopEmbedder", "default_embedder"]
