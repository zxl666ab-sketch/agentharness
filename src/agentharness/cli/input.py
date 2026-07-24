"""Redirected input and the prompt-toolkit TTY composer."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from collections.abc import Iterable, Sequence
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

SLASH_COMMANDS: tuple[str, ...] = (
    "/help",
    "/new",
    "/sessions",
    "/use",
    "/model",
    "/provider",
    "/profile",
    "/config",
    "/quit",
    "/exit",
)

COMMAND_DESCRIPTIONS: dict[str, str] = {
    "/help": "Show interactive commands",
    "/new": "Start a new session",
    "/sessions": "List recent sessions",
    "/use": "Continue a session",
    "/model": "Choose or set the next model",
    "/provider": "Choose the next provider",
    "/profile": "Show, create, or activate a profile",
    "/config": "Show or update masked configuration",
    "/quit": "Exit the CLI",
    "/exit": "Exit the CLI",
}


class SlashCommandCompleter(Completer):
    """Offer commands immediately after `/` without requiring Tab."""

    def __init__(self, commands: Sequence[str] = SLASH_COMMANDS) -> None:
        self.commands = commands

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        before = document.text_before_cursor.lstrip()
        if not before.startswith("/") or " " in before:
            return
        for command in match_slash_commands(before, self.commands):
            yield Completion(
                command,
                start_position=-len(before),
                display=command,
                display_meta=COMMAND_DESCRIPTIONS.get(command, ""),
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


async def async_tty_input(prompt: str) -> str:
    """Cancellation-aware prompt used while a run waits for approval."""
    session: PromptSession[str] = PromptSession()
    with patch_stdout(raw=True):
        return await session.prompt_async(prompt)


def match_slash_commands(
    prefix: str, commands: Sequence[str] = SLASH_COMMANDS
) -> list[str]:
    if not prefix.startswith("/"):
        return []
    return [command for command in commands if command.startswith(prefix)]


def complete_slash_line(
    line: str, commands: Sequence[str] = SLASH_COMMANDS
) -> tuple[str, list[str]]:
    stripped = line.lstrip()
    if not stripped.startswith("/") or " " in stripped:
        return line, []
    matches = match_slash_commands(stripped, commands)
    if not matches:
        return line, []
    if len(matches) == 1:
        command = matches[0]
        if command in {"/use", "/model", "/provider", "/profile", "/config"}:
            return command + " ", matches
        return command, matches
    shared = os.path.commonprefix(matches)
    return (shared if shared and shared != stripped else line), matches


def _composer_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit_or_select(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.complete_state and buffer.complete_state.current_completion:
            buffer.apply_completion(buffer.complete_state.current_completion)
            return
        buffer.validate_and_handle()

    @bindings.add("escape", filter=has_completions)
    def close_menu(event: Any) -> None:
        event.current_buffer.cancel_completion()

    @bindings.add("escape", "enter")
    def insert_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


