from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentharness.contracts import ApprovalMode, RunRequest, ToolContext
from agentharness.tools.fs import ReadFileTool, WriteFileTool


def _ctx(workspace: Path, data_dir: Path) -> ToolContext:
    return ToolContext(
        run_id="file-version-run",
        session_id="file-version-session",
        cwd=str(workspace),
        data_dir=str(data_dir),
        approval_mode=ApprovalMode.auto,
    )


def _version(content: str) -> str:
    match = re.search(r"file_version sha256=([0-9a-f]{64})", content)
    assert match, content
    return match.group(1)


@pytest.mark.asyncio
async def test_read_version_rejects_stale_overwrite_and_supports_recovery(
    workspace: Path, data_dir: Path
) -> None:
    target = workspace / "a.txt"
    reader = ReadFileTool()
    writer = WriteFileTool()
    context = _ctx(workspace, data_dir)

    read = await reader.run(context, {"path": "a.txt"})
    old_version = _version(read.content)
    target.write_text("changed by another agent\n", encoding="utf-8")

    stale = await writer.run(
        context,
        {"path": "a.txt", "content": "must not win", "expected_version": old_version},
    )
    assert stale.is_error
    assert stale.error_code == "file_version_conflict"
    assert stale.error_category == "concurrency"
    assert stale.retryable
    assert "read_file" in (stale.recovery_hint or "")
    assert target.read_text(encoding="utf-8") == "changed by another agent\n"

    reread = await reader.run(context, {"path": "a.txt"})
    recovered = await writer.run(
        context,
        {
            "path": "a.txt",
            "content": "recovered write",
            "expected_version": f"sha256:{_version(reread.content)}",
        },
    )
    assert not recovered.is_error
    assert target.read_text(encoding="utf-8") == "recovered write"
    assert _version(recovered.content)


@pytest.mark.asyncio
async def test_new_file_and_legacy_overwrite_remain_compatible(
    workspace: Path, data_dir: Path
) -> None:
    writer = WriteFileTool()
    context = _ctx(workspace, data_dir)

    created = await writer.run(context, {"path": "new.txt", "content": "new"})
    legacy = await writer.run(context, {"path": "new.txt", "content": "legacy update"})

    assert not created.is_error
    assert not legacy.is_error
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "legacy update"


@pytest.mark.asyncio
async def test_structured_tool_error_is_available_in_events(harness, workspace) -> None:
    result = await harness.run(
        RunRequest(
            message='[fake:tools]read_file\n{"path": "missing.txt"}',
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )

    tool_event = next(
        event
        for event in harness.get_events(run_id=result.run_id, limit=1000)
        if (event.type.value if hasattr(event.type, "value") else str(event.type)) == "tool_result"
    )
    assert tool_event.payload["error_code"] == "read_failed"
    assert tool_event.payload["error_category"] == "filesystem"
    assert tool_event.payload["retryable"] is True
    assert tool_event.payload["recovery_hint"]
