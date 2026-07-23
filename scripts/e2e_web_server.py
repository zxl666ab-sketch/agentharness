"""Seed a disposable observer database and serve it for Playwright."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentharness.api.server import serve
from agentharness.contracts import (
    ApprovalMode,
    EventEnvelope,
    EventType,
    RunRequest,
    RunStatus,
)
from agentharness.harness import Harness


async def seed(data_dir: Path, workspace: Path) -> None:
    harness = Harness(data_dir=data_dir)
    try:
        await harness.run(
            RunRequest(
                message='[fake:tools]read_file\n{"path":"README.md"}',
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        await harness.run(
            RunRequest(
                message=(
                    '[fake:tools]delegate\n'
                    '{"task":"[fake:text]child e2e output","allow_write":false}'
                ),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        await harness.run(
            RunRequest(
                message="[fake:error:provider]",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        long_run = await harness.run(
            RunRequest(
                message="[fake:text]long trace fixture",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        long_row = harness.get_run(long_run.run_id)
        assert long_row is not None
        for start in range(0, 1200, 200):
            harness.storage.append_events(
                [
                    EventEnvelope(
                        session_id=long_run.session_id,
                        root_run_id=long_run.run_id,
                        run_id=long_run.run_id,
                        type=EventType.budget_warning,
                        payload={"message": f"fixture row {index}"},
                    )
                    for index in range(start, start + 200)
                ]
            )

        stale_session = harness.storage.create_session(title="stale fixture")
        harness.storage.create_run(
            run_id="stale-e2e-run",
            session_id=stale_session,
            root_run_id="stale-e2e-run",
            status=RunStatus.running,
            provider="fake",
            approval="auto",
            cwd=str(workspace),
        )
        stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with harness.storage._lock:
            harness.storage._conn.execute(  # noqa: SLF001 - deterministic test fixture
                "UPDATE runs SET created_at = ?, updated_at = ? WHERE id = ?",
                (stale_time, stale_time, "stale-e2e-run"),
            )
    finally:
        harness.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    data_dir = Path(tempfile.mkdtemp(prefix="agentharness-web-e2e-"))
    workspace = data_dir / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("temporary e2e workspace", encoding="utf-8")
    atexit.register(shutil.rmtree, data_dir, True)
    asyncio.run(seed(data_dir, workspace))
    serve(data_dir=data_dir, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
