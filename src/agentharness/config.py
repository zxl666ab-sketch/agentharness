"""Process configuration shared by the Web launcher and provider adapters."""

from __future__ import annotations

import os
from pathlib import Path

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def find_env_file(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).expanduser().resolve()
    home = Path.home().resolve()
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        if current == home or current.parent == current:
            return None
        current = current.parent
    return None


def load_project_env(start: Path | None = None) -> Path | None:
    if os.environ.get("AGENTHARNESS_NO_DOTENV", "").strip().lower() in _TRUE_VALUES:
        return None
    explicit = os.environ.get("AGENTHARNESS_ENV_FILE", "").strip()
    path = Path(explicit).expanduser().resolve() if explicit else find_env_file(start)
    if path is None or not path.is_file():
        return None
    try:
        values = parse_env_file(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return path


__all__ = ["find_env_file", "load_project_env", "parse_env_file"]
