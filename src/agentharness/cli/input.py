"""Shared redirected-stdin reader and TTY slash-command completion."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from collections.abc import Sequence
from typing import Any

from rich.console import Console

# Slash commands offered for Tab completion in interactive mode.
SLASH_COMMANDS: tuple[str, ...] = (
    "/help",
    "/new",
    "/sessions",
    "/use",
    "/model",
    "/provider",
    "/config",
    "/quit",
    "/exit",
)


class _RedirectedLineReader:
    def __init__(self, source: Any) -> None:
        self._source = source
        self._encoding = getattr(source, "encoding", None) or "utf-8"
        self._lines: queue.Queue[str | BaseException] = queue.Queue()
        self._thread = threading.Thread(
            target=self._read,
            name="agentharness-stdin",
            daemon=True,
        )
        self._thread.start()

    def _read(self) -> None:
        try:
            fd = self._source.fileno()
        except (AttributeError, OSError, ValueError):
            self._read_buffered_fallback()
            return

        buffered = bytearray()
        try:
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    if buffered:
                        self._lines.put(
                            bytes(buffered).rstrip(b"\r").decode(
                                self._encoding, errors="replace"
                            )
                        )
                    self._lines.put(EOFError())
                    return
                buffered.extend(chunk)
                while b"\n" in buffered:
                    raw, _, remainder = buffered.partition(b"\n")
                    buffered = bytearray(remainder)
                    self._lines.put(
                        raw.rstrip(b"\r").decode(self._encoding, errors="replace")
                    )
        except BaseException as exc:
            self._lines.put(exc)

    def _read_buffered_fallback(self) -> None:
        try:
            while True:
                value = self._source.readline()
                if value == "":
                    self._lines.put(EOFError())
                    return
                self._lines.put(str(value).rstrip("\r\n"))
        except BaseException as exc:
            self._lines.put(exc)

    def get(self) -> str:
        value = self._lines.get()
        if isinstance(value, BaseException):
            raise value
        return value

    def get_nowait(self) -> str:
        value = self._lines.get_nowait()
        if isinstance(value, BaseException):
            raise value
        return value


_readers: dict[int, _RedirectedLineReader] = {}
_readers_lock = threading.Lock()


def _reader() -> _RedirectedLineReader:
    source = sys.stdin
    key = id(source)
    with _readers_lock:
        reader = _readers.get(key)
        if reader is None:
            reader = _RedirectedLineReader(source)
            _readers[key] = reader
        return reader


def redirected_input(console: Console, prompt: str) -> str:
    console.print(prompt, end="", markup=False, highlight=False)
    return _reader().get()


async def async_redirected_input(console: Console, prompt: str) -> str:
    console.print(prompt, end="", markup=False, highlight=False)
    reader = _reader()
    while True:
        try:
            return reader.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.03)


def match_slash_commands(prefix: str, commands: Sequence[str] = SLASH_COMMANDS) -> list[str]:
    """Return slash commands that start with ``prefix`` (case-sensitive)."""
    if not prefix.startswith("/"):
        return []
    return [cmd for cmd in commands if cmd.startswith(prefix)]


def complete_slash_line(line: str, commands: Sequence[str] = SLASH_COMMANDS) -> tuple[str, list[str]]:
    """Complete a partial slash command line.

    Returns ``(new_line, candidates)``. When exactly one candidate matches,
    ``new_line`` is that command (with a trailing space for multi-arg cmds).
    """
    stripped = line.lstrip()
    if not stripped.startswith("/"):
        return line, []
    # Only complete the command token (before first space).
    if " " in stripped:
        return line, []
    matches = match_slash_commands(stripped, commands)
    if not matches:
        return line, []
    if len(matches) == 1:
        cmd = matches[0]
        # Commands that usually take an argument get a trailing space.
        if cmd in {"/use", "/model", "/provider", "/config"}:
            return cmd + " ", matches
        return cmd, matches
    # Expand shared prefix among matches.
    shared = os.path.commonprefix(list(matches))
    if shared and shared != stripped:
        return shared, matches
    return line, matches


def readline_with_completion(
    console: Console,
    prompt: str,
    *,
    commands: Sequence[str] = SLASH_COMMANDS,
) -> str:
    """Read a line; on TTY, support Tab completion for slash commands.

    Non-TTY / redirected stdin keeps the existing line-reader behaviour so
    subprocess tests continue to work.
    """
    if not sys.stdin.isatty():
        return redirected_input(console, prompt)

    if sys.platform == "win32":
        return _win_readline_with_completion(console, prompt, commands=commands)

    # POSIX: prefer readline if available.
    try:
        import readline
    except ImportError:
        console.print(prompt, end="", markup=False, highlight=False)
        return sys.stdin.readline().rstrip("\r\n")

    def completer(text: str, state: int) -> str | None:
        buf = readline.get_line_buffer()
        if not buf.lstrip().startswith("/"):
            return None
        matches = match_slash_commands(buf.lstrip() if " " not in buf.lstrip() else text, commands)
        # Prefer completing from the start of the token.
        token = buf.lstrip().split(" ", 1)[0] if buf.lstrip().startswith("/") else text
        matches = match_slash_commands(token, commands)
        if state < len(matches):
            return matches[state]
        return None

    prev = readline.get_completer()
    readline.set_completer(completer)
    try:
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass
    try:
        # rich Console.input uses prompt_toolkit-less input
        return console.input(prompt)
    finally:
        readline.set_completer(prev)


def _win_readline_with_completion(
    console: Console,
    prompt: str,
    *,
    commands: Sequence[str],
) -> str:
    import msvcrt

    # Render prompt via Rich then read raw keys for Tab handling.
    console.print(prompt, end="", markup=True, highlight=False)
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in {"\x00", "\xe0"}:
            # Arrow / function keys — consume the follow-up scan code.
            if msvcrt.kbhit() or True:
                msvcrt.getwch()
            continue
        if ch in {"\r", "\n"}:
            console.print()
            return "".join(chars)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1a":  # Ctrl+Z → EOF on Windows consoles
            raise EOFError
        if ch == "\b":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch == "\t":
            line = "".join(chars)
            new_line, matches = complete_slash_line(line, commands)
            if not matches:
                continue
            if len(matches) == 1 or new_line != line:
                # Replace visible input with completed text.
                for _ in chars:
                    sys.stdout.write("\b \b")
                chars = list(new_line)
                sys.stdout.write(new_line)
                sys.stdout.flush()
            else:
                # Show candidates on the next line, re-draw prompt + buffer.
                sys.stdout.write("\n")
                sys.stdout.write("  " + "  ".join(matches) + "\n")
                sys.stdout.flush()
                # Re-print prompt without Rich markup for simplicity.
                plain = prompt
                # strip simple rich tags roughly
                import re

                plain = re.sub(r"\[/?[^\]]+\]", "", prompt)
                sys.stdout.write(plain + "".join(chars))
                sys.stdout.flush()
            continue
        chars.append(ch)
        sys.stdout.write(ch)
        sys.stdout.flush()
