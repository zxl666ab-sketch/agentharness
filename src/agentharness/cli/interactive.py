"""Line-oriented interactive CLI for long-lived multi-turn sessions."""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.table import Table

from agentharness.cli.input import redirected_input
from agentharness.contracts import ApprovalMode, EventType, RunRequest, RunResult
from agentharness.harness import Harness


def _enable_windows_ctrl_c() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    # Processes created in a new Windows process group inherit Ctrl+C disabled.
    # Restore normal handling so asyncio.Runner can turn Ctrl+C into cancellation.
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)  # type: ignore[attr-defined]


def _print_help(console: Console) -> None:
    table = Table(title="Interactive commands", show_header=False, box=None)
    table.add_column("command", style="cyan", no_wrap=True)
    table.add_column("action")
    table.add_row("/new", "Start a new session")
    table.add_row("/sessions", "List recent sessions")
    table.add_row("/use <session>", "Continue an existing session")
    table.add_row("/help", "Show this help")
    table.add_row("/quit", "Exit")
    console.print(table)


def _print_sessions(harness: Harness, console: Console) -> None:
    sessions = harness.list_sessions(limit=50)
    if not sessions:
        console.print("[dim]No sessions yet.[/dim]")
        return
    table = Table(title="Sessions", show_lines=False, pad_edge=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("title")
    table.add_column("status")
    table.add_column("updated")
    for session in sessions:
        table.add_row(
            str(session.get("id", ""))[:12],
            str(session.get("title") or "session"),
            str(session.get("latest_status") or ""),
            str(session.get("updated_at") or "")[:19],
        )
    console.print(table)


def _resolve_session(harness: Harness, value: str) -> tuple[str | None, str | None]:
    value = value.strip()
    if not value:
        return None, "Usage: /use <session>"
    matches = [
        str(session["id"])
        for session in harness.list_sessions(limit=1000)
        if str(session.get("id", "")).startswith(value)
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"Session not found: {value}"
    return None, f"Session prefix is ambiguous: {value}"


def _run_turn(
    *,
    runner: asyncio.Runner,
    harness: Harness,
    console: Console,
    message: str,
    session_id: str | None,
    provider: str,
    model: str | None,
    approval: str,
    cwd: str,
) -> RunResult | None:
    saw_text = False
    active_run_id: str | None = None

    def on_event(event) -> None:
        nonlocal saw_text, active_run_id
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        if event_type == EventType.run_started.value:
            active_run_id = event.run_id
        if event_type == EventType.text_delta.value:
            text = str(event.payload.get("text") or "")
            if text:
                saw_text = True
                console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    unsubscribe = harness.subscribe_events(on_event)
    console.print("[bold green]agent>[/bold green] ", end="")
    try:
        result = runner.run(
            harness.run(
                RunRequest(
                    message=message,
                    session_id=session_id,
                    provider=provider,
                    model=model,
                    approval=ApprovalMode(approval),
                    cwd=cwd,
                )
            )
        )
    except KeyboardInterrupt:
        console.print()
        suffix = f" {active_run_id[:12]}" if active_run_id else ""
        console.print(
            f"[yellow]Interrupted{suffix}. Use `agentharness resume <run_id>` "
            "to continue.[/yellow]"
        )
        return None
    finally:
        unsubscribe()

    if not saw_text and result.output:
        console.print(result.output, markup=False, highlight=False, soft_wrap=True)
    else:
        console.print()
    console.print(
        f"[dim]status={result.status.value}  run={result.run_id[:12]}  "
        f"session={result.session_id[:12]}[/dim]"
    )
    if result.error:
        console.print(f"[red]error:[/red] {result.error}")
    return result


def run_interactive(
    *,
    harness: Harness,
    console: Console,
    provider: str,
    model: str | None,
    approval: str,
    cwd: str,
    session_id: str | None = None,
) -> None:
    """Run the foreground prompt loop until `/quit` or EOF."""
    _enable_windows_ctrl_c()
    console.print(
        f"[bold]Agent Harness[/bold]  provider={provider}  approval={approval}\n"
        "Type [cyan]/help[/cyan] for commands."
    )
    current_session_id = session_id
    runner = asyncio.Runner()
    try:
        while True:
            try:
                if sys.stdin.isatty():
                    raw_line = console.input("[bold cyan]you>[/bold cyan] ")
                else:
                    raw_line = redirected_input(console, "you> ")
                line = raw_line.lstrip("\ufeff").strip()
            except EOFError:
                console.print()
                return
            except KeyboardInterrupt:
                console.print("\n[yellow]No run is active. Use /quit to exit.[/yellow]")
                continue

            if not line:
                continue
            if line in {"/quit", "/exit"}:
                return
            if line == "/help":
                _print_help(console)
                continue
            if line == "/new":
                current_session_id = None
                console.print("[green]New session ready.[/green]")
                continue
            if line == "/sessions":
                _print_sessions(harness, console)
                continue
            if line == "/use" or line.startswith("/use "):
                selected, error = _resolve_session(harness, line[4:])
                if error:
                    console.print(f"[yellow]{error}[/yellow]")
                elif selected is not None:
                    current_session_id = selected
                    console.print(f"[green]Using session {selected[:12]}.[/green]")
                continue
            if line.startswith("/"):
                console.print(f"[yellow]Unknown command:[/yellow] {line}")
                continue

            result = _run_turn(
                runner=runner,
                harness=harness,
                console=console,
                message=line,
                session_id=current_session_id,
                provider=provider,
                model=model,
                approval=approval,
                cwd=cwd,
            )
            if result is not None:
                current_session_id = result.session_id
    finally:
        try:
            runner.run(harness.aclose())
        finally:
            runner.close()
