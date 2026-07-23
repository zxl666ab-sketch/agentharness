"""CLI profile persistence, slash commands, and completion helpers."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.console import Console

from agentharness.cli.config_store import (
    activate_profile,
    apply_settings_to_harness,
    create_profile,
    load_config,
    mask_secret,
    model_choices,
    resolve_runtime_settings,
    save_config,
    summarize_arguments,
    update_provider_fields,
)
from agentharness.cli.input import (
    SlashCommandCompleter,
    complete_slash_line,
    match_slash_commands,
)
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


def test_legacy_config_migrates_losslessly_once_with_backup(tmp_path: Path) -> None:
    original = {
        "provider": "openai",
        "model": "gpt-legacy",
        "providers": {
            "openai": {
                "api_key": "sk-legacy-secret",
                "base_url": "https://legacy.example/v1",
                "model": "gpt-legacy",
                "vendor_extension": {"region": "test"},
            },
            "custom": {"opaque": True},
        },
        "future_top_level": {"preserve": [1, 2, 3]},
    }
    path = tmp_path / "cli_config.json"
    original_text = json.dumps(original, indent=2, ensure_ascii=False) + "\n"
    path.write_text(original_text, encoding="utf-8")

    first = load_config(tmp_path)
    migrated_text = path.read_text(encoding="utf-8")
    backup = tmp_path / "cli_config.json.v1.bak"

    assert first["version"] == 2
    assert first["active_profile"] == "openai"
    assert first["profiles"]["openai"] == {
        "provider": "openai",
        "api_key": "sk-legacy-secret",
        "base_url": "https://legacy.example/v1",
        "model": "gpt-legacy",
        "vendor_extension": {"region": "test"},
        "recent_models": ["gpt-legacy"],
    }
    assert first["future_top_level"] == original["future_top_level"]
    assert first["providers"]["custom"] == {"opaque": True}
    assert backup.read_text(encoding="utf-8") == original_text

    second = load_config(tmp_path)
    assert second == first
    assert path.read_text(encoding="utf-8") == migrated_text
    assert backup.read_text(encoding="utf-8") == original_text


def test_blank_api_key_does_not_overwrite_stored_secret(tmp_path: Path) -> None:
    """Goal 9: a save that blanks/omits api_key must not wipe a valid stored key."""
    create_profile(
        tmp_path, "work", provider="openai", api_key="sk-keep-me", model="gpt-1"
    )
    # A later edit that only changes the model and carries a blank key.
    cfg = load_config(tmp_path)
    cfg["profiles"]["work"]["api_key"] = ""
    cfg["profiles"]["work"]["model"] = "gpt-2"
    save_config(tmp_path, cfg)

    reloaded = load_config(tmp_path)
    assert reloaded["profiles"]["work"]["api_key"] == "sk-keep-me"
    assert reloaded["profiles"]["work"]["model"] == "gpt-2"
    settings = resolve_runtime_settings(tmp_path)
    assert settings.api_key == "sk-keep-me"

    # An explicit non-empty key still overwrites.
    cfg2 = load_config(tmp_path)
    cfg2["profiles"]["work"]["api_key"] = "sk-rotated"
    save_config(tmp_path, cfg2)
    assert load_config(tmp_path)["profiles"]["work"]["api_key"] == "sk-rotated"


def test_named_profiles_persist_active_credentials_and_recent_models(tmp_path: Path) -> None:
    create_profile(
        tmp_path,
        "work",
        provider="openai",
        api_key="sk-work",
        base_url="https://work.example/v1",
        model="gpt-work",
    )
    create_profile(
        tmp_path,
        "personal",
        provider="anthropic",
        api_key="ak-personal",
        model="claude-personal",
    )
    activate_profile(tmp_path, "work")
    update_provider_fields(tmp_path, "openai", model="gpt-next")

    settings = resolve_runtime_settings(tmp_path)
    assert settings.profile == "work"
    assert settings.provider == "openai"
    assert settings.api_key == "sk-work"
    assert settings.base_url == "https://work.example/v1"
    assert settings.model == "gpt-next"
    assert model_choices(tmp_path) == ["gpt-next", "gpt-work"]

    activate_profile(tmp_path, "personal")
    restarted = resolve_runtime_settings(tmp_path)
    assert restarted.profile == "personal"
    assert restarted.provider == "anthropic"
    assert restarted.api_key == "ak-personal"
    assert restarted.model == "claude-personal"


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


def test_slash_menu_opens_as_soon_as_slash_is_typed() -> None:
    completions = list(
        SlashCommandCompleter().get_completions(
            Document("/", cursor_position=1),
            CompleteEvent(text_inserted=True),
        )
    )

    assert {item.text for item in completions} >= {"/model", "/provider", "/config"}
    assert all(item.start_position == -1 for item in completions)


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


def test_named_profile_switch_applies_to_the_next_piped_run(
    tmp_path: Path, monkeypatch
) -> None:
    create_profile(tmp_path, "work", provider="fake", model="model-work")
    create_profile(tmp_path, "personal", provider="fake", model="model-personal")
    harness = Harness(data_dir=tmp_path)
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    monkeypatch.setattr(
        "sys.stdin",
        StringIO(
            "/profile work\n"
            "[fake:text]work result\n"
            "/profile personal\n"
            "[fake:text]personal result\n"
            "/quit\n"
        ),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        run_interactive(
            harness=harness,
            console=console,
            provider="auto",
            model=None,
            approval="auto",
            cwd=str(tmp_path),
            data_dir=tmp_path,
        )
    finally:
        harness.close()

    observer = Harness(data_dir=tmp_path)
    try:
        rows = observer.list_runs(limit=10)
    finally:
        observer.close()

    assert [row["model"] for row in rows] == ["model-personal", "model-work"]
    assert load_config(tmp_path)["active_profile"] == "personal"


def test_idle_tty_ctrl_c_exits_cleanly(tmp_path: Path, monkeypatch) -> None:
    class InterruptingComposer:
        def __init__(self, _commands, **_kwargs) -> None:
            pass

        def read(self) -> str:
            raise KeyboardInterrupt

    harness = Harness(data_dir=tmp_path)
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "agentharness.cli.interactive.TtyComposer", InterruptingComposer
    )

    run_interactive(
        harness=harness,
        console=console,
        provider="fake",
        model=None,
        approval="auto",
        cwd=str(tmp_path),
        data_dir=tmp_path,
    )

    assert "No run is active; exiting." in console.file.getvalue()  # type: ignore[union-attr]


def test_apply_settings_registers_openai(tmp_path: Path) -> None:
    harness = Harness(data_dir=tmp_path)
    try:
        settings = resolve_runtime_settings(tmp_path, provider="openai", model="gpt-test")
        # no key in profile; still registers adapter
        apply_settings_to_harness(harness, settings)
        assert "openai" in harness.providers
    finally:
        harness.close()
