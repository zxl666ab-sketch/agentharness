from __future__ import annotations

import os
from pathlib import Path

import agentharness.config as config


def test_parse_env_file_accepts_common_dotenv_syntax() -> None:
    values = config.parse_env_file(
        """
        # ignored

        export API_KEY = \"secret\"
        EMPTY=
        SINGLE='value with spaces'
        UNQUOTED = plain value
        missing-separator
        =missing-key
        invalid key=value
        """
    )

    assert values == {
        "API_KEY": "secret",
        "EMPTY": "",
        "SINGLE": "value with spaces",
        "UNQUOTED": "plain value",
    }


def test_find_env_file_walks_up_and_stops_at_home(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    nested = root / "nested" / "deeper"
    nested.mkdir(parents=True)
    env_file = root / ".env"
    env_file.write_text("FOUND=yes\n", encoding="utf-8")
    monkeypatch.setattr(config.Path, "home", lambda: root)

    assert config.find_env_file(nested) == env_file

    env_file.unlink()
    assert config.find_env_file(nested) is None


def test_load_project_env_respects_opt_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTHARNESS_NO_DOTENV", "On")

    assert config.load_project_env(tmp_path) is None


def test_load_project_env_loads_explicit_file_without_overwriting(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("NEW_VALUE=loaded\nEXISTING=from-file\n", encoding="utf-8")
    monkeypatch.delenv("AGENTHARNESS_NO_DOTENV", raising=False)
    monkeypatch.setenv("AGENTHARNESS_ENV_FILE", str(env_file))
    monkeypatch.setenv("EXISTING", "already-set")

    assert config.load_project_env() == env_file.resolve()
    assert os.environ["NEW_VALUE"] == "loaded"
    assert os.environ["EXISTING"] == "already-set"


def test_load_project_env_ignores_missing_or_unreadable_file(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.env"
    monkeypatch.delenv("AGENTHARNESS_NO_DOTENV", raising=False)
    monkeypatch.setenv("AGENTHARNESS_ENV_FILE", str(missing))
    assert config.load_project_env() is None

    env_file = tmp_path / "unreadable.env"
    env_file.write_text("VALUE=present\n", encoding="utf-8")
    monkeypatch.setenv("AGENTHARNESS_ENV_FILE", str(env_file))

    def raise_os_error(self: Path, *, encoding: str) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(config.Path, "read_text", raise_os_error)
    assert config.load_project_env() is None
