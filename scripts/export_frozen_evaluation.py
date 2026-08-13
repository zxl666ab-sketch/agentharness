"""Export the frozen procurement evaluation into the Java classpath bundle.

The Java control plane serves the frozen evaluation panel itself in kafka/demo
mode (Python Agent HTTP is intentionally absent there).  This script keeps the
bundled JSON in sync with `agentharness.procurement.evaluation.evaluate_frozen_cases`.

Only `processing.*` timing is wall-clock and will differ between runs; the
companion test (tests/test_frozen_evaluation_bundle.py) normalizes it before
comparing, so the bundled artifact stays verifiable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentharness.procurement.evaluation import evaluate_frozen_cases

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "procurement-service" / "src" / "main" / "resources" / "frozen" / "frozen-evaluation.json"


def main() -> None:
    payload = evaluate_frozen_cases()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(raw + b"\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(raw)} bytes, sha256={hashlib.sha256(raw).hexdigest()})")


if __name__ == "__main__":
    main()
