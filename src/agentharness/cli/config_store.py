"""Persistent CLI provider/model profile stored under the harness data dir.

Secrets live only in ``cli_config.json`` under the data directory (default
``~/.agentharness``). They are never sent to the Web API or written to git.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "cli_config.json"
CONFIG_VERSION = 2
LEGACY_BACKUP_FILENAME = "cli_config.json.v1.bak"
KNOWN_PROVIDERS = ("fake", "openai", "anthropic")


def _empty_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "active_profile": None,
        "profiles": {},
        "provider": None,
        "model": None,
        "providers": {},
    }


def config_path(data_dir: Path | str) -> Path:
    return Path(data_dir).expanduser().resolve() / CONFIG_FILENAME


def load_config(data_dir: Path | str) -> dict[str, Any]:
    path = config_path(data_dir)
    if not path.exists():
        return _empty_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _empty_config()
    if not isinstance(raw, dict):
        return _empty_config()
    migrated, changed = _normalize_config(raw)
    if changed:
        backup = path.with_name(LEGACY_BACKUP_FILENAME)
        if not backup.exists():
            shutil.copyfile(path, backup)
            restrict_file_permissions(backup)
        _write_config(path, migrated)
    return migrated


def save_config(data_dir: Path | str, data: dict[str, Any]) -> Path:
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload, _ = _normalize_config(data)
    # Guard: never let a blank api_key clobber a previously-stored valid key. A caller
    # that omits or blanks the key (e.g. a /config edit that only touches the model)
    # must not silently wipe working credentials.
    _preserve_existing_api_keys(path, payload)
    _write_config(path, payload)
    return path


def _preserve_existing_api_keys(path: Path, payload: dict[str, Any]) -> None:
    """Re-fill blank api_key fields in ``payload`` from the on-disk config, per profile
    and per provider entry. A missing/empty incoming key keeps the existing secret."""
    if not path.exists():
        return
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return
    if not isinstance(previous, dict):
        return

    def _restore(target_map: Any, prev_map: Any) -> None:
        if not isinstance(target_map, dict) or not isinstance(prev_map, dict):
            return
        for name, entry in target_map.items():
            if not isinstance(entry, dict):
                continue
            incoming = entry.get("api_key")
            if isinstance(incoming, str) and incoming.strip():
                continue  # a real key was provided — keep it
            prev_entry = prev_map.get(name)
            prev_key = prev_entry.get("api_key") if isinstance(prev_entry, dict) else None
            if isinstance(prev_key, str) and prev_key.strip():
                entry["api_key"] = prev_key

    _restore(payload.get("profiles"), previous.get("profiles"))
    _restore(payload.get("providers"), previous.get("providers"))


def _write_config(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    restrict_file_permissions(temporary)
    os.replace(temporary, path)
    restrict_file_permissions(path)


def _normalize_config(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade legacy provider settings while preserving every unknown field."""
    data = dict(raw)
    legacy_providers = raw.get("providers")
    providers = dict(legacy_providers) if isinstance(legacy_providers, dict) else {}
    raw_profiles = raw.get("profiles")
    profiles = {
        str(name): dict(value)
        for name, value in (raw_profiles.items() if isinstance(raw_profiles, dict) else [])
        if isinstance(value, dict)
    }

    active = raw.get("active_profile")
    active_profile = str(active) if active and str(active) in profiles else None
    legacy_provider = str(raw.get("provider") or "").strip() or None
    legacy_model = str(raw.get("model") or "").strip() or None

    if not profiles and providers:
        for provider, value in providers.items():
            if not isinstance(value, dict):
                continue
            name = str(provider)
            profile = dict(value)
            profile["provider"] = name
            profile_model = str(profile.get("model") or "").strip() or None
            if name == legacy_provider and legacy_model:
                profile["model"] = legacy_model
                profile_model = legacy_model
            profile["recent_models"] = _recent_models(profile, profile_model)
            profiles[name] = profile

    if legacy_provider and legacy_provider not in profiles:
        entry = providers.get(legacy_provider)
        profile = dict(entry) if isinstance(entry, dict) else {}
        profile["provider"] = legacy_provider
        if legacy_model:
            profile["model"] = legacy_model
        profile["recent_models"] = _recent_models(profile, legacy_model)
        profiles[legacy_provider] = profile

    if active_profile is None:
        if legacy_provider in profiles:
            active_profile = legacy_provider
        elif profiles:
            active_profile = next(iter(profiles))

    for name, profile in profiles.items():
        provider = str(profile.get("provider") or name)
        profile["provider"] = provider
        current_model = str(profile.get("model") or "").strip() or None
        profile["recent_models"] = _recent_models(profile, current_model)

    active_entry = profiles.get(active_profile or "", {})
    active_provider = str(active_entry.get("provider") or legacy_provider or "").strip() or None
    active_model = str(active_entry.get("model") or legacy_model or "").strip() or None

    compatibility = dict(providers)
    for profile in profiles.values():
        provider = str(profile.get("provider") or "").strip()
        if not provider:
            continue
        entry = dict(compatibility.get(provider) or {})
        entry.update(
            {
                key: value
                for key, value in profile.items()
                if key not in {"provider", "recent_models"}
            }
        )
        compatibility[provider] = entry

    data.update(
        {
            "version": CONFIG_VERSION,
            "active_profile": active_profile,
            "profiles": profiles,
            "provider": active_provider,
            "model": active_model,
            "providers": compatibility,
        }
    )
    return data, data != raw


def _recent_models(profile: dict[str, Any], current: str | None) -> list[str]:
    raw = profile.get("recent_models")
    candidates = list(raw) if isinstance(raw, list) else []
    ordered = ([current] if current else []) + [str(item) for item in candidates if item]
    return list(dict.fromkeys(ordered))[:20]


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
    profile: str | None = None


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
    active_profile = cfg.get("active_profile")
    profile = (cfg.get("profiles") or {}).get(active_profile) or {}
    if not isinstance(profile, dict):
        profile = {}

    if provider and provider not in ("auto", ""):
        prov = provider
        source = "flag"
    elif profile.get("provider") or cfg.get("provider"):
        prov = str(profile.get("provider") or cfg["provider"])
        source = "profile"
    else:
        env_prov = resolve_default_provider(None)
        prov = env_prov
        source = "env" if env_prov != "fake" else "fake"

    if profile.get("provider") == prov:
        pcfg = profile
    else:
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
        profile=str(active_profile) if active_profile and profile.get("provider") == prov else None,
    )


def create_profile(
    data_dir: Path | str,
    name: str,
    *,
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    profile_name = name.strip()
    if not profile_name:
        raise ValueError("profile name cannot be empty")
    cfg = load_config(data_dir)
    profiles = dict(cfg.get("profiles") or {})
    entry = dict(profiles.get(profile_name) or {})
    entry["provider"] = provider
    if model is not None:
        entry["model"] = model
        entry["recent_models"] = _recent_models(entry, model)
    if api_key is not None:
        entry["api_key"] = api_key
    if base_url is not None:
        entry["base_url"] = base_url
    profiles[profile_name] = entry
    cfg["profiles"] = profiles
    cfg["active_profile"] = profile_name
    save_config(data_dir, cfg)
    return load_config(data_dir)


def activate_profile(data_dir: Path | str, name: str) -> dict[str, Any]:
    cfg = load_config(data_dir)
    if name not in (cfg.get("profiles") or {}):
        raise KeyError(name)
    cfg["active_profile"] = name
    save_config(data_dir, cfg)
    return load_config(data_dir)


def model_choices(data_dir: Path | str, profile_name: str | None = None) -> list[str]:
    cfg = load_config(data_dir)
    name = profile_name or cfg.get("active_profile")
    entry = (cfg.get("profiles") or {}).get(name) or {}
    if not isinstance(entry, dict):
        return []
    current = str(entry.get("model") or "").strip() or None
    return _recent_models(entry, current)


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
    profiles = dict(cfg.get("profiles") or {})
    active_profile = cfg.get("active_profile")
    profile_name = (
        str(active_profile)
        if active_profile
        and isinstance(profiles.get(str(active_profile)), dict)
        and profiles[str(active_profile)].get("provider") == provider
        else provider
    )
    profile_entry = dict(profiles.get(profile_name) or {})
    profile_entry["provider"] = provider
    providers = dict(cfg.get("providers") or {})
    entry = dict(providers.get(provider) or {})
    if model is not None:
        entry["model"] = model
        entry["recent_models"] = _recent_models(entry, model)
        profile_entry["model"] = model
        profile_entry["recent_models"] = _recent_models(profile_entry, model)
        cfg["model"] = model
    if api_key is not None:
        entry["api_key"] = api_key
        profile_entry["api_key"] = api_key
    if base_url is not None:
        entry["base_url"] = base_url
        profile_entry["base_url"] = base_url
    providers[provider] = entry
    profiles[profile_name] = profile_entry
    cfg["providers"] = providers
    cfg["profiles"] = profiles
    if set_active:
        cfg["active_profile"] = profile_name
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
