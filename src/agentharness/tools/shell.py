"""Shell tool with timeout, output limits, env whitelist, process-tree kill."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec

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


class ShellTool:
    def __init__(self, process_registry: dict[str, list[Any]] | None = None) -> None:
        self.process_registry = process_registry

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            description=(
                "Run a shell command in the workspace cwd. Classified as a process effect: "
                "allowed under --approval auto, still gated under ask/never. "
                "Has timeout and output limits. Prefer read_file/search_files when possible."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_s": {"type": "number", "description": "Timeout seconds (default 30)"},
                },
                "required": ["command"],
            },
            effect=EffectKind.process,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        command = arguments.get("command") or ""
        timeout_s = float(arguments.get("timeout_s") or 30)
        max_output = 50_000

        if ctx.cancel_event and ctx.cancel_event.is_set():
            return ToolResult(
                tool_call_id="", name="shell", content="cancelled", is_error=True
            )

        cwd = ctx.cwd
        env = _filtered_env()
        env["PWD"] = str(cwd)

        import subprocess

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

        try:
            proc = await asyncio.create_subprocess_shell(command, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="", name="shell", content=f"spawn failed: {exc}", is_error=True
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
                    await kill_process_tree(proc)
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
            await kill_process_tree(proc)
            watcher.cancel()
            raise
        except TimeoutError:
            await kill_process_tree(proc)
            watcher.cancel()
            return ToolResult(
                tool_call_id="",
                name="shell",
                content=f"Command timed out after {timeout_s}s",
                is_error=True,
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
        )
