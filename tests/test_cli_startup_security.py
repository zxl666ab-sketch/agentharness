"""Security behavior at the public CLI process boundary."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def test_cli_startup_does_not_implicitly_load_dotenv(monkeypatch) -> None:
    calls: list[object] = []
    fake_dotenv = ModuleType("dotenv")

    def record_load(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    fake_dotenv.load_dotenv = record_load  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.delitem(sys.modules, "agentharness.cli.main", raising=False)

    importlib.import_module("agentharness.cli.main")

    assert calls == []
