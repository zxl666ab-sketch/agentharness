"""Load project ``.env`` files into process environment for CLI startup.

Does not override variables already present in ``os.environ``.
Disable with ``AGENTHARNESS_NO_DOTENV=1``.
"""

from __future__ import annotations

import os
from pathlib import Path

_DISABLE_VALUES = frozenset({"1", "true", "yes", "on"})


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in _DISABLE_VALUES


def parse_env_file(text: str) -> dict[str, str]:
    """Parse a minimal dotenv-style file into key/value pairs."""
    result: dict[str, str] = {}
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
        if not key or any(ch.isspace() for ch in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key] = value
    return result


def find_env_file(start: Path | None = None) -> Path | None:
    """Find ``.env`` starting at *start* (default cwd), walking parents.

    Stops at the filesystem root or the user home directory (inclusive search
    of the home directory itself, not above it).
    """
    current = (start or Path.cwd()).expanduser().resolve()
    home = Path.home().resolve()
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / ".env"
        if candidate.is_file():
            return candidate
        if current == home or current.parent == current:
            break
        current = current.parent
    return None


def apply_env_values(values: dict[str, str], *, override: bool = False) -> list[str]:
    """Apply parsed values to ``os.environ``. Return keys that were set."""
    applied: list[str] = []
    for key, value in values.items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


def load_project_env(
    start: Path | None = None,
    *,
    override: bool = False,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Load the nearest project ``.env`` into the process environment.

    Returns the path loaded, or ``None`` if nothing was loaded.
    """
    env = environ if environ is not None else os.environ
    if _truthy(env.get("AGENTHARNESS_NO_DOTENV")):
        return None

    explicit = (env.get("AGENTHARNESS_ENV_FILE") or "").strip()
    path: Path | None
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            return None
    else:
        path = find_env_file(start)
        if path is None:
            return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    values = parse_env_file(text)
    for key, value in values.items():
        if not override and key in env:
            continue
        env[key] = value
    return path
