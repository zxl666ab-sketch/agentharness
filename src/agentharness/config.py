"""Process configuration shared by the Web launcher and provider adapters."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# AGENT_INTERNAL_TOKEN 最低长度（字节）；显式设置但低于该值视为弱 token，启动即失败。
INTERNAL_TOKEN_MIN_BYTES = 32

# 进程内只生成一次的随机兜底 token：server.py 与 internal_agent.py 必须取到同一个值，
# 且不落盘、不打印全文（内部端点对外事实上关闭）。
_RANDOM_INTERNAL_TOKEN: str | None = None


def resolve_internal_token() -> str:
    """解析 AGENT_INTERNAL_TOKEN（Web 控制面与内部命令面共用，杜绝两处不一致）。

    - env 值 strip 后为空 → 视为未设置（空 token 永不允许通过认证）；
    - env 未设置 → 进程内生成一次临时随机 token（``secrets.token_urlsafe(32)``）；
    - env 显式设置但长度 < 32 字节 → 抛 RuntimeError（fail-fast）。
    """
    global _RANDOM_INTERNAL_TOKEN
    raw = (os.environ.get("AGENT_INTERNAL_TOKEN") or "").strip()
    if not raw:
        if _RANDOM_INTERNAL_TOKEN is None:
            _RANDOM_INTERNAL_TOKEN = secrets.token_urlsafe(INTERNAL_TOKEN_MIN_BYTES)
        return _RANDOM_INTERNAL_TOKEN
    if len(raw.encode("utf-8")) < INTERNAL_TOKEN_MIN_BYTES:
        raise RuntimeError(
            "AGENT_INTERNAL_TOKEN 必须 ≥32 字节随机值"
            '（可用 python -c "import secrets; print(secrets.token_urlsafe(32))" 生成）'
        )
    return raw


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


__all__ = [
    "INTERNAL_TOKEN_MIN_BYTES",
    "find_env_file",
    "load_project_env",
    "parse_env_file",
    "resolve_internal_token",
]
