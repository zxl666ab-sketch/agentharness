"""Live daily-assistant matrix via Harness API (loads .env)."""
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

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ[k.strip()] = v.strip()

from agentharness.contracts import ApprovalMode, RunRequest  # noqa: E402
from agentharness.harness import Harness  # noqa: E402

OUT = ROOT / "output" / "flywheel" / f"matrix-live-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)


def log(row: dict) -> None:
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


async def run_case(h: Harness, name: str, message: str, *, session_id: str | None = None, cwd: str | None = None) -> dict:
    if session_id:
        try:
            session_id = h.resolve_session_id(session_id)
        except Exception as exc:  # noqa: BLE001
            row = {"case": name, "status": "resolve_failed", "error": str(exc), "session_id": session_id}
            log(row)
            return row
    req = RunRequest(
        message=message,
        session_id=session_id,
        provider="openai",
        model=os.environ.get("OPENAI_MODEL") or None,
        approval=ApprovalMode.auto,
        cwd=str(cwd or ROOT),
    )
    result = await h.run(req)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "case": name,
        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
        "run_id": result.run_id,
        "session_id": result.session_id,
        "session_len": len(result.session_id or ""),
        "error": result.error,
        "output": (result.output or "")[:500],
        "usage": result.usage.model_dump() if result.usage else None,
    }
    log(row)
    return row


async def main() -> int:
    sandbox = ROOT / "output" / "flywheel" / f"sandbox-live-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    sandbox.mkdir(parents=True, exist_ok=True)
    h = Harness()
    try:
        r0 = await run_case(h, "quota_probe", "Reply with exactly: QUOTA_OK. No tools. Do not modify files.")
        if r0.get("status") != "completed" or "QUOTA_OK" not in (r0.get("output") or ""):
            print("STOP_QUOTA", flush=True)
            print(f"LOG={OUT}", flush=True)
            return 2

        r1 = await run_case(
            h,
            "S1_remember",
            "日常助理测试。请记住暗号是 LIVE_ORANGE_3。只回复：已记住。不要调用工具，不要改文件。",
        )
        full = r1.get("session_id") or ""
        prefix = full[:12]
        resolved = h.resolve_session_id(prefix)
        log({"case": "S1_prefix_resolve", "prefix": prefix, "resolved": resolved, "ok": resolved == full})
        r1b = await run_case(
            h,
            "S1_recall",
            "暗号是什么？只回答暗号本身。",
            session_id=prefix,
        )

        await run_case(
            h,
            "S3_read",
            "用 read_file 读取 README.md 第一行，只回复那一行原文。不要修改任何文件。",
        )
        await run_case(
            h,
            "S4_search",
            "用 search_files 搜索 agentharness，最多列 3 个路径。不要改文件。",
        )
        await run_case(
            h,
            "S3_sandbox_write",
            "在当前目录创建 note.txt，写入一行 HELLO_LIVE_SANDBOX，再读回确认。不要改仓库其它文件。",
            cwd=str(sandbox),
        )
        await run_case(
            h,
            "S5_shell",
            "用 shell 执行 echo LIVE_SHELL_OK ，只报告输出。不要删除任何文件。",
            cwd=str(sandbox),
        )
        await run_case(
            h,
            "S2_browser",
            "用 browser 打开 B 站搜索「神烦老狗」，根据结果用一两句话说明这是什么 UP。不要修改任何文件。",
        )
    finally:
        await h.aclose()
    print(f"LOG={OUT}", flush=True)
    print(f"SANDBOX={sandbox}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
