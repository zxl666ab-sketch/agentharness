from __future__ import annotations

import sys
from pathlib import Path

import pytest

import agentharness.web_main as web_main


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("not an address", False),
    ],
)
def test_is_loopback_accepts_only_local_hosts(host: str, expected: bool) -> None:
    assert web_main._is_loopback(host) is expected


def _capture_launcher(monkeypatch):
    calls: dict[str, object] = {}
    monkeypatch.setattr(web_main, "load_project_env", lambda: calls.setdefault("env", True))

    def create_app(**kwargs):
        calls["app_kwargs"] = kwargs
        return "app"

    monkeypatch.setattr(
        web_main,
        "create_app",
        create_app,
    )
    monkeypatch.setattr(
        web_main.uvicorn,
        "run",
        lambda app, **kwargs: calls.update(run_app=app, run_kwargs=kwargs),
    )
    return calls


def test_main_configures_internal_control_plane_from_environment(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    calls = _capture_launcher(monkeypatch)
    monkeypatch.setenv("AGENTHARNESS_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentharness",
            "--workspace",
            str(workspace),
            "--internal-only",
            "--no-open",
        ],
    )

    web_main.main()

    assert calls["env"] is True
    assert calls["app_kwargs"] == {
        "data_dir": data_dir,
        "workspace_roots": [workspace.resolve()],
        "execution_enabled": True,
        "internal_only": True,
    }
    assert calls["run_app"] == "app"
    assert calls["run_kwargs"] == {"host": "127.0.0.1", "port": 8742, "log_level": "info"}
    output = capsys.readouterr().out
    assert "http://127.0.0.1:8742" in output
    assert str(workspace.resolve()) in output


def test_main_disables_execution_for_remote_bind_without_opt_in(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = _capture_launcher(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agentharness",
            "--host",
            "0.0.0.0",
            "--port",
            "9123",
            "--workspace",
            str(workspace),
            "--no-open",
        ],
    )

    web_main.main()

    assert calls["app_kwargs"]["execution_enabled"] is False
    assert calls["run_kwargs"]["port"] == 9123
    assert "Web execution disabled" in capsys.readouterr().out


def test_main_opens_a_loopback_browser_once(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = _capture_launcher(monkeypatch)
    timers: list[object] = []

    class FakeTimer:
        def __init__(self, delay: float, callback, *, args: tuple[str, ...]) -> None:
            self.delay = delay
            self.callback = callback
            self.args = args
            self.daemon = False
            self.started = False
            timers.append(self)

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(web_main.threading, "Timer", FakeTimer)
    monkeypatch.setattr(sys, "argv", ["agentharness", "--workspace", str(workspace)])

    web_main.main()

    assert len(timers) == 1
    timer = timers[0]
    assert timer.delay == 0.8
    assert timer.callback is web_main.webbrowser.open
    assert timer.args == ("http://127.0.0.1:8741",)
    assert timer.daemon is True
    assert timer.started is True
    assert calls["app_kwargs"]["execution_enabled"] is True


def test_main_rejects_unknown_workspace_before_starting(tmp_path: Path, monkeypatch) -> None:
    calls = _capture_launcher(monkeypatch)
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        sys,
        "argv",
        ["agentharness", "--workspace", str(missing), "--no-open"],
    )

    with pytest.raises(SystemExit, match="Workspace does not exist"):
        web_main.main()

    assert "app_kwargs" not in calls
