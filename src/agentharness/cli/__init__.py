"""CLI package with a lazy app export for `python -m` compatibility."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentharness.cli.main import app as app

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from agentharness.cli.main import app

        return app
    raise AttributeError(name)
