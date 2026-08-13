"""Verify deterministic Web builds and the checked-in Java static bundle."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = WEB / "dist"
JAVA_STATIC = ROOT / "procurement-service" / "src" / "main" / "resources" / "static"


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)


def main() -> int:
    _build()
    first = _snapshot(DIST)
    _build()
    second = _snapshot(DIST)
    if first != second:
        changed = sorted(
            path for path in set(first) | set(second) if first.get(path) != second.get(path)
        )
        print("web build drift detected:")
        for path in changed:
            print(f"  {path}: {first.get(path, 'missing')} -> {second.get(path, 'missing')}")
        return 1

    print(f"deterministic web build: {len(second)} files are byte-identical")
    embedded = _snapshot(JAVA_STATIC)
    if second != embedded:
        changed = sorted(
            path
            for path in set(second) | set(embedded)
            if second.get(path) != embedded.get(path)
        )
        print("Java static bundle differs from web/dist:")
        for path in changed:
            print(
                f"  {path}: dist={second.get(path, 'missing')} "
                f"java={embedded.get(path, 'missing')}"
            )
        return 1

    print(f"embedded Java static bundle: {len(embedded)} files match web/dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
