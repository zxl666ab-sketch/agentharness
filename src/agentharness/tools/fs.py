"""Filesystem tools — sandboxed read/write/search."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from agentharness.contracts import EffectKind, ToolContext, ToolResult, ToolSpec
from agentharness.security.sandbox import SandboxError, assert_in_workspace


class ReadFileTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_file",
            description="Read a text file within the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute path"},
                    "offset": {"type": "integer", "description": "Start line (0-based)"},
                    "limit": {"type": "integer", "description": "Max lines"},
                },
                "required": ["path"],
            },
            effect=EffectKind.workspace_read,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path") or ""
        try:
            target = assert_in_workspace(
                path, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs, must_exist=True
            )
            if not target.is_file():
                return ToolResult(
                    tool_call_id="", name="read_file", content=f"Not a file: {path}", is_error=True
                )
            offset = max(0, int(arguments.get("offset") or 0))
            limit = arguments.get("limit")
            line_limit = max(0, int(limit)) if limit is not None else None
            parts: list[str] = []
            truncated = False
            chunks_seen = 0
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                line_number = 0
                while line_number < offset:
                    segment = handle.readline(64 * 1024)
                    if not segment:
                        break
                    if segment.endswith(("\n", "\r")):
                        line_number += 1
                    chunks_seen += 1
                    if chunks_seen % 128 == 0:
                        if ctx.cancel_event and ctx.cancel_event.is_set():
                            return ToolResult(
                                tool_call_id="",
                                name="read_file",
                                content="cancelled",
                                is_error=True,
                            )
                        await asyncio.sleep(0)

                lines_read = 0
                body_length = 0
                while line_number >= offset and (
                    line_limit is None or lines_read < line_limit
                ):
                    remaining = 100_001 - body_length
                    if remaining <= 0:
                        truncated = True
                        break
                    segment = handle.readline(min(64 * 1024, remaining))
                    if not segment:
                        break
                    parts.append(segment)
                    body_length += len(segment)
                    if segment.endswith(("\n", "\r")):
                        lines_read += 1
                    if body_length > 100_000:
                        truncated = True
                        break
                    chunks_seen += 1
                    if chunks_seen % 128 == 0:
                        if ctx.cancel_event and ctx.cancel_event.is_set():
                            return ToolResult(
                                tool_call_id="",
                                name="read_file",
                                content="cancelled",
                                is_error=True,
                            )
                        await asyncio.sleep(0)

            body = "\n".join("".join(parts)[:100_000].splitlines())
            if truncated:
                body += "\n...[truncated]"
            return ToolResult(tool_call_id="", name="read_file", content=body)
        except (SandboxError, OSError, FileNotFoundError) as exc:
            return ToolResult(
                tool_call_id="", name="read_file", content=str(exc), is_error=True
            )


class WriteFileTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="write_file",
            description="Write text to a file within the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            effect=EffectKind.workspace_write,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        if not ctx.allow_write:
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content="Write not allowed",
                is_error=True,
            )
        path = arguments.get("path") or ""
        content = arguments.get("content") or ""
        try:
            target = assert_in_workspace(path, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content=f"Wrote {len(content)} bytes to {target}",
            )
        except (SandboxError, OSError) as exc:
            return ToolResult(
                tool_call_id="", name="write_file", content=str(exc), is_error=True
            )


class SearchFilesTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_files",
            description="Search file contents in the workspace by literal substring.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "glob": {"type": "string", "description": "Optional glob like *.py"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            effect=EffectKind.workspace_read,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query") or ""
        glob_pat = arguments.get("glob") or "*"
        max_results = max(1, min(int(arguments.get("max_results") or 20), 1000))
        root = Path(ctx.cwd).resolve()
        hits: list[str] = []
        if not query:
            return ToolResult(
                tool_call_id="",
                name="search_files",
                content="query must not be empty",
                is_error=True,
            )
        if len(query) > 1000:
            return ToolResult(
                tool_call_id="",
                name="search_files",
                content="query exceeds 1000 characters",
                is_error=True,
            )

        chunks_seen = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # skip common noise
            dirnames[:] = [
                d
                for d in dirnames
                if d not in {".git", "node_modules", ".venv", "__pycache__", ".agentharness"}
            ]
            for fn in filenames:
                if glob_pat != "*" and not Path(fn).match(glob_pat):
                    continue
                fp = Path(dirpath) / fn
                try:
                    assert_in_workspace(fp, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs)
                    with fp.open("r", encoding="utf-8", errors="replace") as handle:
                        line_number = 1
                        overlap = ""
                        while True:
                            if ctx.cancel_event and ctx.cancel_event.is_set():
                                return ToolResult(
                                    tool_call_id="",
                                    name="search_files",
                                    content="cancelled",
                                    is_error=True,
                                )
                            segment = handle.readline(64 * 1024)
                            if not segment:
                                break
                            searchable = overlap + segment
                            if query in searchable:
                                rel = fp.relative_to(root)
                                preview = searchable.rstrip("\r\n")[:200]
                                hits.append(f"{rel}:{line_number}:{preview}")
                                if len(hits) >= max_results:
                                    return ToolResult(
                                        tool_call_id="",
                                        name="search_files",
                                        content="\n".join(hits),
                                    )
                            if segment.endswith(("\n", "\r")):
                                line_number += 1
                                overlap = ""
                            else:
                                overlap = searchable[-(len(query) - 1) :] if len(query) > 1 else ""
                            chunks_seen += 1
                            if chunks_seen % 128 == 0:
                                await asyncio.sleep(0)
                except (SandboxError, OSError, UnicodeError):
                    continue
        return ToolResult(
            tool_call_id="",
            name="search_files",
            content="\n".join(hits) if hits else "No matches",
        )
