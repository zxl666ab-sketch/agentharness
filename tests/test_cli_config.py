"""CLI profile persistence, slash commands, and completion helpers."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from agentharness.cli.config_store import (
    apply_settings_to_harness,
    load_config,
    mask_secret,
    resolve_runtime_settings,
    save_config,
    summarize_arguments,
    update_provider_fields,
)
from agentharness.cli.input import complete_slash_line, match_slash_commands
from agentharness.cli.interactive import run_interactive
from agentharness.harness import Harness
from agentharness.providers.fake import FakeModelAdapter


def test_config_roundtrip(tmp_path: Path) -> None:
    save_config(
        tmp_path,
        {
            "provider": "openai",
            "model": "grok-4.5",
            "providers": {
                "openai": {
                    "api_key": "sk-test-secret-key-123456",
                    "base_url": "https://example.test/v1",
                    "model": "grok-4.5",
                }
            },
        },
    )
    cfg = load_config(tmp_path)
    assert cfg["provider"] == "openai"
    assert cfg["providers"]["openai"]["api_key"] == "sk-test-secret-key-123456"
    settings = resolve_runtime_settings(tmp_path)
    assert settings.provider == "openai"
    assert settings.model == "grok-4.5"
    assert settings.api_key == "sk-test-secret-key-123456"
    assert settings.base_url == "https://example.test/v1"
    assert settings.source == "profile"
    assert "sk-test" not in mask_secret(settings.api_key)
    assert "…" in mask_secret(settings.api_key) or "****" in mask_secret(settings.api_key)


def test_cli_flag_overrides_profile(tmp_path: Path, monkeypatch) -> None:
    save_config(
        tmp_path,
        {
            "provider": "openai",
            "model": "from-profile",
            "providers": {"openai": {"model": "from-profile", "api_key": "k"}},
        },
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = resolve_runtime_settings(tmp_path, provider="fake", model=None)
    assert settings.provider == "fake"
    assert settings.source == "flag"


def test_env_used_when_no_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    settings = resolve_runtime_settings(tmp_path)
    assert settings.provider == "openai"
    assert settings.model == "env-model"
    assert settings.source == "env"


def test_update_provider_fields_persists(tmp_path: Path) -> None:
    update_provider_fields(tmp_path, "openai", api_key="abc123456789", model="m1")
    cfg = load_config(tmp_path)
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "m1"
    assert cfg["providers"]["openai"]["api_key"] == "abc123456789"


def test_slash_completion() -> None:
    assert "/model" in match_slash_commands("/m")
    line, matches = complete_slash_line("/mo")
    assert "/model" in matches
    assert line.startswith("/model")
    line2, matches2 = complete_slash_line("/help")
    assert matches2 == ["/help"]
    assert line2 == "/help"


def test_summarize_arguments_prefers_action_url() -> None:
    text = summarize_arguments(
        {"action": "goto", "url": "https://search.bilibili.com/upuser?keyword=x", "noise": 1}
    )
    assert "action=goto" in text
    assert "bilibili" in text


def test_interactive_model_provider_config_commands(tmp_path: Path, monkeypatch) -> None:
    harness = Harness(
        data_dir=tmp_path,
        providers={"fake": FakeModelAdapter(script=[{"kind": "text", "text": "ok"}])},
        tools={},
    )
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    monkeypatch.setattr(
        "sys.stdin",
        StringIO(
            "/provider fake\n"
            "/model toy-model\n"
            "/config set api_key sk-super-secret-value-999\n"
            "/config\n"
            "/help\n"
            "/quit\n"
        ),
    )
    # Force non-tty path for redirected_input
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        run_interactive(
            harness=harness,
            console=console,
            provider="fake",
            model=None,
            approval="auto",
            cwd=str(tmp_path),
            data_dir=tmp_path,
        )
    finally:
        harness.close()

    cfg = load_config(tmp_path)
    assert cfg["provider"] == "fake"
    assert cfg["model"] == "toy-model"
    # api_key saved under active provider (fake)
    assert cfg["providers"]["fake"]["api_key"] == "sk-super-secret-value-999"
    output = console.file.getvalue()  # type: ignore[union-attr]
    assert "toy-model" in output or "model" in output.lower()
    assert "sk-super-secret-value-999" not in output  # must be masked in /config display


def test_apply_settings_registers_openai(tmp_path: Path) -> None:
    harness = Harness(data_dir=tmp_path)
    try:
        settings = resolve_runtime_settings(tmp_path, provider="openai", model="gpt-test")
        # no key in profile; still registers adapter
        apply_settings_to_harness(harness, settings)
        assert "openai" in harness.providers
    finally:
        harness.close()
