"""Default provider resolution: explicit arg → OpenAI env → Anthropic env → fake."""

from __future__ import annotations

import os


def resolve_default_provider(explicit: str | None = None) -> str:
    """Return provider name using priority: explicit → OPENAI_* → ANTHROPIC_* → fake.

    ``explicit`` is treated as set when non-empty and not the sentinel ``auto``.
    CLI options may pass ``None`` or omit the flag to use env detection.
    """
    if explicit and explicit not in ("auto", ""):
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "fake"


def resolve_default_model(provider: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    if provider == "openai":
        return os.environ.get("OPENAI_MODEL") or None
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL") or None
    return None
