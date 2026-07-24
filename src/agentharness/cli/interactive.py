"""Interactive CLI: full-screen TTY workbench or line-oriented non-TTY REPL."""

from __future__ import annotations

import asyncio
import sys
import time
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.markup import escape as _escape
from rich.table import Table

from agentharness.cli.config_store import (
    KNOWN_PROVIDERS,
    RuntimeSettings,
    activate_profile,
    apply_settings_to_harness,
    config_path,
    create_profile,
    load_config,
    mask_secret,
    model_choices,
    resolve_runtime_settings,
    update_provider_fields,
)
from agentharness.cli.input import redirected_input
from agentharness.cli.workbench import LiveRedrawController, Workbench
from agentharness.contracts import (
    ApprovalMode,
    EventType,
    RunRequest,
    RunResult,
    format_usage_brief,
)
from agentharness.harness import Harness


def _enable_windows_ctrl_c() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    # Processes created in a new Windows process group inherit Ctrl+C disabled.
    # Restore normal handling so asyncio.Runner can turn Ctrl+C into cancellation.
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)  # type: ignore[attr-defined]


def _ui_console(console: Console, workbench: Workbench | None) -> tuple[Console, StringIO | None]:
    """When the workbench owns the screen, capture Rich output into the run area."""
    if workbench is None:
        return console, None
    buf = StringIO()
    return (
        Console(file=buf, force_terminal=False, color_system=None, width=100),
        buf,
    )


def _flush_ui(workbench: Workbench | None, buf: StringIO | None) -> None:
    if workbench is not None and buf is not None:
        text = buf.getvalue().rstrip()
        if text:
            workbench.append_system(text)


def _print_help(console: Console) -> None:
    table = Table(title="Interactive commands", show_header=False, box=None)
    table.add_column("command", style="cyan", no_wrap=True)
    table.add_column("action")
    table.add_row("/new", "Start a new session")
    table.add_row("/sessions", "List recent sessions")
    table.add_row("/use <session>", "Continue an existing session")
    table.add_row("/model [name]", "Show or set model (persisted)")
    table.add_row("/provider [name]", "Show or set provider (fake|openai|anthropic)")
    table.add_row("/profile [name]", "List or activate named profiles")
    table.add_row("/profile create <name> <provider>", "Create and activate a profile")
    table.add_row("/config", "Show provider config (keys masked)")
    table.add_row("/config set <key> <value>", "Set api_key|base_url|model for active provider")
    table.add_row("/help", "Show this help")
    table.add_row("/quit", "Exit")
    console.print(table)
    console.print(
        "[dim]Slash commands open as you type. Alt+Enter inserts a newline. "
        "Secrets stay in data-dir cli_config.json.[/dim]"
    )


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
    try:
        return harness.resolve_session_id(value), None
    except KeyError as exc:
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)


def _print_banner(console: Console, settings: RuntimeSettings, approval: str) -> None:
    model = settings.model or "(provider default)"
    console.print(
        f"[bold]Agent Harness[/bold]  provider=[cyan]{settings.provider}[/cyan]  "
        f"model=[cyan]{model}[/cyan]  approval={approval}  source={settings.source}\n"
        "Type [cyan]/help[/cyan] for commands. Tab completes [cyan]/[/cyan] commands."
    )


def _print_config(console: Console, data_dir: Path, settings: RuntimeSettings) -> None:
    cfg = load_config(data_dir)
    pcfg = (cfg.get("providers") or {}).get(settings.provider) or {}
    table = Table(title="CLI config", show_header=True, header_style="bold")
    table.add_column("key")
    table.add_column("value")
    table.add_row("data_dir", str(data_dir))
    table.add_row("config_file", str(config_path(data_dir)))
    table.add_row("provider", settings.provider)
    table.add_row("profile", settings.profile or "(legacy/default)")
    table.add_row("model", settings.model or "(provider default)")
    table.add_row("source", settings.source)
    table.add_row("api_key", mask_secret(pcfg.get("api_key") or settings.api_key))
    table.add_row("base_url", str(pcfg.get("base_url") or settings.base_url or "(env/default)"))
    console.print(table)
    console.print(
        "[dim]Set with: /config set api_key <value> | /config set base_url <url> | "
        "/config set model <name>[/dim]"
    )
    console.print("[dim]Project .env is auto-loaded at startup (existing process env wins). Use /config to persist overrides.[/dim]")


def _handle_config_command(
    line: str,
    *,
    harness: Harness,
    console: Console,
    data_dir: Path,
    settings: RuntimeSettings,
) -> RuntimeSettings:
    parts = line.split(maxsplit=3)
    # /config
    if len(parts) == 1:
        _print_config(console, data_dir, settings)
        return settings
    # /config set KEY VALUE
    if len(parts) >= 4 and parts[1] == "set":
        key = parts[2].lower().replace("-", "_")
        value = parts[3].strip()
        if key in {"api_key", "key", "apikey"}:
            update_provider_fields(data_dir, settings.provider, api_key=value, set_active=True)
            console.print(f"[green]api_key saved for {settings.provider}[/green] ({mask_secret(value)})")
        elif key in {"base_url", "baseurl", "url"}:
            update_provider_fields(data_dir, settings.provider, base_url=value, set_active=True)
            console.print(f"[green]base_url saved for {settings.provider}[/green]: {value}")
        elif key == "model":
            update_provider_fields(data_dir, settings.provider, model=value, set_active=True)
            console.print(f"[green]model saved[/green]: {value}")
        else:
            console.print(
                "[yellow]Unknown config key. Use api_key, base_url, or model.[/yellow]"
            )
            return settings
        settings = resolve_runtime_settings(data_dir, provider=settings.provider, model=None)
        # Prefer just-set model if active provider profile has it
        apply_settings_to_harness(harness, settings)
        return settings
    if len(parts) >= 2 and parts[1] in {"show", "get"}:
        _print_config(console, data_dir, settings)
        return settings
    console.print("[yellow]Usage: /config | /config set <api_key|base_url|model> <value>[/yellow]")
    return settings


def _handle_model_command(
    line: str,
    *,
    harness: Harness,
    console: Console,
    data_dir: Path,
    settings: RuntimeSettings,
    composer: Workbench | None = None,
) -> RuntimeSettings:
    parts = line.split(maxsplit=1)
    if len(parts) == 1:
        choices = model_choices(data_dir, settings.profile)
        if composer is not None and choices:
            name = composer.choose("model", choices, settings.model)
            if name is None:
                return settings
        else:
            console.print(
                f"model=[cyan]{settings.model or '(provider default)'}[/cyan]  "
                f"provider={settings.provider}"
            )
            if choices:
                console.print("models: " + ", ".join(choices), markup=False)
            return settings
    else:
        name = parts[1].strip()
    if not name:
        console.print("[yellow]Usage: /model <name>[/yellow]")
        return settings
    update_provider_fields(data_dir, settings.provider, model=name, set_active=True)
    settings = resolve_runtime_settings(data_dir, provider=settings.provider, model=name)
    apply_settings_to_harness(harness, settings)
    console.print(f"[green]model set[/green]: {settings.model} (provider={settings.provider})")
    return settings


def _print_profiles(console: Console, data_dir: Path, active: str | None) -> None:
    profiles = load_config(data_dir).get("profiles") or {}
    if not profiles:
        console.print("[dim]No named profiles yet.[/dim]")
        return
    table = Table(title="Profiles", box=None)
    table.add_column("active", no_wrap=True)
    table.add_column("name", style="cyan")
    table.add_column("provider")
    table.add_column("model")
    for name, entry in profiles.items():
        table.add_row(
            "*" if name == active else "",
            str(name),
            str(entry.get("provider") or "-"),
            str(entry.get("model") or "(default)"),
        )
    console.print(table)


def _handle_profile_command(
    line: str,
    *,
    harness: Harness,
    console: Console,
    data_dir: Path,
    settings: RuntimeSettings,
    composer: Workbench | None = None,
) -> RuntimeSettings:
    parts = line.split()
    profiles = load_config(data_dir).get("profiles") or {}
    if len(parts) == 1:
        if composer is None:
            _print_profiles(console, data_dir, settings.profile)
            return settings
        name = composer.choose("profile", list(profiles), settings.profile)
        if name is None:
            return settings
    elif len(parts) == 4 and parts[1] == "create":
        name, provider = parts[2], parts[3].lower()
        if provider not in KNOWN_PROVIDERS:
            console.print(f"[yellow]Unknown provider:[/yellow] {provider}")
            return settings
        create_profile(data_dir, name, provider=provider)
    elif len(parts) == 3 and parts[1] == "use":
        name = parts[2]
    elif len(parts) == 2:
        name = parts[1]
    else:
        console.print(
            "[yellow]Usage: /profile [name] | /profile use <name> | "
            "/profile create <name> <provider>[/yellow]"
        )
        return settings
    try:
        activate_profile(data_dir, name)
    except KeyError:
        console.print(f"[yellow]Profile not found:[/yellow] {name}")
        return settings
    settings = resolve_runtime_settings(data_dir)
    apply_settings_to_harness(harness, settings)
    console.print(
        f"[green]profile set[/green]: {name}  provider={settings.provider}  "
        f"model={settings.model or '(default)'}"
    )
    return settings


def _handle_provider_command(
    line: str,
    *,
    harness: Harness,
    console: Console,
    data_dir: Path,
    settings: RuntimeSettings,
) -> RuntimeSettings:
    parts = line.split(maxsplit=1)
    if len(parts) == 1:
        console.print(
            f"provider=[cyan]{settings.provider}[/cyan]  model={settings.model or '(default)'}  "
            f"known={', '.join(KNOWN_PROVIDERS)}"
        )
        return settings
    name = parts[1].strip().lower()
    if name not in KNOWN_PROVIDERS:
        console.print(
            f"[yellow]Unknown provider:[/yellow] {name}. Choose: {', '.join(KNOWN_PROVIDERS)}"
        )
        return settings
    update_provider_fields(data_dir, name, set_active=True)
    settings = resolve_runtime_settings(data_dir, provider=name, model=None)
    apply_settings_to_harness(harness, settings)
    console.print(
        f"[green]provider set[/green]: {settings.provider}  model={settings.model or '(default)'}"
    )
    return settings


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
    workbench: Workbench | None = None,
) -> RunResult | None:
    saw_text = False
    active_run_id: str | None = None
    line_open = False  # cursor is mid-line (after prompt prefix or a text delta)
    started = time.monotonic()

    def _newline_if_open() -> None:
        nonlocal line_open
        if line_open:
            console.print()
            line_open = False

    def on_event(event) -> None:
        nonlocal saw_text, active_run_id, line_open
        if workbench is not None:
            workbench.on_event(event)
            event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
            if event_type == EventType.run_started.value:
                active_run_id = event.run_id
            elif event_type == EventType.text_delta.value:
                if str(event.payload.get("text") or ""):
                    saw_text = True
            return

        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        if event_type == EventType.run_started.value:
            active_run_id = event.run_id
        elif event_type == EventType.text_delta.value:
            text = str(event.payload.get("text") or "")
            if text:
                saw_text = True
                console.print(text, end="", markup=False, highlight=False, soft_wrap=True)
                line_open = True
        elif event_type == EventType.tool_call_start.value:
            _newline_if_open()
            name = _escape(str(event.payload.get("name") or "tool"))
            console.print(f"[cyan]•[/cyan] [bold]{name}[/bold]")
        elif event_type == EventType.tool_call_end.value:
            args = str(event.payload.get("arguments_summary") or "").strip()
            if args:
                console.print(f"    [dim]{_escape(args)}[/dim]")
        elif event_type == EventType.tool_result.value:
            is_error = bool(event.payload.get("is_error"))
            duration = event.payload.get("duration_ms")
            preview = str(event.payload.get("content_preview") or "")
            preview = " ".join(preview.split())
            if len(preview) > 120:
                preview = preview[:119] + "…"
            tag = "[red]err[/red]" if is_error else "[green]ok[/green]"
            dur = f" {int(duration)}ms" if isinstance(duration, (int, float)) else ""
            line = f"    {tag}{dur}"
            if preview:
                line += f"  [dim]{_escape(preview)}[/dim]"
            console.print(line)

    unsubscribe = harness.subscribe_events(on_event)
    if workbench is not None:
        workbench.begin_user_turn(message)
        live = LiveRedrawController(workbench)
        live.__enter__()
    else:
        live = None
        console.print("[bold green]agent>[/bold green] ", end="")
        line_open = True
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
        if workbench is not None:
            workbench.mark_interrupted(active_run_id)
        else:
            _newline_if_open()
            suffix = f" {active_run_id[:12]}" if active_run_id else ""
            console.print(
                f"[yellow]Interrupted{suffix}. Use `agentharness resume <run_id>` "
                "to continue.[/yellow]"
            )
        return None
    except Exception as exc:  # keep the REPL alive on provider/tool faults
        if workbench is not None:
            workbench.mark_error(str(exc))
        else:
            _newline_if_open()
            console.print(f"[red]error:[/red] {_escape(str(exc))}")
        return None
    finally:
        unsubscribe()
        if live is not None:
            live.__exit__(None, None, None)

    elapsed = time.monotonic() - started
    usage = result.usage
    tokens_in = usage.input_tokens if usage else 0
    tokens_out = usage.output_tokens if usage else 0
    usage_line = format_usage_brief(usage)

    if workbench is not None:
        final_output = None if saw_text else result.output
        workbench.finish_turn(
            status=result.status.value,
            run_id=result.run_id,
            session_id=result.session_id,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            error=result.error,
            duration_s=elapsed,
            final_output=final_output,
            usage_detail=usage_line,
        )
        return result

    if not saw_text and result.output:
        _newline_if_open()
        console.print(result.output, markup=False, highlight=False, soft_wrap=True)
    else:
        _newline_if_open()
    tokens = f"  {usage_line}" if usage_line else ""
    console.print(
        f"[dim]status={result.status.value}  run={result.run_id[:12]}  "
        f"session={result.session_id}  provider={provider}  model={model or '-'}"
        f"{tokens}[/dim]"
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
    data_dir: Path | str | None = None,
) -> None:
    """Run the foreground prompt loop until `/quit` or EOF."""
    _enable_windows_ctrl_c()
    dd = Path(data_dir or harness.data_dir).expanduser().resolve()
    settings = resolve_runtime_settings(dd, provider=provider, model=model)
    # Honour explicit CLI provider/model when provided.
    if provider and provider not in ("auto", ""):
        settings = RuntimeSettings(
            provider=provider,
            model=model if model else settings.model,
            api_key=settings.api_key if settings.provider == provider else None,
            base_url=settings.base_url if settings.provider == provider else None,
            source="flag",
        )
        # Re-resolve credentials for the explicit provider.
        refreshed = resolve_runtime_settings(dd, provider=provider, model=model)
        settings = RuntimeSettings(
            provider=provider,
            model=model if model else refreshed.model,
            api_key=refreshed.api_key,
            base_url=refreshed.base_url,
            source="flag",
            profile=refreshed.profile,
        )
    apply_settings_to_harness(harness, settings)

    workbench: Workbench | None = None
    composer: Workbench | None = None
    if sys.stdin.isatty():
        workbench = Workbench(history_path=dd / "cli_history")
        workbench.configure(
            cwd=cwd,
            provider=settings.provider,
            model=settings.model,
            approval=approval,
            profile=settings.profile,
        )
        composer = workbench
        # First paint is the full-screen idle chrome (no bare you> prompt).
        workbench.vm.set_idle()
    else:
        _print_banner(console, settings, approval)

    current_session_id = session_id
    runner = asyncio.Runner()
    try:
        while True:
            try:
                if composer is not None:
                    raw_line = composer.read()
                else:
                    raw_line = redirected_input(console, "you> ")
                line = raw_line.lstrip("\ufeff").strip()
            except EOFError:
                if workbench is None:
                    console.print()
                return
            except KeyboardInterrupt:
                msg = "No run is active; exiting."
                if workbench is not None:
                    workbench.append_system(msg)
                    # Ensure message is visible on exit for tests/users.
                    console.print(f"[yellow]{msg}[/yellow]")
                else:
                    console.print(f"\n[yellow]{msg}[/yellow]")
                return

            if not line:
                continue
            if line in {"/quit", "/exit"}:
                return

            ui, buf = _ui_console(console, workbench)
            if line == "/help":
                _print_help(ui)
                _flush_ui(workbench, buf)
                continue
            if line == "/new":
                current_session_id = None
                ui.print("[green]New session ready.[/green]")
                _flush_ui(workbench, buf)
                continue
            if line == "/sessions":
                _print_sessions(harness, ui)
                _flush_ui(workbench, buf)
                continue
            if line == "/use" or line.startswith("/use "):
                selected, error = _resolve_session(harness, line[4:])
                if error:
                    ui.print(f"[yellow]{error}[/yellow]")
                elif selected is not None:
                    current_session_id = selected
                    ui.print(f"[green]Using session {selected[:12]}.[/green]")
                _flush_ui(workbench, buf)
                continue
            if line == "/model" or line.startswith("/model "):
                settings = _handle_model_command(
                    line,
                    harness=harness,
                    console=ui,
                    data_dir=dd,
                    settings=settings,
                    composer=composer,
                )
                _flush_ui(workbench, buf)
                if workbench is not None:
                    workbench.configure(
                        cwd=cwd,
                        provider=settings.provider,
                        model=settings.model,
                        approval=approval,
                        profile=settings.profile,
                    )
                continue
            if line == "/provider" or line.startswith("/provider "):
                settings = _handle_provider_command(
                    line, harness=harness, console=ui, data_dir=dd, settings=settings
                )
                _flush_ui(workbench, buf)
                if workbench is not None:
                    workbench.configure(
                        cwd=cwd,
                        provider=settings.provider,
                        model=settings.model,
                        approval=approval,
                        profile=settings.profile,
                    )
                continue
            if line == "/profile" or line.startswith("/profile "):
                settings = _handle_profile_command(
                    line,
                    harness=harness,
                    console=ui,
                    data_dir=dd,
                    settings=settings,
                    composer=composer,
                )
                _flush_ui(workbench, buf)
                if workbench is not None:
                    workbench.configure(
                        cwd=cwd,
                        provider=settings.provider,
                        model=settings.model,
                        approval=approval,
                        profile=settings.profile,
                    )
                continue
            if line == "/config" or line.startswith("/config "):
                settings = _handle_config_command(
                    line, harness=harness, console=ui, data_dir=dd, settings=settings
                )
                _flush_ui(workbench, buf)
                if workbench is not None:
                    workbench.configure(
                        cwd=cwd,
                        provider=settings.provider,
                        model=settings.model,
                        approval=approval,
                        profile=settings.profile,
                    )
                continue
            if line.startswith("/"):
                ui.print(f"[yellow]Unknown command:[/yellow] {line}")
                _flush_ui(workbench, buf)
                continue

            result = _run_turn(
                runner=runner,
                harness=harness,
                console=console,
                message=line,
                session_id=current_session_id,
                provider=settings.provider,
                model=settings.model,
                approval=approval,
                cwd=cwd,
                workbench=workbench,
            )
            if result is not None:
                current_session_id = result.session_id
    finally:
        try:
            runner.run(harness.aclose())
        finally:
            runner.close()
