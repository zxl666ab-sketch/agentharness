"""Security / env-loading behavior at the public CLI process boundary."""

from __future__ import annotations

import os
from pathlib import Path

from agentharness.cli.envfile import find_env_file, load_project_env, parse_env_file


def test_parse_env_file_supports_export_and_quotes() -> None:
    values = parse_env_file(
        """
# comment
export OPENAI_API_KEY="sk-test"
OPENAI_MODEL=deepseek-v4-flash
ANTHROPIC_API_KEY=
"""
    )
    assert values["OPENAI_API_KEY"] == "sk-test"
    assert values["OPENAI_MODEL"] == "deepseek-v4-flash"
    assert values["ANTHROPIC_API_KEY"] == ""


def test_load_project_env_sets_missing_keys_only(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=sk-from-file\nOPENAI_MODEL=model-from-file\nOPENAI_BASE_URL=https://example.test\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENTHARNESS_NO_DOTENV", raising=False)
    monkeypatch.delenv("AGENTHARNESS_ENV_FILE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-already-set")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    loaded = load_project_env(tmp_path)
    assert loaded == env_path.resolve()
    assert os.environ["OPENAI_API_KEY"] == "sk-already-set"
    assert os.environ["OPENAI_MODEL"] == "model-from-file"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.test"


def test_load_project_env_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-should-not-load\n", encoding="utf-8")
    monkeypatch.setenv("AGENTHARNESS_NO_DOTENV", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_project_env(tmp_path) is None
    assert "OPENAI_API_KEY" not in os.environ


def test_find_env_file_walks_parents(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    child = root / "nested" / "deep"
    child.mkdir(parents=True)
    env_path = root / ".env"
    env_path.write_text("OPENAI_MODEL=walked\n", encoding="utf-8")
    assert find_env_file(child) == env_path.resolve()


def test_cli_callback_loads_env_before_provider_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from agentharness.cli.main import app

    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=sk-cli-env\nOPENAI_MODEL=cli-env-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTHARNESS_NO_DOTENV", raising=False)
    monkeypatch.delenv("AGENTHARNESS_ENV_FILE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = CliRunner().invoke(app, ["doctor", "--data-dir", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    assert "cli-env-model" in result.output
    assert "openai" in result.output.lower()
