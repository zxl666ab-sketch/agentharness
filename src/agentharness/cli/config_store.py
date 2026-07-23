"""Persistent CLI provider/model profile stored under the harness data dir.

Secrets live only in ``cli_config.json`` under the data directory (default
``~/.agentharness``). They are never sent to the Web API or written to git.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "cli_config.json"
KNOWN_PROVIDERS = ("fake", "openai", "anthropic")


def config_path(data_dir: Path | str) -> Path:
    return Path(data_dir).expanduser().resolve() / CONFIG_FILENAME


def load_config(data_dir: Path | str) -> dict[str, Any]:
    path = config_path(data_dir)
    if not path.exists():
        return {"provider": None, "model": None, "providers": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"provider": None, "model": None, "providers": {}}
    if not isinstance(raw, dict):
        return {"provider": None, "model": None, "providers": {}}
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    return {
        "provider": raw.get("provider") or None,
        "model": raw.get("model") or None,
        "providers": providers,
    }


def save_config(data_dir: Path | str, data: dict[str, Any]) -> Path:
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": data.get("provider"),
        "model": data.get("model"),
        "providers": data.get("providers") or {},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    restrict_file_permissions(path)
    return path


def restrict_file_permissions(path: Path) -> None:
    """Best-effort owner-only permissions for the secrets file."""
    try:
        if os.name == "nt":
            import subprocess

            user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
        else:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def mask_secret(value: str | None) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-4:]}"


def summarize_arguments(arguments: Any, *, max_len: int = 160) -> str:
    """Compact args preview for timeline / events (no secret keys)."""
    if arguments is None:
        return ""
    if not isinstance(arguments, dict):
        text = str(arguments)
        return text if len(text) <= max_len else text[: max_len - 1] + "…"
    preferred = (
        "action",
        "url",
        "path",
        "command",
        "query",
        "method",
        "selector",
        "name",
        "skill",
        "memory",
        "context_id",
    )
    secret_keys = {"api_key", "token", "password", "authorization", "secret", "key"}
    parts: list[str] = []
    for key in preferred:
        if key in arguments and arguments[key] not in (None, ""):
            val = arguments[key]
            if isinstance(val, str) and len(val) > 80:
                val = val[:79] + "…"
            parts.append(f"{key}={val}")
    if not parts:
        for key, val in list(arguments.items())[:4]:
            if str(key).lower() in secret_keys or "token" in str(key).lower():
                parts.append(f"{key}=[REDACTED]")
            else:
                rendered = val if not isinstance(val, str) or len(val) <= 60 else val[:59] + "…"
                parts.append(f"{key}={rendered}")
    text = " ".join(parts)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


@dataclass
class RuntimeSettings:
    provider: str
    model: str | None
    api_key: str | None = None
    base_url: str | None = None
    source: str = "default"  # flag | profile | env | fake


def resolve_runtime_settings(
    data_dir: Path | str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> RuntimeSettings:
    """Priority: CLI flag > saved profile > environment > fake.

    Does **not** load a project ``.env`` file. Export vars or use ``/config``.
    """
    from agentharness.cli.provider_defaults import resolve_default_model, resolve_default_provider

    cfg = load_config(data_dir)
    source = "fake"

    if provider and provider not in ("auto", ""):
        prov = provider
        source = "flag"
    elif cfg.get("provider"):
        prov = str(cfg["provider"])
        source = "profile"
    else:
        env_prov = resolve_default_provider(None)
        prov = env_prov
        source = "env" if env_prov != "fake" else "fake"

    pcfg = (cfg.get("providers") or {}).get(prov) or {}
    if not isinstance(pcfg, dict):
        pcfg = {}

    if model:
        mod: str | None = model
        if source != "flag":
            source = "flag"
    elif cfg.get("model") and (cfg.get("provider") in (None, prov) or not cfg.get("provider")):
        mod = str(cfg["model"]) if cfg.get("model") else None
        if source == "fake":
            source = "profile"
    elif pcfg.get("model"):
        mod = str(pcfg["model"])
        if source == "fake":
            source = "profile"
    else:
        mod = resolve_default_model(prov, None)

    api_key = pcfg.get("api_key") or None
    base_url = pcfg.get("base_url") or None
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    if isinstance(base_url, str):
        base_url = base_url.strip() or None

    return RuntimeSettings(
        provider=prov,
        model=mod,
        api_key=api_key,
        base_url=base_url,
        source=source,
    )


def update_provider_fields(
    data_dir: Path | str,
    provider: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    set_active: bool = True,
) -> dict[str, Any]:
    cfg = load_config(data_dir)
    providers = dict(cfg.get("providers") or {})
    entry = dict(providers.get(provider) or {})
    if model is not None:
        entry["model"] = model
        cfg["model"] = model
    if api_key is not None:
        entry["api_key"] = api_key
    if base_url is not None:
        entry["base_url"] = base_url
    providers[provider] = entry
    cfg["providers"] = providers
    if set_active:
        cfg["provider"] = provider
    save_config(data_dir, cfg)
    return cfg


def apply_settings_to_harness(harness: Any, settings: RuntimeSettings) -> None:
    """Rebuild provider adapters so session-level /config takes effect immediately.

    Does not clobber an existing custom ``fake`` adapter (tests / scripts inject one).
    """
    from agentharness.providers.anthropic_adapter import AnthropicMessagesAdapter
    from agentharness.providers.fake import FakeModelAdapter
    from agentharness.providers.openai_adapter import OpenAIResponsesAdapter

    if settings.provider == "openai":
        harness.register_provider(
            "openai",
            OpenAIResponsesAdapter(
                api_key=settings.api_key,
                base_url=settings.base_url,
                default_model=settings.model,
            ),
        )
    elif settings.provider == "anthropic":
        harness.register_provider(
            "anthropic",
            AnthropicMessagesAdapter(
                api_key=settings.api_key,
                base_url=settings.base_url,
                default_model=settings.model,
            ),
        )
    elif settings.provider == "fake":
        existing = getattr(harness, "providers", {}).get("fake")
        # Keep injected scripted fakes; only ensure a default exists.
        if existing is None:
            harness.register_provider("fake", FakeModelAdapter())
