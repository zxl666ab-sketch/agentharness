"""Pure CLI view model: fold harness events into renderable workbench regions.

No I/O — unit tests drive sequences without a live terminal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UiPhase(StrEnum):
    idle = "idle"
    connecting = "connecting"
    running = "running"
    tool_running = "tool_running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class ItemKind(StrEnum):
    user = "user"
    assistant = "assistant"
    tool = "tool"
    system = "system"
    status = "status"
    error = "error"


@dataclass
class HeaderState:
    branch: str = ""
    cwd: str = ""
    repo_or_cwd: str = ""
    provider: str = ""
    model: str = ""
    approval: str = "ask"
    profile: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    token_budget: int | None = None

    def right_parts(self) -> list[str]:
        model = self.model or "(default)"
        parts = [f"{self.provider}/{model}", self.approval]
        if self.profile:
            parts.insert(1, self.profile)
        if self.tokens_in or self.tokens_out:
            parts.append(f"{_fmt_tokens(self.tokens_in)}/{_fmt_tokens(self.tokens_out)}")
        elif self.token_budget is not None:
            parts.append(f"0 / {_fmt_tokens(self.token_budget)}")
        return parts


@dataclass
class ToolRow:
    tool_call_id: str
    name: str
    args_summary: str = ""
    state: str = "running"  # running | ok | err
    duration_ms: float | None = None
    preview: str = ""
    is_error: bool = False


@dataclass
class RunItem:
    kind: ItemKind
    text: str = ""
    tool: ToolRow | None = None
    key: str = ""


@dataclass
class CoalesceStats:
    """Track text_delta events vs actual UI invalidations."""

    delta_events: int = 0
    redraws: int = 0
    coalesced_skips: int = 0
    last_redraw_at: float = 0.0
    min_interval_s: float = 0.05
    pending: bool = False

    def note_delta(self, now: float | None = None) -> bool:
        """Record a text_delta. Return True when a redraw should happen now."""
        self.delta_events += 1
        ts = time.monotonic() if now is None else now
        if self.delta_events == 1 or (ts - self.last_redraw_at) >= self.min_interval_s:
            self.last_redraw_at = ts
            self.redraws += 1
            self.pending = False
            return True
        self.pending = True
        self.coalesced_skips += 1
        return False

    def flush(self, now: float | None = None) -> bool:
        """Force a final redraw if one is pending (or always bump for completeness)."""
        ts = time.monotonic() if now is None else now
        if self.pending or self.delta_events > 0:
            self.pending = False
            self.last_redraw_at = ts
            self.redraws += 1
            return True
        return False


@dataclass
class CliViewModel:
    """Folded interactive UI state for fixed chrome + scrollable run area."""

    header: HeaderState = field(default_factory=HeaderState)
    phase: UiPhase = UiPhase.idle
    items: list[RunItem] = field(default_factory=list)
    tools: dict[str, ToolRow] = field(default_factory=dict)
    stream_text: str = ""
    stream_item_index: int | None = None
    active_run_id: str | None = None
    active_tool_name: str | None = None
    status_detail: str = ""
    coalesce: CoalesceStats = field(default_factory=CoalesceStats)
    _seq: int = 0

    # ------------------------------------------------------------------
    # Header / composer meta
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        cwd: str = "",
        branch: str = "",
        provider: str = "",
        model: str | None = None,
        approval: str = "ask",
        profile: str | None = None,
        token_budget: int | None = None,
    ) -> None:
        self.header.cwd = cwd
        self.header.branch = branch
        label = branch or _basename(cwd) or cwd or "."
        if branch and cwd:
            self.header.repo_or_cwd = f"{branch} {cwd}"
        else:
            self.header.repo_or_cwd = label
        self.header.provider = provider
        self.header.model = model or ""
        self.header.approval = approval
        self.header.profile = profile or ""
        self.header.token_budget = token_budget

    def set_runtime(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        approval: str | None = None,
        profile: str | None = None,
    ) -> None:
        if provider is not None:
            self.header.provider = provider
        if model is not None:
            self.header.model = model
        if approval is not None:
            self.header.approval = approval
        if profile is not None:
            self.header.profile = profile

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def begin_user_turn(self, message: str) -> None:
        self._close_stream_item()
        self.phase = UiPhase.connecting
        self.status_detail = "connecting…"
        self.stream_text = ""
        self.stream_item_index = None
        self.active_tool_name = None
        self.coalesce = CoalesceStats(min_interval_s=self.coalesce.min_interval_s)
        self.items.append(
            RunItem(kind=ItemKind.user, text=message, key=self._next_key("user"))
        )

    def finish_turn(
        self,
        *,
        status: str,
        run_id: str = "",
        session_id: str = "",
        provider: str = "",
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: str | None = None,
        duration_s: float | None = None,
        final_output: str | None = None,
        usage_detail: str | None = None,
    ) -> None:
        self._close_stream_item()
        if final_output and not any(
            item.kind == ItemKind.assistant and item.text.strip() for item in self.items
        ):
            self.items.append(
                RunItem(
                    kind=ItemKind.assistant,
                    text=final_output,
                    key=self._next_key("assistant"),
                )
            )
        phase = _status_to_phase(status)
        self.phase = phase
        self.header.tokens_in = tokens_in
        self.header.tokens_out = tokens_out
        if tokens_in or tokens_out:
            self.header.token_budget = None
        parts = [f"status={status}"]
        if run_id:
            parts.append(f"run={run_id[:12]}")
        if session_id:
            parts.append(f"session={session_id[:12]}")
        if provider:
            parts.append(f"provider={provider}")
        if model:
            parts.append(f"model={model}")
        elif self.header.model:
            parts.append(f"model={self.header.model or '-'}")
        if usage_detail:
            parts.append(usage_detail)
        elif tokens_in or tokens_out:
            parts.append(f"tokens={tokens_in}/{tokens_out}")
        if duration_s is not None:
            parts.append(f"{duration_s:.1f}s")
        detail = "  ".join(parts)
        self.status_detail = detail
        self.items.append(
            RunItem(kind=ItemKind.status, text=detail, key=self._next_key("status"))
        )
        if error:
            self.items.append(
                RunItem(kind=ItemKind.error, text=error, key=self._next_key("error"))
            )
        self.active_run_id = None
        self.active_tool_name = None
        self.coalesce.flush()

    def mark_interrupted(self, run_id: str | None = None) -> None:
        self._close_stream_item()
        self.phase = UiPhase.interrupted
        suffix = f" {run_id[:12]}" if run_id else ""
        detail = (
            f"Interrupted{suffix}. Use `agentharness resume <run_id>` to continue."
        )
        self.status_detail = detail
        self.items.append(
            RunItem(kind=ItemKind.status, text=detail, key=self._next_key("status"))
        )
        self.active_run_id = None
        self.active_tool_name = None
        self.coalesce.flush()

    def mark_error(self, message: str) -> None:
        self._close_stream_item()
        self.phase = UiPhase.failed
        self.status_detail = message
        self.items.append(
            RunItem(kind=ItemKind.error, text=message, key=self._next_key("error"))
        )
        self.active_run_id = None
        self.active_tool_name = None
        self.coalesce.flush()

    def add_system(self, text: str) -> None:
        cleaned = text.rstrip("\n")
        if not cleaned:
            return
        self.items.append(
            RunItem(kind=ItemKind.system, text=cleaned, key=self._next_key("system"))
        )

    def set_idle(self) -> None:
        if self.phase in {
            UiPhase.completed,
            UiPhase.failed,
            UiPhase.interrupted,
            UiPhase.cancelled,
            UiPhase.connecting,
            UiPhase.running,
            UiPhase.tool_running,
            UiPhase.waiting_approval,
        }:
            # Keep last status visible; phase returns to idle for chrome.
            pass
        self.phase = UiPhase.idle
        self.active_tool_name = None
        self.status_detail = self._idle_detail()

    def _idle_detail(self) -> str:
        model = self.header.model or "(provider default)"
        return (
            f"provider={self.header.provider}  model={model}  "
            f"approval={self.header.approval}  cwd={self.header.cwd or '.'}"
        )

    # ------------------------------------------------------------------
    # Event fold
    # ------------------------------------------------------------------

    def apply_event(self, event: Any, *, now: float | None = None) -> bool:
        """Apply a harness EventEnvelope (or duck-typed stand-in).

        Returns True when the UI should invalidate/redraw.
        """
        event_type = _event_type(event)
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type == "run_started":
            self.active_run_id = getattr(event, "run_id", None) or payload.get("run_id")
            self.phase = UiPhase.running
            self.status_detail = "running…"
            return True

        if event_type == "run_status":
            status = str(payload.get("status") or "")
            if status == "waiting_approval":
                self.phase = UiPhase.waiting_approval
                tool = str(payload.get("tool") or payload.get("tool_name") or "")
                self.status_detail = (
                    f"waiting approval{f' · {tool}' if tool else ''}"
                )
                return True
            if status == "running":
                self.phase = (
                    UiPhase.tool_running if self.active_tool_name else UiPhase.running
                )
                self.status_detail = (
                    f"tool · {self.active_tool_name}"
                    if self.active_tool_name
                    else "running…"
                )
                return True
            return False

        if event_type == "text_delta":
            text = str(payload.get("text") or "")
            if not text:
                return False
            self.phase = UiPhase.running
            self.status_detail = "streaming…"
            self.stream_text += text
            if self.stream_item_index is None:
                self.items.append(
                    RunItem(
                        kind=ItemKind.assistant,
                        text=self.stream_text,
                        key=self._next_key("assistant"),
                    )
                )
                self.stream_item_index = len(self.items) - 1
            else:
                self.items[self.stream_item_index].text = self.stream_text
            return self.coalesce.note_delta(now)

        if event_type == "tool_call_start":
            self._close_stream_item()
            tc_id = str(
                payload.get("tool_call_id") or payload.get("id") or self._next_key("tc")
            )
            name = str(payload.get("name") or "tool")
            row = self.tools.get(tc_id)
            if row is None:
                row = ToolRow(tool_call_id=tc_id, name=name, state="running")
                self.tools[tc_id] = row
                self.items.append(
                    RunItem(
                        kind=ItemKind.tool,
                        tool=row,
                        key=tc_id,
                    )
                )
            else:
                row.name = name or row.name
                row.state = "running"
            self.active_tool_name = row.name
            self.phase = UiPhase.tool_running
            self.status_detail = f"tool · {row.name}"
            return True

        if event_type == "tool_call_end":
            tc_id = str(payload.get("tool_call_id") or payload.get("id") or "")
            row = self._tool_row(tc_id, payload)
            args = str(payload.get("arguments_summary") or "").strip()
            if args:
                row.args_summary = args
            if "is_error" in payload:
                row.is_error = bool(payload.get("is_error"))
                row.state = "err" if row.is_error else "ok"
            return True

        if event_type == "tool_result":
            tc_id = str(payload.get("tool_call_id") or payload.get("id") or "")
            row = self._tool_row(tc_id, payload)
            row.is_error = bool(payload.get("is_error"))
            row.state = "err" if row.is_error else "ok"
            duration = payload.get("duration_ms")
            if isinstance(duration, (int, float)):
                row.duration_ms = float(duration)
            preview = str(payload.get("content_preview") or "")
            preview = " ".join(preview.split())
            row.preview = preview
            if self.active_tool_name == row.name:
                self.active_tool_name = None
            if self.phase == UiPhase.tool_running:
                self.phase = UiPhase.running
                self.status_detail = "running…"
            return True

        if event_type == "approval_requested":
            self.phase = UiPhase.waiting_approval
            tool = str(payload.get("tool") or payload.get("tool_name") or "")
            self.status_detail = f"waiting approval{f' · {tool}' if tool else ''}"
            summary = str(payload.get("arguments_summary") or "").strip()
            if tool or summary:
                msg = f"审批请求 tool={tool or '?'} {summary}".strip()
                self.items.append(
                    RunItem(
                        kind=ItemKind.system,
                        text=msg,
                        key=self._next_key("approval"),
                    )
                )
            return True

        if event_type == "approval_resolved":
            if self.phase == UiPhase.waiting_approval:
                self.phase = UiPhase.running
                self.status_detail = "running…"
            return True

        if event_type in {
            "run_completed",
            "run_failed",
            "run_cancelled",
            "run_interrupted",
        }:
            # finish_turn is the authoritative end; still update phase early.
            self.phase = _status_to_phase(event_type.replace("run_", ""))
            return True

        if event_type == "error":
            msg = str(payload.get("message") or payload.get("error") or "error")
            self.items.append(
                RunItem(kind=ItemKind.error, text=msg, key=self._next_key("error"))
            )
            return True

        return False

    def _tool_row(self, tc_id: str, payload: dict[str, Any]) -> ToolRow:
        if not tc_id:
            tc_id = self._next_key("tc")
        row = self.tools.get(tc_id)
        if row is None:
            name = str(payload.get("name") or "tool")
            row = ToolRow(tool_call_id=tc_id, name=name, state="running")
            self.tools[tc_id] = row
            self.items.append(RunItem(kind=ItemKind.tool, tool=row, key=tc_id))
        return row

    def _close_stream_item(self) -> None:
        if self.stream_item_index is not None:
            self.items[self.stream_item_index].text = self.stream_text
        self.stream_item_index = None
        # Keep stream_text for finish_turn dedup checks; reset on next begin.

    def _next_key(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    # ------------------------------------------------------------------
    # Display formatting (pure, width-aware)
    # ------------------------------------------------------------------

    def format_header_line(self, width: int) -> str:
        left = self.header.repo_or_cwd or self.header.cwd or "."
        right = " · ".join(self.header.right_parts())
        return _fit_left_right(left, right, max(width, 20))

    def format_shortcut_line(self) -> str:
        return (
            "Enter:send  |  Alt+Enter:newline  |  Tab:complete  |  "
            "Esc:close  |  Ctrl+C:interrupt"
        )

    def format_composer_meta(self) -> str:
        profile = self.header.profile or self.header.provider or "default"
        return f"{profile} · {self.header.approval}"

    def format_phase_line(self, width: int) -> str:
        label = {
            UiPhase.idle: "idle",
            UiPhase.connecting: "connecting",
            UiPhase.running: "running",
            UiPhase.tool_running: f"tool:{self.active_tool_name or '…'}",
            UiPhase.waiting_approval: "waiting approval",
            UiPhase.completed: "completed",
            UiPhase.failed: "failed",
            UiPhase.interrupted: "interrupted",
            UiPhase.cancelled: "cancelled",
        }.get(self.phase, self.phase.value)
        detail = self.status_detail or self._idle_detail()
        return truncate_display(f"[{label}] {detail}", width)

    def iter_body_lines(self, width: int) -> list[str]:
        """Return display lines for the scrollable run area (no ANSI)."""
        w = max(width, 20)
        lines: list[str] = []
        for item in self.items:
            if item.kind == ItemKind.user:
                lines.append(truncate_display(f"you  {item.text}", w))
                for cont in _wrap_remainder(item.text, w, prefix="     "):
                    lines.append(cont)
            elif item.kind == ItemKind.assistant:
                text = item.text
                if not text:
                    continue
                first, *rest = text.splitlines() or [""]
                lines.append(truncate_display(f"agent  {first}", w))
                for part in rest:
                    lines.append(truncate_display(f"      {part}", w))
                if not text.endswith("\n") and "\n" not in text and len(text) > w - 7:
                    # long single line: hard-split remainder
                    extra = _hard_wrap(text, w - 7)
                    lines = lines[:-1]
                    if extra:
                        lines.append(truncate_display(f"agent  {extra[0]}", w))
                        for part in extra[1:]:
                            lines.append(truncate_display(f"      {part}", w))
            elif item.kind == ItemKind.tool and item.tool is not None:
                lines.extend(self._tool_lines(item.tool, w))
            elif item.kind == ItemKind.system:
                for part in item.text.splitlines() or [""]:
                    lines.append(truncate_display(part, w))
            elif item.kind == ItemKind.status:
                lines.append(truncate_display(item.text, w))
            elif item.kind == ItemKind.error:
                lines.append(truncate_display(f"error: {item.text}", w))
        return lines

    def _tool_lines(self, tool: ToolRow, width: int) -> list[str]:
        state = tool.state
        if state == "running":
            badge = "…"
        elif state == "err":
            badge = "err"
        else:
            badge = "ok"
        head = f"• {tool.name}  [{badge}]"
        if tool.duration_ms is not None:
            head += f"  {int(tool.duration_ms)}ms"
        lines = [truncate_display(head, width)]
        if tool.args_summary:
            lines.append(
                truncate_display(f"  args  {tool.args_summary}", width)
            )
        if tool.preview:
            lines.append(truncate_display(f"  {tool.preview}", width))
        return lines

    def render_frame(self, width: int, height: int) -> list[str]:
        """Render a full static frame: header, body, composer chrome, shortcuts.

        Pure function of state + dimensions. Used for tests and screenshots.
        """
        w = max(width, 40)
        h = max(height, 10)
        header = self.format_header_line(w)
        phase = self.format_phase_line(w)
        shortcuts = truncate_display(self.format_shortcut_line(), w)
        meta = self.format_composer_meta()
        # Fixed chrome rows (excluding body):
        # header, phase, border, box×4, meta, shortcuts = 9
        chrome = 9
        body_h = max(1, h - chrome)
        body = self.iter_body_lines(w)
        if len(body) > body_h:
            body = body[-body_h:]
        else:
            body = body + [""] * (body_h - len(body))

        border = "─" * w
        composer_inner = max(w - 2, 1)
        prompt_line = truncate_display("> ", composer_inner)
        # empty second line inside box for multi-line feel
        empty_line = " " * composer_inner
        meta_line = _fit_left_right("", meta, w)

        frame = [header, phase, *body, border]
        frame.append("┌" + "─" * composer_inner + "┐")
        frame.append("│" + prompt_line.ljust(composer_inner)[:composer_inner] + "│")
        frame.append("│" + empty_line + "│")
        frame.append("└" + "─" * composer_inner + "┘")
        frame.append(meta_line)
        frame.append(shortcuts)
        # Pad / trim to exact height (never drop shortcuts/composer: body already sized)
        if len(frame) < h:
            # Insert blank body padding above border if needed
            border_idx = 2 + body_h
            pad = h - len(frame)
            frame[border_idx:border_idx] = [""] * pad
        return frame[:h]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def truncate_display(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate a string to display width without mid-word panic; no wrap overflow."""
    if width <= 0:
        return ""
    text = text.replace("\t", " ")
    if _display_len(text) <= width:
        return text
    ell_w = _display_len(ellipsis)
    if width <= ell_w:
        # Degenerate: return as many narrow chars as fit from ellipsis ascii fallback
        return "." * width
    budget = width - ell_w
    out: list[str] = []
    used = 0
    for ch in text:
        cl = 2 if ord(ch) > 0xFF else 1
        if used + cl > budget:
            break
        out.append(ch)
        used += cl
    result = "".join(out) + ellipsis
    # Safety: never exceed width even if ellipsis is wide
    while result and _display_len(result) > width:
        if len(out) == 0:
            return "." * width
        out.pop()
        result = "".join(out) + ellipsis
    return result


def _display_len(text: str) -> int:
    return sum(2 if ord(ch) > 0xFF else 1 for ch in text)


def _fit_left_right(left: str, right: str, width: int) -> str:
    right = truncate_display(right, max(8, width // 2))
    gap = 2
    left_budget = max(4, width - _display_len(right) - gap)
    left = truncate_display(left, left_budget)
    pad = max(1, width - _display_len(left) - _display_len(right))
    line = left + (" " * pad) + right
    return truncate_display(line, width) if _display_len(line) > width else line


def _basename(path: str) -> str:
    if not path:
        return ""
    p = path.replace("\\", "/").rstrip("/")
    if not p:
        return path
    return p.rsplit("/", 1)[-1]


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)


def _event_type(event: Any) -> str:
    raw = getattr(event, "type", event)
    if hasattr(raw, "value"):
        return str(raw.value)
    return str(raw)


def _status_to_phase(status: str) -> UiPhase:
    mapping = {
        "completed": UiPhase.completed,
        "failed": UiPhase.failed,
        "interrupted": UiPhase.interrupted,
        "cancelled": UiPhase.cancelled,
        "running": UiPhase.running,
        "waiting_approval": UiPhase.waiting_approval,
        "connecting": UiPhase.connecting,
    }
    return mapping.get(status, UiPhase.idle)


def _wrap_remainder(text: str, width: int, prefix: str) -> list[str]:
    """If user message fits one line after 'you  ', no extra lines.

    Multi-line messages get subsequent lines with prefix.
    """
    parts = text.splitlines()
    if len(parts) <= 1:
        return []
    lines: list[str] = []
    for part in parts[1:]:
        lines.append(truncate_display(f"{prefix}{part}", width))
    return lines


def _hard_wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    lines: list[str] = []
    buf = ""
    used = 0
    for ch in text:
        cl = 2 if ord(ch) > 0xFF else 1
        if used + cl > width and buf:
            lines.append(buf)
            buf = ch
            used = cl
        else:
            buf += ch
            used += cl
    if buf:
        lines.append(buf)
    return lines or [""]


def detect_git_branch(cwd: str) -> str:
    """Best-effort branch detection; empty on failure. Safe for pure display."""
    import subprocess
    from pathlib import Path

    try:
        root = Path(cwd) if cwd else Path.cwd()
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return ""
