"""Lifecycle helper for the Web Inspector started alongside the interactive CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from agentharness.api.compatibility import API_SCHEMA_VERSION


@dataclass
class WebCompanion:
    url: str
    process: subprocess.Popen[bytes] | None = None
    reused: bool = False

    def stop(self) -> None:
        """Stop only a server process owned by this CLI invocation."""
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def start_web_companion(
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    preferred_port: int = 8741,
    open_browser: bool = True,
) -> WebCompanion:
    """Reuse a matching server or start one on the first free port in a 10-port range."""
    resolved = data_dir.expanduser().resolve()
    for port in range(preferred_port, preferred_port + 11):
        url = f"http://{host}:{port}"
        health = _health(url)
        if health is not None:
            existing_dir = Path(str(health.get("data_dir") or "")).expanduser()
            try:
                matches = existing_dir.resolve() == resolved
            except OSError:
                matches = False
            compatible = health.get("api_schema_version") == API_SCHEMA_VERSION
            if matches and compatible:
                companion = WebCompanion(url=url, reused=True)
                if open_browser:
                    webbrowser.open(url)
                return companion
            continue

        process = _spawn_server(resolved, host=host, port=port)
        if _wait_healthy(url, resolved, process):
            companion = WebCompanion(url=url, process=process)
            if open_browser:
                webbrowser.open(url)
            return companion
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        # A foreign process may own the port; continue without touching it.

    raise RuntimeError(
        f"could not start Web Inspector on {host}:{preferred_port}-{preferred_port + 10}"
    )


def _health(url: str) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=0.35) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("service") != "agentharness":
                return None
            return payload
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _wait_healthy(
    url: str, data_dir: Path, process: subprocess.Popen[bytes], timeout_s: float = 8.0
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        health = _health(url)
        if health is not None:
            try:
                return Path(str(health.get("data_dir") or "")).resolve() == data_dir
            except OSError:
                return False
        time.sleep(0.1)
    return False


def _spawn_server(data_dir: Path, *, host: str, port: int) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "agentharness.cli.main",
        "web",
        "--data-dir",
        str(data_dir),
        "--host",
        host,
        "--port",
        str(port),
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
