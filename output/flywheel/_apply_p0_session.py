from pathlib import Path

def patch(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label}: anchor not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"ok {label}")

# harness.resolve_session_id
patch(
    "src/agentharness/harness.py",
    '''    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List sessions with latest top-level run status for the observer UI."""
        sessions = self.storage.list_sessions(limit=limit)
        return [self._enrich_session(s) for s in sessions]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
''',
    '''    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List sessions with latest top-level run status for the observer UI."""
        sessions = self.storage.list_sessions(limit=limit)
        return [self._enrich_session(s) for s in sessions]

    def resolve_session_id(self, value: str, *, limit: int = 1000) -> str:
        """Resolve an exact session id or a unique prefix.

        Used by interactive ``/use`` and scriptable ``run --session`` so a
        truncated CLI display id cannot create a brand-new 12-char session.
        """
        value = (value or "").strip()
        if not value:
            raise ValueError("session id is required")
        # Exact match first (including intentional short ids already in DB).
        if self.storage.get_session(value) is not None:
            return value
        matches = [
            str(session["id"])
            for session in self.storage.list_sessions(limit=limit)
            if str(session.get("id", "")).startswith(value)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"Session not found: {value}")
        raise ValueError(f"Session prefix is ambiguous: {value}")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
''',
    "harness.resolve_session_id",
)

# interactive _resolve_session uses harness method
patch(
    "src/agentharness/cli/interactive.py",
    '''def _resolve_session(harness: Harness, value: str) -> tuple[str | None, str | None]:
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
''',
    '''def _resolve_session(harness: Harness, value: str) -> tuple[str | None, str | None]:
    value = value.strip()
    if not value:
        return None, "Usage: /use <session>"
    try:
        return harness.resolve_session_id(value), None
    except KeyError as exc:
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)
''',
    "interactive.resolve_session",
)

# interactive status line: full session id
patch(
    "src/agentharness/cli/interactive.py",
    '''        f"session={result.session_id[:12]}  provider={provider}  model={model or '-'}"
''',
    '''        f"session={result.session_id}  provider={provider}  model={model or '-'}"
''',
    "interactive.full_session_print",
)

# main.py run: resolve session + print full ids
patch(
    "src/agentharness/cli/main.py",
    '''    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    req = RunRequest(
        message=message,
        session_id=session,
        provider=settings.provider,
        model=settings.model,
        approval=ApprovalMode(approval),
        cwd=str(work),
    )
''',
    '''    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    resolved_session = session
    if session:
        try:
            resolved_session = h.resolve_session_id(session)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
    req = RunRequest(
        message=message,
        session_id=resolved_session,
        provider=settings.provider,
        model=settings.model,
        approval=ApprovalMode(approval),
        cwd=str(work),
    )
''',
    "main.run_resolve_session",
)

patch(
    "src/agentharness/cli/main.py",
    '''    console.print(
        f"\\n[bold]status[/bold]={_status_style(result.status.value)}  "
        f"run_id={result.run_id[:12]}  session={result.session_id[:12]}"
        f"{usage_suffix}"
    )
''',
    '''    console.print(
        f"\\n[bold]status[/bold]={_status_style(result.status.value)}  "
        f"run_id={result.run_id}  session={result.session_id}"
        f"{usage_suffix}"
    )
''',
    "main.run_full_ids",
)

# root interactive --session also resolve
patch(
    "src/agentharness/cli/main.py",
    '''    h = _make_harness(str(dd), approval=approval)
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
''',
    '''    h = _make_harness(str(dd), approval=approval)
    apply_settings_to_harness(h, settings)
    from agentharness.cli.interactive import run_interactive

    resolved_session = session
    if session:
        try:
            resolved_session = h.resolve_session_id(session)
        except (KeyError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None

    try:
        run_interactive(
            harness=h,
            console=console,
            provider=provider or "auto",
            model=model,
            approval=approval,
            cwd=work,
            session_id=resolved_session,
            data_dir=dd,
        )
''',
    "main.root_resolve_session",
)

print("session patches done")
