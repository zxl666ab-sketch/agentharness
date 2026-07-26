from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentharness.contracts import (
    ShellExecutionConfig,
    ToolContext,
    ToolResult,
)
from agentharness.tools import shell as shell_module
from agentharness.tools.shell import (
    DockerShellExecutor,
    ShellTool,
    _docker_run_args,
    docker_diagnostics,
)


def _context(
    workspace: Path,
    *,
    extra_dirs: list[str] | None = None,
    allow_write: bool = False,
    shell: ShellExecutionConfig | None = None,
) -> ToolContext:
    return ToolContext(
        run_id="run-docker-policy",
        session_id="session-docker-policy",
        cwd=str(workspace),
        extra_dirs=extra_dirs or [],
        data_dir=str(workspace / ".data"),
        allow_write=allow_write,
        shell=shell or ShellExecutionConfig(executor="docker"),
    )


def test_shell_schema_rejects_unknown_and_unbounded_arguments() -> None:
    schema = ShellTool().spec.parameters
    assert schema["additionalProperties"] is False
    assert schema["properties"]["command"]["minLength"] == 1
    assert schema["properties"]["command"]["maxLength"] == 65_536
    assert schema["properties"]["timeout_s"]["minimum"] > 0
    assert schema["properties"]["timeout_s"]["maximum"] == 300


def test_docker_image_must_be_version_locked() -> None:
    with pytest.raises(ValueError, match="version tag or sha256"):
        ShellExecutionConfig(executor="docker", docker_image="python:latest")
    with pytest.raises(ValueError, match="version tag or sha256"):
        ShellExecutionConfig(executor="docker", docker_image="python")


def test_docker_args_enforce_isolation_and_readonly_authorized_mounts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    extra = tmp_path / "extra"
    workspace.mkdir()
    extra.mkdir()
    config = ShellExecutionConfig(
        executor="docker",
        docker_image="python:3.12.4-slim-bookworm",
        docker_cpus=0.5,
        docker_memory_mb=256,
        docker_pids_limit=64,
    )
    args = _docker_run_args(
        _context(workspace, extra_dirs=[str(extra)], shell=config),
        config,
        "python -V",
        "agentharness-test",
    )
    joined = " ".join(args)
    assert "--pull never" in joined
    assert "--user 65532:65532" in joined
    assert "--read-only" in args
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--network none" in joined
    assert "--cpus 0.5" in joined
    assert "--memory 256m" in joined
    assert "--pids-limit 64" in joined
    mounts = [args[index + 1] for index, item in enumerate(args[:-1]) if item == "--mount"]
    assert len(mounts) == 2
    assert all(mount.endswith(",readonly") for mount in mounts)
    assert any("dst=/workspace" in mount for mount in mounts)
    assert any("dst=/extra/0" in mount for mount in mounts)
    assert "AGENTHARNESS_EXTRA_DIR_0=/extra/0" in args
    assert args[-4:] == [
        "python:3.12.4-slim-bookworm",
        "/bin/sh",
        "-lc",
        "python -V",
    ]


def test_docker_write_and_network_require_explicit_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = ShellExecutionConfig(
        executor="docker",
        docker_network=True,
    )
    args = _docker_run_args(
        _context(workspace, allow_write=True, shell=config),
        config,
        "echo ok",
        "agentharness-test",
    )
    assert "--network" not in args
    mount = args[args.index("--mount") + 1]
    assert not mount.endswith(",readonly")


def test_docker_diagnostics_explains_silent_daemon_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_module.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(
        shell_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    result = docker_diagnostics()
    assert result["status"] == "unavailable"
    assert result["daemon"] == "unavailable"
    assert result["detail"] == "docker info exited with code 1"


@pytest.mark.asyncio
async def test_docker_unavailable_is_hard_failure_without_local_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        shell_module,
        "docker_diagnostics",
        lambda timeout_s=3.0: {
            "status": "missing",
            "detail": "docker executable was not found on PATH",
        },
    )

    class RecordingLocal:
        called = False

        async def run(self, ctx, arguments, config):
            self.called = True
            return ToolResult(tool_call_id="", name="shell", content="local")

    local = RecordingLocal()
    tool = ShellTool(
        executors={
            "local": local,
            "docker": DockerShellExecutor(),
        }
    )
    result = await tool.run(
        _context(workspace),
        {"command": "echo must-not-run-locally"},
    )
    assert result.is_error is True
    assert result.error_code == "docker_unavailable"
    assert local.called is False
    assert "fallback is intentionally disabled" in (result.recovery_hint or "")
