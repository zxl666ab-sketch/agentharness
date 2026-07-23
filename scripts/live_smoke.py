#!/usr/bin/env python3
"""Automated live provider smoke tests (real network + real keys).

Usage (from repo root):
  uv run python scripts/live_smoke.py
  uv run python scripts/live_smoke.py --provider openai
  uv run python scripts/live_smoke.py --provider anthropic

Never prints API keys. Skips providers without keys.
Exit code 0 only if every *selected available* provider smoke passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

# Ensure package import
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentharness.contracts import (  # noqa: E402
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
    RunRequest,
)
from agentharness.harness import Harness  # noqa: E402
from agentharness.security.redaction import Redactor  # noqa: E402


def _mask(v: str | None) -> str:
    return "set" if v else "unset"


async def _smoke_provider(
    provider: str,
    *,
    data_dir: Path,
    cwd: Path,
    model: str | None,
) -> dict:
    t0 = time.monotonic()
    redactor = Redactor(
        extra_sentinels=[
            value
            for value in (
                os.environ.get("OPENAI_API_KEY"),
                os.environ.get("ANTHROPIC_API_KEY"),
            )
            if value
        ]
    )
    out: dict = {
        "provider": provider,
        "model": model,
        "ok": False,
        "run_id": None,
        "status": None,
        "output_preview": None,
        "error": None,
        "elapsed_s": None,
        "steps": None,
    }

    async def auto(req: ApprovalRequest) -> ApprovalDecision:
        if req.effect.value == "destructive":
            return ApprovalDecision.deny
        return ApprovalDecision.allow_run

    h = Harness(data_dir=data_dir, redactor=redactor)
    h.set_approval_callback(auto)

    # 1) plain text stream
    try:
        r1 = await h.run(
            RunRequest(
                message=(
                    "Reply with exactly three English words: live smoke ok. "
                    "No tools. No markdown."
                ),
                provider=provider,
                model=model,
                approval=ApprovalMode.auto,
                cwd=str(cwd),
                tools=[],  # no tools for first turn
            )
        )
        out["run_id"] = r1.run_id
        out["status"] = r1.status.value
        out["output_preview"] = (r1.output or "")[:200]
        out["error"] = r1.error
        out["steps"] = r1.steps
        if r1.status.value != "completed":
            out["error"] = out["error"] or f"status={r1.status.value}"
            out["elapsed_s"] = round(time.monotonic() - t0, 2)
            await h.aclose()
            return out
        if not (r1.output or "").strip():
            out["error"] = "empty model output"
            out["elapsed_s"] = round(time.monotonic() - t0, 2)
            await h.aclose()
            return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = redactor.redact_text(f"{type(exc).__name__}: {exc}")
        out["traceback"] = redactor.redact_text(traceback.format_exc()[-800:])
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        await h.aclose()
        return out

    # 2) optional tool-using turn (read_file) if model cooperates
    try:
        r2 = await h.run(
            RunRequest(
                message=(
                    "Use the read_file tool to read path README.md, then reply with "
                    "one short sentence starting with TOOL_OK and mentioning Agent Harness."
                ),
                provider=provider,
                model=model,
                approval=ApprovalMode.auto,
                cwd=str(cwd),
                tools=["read_file"],
            )
        )
        out["tool_run_id"] = r2.run_id
        out["tool_status"] = r2.status.value
        out["tool_output_preview"] = (r2.output or "")[:300]
        out["tool_error"] = r2.error
        # Soft requirement: completed is enough; tool use is best-effort
        if r2.status.value != "completed":
            out["error"] = out["error"] or r2.error or f"tool turn status={r2.status.value}"
            out["elapsed_s"] = round(time.monotonic() - t0, 2)
            await h.aclose()
            return out
    except Exception as exc:  # noqa: BLE001
        out["tool_error"] = redactor.redact_text(f"{type(exc).__name__}: {exc}")
        # Text smoke already passed; tool failure is reported but still fail overall
        out["error"] = redactor.redact_text(f"tool turn failed: {exc}")
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        await h.aclose()
        return out

    out["ok"] = True
    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    await h.aclose()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Live provider smoke tests")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "all"],
        default="all",
        help="Which provider(s) to test",
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / ".agentharness" / "live_smoke"),
        help="Isolated data dir for smoke runs",
    )
    args = parser.parse_args()

    print("=== Agent Harness Live Smoke ===")
    print(f"OPENAI_API_KEY: {_mask(os.environ.get('OPENAI_API_KEY'))}")
    print(f"OPENAI_BASE_URL: {_mask(os.environ.get('OPENAI_BASE_URL'))}")
    print(f"OPENAI_MODEL: {_mask(os.environ.get('OPENAI_MODEL'))}")
    print(f"ANTHROPIC_API_KEY: {_mask(os.environ.get('ANTHROPIC_API_KEY'))}")
    print(f"ANTHROPIC_BASE_URL: {_mask(os.environ.get('ANTHROPIC_BASE_URL'))}")
    print(f"ANTHROPIC_MODEL: {_mask(os.environ.get('ANTHROPIC_MODEL'))}")

    targets: list[tuple[str, str | None]] = []
    if args.provider in ("openai", "all") and os.environ.get("OPENAI_API_KEY"):
        targets.append(("openai", os.environ.get("OPENAI_MODEL") or None))
    if args.provider in ("anthropic", "all") and os.environ.get("ANTHROPIC_API_KEY"):
        targets.append(("anthropic", os.environ.get("ANTHROPIC_MODEL") or None))

    if not targets:
        print("\nNO_KEYS: export OPENAI_API_KEY or ANTHROPIC_API_KEY and retry.")
        return 2

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cwd = ROOT
    results: list[dict] = []

    for provider, model in targets:
        print(f"\n--- smoking provider={provider} model={model or 'default'} ---")
        result = asyncio.run(
            _smoke_provider(provider, data_dir=data_dir, cwd=cwd, model=model)
        )
        results.append(result)
        status = "PASS" if result["ok"] else "FAIL"
        print(f"  {status}  status={result.get('status')} elapsed={result.get('elapsed_s')}s")
        print(f"  run_id={result.get('run_id')}")
        if result.get("output_preview"):
            print(f"  output: {result['output_preview']!r}")
        if result.get("tool_output_preview"):
            print(f"  tool:   {result['tool_output_preview']!r}")
        if result.get("error"):
            print(f"  error:  {result['error']}")

    report_path = data_dir / "live_smoke_report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport: {report_path}")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\nFAILED: {len(failed)}/{len(results)} provider smoke(s)")
        return 1
    print(f"\nOK: {len(results)}/{len(results)} provider smoke(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
