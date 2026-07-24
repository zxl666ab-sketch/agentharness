from __future__ import annotations

from pathlib import Path

from agentharness.api.compatibility import API_SCHEMA_VERSION
from agentharness.cli import web_companion as module
from agentharness.cli.web_companion import WebCompanion, start_web_companion


class _Process:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):  # noqa: ANN201
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):  # noqa: ANN001, ANN201
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_reuses_matching_server_without_owning_it(tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        module,
        "_health",
        lambda _url: {
            "service": "agentharness",
            "data_dir": str(tmp_path),
            "api_schema_version": API_SCHEMA_VERSION,
        },
    )
    monkeypatch.setattr(module.webbrowser, "open", opened.append)
    companion = start_web_companion(tmp_path, open_browser=True)
    assert companion.reused is True
    assert companion.process is None
    assert opened == ["http://127.0.0.1:8741"]
    companion.stop()  # reused servers are never stopped


def test_skips_foreign_port_and_owns_next_server(tmp_path: Path, monkeypatch) -> None:
    process = _Process()

    def health(url: str):  # noqa: ANN202
        if url.endswith(":8741"):
            return {"service": "agentharness", "data_dir": str(tmp_path / "other")}
        return None

    monkeypatch.setattr(module, "_health", health)
    monkeypatch.setattr(module, "_spawn_server", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module, "_wait_healthy", lambda *_args, **_kwargs: True)
    companion = start_web_companion(tmp_path, open_browser=False)
    assert companion.url.endswith(":8742")
    assert companion.process is process
    companion.stop()
    assert process.terminated is True


def test_does_not_reuse_matching_data_dir_with_stale_api(tmp_path: Path, monkeypatch) -> None:
    process = _Process()

    def health(url: str):  # noqa: ANN202
        if url.endswith(":8741"):
            return {
                "service": "agentharness",
                "data_dir": str(tmp_path),
                "api_schema_version": API_SCHEMA_VERSION - 1,
            }
        return None

    monkeypatch.setattr(module, "_health", health)
    monkeypatch.setattr(module, "_spawn_server", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module, "_wait_healthy", lambda *_args, **_kwargs: True)

    companion = start_web_companion(tmp_path, open_browser=False)

    assert companion.reused is False
    assert companion.url.endswith(":8742")
    assert companion.process is process
    companion.stop()


def test_stop_does_not_touch_reused_server() -> None:
    companion = WebCompanion("http://127.0.0.1:8741", reused=True)
    companion.stop()
