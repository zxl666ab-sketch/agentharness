"""CLI entrypoints: interactive prompt, run, resume, cancel, runs, web, eval, doctor."""

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
from agentharness.cli.envfile import load_project_env
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
    help="Agent Harness — interactive local agent with a run inspector",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()

_current_harness: Harness | None = None

_UNSUCCESSFUL_RESULT_STATUSES = frozenset(
    {RunStatus.failed, RunStatus.cancelled, RunStatus.interrupted, RunStatus.require_human}
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
        "require_human": "magenta",
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
    with_web: bool = typer.Option(True, "--web/--no-web", help="Start Web Inspector with interactive CLI"),
    open_web: bool = typer.Option(True, "--open/--no-open", help="Open the Inspector in a browser"),
    web_port: int = typer.Option(8741, "--web-port", min=1, max=65525),
) -> None:
    """With no subcommand, open the line-oriented multi-turn CLI."""
    load_project_env()
    if ctx.invoked_subcommand is not None:
        return
    dd = _data_dir(data_dir)
    work = str(Path(cwd).resolve()) if cwd else str(Path.cwd())
    settings = resolve_runtime_settings(dd, provider=provider, model=model)
    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    from agentharness.cli.interactive import run_interactive

    companion = None
    if sys.stdin.isatty() and with_web:
        from agentharness.cli.web_companion import start_web_companion

        try:
            companion = start_web_companion(
                dd, preferred_port=web_port, open_browser=open_web
            )
            reused = " (reused)" if companion.reused else ""
            console.print(f"[green]Web Inspector[/green] {companion.url}{reused}")
        except Exception as exc:  # noqa: BLE001 - CLI remains usable without Web
            console.print(f"[yellow]Web Inspector unavailable:[/yellow] {exc}")

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
        if companion is not None:
            companion.stop()


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
    """Start the run inspector as a foreground process."""
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
    from agentharness.api.compatibility import API_SCHEMA_VERSION
    from agentharness.api.server import create_app

    application = create_app(harness=h)
    url = f"http://{host}:{port}"
    console.print(f"[green]Run inspector[/green] {url}")
    console.print(f"data_dir={dd}")
    console.print(f"python={sys.executable}")
    console.print(f"api_schema={API_SCHEMA_VERSION}")
    import uvicorn

    try:
        uvicorn.run(application, host=host, port=port, log_level="info")
    finally:
        h.close()



@app.command("eval")
def eval_cmd(
    suite: Path = typer.Argument(..., help="Suite file: .yaml / .json / .jsonl"),
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="Default provider (overridden by case)"
    ),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Default model (overridden by case)"
    ),
    concurrency: int = typer.Option(3, "--concurrency", min=1, help="Max parallel cases"),
    baseline: Path | None = typer.Option(
        None, "--baseline", help="Prior JSON report for regression gates"
    ),
    report_json: Path | None = typer.Option(
        None, "--report-json", help="Write JSON report (also usable as next baseline)"
    ),
    report_junit: Path | None = typer.Option(
        None, "--report-junit", help="Write JUnit XML for CI"
    ),
    fail_on_regression: bool = typer.Option(
        False,
        "--fail-on-regression",
        help="Exit 1 when baseline gates / new failures trigger (requires --baseline)",
    ),
    data_dir: str | None = typer.Option(
        None,
        "--data-dir",
        help="Persist runs here for Web Inspector deep-links (default: temp)",
    ),
    min_pass_rate: float | None = typer.Option(
        None, "--min-pass-rate", help="Baseline gate: minimum pass rate"
    ),
    min_mean_score: float | None = typer.Option(
        None, "--min-mean-score", help="Baseline gate: minimum mean score"
    ),
    max_score_regression: float | None = typer.Option(
        None, "--max-score-regression", help="Baseline gate: max absolute score drop"
    ),
    max_token_regression: float | None = typer.Option(
        None, "--max-token-regression", help="Baseline gate: max token increase ratio"
    ),
    max_latency_regression: float | None = typer.Option(
        None,
        "--max-latency-regression",
        help="Baseline gate: max mean-latency increase ratio",
    ),
) -> None:
    """Run an offline eval suite and write optional JSON/JUnit reports.

    Exit codes:
      0 - all cases passed (and no regression when --fail-on-regression)
      1 - case/grader failure or regression gate triggered
      2 - suite/CLI/baseline configuration error
    """
    from agentharness.eval.baseline import load_baseline
    from agentharness.eval.contracts import RegressionPolicy
    from agentharness.eval.dataset import EvalConfigError, load_suite
    from agentharness.eval.regression import RegressionGate, normalize_regression_set
    from agentharness.eval.report import write_json_report, write_junit_xml
    from agentharness.eval.runner import run_suite

    try:
        loaded = load_suite(suite)
    except EvalConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(2) from None
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(2) from None

    try:
        report = asyncio.run(
            run_suite(
                loaded,
                provider=provider,
                model=model,
                concurrency=concurrency,
                data_dir=data_dir,
            )
        )
    except EvalConfigError as exc:
        console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(2) from None
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]eval failed:[/red] {exc}")
        raise typer.Exit(1) from None

    regression_failed = False
    reg = None
    if baseline is not None:
        try:
            baseline_payload = load_baseline(baseline)
            decision = RegressionGate.compare(
                baseline_payload,
                report,
                RegressionPolicy(
                    min_pass_rate=min_pass_rate,
                    min_mean_score=min_mean_score,
                    max_score_drop=max_score_regression,
                    max_token_ratio_increase=max_token_regression,
                    max_latency_ratio_increase=max_latency_regression,
                ),
            )
            report.gate_decision = decision
            reg = decision.regression
            base_set = normalize_regression_set(
                baseline_payload, fallback_id=str(baseline)
            )
            base_index = {item.case_id: item for item in base_set.cases}
            for item in report.results:
                previous = base_index.get(item.case_id)
                item.baseline_diff = {
                    "baseline_passed": (
                        previous.evaluation.passed if previous is not None else None
                    ),
                    "candidate_passed": item.passed,
                    "score_delta": (
                        round(item.score - previous.evaluation.score, 6)
                        if previous is not None
                        and item.score is not None
                        and previous.evaluation.score is not None
                        else None
                    ),
                }
            if report.data_dir and Path(report.data_dir).is_dir():
                retained = Harness(data_dir=report.data_dir)
                try:
                    for item in report.results:
                        if item.run_id:
                            retained.retain_run_regression(
                                item.run_id,
                                regression=decision.regression,
                                gate_decision=decision,
                                baseline_diff=item.baseline_diff,
                                rerun_statistics=report.rerun_statistics,
                            )
                finally:
                    retained.close()
            if fail_on_regression and not decision.passed:
                regression_failed = True
        except EvalConfigError as exc:
            console.print(f"[red]baseline error:[/red] {exc}")
            raise typer.Exit(2) from None

    json_path: Path | None = None
    junit_path: Path | None = None
    if report_json is not None:
        json_path = write_json_report(report, report_json)
    if report_junit is not None:
        junit_path = write_junit_xml(report, report_junit)

    table = Table(title=f"Eval · {report.suite}", show_header=True, header_style="bold")
    table.add_column("case")
    table.add_column("pass")
    table.add_column("score")
    table.add_column("tokens")
    table.add_column("steps")
    table.add_column("latency")
    table.add_column("status")
    for r in report.results:
        style = "green" if r.passed else "red"
        table.add_row(
            r.case_id,
            "Y" if r.passed else "N",
            f"{r.score:.2f}" if r.score is not None else "unscored",
            str(r.total_tokens),
            str(r.steps),
            f"{r.latency_s:.3f}s",
            f"[{style}]{r.status}[/{style}]",
        )
    console.print(table)
    console.print(
        f"pass_rate={report.pass_rate:.2%}  "
        f"mean_score={report.mean_score:.3f}  " if report.mean_score is not None else
        f"pass_rate={report.pass_rate:.2%}  mean_score=unscored  "
    )
    console.print(
        f"total={report.total}  passed={report.passed}"
    )
    if report.rerun_statistics is not None:
        stats = report.rerun_statistics
        console.print(
            "rerun "
            f"n={stats.sample_count} success_rate={stats.success_rate:.2%} "
            f"wilson=[{stats.wilson_low:.3f}, {stats.wilson_high:.3f}] "
            f"variance={stats.score_variance:.6f} "
            f"p50={stats.p50_latency_ms:.1f}ms p95={stats.p95_latency_ms:.1f}ms"
        )

    if json_path is not None:
        console.print(f"[green]JSON report[/green] {json_path}")
    if junit_path is not None:
        console.print(f"[green]JUnit report[/green] {junit_path}")
    if data_dir:
        console.print(
            f"[dim]Web Inspector:[/dim] uv run agentharness web --data-dir {data_dir}"
        )
        console.print(
            "[dim]Load the JSON report in the Eval view, then open failed cases via run_id.[/dim]"
        )
    else:
        console.print(
            "[dim]Default data_dir was temporary - deep-link trajectories are not retained. "
            "Re-run with --data-dir to inspect runs in the Web UI.[/dim]"
        )

    if reg is not None:
        console.print(
            f"regression failed={reg.failed}  new_failures={len(reg.new_failures)}"
        )
        for g in reg.gates:
            mark = "FAIL" if g.triggered else "ok"
            console.print(f"  [{mark}] {g.gate}: {g.message}")
    if report.passed < report.total or regression_failed:
        raise typer.Exit(1)
    raise typer.Exit(0)


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
    load_project_env()
    app()


if __name__ != "__main__":
    pass
else:
    main()
