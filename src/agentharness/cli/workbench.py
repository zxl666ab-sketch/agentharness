"""Full-screen TTY workbench: fixed header, scrollable run area, composer, shortcuts.

Uses prompt-toolkit Application/Layout for input; coalesced ANSI frames during runs.
Non-TTY callers must not use this module — keep redirected_input + Rich instead.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML, StyleAndTextTuples
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Dimension, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style

from agentharness.cli.input import SLASH_COMMANDS, SlashCommandCompleter, _composer_key_bindings
from agentharness.cli.view_model import CliViewModel, detect_git_branch

# ANSI helpers for live paint during runs (main buffer, not alt-screen).
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_HOME_CLEAR = "\033[H\033[J"
_ALT_ENTER = "\033[?1049h"
_ALT_LEAVE = "\033[?1049l"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("AGENTHARNESS_FORCE_COLOR") == "1":
        return True
    return sys.stdout.isatty()


def _style_for_terminal() -> Style:
    if not _color_enabled():
        return Style.from_dict(
            {
                "header": "",
                "phase": "",
                "body": "",
                "border": "",
                "composer": "",
                "meta": "",
                "shortcut": "",
                "frame": "",
            }
        )
    return Style.from_dict(
        {
            "header": "bold",
            "phase": "italic #888888",
            "body": "",
            "user": "bold #5dade2",
            "agent": "",
            "tool": "#5dade2",
            "status": "#888888",
            "error": "bold #e74c3c",
            "border": "#555555",
            "composer": "",
            "meta": "#888888",
            "shortcut": "#666666",
            "frame": "#666666",
            "prompt": "bold #5dade2",
        }
    )


class Workbench:
    """TTY full-screen workbench bound to a shared :class:`CliViewModel`."""

    def __init__(
        self,
        *,
        view_model: CliViewModel | None = None,
        commands: Sequence[str] = SLASH_COMMANDS,
        history_path: str | Path | None = None,
        output: TextIO | None = None,
    ) -> None:
        self.vm = view_model or CliViewModel()
        self.commands = list(commands)
        self._out = output or sys.stdout
        self._history: History
        if history_path is not None:
            try:
                Path(history_path).parent.mkdir(parents=True, exist_ok=True)
                self._history = FileHistory(str(history_path))
            except OSError:
                self._history = InMemoryHistory()
        else:
            self._history = InMemoryHistory()
        self._lock = threading.Lock()
        self._needs_redraw = False
        self._live_active = False
        self._alt_screen = False
        self._last_size = (0, 0)

    # ------------------------------------------------------------------
    # Public API used by interactive.py
    # ------------------------------------------------------------------

    def configure(self, **kwargs: Any) -> None:
        if "branch" not in kwargs or not kwargs.get("branch"):
            cwd = kwargs.get("cwd") or self.vm.header.cwd
            kwargs = {**kwargs, "branch": detect_git_branch(str(cwd or ""))}
        self.vm.configure(**kwargs)

    def read(self) -> str:
        """Block for a multi-line composer submission with full chrome."""
        self.vm.set_idle()
        self._leave_alt_if_needed()
        return self._prompt_line()

    def choose(
        self, title: str, choices: Sequence[str], current: str | None = None
    ) -> str | None:
        if not choices:
            return None
        bindings = KeyBindings()

        @bindings.add("enter")
        def accept_choice(event: Any) -> None:
            buffer = event.current_buffer
            if buffer.complete_state and buffer.complete_state.current_completion:
                buffer.apply_completion(buffer.complete_state.current_completion)
            buffer.validate_and_handle()

        from prompt_toolkit import PromptSession

        session: PromptSession[str] = PromptSession(
            completer=WordCompleter(list(choices), sentence=True),
            complete_while_typing=True,
            key_bindings=bindings,
            reserve_space_for_menu=min(10, len(choices)),
        )

        def open_menu() -> None:
            get_app().current_buffer.start_completion(select_first=True)

        value = session.prompt(
            HTML(f"<ansicyan><b>{title}&gt;</b></ansicyan> "),
            pre_run=open_menu,
            bottom_toolbar=f" current: {current or '(provider default)'} ",
        ).strip()
        return value or None

    def append_system(self, text: str) -> None:
        with self._lock:
            self.vm.add_system(text)
            self._needs_redraw = True
        if self._live_active:
            self.paint_live(force=True)

    def on_event(self, event: Any) -> None:
        with self._lock:
            should = self.vm.apply_event(event)
            if should:
                self._needs_redraw = True
        if self._live_active and self._needs_redraw:
            self.paint_live()

    def begin_user_turn(self, message: str) -> None:
        with self._lock:
            self.vm.begin_user_turn(message)
            self._needs_redraw = True
        self._enter_live()
        self.paint_live(force=True)

    def finish_turn(self, **kwargs: Any) -> None:
        """kwargs match :meth:`CliViewModel.finish_turn` (incl. usage_detail)."""
        with self._lock:
            self.vm.finish_turn(**kwargs)
            self._needs_redraw = True
        self.paint_live(force=True)
        self._leave_live()

    def mark_interrupted(self, run_id: str | None = None) -> None:
        with self._lock:
            self.vm.mark_interrupted(run_id)
            self._needs_redraw = True
        self.paint_live(force=True)
        self._leave_live()

    def mark_error(self, message: str) -> None:
        with self._lock:
            self.vm.mark_error(message)
            self._needs_redraw = True
        self.paint_live(force=True)
        self._leave_live()

    def paint_live(self, *, force: bool = False) -> None:
        """Coalesced full-frame paint for the active turn."""
        with self._lock:
            if not force and not self._needs_redraw:
                return
            cols, rows = shutil.get_terminal_size(fallback=(100, 28))
            self._last_size = (cols, rows)
            lines = self.vm.render_frame(cols, rows)
            self._needs_redraw = False
        self._write_frame(lines)

    def flush_stream(self) -> None:
        with self._lock:
            self.vm.coalesce.flush()
            self._needs_redraw = True
        self.paint_live(force=True)

    # ------------------------------------------------------------------
    # Live / alt-screen
    # ------------------------------------------------------------------

    def _enter_live(self) -> None:
        self._live_active = True
        if self._out.isatty() and not self._alt_screen:
            try:
                self._out.write(_ALT_ENTER + _HIDE_CURSOR)
                self._out.flush()
                self._alt_screen = True
            except OSError:
                self._alt_screen = False

    def _leave_live(self) -> None:
        self._live_active = False
        # Keep final frame visible on main buffer: leave alt with content copy.
        self._leave_alt_if_needed(restore_frame=True)

    def _leave_alt_if_needed(self, *, restore_frame: bool = False) -> None:
        if not self._alt_screen:
            return
        try:
            if restore_frame:
                cols, rows = shutil.get_terminal_size(fallback=self._last_size or (100, 28))
                lines = self.vm.render_frame(cols, rows)
                self._out.write(_ALT_LEAVE + _SHOW_CURSOR + _HOME_CLEAR)
                self._out.write("\n".join(lines) + "\n")
            else:
                self._out.write(_ALT_LEAVE + _SHOW_CURSOR)
            self._out.flush()
        except OSError:
            pass
        self._alt_screen = False

    def _write_frame(self, lines: list[str]) -> None:
        try:
            self._out.write(_HOME_CLEAR + _HIDE_CURSOR)
            self._out.write("\n".join(lines))
            if not lines or not lines[-1].endswith("\n"):
                pass
            self._out.flush()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # prompt-toolkit Application for idle composer
    # ------------------------------------------------------------------

    def _prompt_line(self) -> str:
        result: dict[str, str] = {}
        buffer = Buffer(
            completer=SlashCommandCompleter(self.commands),
            complete_while_typing=True,
            history=self._history,
            multiline=True,
            enable_history_search=False,
            accept_handler=lambda buff: self._accept(buff, result),
        )
        kb = _composer_key_bindings()

        @kb.add("c-c")
        def _interrupt(event: Any) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        @kb.add("c-d")
        def _eof(event: Any) -> None:
            if not event.current_buffer.text:
                event.app.exit(exception=EOFError())

        def get_line_prefix(line_number: int, wrap_count: int) -> StyleAndTextTuples:
            if line_number == 0 and wrap_count == 0:
                return [("class:prompt", "> ")]
            return [("class:prompt", "  ")]

        layout = Layout(
            HSplit(
                [
                    Window(
                        FormattedTextControl(self._header_fragments),
                        height=1,
                        style="class:header",
                    ),
                    Window(
                        FormattedTextControl(self._phase_fragments),
                        height=1,
                        style="class:phase",
                    ),
                    Window(
                        FormattedTextControl(self._body_fragments),
                        wrap_lines=False,
                        style="class:body",
                    ),
                    Window(height=1, char="─", style="class:border"),
                    Window(
                        FormattedTextControl(lambda: [("class:frame", self._composer_top())]),
                        height=1,
                    ),
                    Window(
                        BufferControl(buffer=buffer, focusable=True),
                        height=Dimension(min=1, max=6, preferred=2),
                        wrap_lines=True,
                        dont_extend_height=True,
                        style="class:composer",
                        get_line_prefix=get_line_prefix,
                    ),
                    Window(
                        FormattedTextControl(lambda: [("class:frame", self._composer_bottom())]),
                        height=1,
                    ),
                    Window(
                        FormattedTextControl(self._meta_fragments),
                        height=1,
                        style="class:meta",
                    ),
                    Window(
                        FormattedTextControl(self._shortcut_fragments),
                        height=1,
                        style="class:shortcut",
                    ),
                ]
            )
        )
        app: Application[None] = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
            style=_style_for_terminal(),
            mouse_support=False,
            erase_when_done=True,
        )
        try:
            app.run()
        except KeyboardInterrupt:
            raise
        except EOFError:
            raise
        if "text" not in result:
            raise EOFError
        return result["text"]

    @staticmethod
    def _accept(buff: Buffer, result: dict[str, str]) -> bool:
        result["text"] = buff.text
        if buff.text.strip():
            buff.append_to_history()
        buff.reset()
        get_app().exit()
        return True

    def _term_width(self) -> int:
        return shutil.get_terminal_size(fallback=(100, 28)).columns

    def _header_fragments(self) -> StyleAndTextTuples:
        line = self.vm.format_header_line(self._term_width())
        return [("class:header", line)]

    def _phase_fragments(self) -> StyleAndTextTuples:
        line = self.vm.format_phase_line(self._term_width())
        return [("class:phase", line)]

    def _body_fragments(self) -> StyleAndTextTuples:
        width = self._term_width()
        lines = self.vm.iter_body_lines(width)
        # Show a trailing idle hint when empty.
        if not lines:
            lines = [
                "",
                "  Type a task and press Enter.  /help for commands.",
            ]
        # Color lightly by prefix.
        parts: StyleAndTextTuples = []
        for i, line in enumerate(lines):
            style = "class:body"
            if line.startswith("you"):
                style = "class:user"
            elif line.startswith("agent"):
                style = "class:agent"
            elif line.startswith("•"):
                style = "class:tool"
            elif line.startswith("error"):
                style = "class:error"
            elif line.startswith("status=") or (
                line and not line[0].isalnum() and "status=" in line
            ):
                style = "class:status"
            elif line.startswith("status=") or "run=" in line and "session=" in line:
                style = "class:status"
            parts.append((style, line))
            if i < len(lines) - 1:
                parts.append(("", "\n"))
        return parts

    def _meta_fragments(self) -> StyleAndTextTuples:
        width = self._term_width()
        from agentharness.cli.view_model import _fit_left_right

        line = _fit_left_right("", self.vm.format_composer_meta(), width)
        return [("class:meta", line)]

    def _shortcut_fragments(self) -> StyleAndTextTuples:
        line = self.vm.format_shortcut_line()
        width = self._term_width()
        from agentharness.cli.view_model import truncate_display

        return [("class:shortcut", truncate_display(line, width))]

    def _composer_top(self) -> str:
        w = max(self._term_width(), 10)
        inner = max(w - 2, 1)
        return "┌" + "─" * inner + "┐"

    def _composer_bottom(self) -> str:
        w = max(self._term_width(), 10)
        inner = max(w - 2, 1)
        return "└" + "─" * inner + "┘"


class LiveRedrawController:
    """Background ticker that flushes coalesced stream paints during a turn."""

    def __init__(self, workbench: Workbench, interval_s: float = 0.05) -> None:
        self.workbench = workbench
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> LiveRedrawController:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="agentharness-live-ui", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.workbench.flush_stream()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            wb = self.workbench
            with wb._lock:
                pending = wb.vm.coalesce.pending or wb._needs_redraw
            if pending:
                wb.paint_live(force=True)
