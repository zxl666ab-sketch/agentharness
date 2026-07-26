"""Filesystem tools — sandboxed read/write/search."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

from agentharness.contracts import EffectKind, ReplayPolicy, ToolContext, ToolResult, ToolSpec
from agentharness.security.sandbox import SandboxError, assert_in_workspace


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_version(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    for prefix in ("sha256:", "sha256="):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


class ReadFileTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_file",
            description="Read a text file within the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 32_768,
                        "description": "Relative or absolute path",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 10_000_000,
                        "description": "Start line (0-based)",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100_000,
                        "description": "Max lines",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_read,
            replay_policy=ReplayPolicy.safe,
            parallel_safe=True,
            max_attempts=2,
        )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path") or ""
        try:
            target = assert_in_workspace(
                path, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs, must_exist=True
            )
            if not target.is_file():
                return ToolResult(
                    tool_call_id="",
                    name="read_file",
                    content=f"Not a file: {path}",
                    is_error=True,
                    error_code="not_a_file",
                    error_category="filesystem",
                    retryable=False,
                    recovery_hint="Choose an existing text file inside the workspace.",
                )
            version = _sha256_file(target)
            offset = max(0, int(arguments.get("offset") or 0))
            limit = arguments.get("limit")
            line_limit = max(0, int(limit)) if limit is not None else None
            parts: list[str] = []
            truncated = False
            chunks_seen = 0
            with target.open(
                "r", encoding="utf-8", errors="replace", newline=""
            ) as handle:
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

            # Preserve original bytes/newlines verbatim (no splitlines normalization,
            # which would collapse \r\n / \r and Unicode line separators).
            body = "".join(parts)[:100_000]
            if truncated:
                body += "\n...[truncated]"
            separator = "" if not body or body.endswith("\n") else "\n"
            return ToolResult(
                tool_call_id="",
                name="read_file",
                content=body + separator + f"[agentharness:file_version sha256={version}]",
            )
        except (SandboxError, OSError, FileNotFoundError) as exc:
            return ToolResult(
                tool_call_id="",
                name="read_file",
                content=str(exc),
                is_error=True,
                error_code=("workspace_violation" if isinstance(exc, SandboxError) else "read_failed"),
                error_category="sandbox" if isinstance(exc, SandboxError) else "filesystem",
                retryable=isinstance(exc, FileNotFoundError),
                recovery_hint="Re-read the path after checking that it exists inside the workspace.",
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
                    "path": {"type": "string", "minLength": 1, "maxLength": 32_768},
                    "content": {"type": "string", "maxLength": 262_144},
                    "expected_version": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "description": (
                            "SHA-256 returned by read_file. Existing files are written only "
                            "when this matches; omitted remains legacy-compatible."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_write,
            replay_policy=ReplayPolicy.reconcile,
        )

    def reconcile(
        self, ctx: ToolContext, arguments: dict[str, Any]
    ) -> ToolResult | None:
        path = arguments.get("path") or ""
        try:
            target = assert_in_workspace(
                path, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs, must_exist=False
            )
            if not target.exists():
                return None
            expected = str(arguments.get("content") or "")
            expected_bytes = expected.encode("utf-8")
            with target.open("rb") as handle:
                actual = handle.read(len(expected_bytes) + 1)
            if actual == expected_bytes:
                version = hashlib.sha256(expected_bytes).hexdigest()
                return ToolResult(
                    tool_call_id="",
                    name="write_file",
                    content=(
                        f"Reconciled completed write: {target}\n"
                        f"file_version sha256={version}"
                    ),
                )
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content="File state differs from the interrupted write",
                is_error=True,
                error_code="outcome_indeterminate",
                error_category="recovery",
                retryable=False,
                recovery_hint="Inspect the file before deciding whether to overwrite it.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content=f"Could not reconcile file write: {exc}",
                is_error=True,
                error_code="outcome_indeterminate",
                error_category="recovery",
                retryable=False,
            )

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        if not ctx.allow_write:
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content="Write not allowed",
                is_error=True,
                error_code="write_not_allowed",
                error_category="permission",
                retryable=False,
                recovery_hint="Use a writable run or ask a human for write permission.",
            )
        path = arguments.get("path") or ""
        content = arguments.get("content") or ""
        expected_version = _normalize_version(arguments.get("expected_version"))
        try:
            target = assert_in_workspace(path, cwd=ctx.cwd, extra_dirs=ctx.extra_dirs)
            if target.exists() and not target.is_file():
                return ToolResult(
                    tool_call_id="",
                    name="write_file",
                    content=f"Not a file: {path}",
                    is_error=True,
                    error_code="not_a_file",
                    error_category="filesystem",
                    retryable=False,
                    recovery_hint="Choose a regular file path.",
                )
            if target.exists() and expected_version is not None:
                current_version = _sha256_file(target)
                if current_version != expected_version:
                    return ToolResult(
                        tool_call_id="",
                        name="write_file",
                        content=(
                            "File version conflict: the file changed after it was read. "
                            f"expected sha256={expected_version}, current sha256={current_version}. "
                            "Re-read the file before writing."
                        ),
                        is_error=True,
                        error_code="file_version_conflict",
                        error_category="concurrency",
                        retryable=True,
                        recovery_hint="Call read_file again and retry with its latest SHA-256.",
                    )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written_version = _sha256_file(target)
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content=(
                    f"Wrote {len(content)} bytes to {target}\n"
                    f"[agentharness:file_version sha256={written_version}]"
                ),
            )
        except (SandboxError, OSError) as exc:
            return ToolResult(
                tool_call_id="",
                name="write_file",
                content=str(exc),
                is_error=True,
                error_code=("workspace_violation" if isinstance(exc, SandboxError) else "write_failed"),
                error_category="sandbox" if isinstance(exc, SandboxError) else "filesystem",
                retryable=not isinstance(exc, SandboxError),
                recovery_hint="Re-read the target and retry only inside the writable workspace.",
            )


def _search_roots(cwd: Path, extra_dirs: list[str] | None) -> list[Path]:
    """cwd plus any granted extra dirs, de-duplicated and nested paths dropped."""
    roots: list[Path] = [cwd]
    for entry in extra_dirs or []:
        try:
            candidate = Path(entry).expanduser().resolve()
        except OSError:
            continue
        if not candidate.is_dir():
            continue
        if any(candidate == root or root in candidate.parents for root in roots):
            continue
        roots.append(candidate)
    return roots


def _display_path(path: Path, cwd: Path) -> str:
    """Relative to cwd when inside it, else absolute so extra-dir hits are unambiguous."""
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


class SearchFilesTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_files",
            description="Search file contents in the workspace by literal substring.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 16_384},
                    "glob": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4_096,
                        "description": "Optional glob like *.py",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 1_000},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            effect=EffectKind.workspace_read,
            replay_policy=ReplayPolicy.safe,
            parallel_safe=True,
            max_attempts=2,
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
        # Search every readable root, not just cwd: read_file/write_file honour
        # extra_dirs, so a search that silently skipped them made granted
        # directories look empty.
        for search_root in _search_roots(root, ctx.extra_dirs):
            for dirpath, dirnames, filenames in os.walk(search_root):
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
                                    rel = _display_path(fp, root)
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
                                    overlap = (
                                        searchable[-(len(query) - 1) :] if len(query) > 1 else ""
                                    )
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
