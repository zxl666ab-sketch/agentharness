"""Workspace path sandbox — normalize, resolve, block traversal/symlink bypass."""

from __future__ import annotations

import os
from pathlib import Path


class SandboxError(PermissionError):
    """Raised when a path escapes the authorized workspace."""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_path(path: str | Path, *, cwd: str | Path) -> Path:
    """Resolve path relative to cwd without following final symlink yet."""
    base = Path(cwd).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    # Resolve with strict=False so missing files still normalize; then re-check.
    try:
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError):
        # Fallback: manual normpath
        resolved = Path(os.path.normpath(str(p)))
    return resolved


def assert_in_workspace(
    path: str | Path,
    *,
    cwd: str | Path,
    extra_dirs: list[str] | Path | None = None,
    must_exist: bool = False,
) -> Path:
    """Ensure path is under cwd or an explicitly authorized extra directory.

    Blocks path traversal, junction/symlink escapes by resolving real paths
    of both the target and all authorized roots.
    """
    roots: list[Path] = [Path(cwd).resolve()]
    extras = extra_dirs or []
    if isinstance(extras, (str, Path)):
        extras = [str(extras)]
    for d in extras:
        roots.append(Path(d).resolve())

    candidate = normalize_path(path, cwd=cwd)

    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))

    # If the path exists, resolve fully (follows symlinks/junctions).
    check = candidate
    if candidate.exists():
        try:
            check = candidate.resolve(strict=True)
        except OSError as exc:
            raise SandboxError(f"Cannot resolve path: {path}") from exc
    else:
        # Check parent chain — ensure parent is inside workspace
        parent = candidate.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if parent.exists():
            try:
                parent_real = parent.resolve(strict=True)
            except OSError as exc:
                raise SandboxError(f"Cannot resolve parent: {parent}") from exc
            if not any(_is_within(parent_real, r) for r in roots):
                raise SandboxError(f"Path escapes workspace: {path}")
            return candidate

    if not any(_is_within(check, r) for r in roots):
        raise SandboxError(f"Path escapes workspace: {path}")
    return candidate


def safe_join(cwd: str | Path, *parts: str, extra_dirs: list[str] | None = None) -> Path:
    joined = Path(cwd)
    for part in parts:
        joined = joined / part
    return assert_in_workspace(joined, cwd=cwd, extra_dirs=extra_dirs)
