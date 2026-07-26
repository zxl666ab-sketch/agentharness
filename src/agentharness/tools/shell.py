"""Shell tool with timeout, output limits, env whitelist, process-tree kill."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentharness.contracts import (
    EffectKind,
    ShellExecutionConfig,
    ToolContext,
    ToolResult,
    ToolSpec,
)

# Whitelisted environment variable names passed to subprocess
_ENV_WHITELIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "TERM",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "USERNAME",
    "USER",
    "SHELL",
    "PWD",
}


def _filtered_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in _ENV_WHITELIST:
            env[k] = v
    return env


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Cross-platform process tree termination."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        if sys.platform == "win32":
            # taskkill /T kills the tree; bound wait so we never hang tests
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=5)
            except TimeoutError:
                try:
                    killer.kill()
                except ProcessLookupError:
                    pass
            # Also kill the Process handle directly
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


StopCallback = Callable[[], Awaitable[None]]


def docker_diagnostics(timeout_s: float = 3.0) -> dict[str, Any]:
    """Report Docker CLI and daemon readiness without changing Docker state."""
    executable = shutil.which("docker")
    if executable is None:
        return {
            "status": "missing",
            "cli": "missing",
            "daemon": "unknown",
            "detail": "docker executable was not found on PATH",
        }
    try:
        completed = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_filtered_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "unavailable",
            "cli": "ready",
            "daemon": "unavailable",
            "detail": str(exc),
        }
    detail = (completed.stdout or completed.stderr or "").strip()[:500]
    if completed.returncode != 0 and not detail:
        detail = f"docker info exited with code {completed.returncode}"
    return {
        "status": "ready" if completed.returncode == 0 else "unavailable",
        "cli": "ready",
        "daemon": "ready" if completed.returncode == 0 else "unavailable",
        "server_version": detail if completed.returncode == 0 else None,
        "detail": detail if completed.returncode != 0 else "Docker daemon is reachable",
    }


def _docker_run_args(
    ctx: ToolContext,
    config: ShellExecutionConfig,
    command: str,
    container_name: str,
) -> list[str]:
    """Build the complete Docker policy as argv for deterministic inspection/tests."""
    cwd = Path(ctx.cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise NotADirectoryError(str(cwd))
    roots: list[Path] = [cwd]
    for raw in ctx.extra_dirs:
        root = Path(raw).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        if root not in roots:
            roots.append(root)

    args = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        container_name,
        "--user",
        "65532:65532",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--ipc",
        "none",
        "--cpus",
        str(config.docker_cpus),
        "--memory",
        f"{config.docker_memory_mb}m",
        "--pids-limit",
        str(config.docker_pids_limit),
        "--stop-timeout",
        "1",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m",
        "--workdir",
        "/workspace",
    ]
    if not config.docker_network:
        args.extend(["--network", "none"])
    mode = "" if ctx.allow_write else ",readonly"
    args.extend(
        ["--mount", f"type=bind,src={cwd},dst=/workspace{mode}"]
    )
    for index, root in enumerate(roots[1:]):
        target = f"/extra/{index}"
        args.extend(["--mount", f"type=bind,src={root},dst={target}{mode}"])
        args.extend(["--env", f"AGENTHARNESS_EXTRA_DIR_{index}={target}"])
    args.extend([config.docker_image, "/bin/sh", "-lc", command])
    return args


class LocalShellExecutor:
    name = "local"

    def __init__(self, process_registry: dict[str, list[Any]] | None = None) -> None:
        self.process_registry = process_registry

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            description=(
                "Run a shell command in the workspace cwd. Classified as destructive: "
                "always requires confirmation unless approval mode is never (denied). "
                "Has timeout and output limits. Prefer read_file/search_files when possible."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {"type": "number", "description": "Timeout seconds (default 30)"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            effect=EffectKind.destructive,
            timeout_s=300,
        )

    async def _spawn(
        self,
        ctx: ToolContext,
        command: str,
        config: ShellExecutionConfig,
    ) -> tuple[asyncio.subprocess.Process, StopCallback | None]:
        del config
        cwd = ctx.cwd
        env = _filtered_env()
        env["PWD"] = str(cwd)
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            # Agent shell commands are non-interactive. Never let a child consume
            # CLI commands or approval answers from the harness stdin stream.
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
        else:
            kwargs["preexec_fn"] = os.setsid
        process = await asyncio.create_subprocess_shell(command, **kwargs)
        return process, None

    @staticmethod
    async def _stop_process(
        proc: asyncio.subprocess.Process,
        stop_callback: StopCallback | None,
    ) -> None:
        if stop_callback is not None:
            try:
                await stop_callback()
            except Exception:  # noqa: BLE001 - process tree kill remains mandatory
                pass
        await kill_process_tree(proc)

    async def run(
        self,
        ctx: ToolContext,
        arguments: dict[str, Any],
        config: ShellExecutionConfig | None = None,
    ) -> ToolResult:
        command = arguments.get("command") or ""
        timeout_s = float(arguments.get("timeout_s") or 30)
        max_output = 50_000
        config = config or ShellExecutionConfig()

        if ctx.cancel_event and ctx.cancel_event.is_set():
            return ToolResult(
                tool_call_id="",
                name="shell",
                content="cancelled",
                is_error=True,
                error_code="cancelled",
                error_category="cancellation",
                retryable=True,
                recovery_hint="Resume the run and retry the command.",
            )

        try:
            proc, stop_callback = await self._spawn(ctx, command, config)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                name="shell",
                content=f"spawn failed: {exc}",
                is_error=True,
                error_code="process_spawn_failed",
                error_category="process",
                retryable=True,
                recovery_hint="Check the command and executable path, then retry.",
            )

        if self.process_registry is not None:
            self.process_registry.setdefault(ctx.run_id, []).append(proc)

        killed_by_cancel = False

        # Cancel watcher
        async def _watch_cancel() -> None:
            nonlocal killed_by_cancel
            if ctx.cancel_event is None:
                return
            while proc.returncode is None:
                if ctx.cancel_event.is_set():
                    killed_by_cancel = True
                    await self._stop_process(proc, stop_callback)
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(_watch_cancel())

        async def _read_limited(
            stream: asyncio.StreamReader | None,
        ) -> tuple[bytes, bool]:
            if stream is None:
                return b"", False
            kept = bytearray()
            truncated = False
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                remaining = max_output - len(kept)
                if remaining > 0:
                    kept.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
            return bytes(kept), truncated

        async def _collect_output() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
            collected = await asyncio.gather(
                _read_limited(proc.stdout),
                _read_limited(proc.stderr),
            )
            await proc.wait()
            return collected[0], collected[1]

        try:
            (stdout_b, stdout_truncated), (stderr_b, stderr_truncated) = (
                await asyncio.wait_for(_collect_output(), timeout=timeout_s)
            )
        except asyncio.CancelledError:
            await self._stop_process(proc, stop_callback)
            watcher.cancel()
            raise
        except TimeoutError:
            await self._stop_process(proc, stop_callback)
            watcher.cancel()
            return ToolResult(
                tool_call_id="",
                name="shell",
                content=f"Command timed out after {timeout_s}s",
                is_error=True,
                error_code="outcome_indeterminate",
                error_category="recovery",
                retryable=False,
                recovery_hint=(
                    "Inspect the command's target state before deciding whether to run it again."
                ),
            )
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            if self.process_registry is not None:
                lst = self.process_registry.get(ctx.run_id, [])
                if proc in lst:
                    lst.remove(proc)

        # Cancelled mid-flight — mark explicitly so engine keeps tool pending for resume
        if killed_by_cancel or (ctx.cancel_event is not None and ctx.cancel_event.is_set()):
            return ToolResult(
                tool_call_id="",
                name="shell",
                content="cancelled",
                is_error=True,
                error_code="cancelled",
                error_category="cancellation",
                retryable=True,
                recovery_hint="Resume the run and retry the command.",
            )

        out = stdout_b.decode("utf-8", errors="replace")
        err = stderr_b.decode("utf-8", errors="replace")
        combined = out
        if err:
            combined = (combined + "\n[stderr]\n" + err) if combined else err
        if stdout_truncated or stderr_truncated or len(combined) > max_output:
            combined = combined[:max_output] + "\n...[truncated]"
        code = proc.returncode or 0
        if code != 0:
            combined = f"exit={code}\n{combined}"
        return ToolResult(
            tool_call_id="",
            name="shell",
            content=combined or f"exit={code}",
            is_error=code != 0,
            error_code="command_failed" if code != 0 else None,
            error_category="process" if code != 0 else None,
            retryable=code != 0,
            recovery_hint=(
                "Inspect stdout/stderr, correct the failing code or command, and retry."
                if code != 0
                else None
            ),
        )


class DockerShellExecutor(LocalShellExecutor):
    name = "docker"

    async def run(
        self,
        ctx: ToolContext,
        arguments: dict[str, Any],
        config: ShellExecutionConfig | None = None,
    ) -> ToolResult:
        config = config or ShellExecutionConfig(executor="docker")
        diagnosis = await asyncio.to_thread(docker_diagnostics)
        if diagnosis["status"] != "ready":
            return ToolResult(
                tool_call_id="",
                name="shell",
                content=(
                    "Docker execution required, but Docker is unavailable: "
                    f"{diagnosis.get('detail') or diagnosis['status']}"
                ),
                is_error=True,
                error_code="docker_unavailable",
                error_category="sandbox",
                retryable=False,
                recovery_hint="Install/start Docker and rerun; local fallback is intentionally disabled.",
            )
        return await super().run(ctx, arguments, config)

    async def _spawn(
        self,
        ctx: ToolContext,
        command: str,
        config: ShellExecutionConfig,
    ) -> tuple[asyncio.subprocess.Process, StopCallback | None]:
        container_name = f"agentharness-{ctx.run_id[:12]}-{uuid4().hex[:8]}"
        args = _docker_run_args(ctx, config, command, container_name)
        args[0] = shutil.which("docker") or "docker"
        env = _filtered_env()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=ctx.cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def force_remove() -> None:
            remover = await asyncio.create_subprocess_exec(
                args[0],
                "rm",
                "-f",
                container_name,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(remover.wait(), timeout=5)
            except TimeoutError:
                try:
                    remover.kill()
                except ProcessLookupError:
                    pass

        return process, force_remove


class ShellTool:
    """Stable shell ToolSpec dispatching to explicitly configured executors."""

    def __init__(
        self,
        process_registry: dict[str, list[Any]] | None = None,
        *,
        executors: dict[str, Any] | None = None,
    ) -> None:
        self.executors = executors or {
            "local": LocalShellExecutor(process_registry=process_registry),
            "docker": DockerShellExecutor(process_registry=process_registry),
        }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            description=(
                "Run a governed shell command in the workspace cwd. The run chooses "
                "the local or Docker executor; Docker never silently falls back. "
                "Always requires confirmation and has timeout/output limits."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "minLength": 1, "maxLength": 65_536},
                    "timeout_s": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 300,
                        "description": "Timeout seconds (default 30)",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            effect=EffectKind.destructive,
            timeout_s=300,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        config = ctx.shell
        executor = self.executors.get(config.executor)
        if executor is None:
            return ToolResult(
                tool_call_id="",
                name="shell",
                content=f"Unknown shell executor: {config.executor}",
                is_error=True,
                error_code="shell_executor_invalid",
                error_category="configuration",
                retryable=False,
            )
        return await executor.run(ctx, arguments, config)
