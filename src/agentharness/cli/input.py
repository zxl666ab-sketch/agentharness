"""Shared redirected-stdin reader used by the prompt loop and async approvals."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
from typing import Any

from rich.console import Console


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
