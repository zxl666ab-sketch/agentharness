"""Build the Web bundle twice and fail if any emitted byte changes."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "src" / "agentharness" / "web_dist"


def _snapshot() -> dict[str, str]:
    return {
        path.relative_to(DIST).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DIST.rglob("*"))
        if path.is_file()
    }


def _build() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)


def main() -> int:
    _build()
    first = _snapshot()
    _build()
    second = _snapshot()
    if first == second:
        print(f"deterministic web build: {len(second)} files are byte-identical")
        return 0
    changed = sorted(
        path for path in set(first) | set(second) if first.get(path) != second.get(path)
    )
    print("web build drift detected:")
    for path in changed:
        print(f"  {path}: {first.get(path, 'missing')} -> {second.get(path, 'missing')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
