"""CLI entrypoints: interactive prompt, run, resume, cancel, runs, web, doctor."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from agentharness.cli.config_store import apply_settings_to_harness, resolve_runtime_settings
from agentharness.cli.input import async_redirected_input, async_tty_input
from agentharness.cli.provider_defaults import resolve_default_provider
from agentharness.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    RunRequest,
    RunStatus,
)
from agentharness.harness import Harness

app = typer.Typer(
    name="agentharness",
    help="Agent Harness — interactive local agent with a readonly run inspector",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()

_current_harness: Harness | None = None

_UNSUCCESSFUL_RESULT_STATUSES = frozenset(
    {RunStatus.failed, RunStatus.cancelled, RunStatus.interrupted}
)


def _exit_for_result_status(status: RunStatus) -> None:
    if status in _UNSUCCESSFUL_RESULT_STATUSES:
        raise typer.Exit(1)


def _data_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("AGENTHARNESS_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".agentharness"


def _make_harness(
    data_dir: str | None,
    approval: str = "ask",
) -> Harness:
    global _current_harness
    h = Harness(data_dir=_data_dir(data_dir))

    async def approval_cb(req: ApprovalRequest) -> ApprovalDecision:
        if approval == "auto":
            if req.effect.value == "destructive":
                return await _prompt_approval(req)
            return ApprovalDecision.allow_run
        if approval == "never":
            return ApprovalDecision.deny
        return await _prompt_approval(req)

    h.set_approval_callback(approval_cb)
    _current_harness = h
    return h


async def _console_input(prompt: str) -> str:
    """Cancellation-aware console input for approval prompts."""
    if sys.stdin.isatty():
        return await async_tty_input(prompt)

    return await async_redirected_input(console, prompt)


async def _prompt_approval(req: ApprovalRequest) -> ApprovalDecision:
    console.print(
        f"\n[yellow]审批请求[/yellow] tool=[bold]{req.tool_name}[/bold] "
        f"effect=[bold]{req.effect.value}[/bold]"
    )
    console.print(f"  args: {req.arguments_summary}")
    if req.effect.value == "destructive":
        console.print("[red]destructive — 必须单独确认[/red]")
        console.print("  [1] 允许一次  [3] 拒绝")
        choice = (await _console_input("选择> ")).strip()
        if choice == "1":
            return ApprovalDecision.allow_once
        return ApprovalDecision.deny
    console.print("  [1] 允许一次  [2] 允许本次运行  [3] 拒绝")
    choice = (await _console_input("选择> ")).strip()
    if choice == "1":
        return ApprovalDecision.allow_once
    if choice == "2":
        return ApprovalDecision.allow_run
    return ApprovalDecision.deny


def _status_style(status: str) -> str:
    mapping = {
        "completed": "green",
        "failed": "red",
        "cancelled": "yellow",
        "interrupted": "yellow",
        "running": "cyan",
        "waiting_approval": "magenta",
        "pending": "dim",
    }
    color = mapping.get(status, "white")
    return f"[{color}]{status}[/{color}]"


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider (default: env detection → fake)",
    ),
    model: str | None = typer.Option(None, "--model", "-m"),
    approval: str = typer.Option("ask", "--approval", help="ask|auto|never"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
    cwd: str | None = typer.Option(None, "--cwd"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """With no subcommand, open the line-oriented multi-turn CLI."""
    if ctx.invoked_subcommand is not None:
        return
    dd = _data_dir(data_dir)
    work = str(Path(cwd).resolve()) if cwd else str(Path.cwd())
    settings = resolve_runtime_settings(dd, provider=provider, model=model)
    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    from agentharness.cli.interactive import run_interactive

    try:
        run_interactive(
            harness=h,
            console=console,
            provider=provider or "auto",
            model=model,
            approval=approval,
            cwd=work,
            session_id=session,
            data_dir=dd,
        )
    finally:
        h.close()


@app.command()
def run(
    message: str = typer.Argument(..., help="User task / prompt"),
    provider: str | None = typer.Option(None, "--provider", "-p"),
    model: str | None = typer.Option(None, "--model", "-m"),
    approval: str = typer.Option("ask", "--approval", help="ask|auto|never"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
    cwd: str | None = typer.Option(None, "--cwd"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Run a single agent task and exit with a scriptable status code."""
    dd = _data_dir(data_dir)
    work = Path(cwd).resolve() if cwd else Path.cwd()
    settings = resolve_runtime_settings(dd, provider=provider, model=model)

    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    req = RunRequest(
        message=message,
        session_id=session,
        provider=settings.provider,
        model=settings.model,
        approval=ApprovalMode(approval),
        cwd=str(work),
    )
    runner = asyncio.Runner()
    started_run_id: str | None = None

    def remember_run(event) -> None:
        nonlocal started_run_id
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        if event_type == "run_started":
            started_run_id = event.run_id

    unsubscribe = h.subscribe_events(remember_run)
    try:
        result = runner.run(h.run(req))
    except KeyboardInterrupt:
        if started_run_id:
            console.print(
                f"[yellow]Interrupted — run {started_run_id} marked interrupted; "
                f"use: agentharness resume {started_run_id}[/yellow]"
            )
        else:
            console.print("[yellow]Interrupted (no active run id)[/yellow]")
        raise typer.Exit(130) from None
    finally:
        unsubscribe()
        try:
            runner.run(h.aclose())
        finally:
            runner.close()

    console.print(
        f"\n[bold]status[/bold]={_status_style(result.status.value)}  "
        f"run_id={result.run_id[:12]}  session={result.session_id[:12]}"
    )
    if result.output:
        console.print(Markdown(result.output))
    if result.error:
        console.print(f"[red]error[/red]: {result.error}")
    _exit_for_result_status(result.status)


@app.command()
def resume(
    run_id: str = typer.Argument(...),
    message: str | None = typer.Option(None, "--message", "-m"),
    approval: str = typer.Option("ask", "--approval"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Resume an interrupted / waiting run from checkpoint."""
    h = _make_harness(data_dir, approval=approval)
    runner = asyncio.Runner()
    try:
        try:
            result = runner.run(h.resume(run_id, input=message))
        except (KeyError, RuntimeError) as exc:
            console.print(f"[red]{str(exc).strip(chr(39))}[/red]")
            raise typer.Exit(1) from None
    finally:
        try:
            runner.run(h.aclose())
        finally:
            runner.close()
    console.print(f"status={_status_style(result.status.value)}")
    if result.output:
        console.print(Markdown(result.output))
    if result.error:
        console.print(f"[red]{result.error}[/red]")
    _exit_for_result_status(result.status)


@app.command()
def cancel(
    run_id: str = typer.Argument(...),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Cancel a running agent (propagates to children, kills shell trees)."""
    h = _make_harness(data_dir)
    try:
        try:
            asyncio.run(h.cancel(run_id))
        except (KeyError, RuntimeError) as exc:
            console.print(f"[red]{str(exc).strip(chr(39))}[/red]")
            raise typer.Exit(1) from None
    finally:
        h.close()
    console.print(f"cancelled {run_id}")


@app.command("runs")
def list_runs(
    session: str | None = typer.Option(None, "--session"),
    limit: int = typer.Option(20, "--limit"),
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """List recent runs (compact table)."""
    h = _make_harness(data_dir)
    try:
        rows = h.list_runs(session_id=session, limit=limit)
    finally:
        h.close()
    table = Table(title="Runs", show_lines=False, pad_edge=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("status")
    table.add_column("provider")
    table.add_column("session", no_wrap=True)
    table.add_column("created")
    for r in rows:
        table.add_row(
            r["id"][:12],
            _status_style(r["status"]),
            r.get("provider") or "",
            (r.get("session_id") or "")[:8],
            (r.get("created_at") or "")[:19],
        )
    console.print(table)


@app.command()
def web(
    data_dir: str | None = typer.Option(None, "--data-dir"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8741, "--port"),
) -> None:
    """Start the readonly run inspector as a foreground process."""
    from agentharness.cli.interactive import _enable_windows_ctrl_c

    _enable_windows_ctrl_c()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            console.print(f"[red]Port {port} is already in use on {host}.[/red]")
            raise typer.Exit(1) from None
    dd = _data_dir(data_dir)
    h = Harness(data_dir=dd)
    from agentharness.api.server import create_app

    application = create_app(harness=h)
    url = f"http://{host}:{port}"
    console.print(f"[green]Run inspector[/green] {url}")
    console.print(f"data_dir={dd}")
    import uvicorn

    try:
        uvicorn.run(application, host=host, port=port, log_level="info")
    finally:
        h.close()


@app.command()
def doctor(
    data_dir: str | None = typer.Option(None, "--data-dir"),
) -> None:
    """Health / environment diagnostics."""
    h = _make_harness(data_dir)
    try:
        info = h.doctor()
    finally:
        h.close()
    table = Table(title="Doctor", show_header=True, header_style="bold")
    table.add_column("key")
    table.add_column("value")
    for k, v in info.items():
        table.add_row(str(k), str(v))
    table.add_row(
        "OPENAI_API_KEY",
        "set" if os.environ.get("OPENAI_API_KEY") else "unset",
    )
    table.add_row(
        "ANTHROPIC_API_KEY",
        "set" if os.environ.get("ANTHROPIC_API_KEY") else "unset",
    )
    table.add_row("default_provider", resolve_default_provider(None))
    settings = resolve_runtime_settings(_data_dir(data_dir))
    table.add_row("profile_provider", settings.provider)
    table.add_row("profile_model", settings.model or "(none)")
    table.add_row("profile_source", settings.source)
    console.print(table)


def main() -> None:
    app()


if __name__ != "__main__":
    pass
else:
    main()
