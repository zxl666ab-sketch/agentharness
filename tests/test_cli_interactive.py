"""Interactive CLI behavior through the installed process entrypoint."""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from io import StringIO
from pathlib import Path

from rich.console import Console

from agentharness.cli.interactive import run_interactive
from agentharness.contracts import (
    ApprovalMode,
    EffectKind,
    RunRequest,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from agentharness.harness import Harness
from agentharness.providers.fake import FakeModelAdapter


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


class _FakeCompletion:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeCompleteState:
    def __init__(self, current: _FakeCompletion | None) -> None:
        self.current_completion = current


class _FakeBuffer:
    """Minimal prompt_toolkit Buffer stand-in that records handler effects."""

    def __init__(self, current_completion: _FakeCompletion | None = None) -> None:
        self.complete_state = (
            _FakeCompleteState(current_completion) if current_completion else None
        )
        self.applied: _FakeCompletion | None = None
        self.validated = False
        self.cancelled = False
        self.inserted = ""

    def apply_completion(self, completion: _FakeCompletion) -> None:
        self.applied = completion

    def validate_and_handle(self) -> None:
        self.validated = True

    def cancel_completion(self) -> None:
        self.cancelled = True

    def insert_text(self, text: str) -> None:
        self.inserted += text


class _FakeEvent:
    def __init__(self, buffer: _FakeBuffer) -> None:
        self.current_buffer = buffer


def _handler_for(keys: tuple[str, ...]):
    from agentharness.cli.input import _composer_key_bindings

    def _key_value(key: object) -> str:
        return getattr(key, "value", str(key))

    for binding in _composer_key_bindings().bindings:
        if tuple(_key_value(k) for k in binding.keys) == keys:
            return binding.handler
    raise AssertionError(f"no binding for keys {keys}")


def test_composer_enter_selects_active_completion_else_submits() -> None:
    """Goal 6: Enter applies the highlighted completion, otherwise submits the line."""
    enter = _handler_for(("c-m",))

    # A completion is highlighted → Enter selects it, does not submit.
    completion = _FakeCompletion("/model")
    buffer = _FakeBuffer(current_completion=completion)
    enter(_FakeEvent(buffer))
    assert buffer.applied is completion
    assert buffer.validated is False

    # No completion highlighted → Enter submits the line.
    plain = _FakeBuffer(current_completion=None)
    enter(_FakeEvent(plain))
    assert plain.applied is None
    assert plain.validated is True


def test_composer_alt_enter_inserts_newline() -> None:
    """Goal 6: Alt+Enter (escape,enter) inserts a newline instead of submitting."""
    handler = _handler_for(("escape", "c-m"))
    buffer = _FakeBuffer()
    handler(_FakeEvent(buffer))
    assert buffer.inserted == "\n"
    assert buffer.validated is False


def test_composer_escape_closes_completion_menu() -> None:
    """Goal 6: Esc cancels the open completion menu."""
    handler = _handler_for(("escape",))
    buffer = _FakeBuffer(current_completion=_FakeCompletion("/model"))
    handler(_FakeEvent(buffer))
    assert buffer.cancelled is True


def test_bare_command_opens_interactive_help_and_quits(
    data_dir: Path, workspace: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="/help\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=8,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RuntimeWarning" not in output
    assert "/new" in output
    assert "/sessions" in output
    assert "/use" in output
    assert "/quit" in output


def test_interactive_accepts_utf8_bom_before_first_slash_command(
    data_dir: Path, workspace: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="\ufeff/help\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=8,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Interactive commands" in output
    observed = Harness(data_dir=data_dir)
    try:
        assert observed.list_runs() == []
    finally:
        observed.close()


def test_cli_help_exposes_product_commands_without_tui_aliases() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "agentharness.cli.main", "--help"],
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=8,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    for command in ("run", "resume", "cancel", "runs", "doctor", "web"):
        assert command in output
    assert "chat" not in output
    assert " ui " not in output
    assert "TUI" not in output


def test_web_command_reports_port_conflict_without_traceback(data_dir: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentharness.cli.main",
                "web",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data-dir",
                str(data_dir),
            ],
            text=True,
            capture_output=True,
            env=_cli_env(),
            timeout=8,
            check=False,
        )
    finally:
        listener.close()

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert f"port {port} is already in use" in output.lower()
    assert "Traceback" not in output


def test_web_command_starts_healthily_and_stops_on_ctrl_c(data_dir: Path) -> None:
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()
    popen_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_cli_env(),
        **popen_kwargs,
    )
    stopped_cleanly = False
    try:
        health: dict[str, object] | None = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=0.3
                ) as response:
                    import json

                    health = json.loads(response.read().decode("utf-8"))
                    break
            except OSError:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
        assert health is not None, "web command never became healthy"
        assert health["service"] == "agentharness"
        assert Path(str(health["data_dir"])).resolve() == data_dir.resolve()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
            index_html = response.read().decode("utf-8")
        assert response.status == 200
        assert '<div id="root"></div>' in index_html

        if sys.platform == "win32":
            process.send_signal(signal.CTRL_C_EVENT)
        else:
            os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        stopped_cleanly = True
    finally:
        if process.poll() is None:
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
                time.sleep(0.2)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    output = stdout + stderr
    assert stopped_cleanly
    assert process.returncode == 0, output
    assert "Application shutdown complete" in output


def test_interactive_streams_turns_and_switches_sessions(
    data_dir: Path, workspace: Path
) -> None:
    seed = Harness(data_dir=data_dir)
    try:
        first = asyncio.run(
            seed.run(
                RunRequest(
                    message="[fake:text]seed answer",
                    provider="fake",
                    approval=ApprovalMode.auto,
                    cwd=str(workspace),
                )
            )
        )
    finally:
        seed.close()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input=(
            f"/sessions\n/use {first.session_id[:12]}\n"
            "[fake:text]continued answer\n/new\n"
            "[fake:text]fresh answer\n/sessions\n/quit\n"
        ),
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=15,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert first.session_id[:12] in output
    assert "continued answer" in output
    assert "fresh answer" in output
    assert "New session" in output

    observed = Harness(data_dir=data_dir)
    try:
        original_turns = observed.get_session_transcript(first.session_id)
        sessions = observed.list_sessions()
    finally:
        observed.close()
    assert len(original_turns) == 2
    assert original_turns[-1].assistant_content == "continued answer"
    assert len(sessions) == 2


def test_interactive_auto_requires_confirmation_for_destructive_shell(
    data_dir: Path, workspace: Path
) -> None:
    """Shell remains explicitly confirmed because every command is destructive."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="shell echo approved-by-auto\n1\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=15,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "审批请求" in output
    assert "effect=destructive" in output
    assert "approved-by-auto" in output
    assert "status=completed" in output


def test_interactive_ask_still_prompts_for_shell(
    data_dir: Path, workspace: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "ask",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        input="shell echo approved-by-user\n1\n/quit\n",
        text=True,
        capture_output=True,
        env=_cli_env(),
        timeout=15,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "审批请求" in output
    assert "approved-by-user" in output
    assert "status=completed" in output


def _process_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_ctrl_c_interrupts_run_kills_shell_tree_and_returns_to_prompt(
    data_dir: Path, workspace: Path
) -> None:
    pid_file = workspace / "slow-child.pid"
    child_code = (
        "import os,time,pathlib;"
        f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    command = f'shell "{sys.executable}" -c "{child_code}"'
    popen_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_cli_env(),
        **popen_kwargs,
    )
    child_pid: int | None = None
    child_alive_after_cli: bool | None = None
    try:
        assert process.stdin is not None
        process.stdin.write(f"{command}\n")
        process.stdin.write("1\n")
        process.stdin.flush()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.05)
        assert pid_file.exists(), "approved shell process never started"
        child_pid = int(pid_file.read_text(encoding="utf-8"))

        if sys.platform == "win32":
            process.send_signal(signal.CTRL_C_EVENT)
        else:
            os.killpg(process.pid, signal.SIGINT)
        time.sleep(0.5)
        process.stdin.write("/quit\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=12)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _process_is_alive(child_pid):
            time.sleep(0.05)
        child_alive_after_cli = _process_is_alive(child_pid)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child_pid is not None and _process_is_alive(child_pid):
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            else:
                os.kill(child_pid, signal.SIGKILL)

    output = stdout + stderr
    assert process.returncode == 0, output
    assert "Interrupted" in output
    observed = Harness(data_dir=data_dir)
    try:
        runs = observed.list_runs()
    finally:
        observed.close()
    assert runs[0]["status"] == "interrupted"

    assert child_alive_after_cli is False


def test_cancel_command_stops_run_owned_by_another_process(
    data_dir: Path, workspace: Path
) -> None:
    pid_file = workspace / "cancelled-child.pid"
    child_code = (
        "import os,time,pathlib;"
        f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    command = f'shell "{sys.executable}" -c "{child_code}"'
    run_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "run",
            command,
            "--provider",
            "fake",
            "--approval",
            "auto",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_cli_env(),
    )
    child_pid: int | None = None
    child_alive_after_cancel: bool | None = None
    try:
        assert run_process.stdin is not None
        # Destructive shell commands still require allow-once under auto.
        run_process.stdin.write("1\n")
        run_process.stdin.flush()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not pid_file.exists():
            if run_process.poll() is not None:
                break
            time.sleep(0.05)
        assert pid_file.exists(), "shell child never started"
        child_pid = int(pid_file.read_text(encoding="utf-8"))

        observer = Harness(data_dir=data_dir)
        try:
            rows = observer.list_runs()
        finally:
            observer.close()
        assert rows
        run_id = rows[0]["id"]

        cancel_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentharness.cli.main",
                "cancel",
                run_id,
                "--data-dir",
                str(data_dir),
            ],
            text=True,
            capture_output=True,
            env=_cli_env(),
            timeout=8,
            check=False,
        )
        stdout, stderr = run_process.communicate(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _process_is_alive(child_pid):
            time.sleep(0.05)
        child_alive_after_cancel = _process_is_alive(child_pid)
    finally:
        if run_process.poll() is None:
            run_process.kill()
            run_process.wait(timeout=5)
        if child_pid is not None and _process_is_alive(child_pid):
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            else:
                os.kill(child_pid, signal.SIGKILL)

    run_output = stdout + stderr
    assert cancel_result.returncode == 0, cancel_result.stdout + cancel_result.stderr
    assert run_process.returncode != 0, run_output
    assert "cancelled" in run_output
    final = Harness(data_dir=data_dir)
    try:
        row = final.get_run(run_id)
    finally:
        final.close()
    assert row is not None
    assert row["status"] == "cancelled"
    assert child_alive_after_cancel is False


def test_cancel_command_unblocks_external_approval_without_side_effect(
    data_dir: Path, workspace: Path
) -> None:
    marker = workspace / "approval-must-not-run.txt"
    child_code = f"from pathlib import Path;Path(r'{marker}').write_text('BAD')"
    command = f'shell "{sys.executable}" -c "{child_code}"'
    run_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentharness.cli.main",
            "run",
            command,
            "--provider",
            "fake",
            "--approval",
            "ask",
            "--data-dir",
            str(data_dir),
            "--cwd",
            str(workspace),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_cli_env(),
    )
    run_id: str | None = None
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            observer = Harness(data_dir=data_dir)
            try:
                rows = observer.list_runs()
            finally:
                observer.close()
            if rows and rows[0]["status"] == "waiting_approval":
                run_id = rows[0]["id"]
                break
            if run_process.poll() is not None:
                break
            time.sleep(0.05)
        assert run_id is not None, "run never reached approval"

        cancel_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentharness.cli.main",
                "cancel",
                run_id,
                "--data-dir",
                str(data_dir),
            ],
            text=True,
            capture_output=True,
            env=_cli_env(),
            timeout=8,
            check=False,
        )
        exit_deadline = time.monotonic() + 3
        while time.monotonic() < exit_deadline and run_process.poll() is None:
            time.sleep(0.05)
        assert run_process.poll() is not None, "approval prompt did not unblock on cancel"
        stdout, stderr = run_process.communicate(timeout=8)
    finally:
        if run_process.poll() is None:
            run_process.kill()
            run_process.wait(timeout=5)

    output = stdout + stderr
    assert cancel_result.returncode == 0, cancel_result.stdout + cancel_result.stderr
    assert run_process.returncode != 0, output
    assert "cancelled" in output
    assert not marker.exists()

    final = Harness(data_dir=data_dir)
    try:
        row = final.get_run(run_id)
        stop_request = final.storage.get_stop_request(run_id)
    finally:
        final.close()
    assert row is not None
    assert row["status"] == "cancelled"
    assert stop_request is None


class _LoopRecordingTool:
    def __init__(self) -> None:
        self.loop_ids: list[int] = []
        self.closed_on_loop: int | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="loop_tool", description="record loop", effect=EffectKind.pure)

    async def run(self, ctx: ToolContext, arguments: dict) -> ToolResult:
        self.loop_ids.append(id(asyncio.get_running_loop()))
        return ToolResult(tool_call_id="", name="loop_tool", content="ok")

    async def close_all(self) -> None:
        self.closed_on_loop = id(asyncio.get_running_loop())


def test_interactive_reuses_one_event_loop_across_turns(
    data_dir: Path, workspace: Path, monkeypatch
) -> None:
    tool = _LoopRecordingTool()
    provider = FakeModelAdapter(
        script=[
            {"kind": "tools", "tools": [{"name": "loop_tool"}]},
            {"kind": "text", "text": "first done"},
            {"kind": "tools", "tools": [{"name": "loop_tool"}]},
            {"kind": "text", "text": "second done"},
        ]
    )
    harness = Harness(
        data_dir=data_dir,
        providers={"fake": provider},
        tools={"loop_tool": tool},
    )
    console = Console(file=StringIO(), force_terminal=False, color_system=None)
    monkeypatch.setattr(sys, "stdin", StringIO("first\nsecond\n/quit\n"))
    try:
        run_interactive(
            harness=harness,
            console=console,
            provider="fake",
            model=None,
            approval="auto",
            cwd=str(workspace),
        )
    finally:
        harness.close()

    assert len(tool.loop_ids) == 2
    assert len(set(tool.loop_ids)) == 1
    assert tool.closed_on_loop == tool.loop_ids[0]
