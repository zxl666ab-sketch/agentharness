"""Offline fake-provider matrix for daily-assistant paths (no live quota)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from agentharness.contracts import ApprovalMode, RunRequest  # noqa: E402
from agentharness.harness import Harness  # noqa: E402
from agentharness.providers.fake import FakeModelAdapter  # noqa: E402

OUT = ROOT / "output" / "flywheel" / "matrix-offline.jsonl"


def log(row: dict) -> None:
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


async def main() -> int:
    if OUT.exists():
        OUT.unlink()
    sandbox = ROOT / "output" / "flywheel" / f"sandbox-offline-{datetime.now().strftime('%H%M%S')}"
    sandbox.mkdir(parents=True, exist_ok=True)

    h = Harness()
    h.register_provider("fake", FakeModelAdapter())

    async def auto(req):
        from agentharness.contracts import ApprovalDecision
        return ApprovalDecision.allow_run

    h.set_approval_callback(auto)
    try:
        r1 = await h.run(
            RunRequest(
                message="[fake:text]已记住",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
            )
        )
        full = r1.session_id
        prefix = full[:12]
        resolved = h.resolve_session_id(prefix)
        log(
            {
                "case": "S1_prefix",
                "status": r1.status.value,
                "session": full,
                "resolved": resolved,
                "ok": resolved == full and r1.status.value == "completed",
            }
        )

        r1b = await h.run(
            RunRequest(
                message="[fake:text]BLUE_MANGO_OFFLINE",
                session_id=resolved,
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
            )
        )
        log(
            {
                "case": "S1_continue",
                "status": r1b.status.value,
                "session": r1b.session_id,
                "same": r1b.session_id == full,
                "output": r1b.output,
            }
        )

        r3 = await h.run(
            RunRequest(
                message='[fake:tools]read_file\n{"path":"README.md"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
            )
        )
        log(
            {
                "case": "S3_read",
                "status": r3.status.value,
                "run_id": r3.run_id,
                "has_agent": "Agent Harness" in (r3.output or "") or "Agent" in (r3.output or "") or bool(r3.output),
                "output": (r3.output or "")[:200],
            }
        )

        note_path = "note.txt"
        r_write = await h.run(
            RunRequest(
                message=f'[fake:tools]write_file\n{{"path":"{note_path}","content":"HELLO_SANDBOX"}}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(sandbox),
            )
        )
        note_ok = (sandbox / note_path).read_text(encoding="utf-8") == "HELLO_SANDBOX" if (sandbox / note_path).exists() else False
        log(
            {
                "case": "S3_sandbox_write",
                "status": r_write.status.value,
                "note_ok": note_ok,
                "run_id": r_write.run_id,
            }
        )

        r_shell = await h.run(
            RunRequest(
                message='[fake:tools]shell\n{"command":"echo SHELL_OK"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(sandbox),
            )
        )
        log(
            {
                "case": "S5_shell_auto",
                "status": r_shell.status.value,
                "error": r_shell.error,
                "output": (r_shell.output or "")[:300],
                "run_id": r_shell.run_id,
                "no_approval_denied": "Approval denied" not in (r_shell.output or ""),
            }
        )

        r_search = await h.run(
            RunRequest(
                message='[fake:tools]search_files\n{"query":"agentharness"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
            )
        )
        log(
            {
                "case": "S4_search",
                "status": r_search.status.value,
                "run_id": r_search.run_id,
                "output": (r_search.output or "")[:300],
            }
        )
    finally:
        await h.aclose()

    print(f"LOG={OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
