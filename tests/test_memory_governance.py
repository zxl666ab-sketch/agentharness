from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentharness.contracts import ApprovalMode, RunRequest, RunStatus, ToolContext
from agentharness.memory_scope import workspace_memory_scope
from agentharness.storage.sqlite import Storage
from agentharness.tools.memory import (
    MemoryDeleteTool,
    MemorySearchTool,
    MemoryStoreTool,
    MemoryUpdateTool,
)
from tests.fake_provider import create_test_harness


def test_memory_hash_dedup_update_delete_and_versioning(data_dir) -> None:
    storage = Storage(data_dir)
    try:
        first = storage.add_memory("stable fact", scope="workspace:test")
        duplicate = storage.add_memory(" stable   fact ", scope="workspace:test")
        assert duplicate == first
        row = storage.get_memory(first)
        assert row is not None
        old_hash = row["content_hash"]

        updated = storage.update_memory(
            first,
            content="updated stable fact",
            expected_hash=f"sha256:{old_hash}",
        )
        assert updated["content_hash"] != old_hash
        with pytest.raises(ValueError, match="version conflict"):
            storage.update_memory(first, content="stale", expected_hash=old_hash)

        hits = storage.search_memories(
            "updated stable", scopes=["workspace:test"]
        )
        assert hits and hits[0]["id"] == first
        assert storage.get_memory(first)["use_count"] >= 1  # type: ignore[index]
        assert storage.delete_memory(
            first, expected_hash=updated["content_hash"]
        )
        assert storage.get_memory(first) is None
    finally:
        storage.close()


def test_memory_scope_order_and_expiry_prevent_cross_workspace_leak(data_dir) -> None:
    storage = Storage(data_dir)
    try:
        storage.add_memory("shared KEYWORD workspace", scope="workspace:a")
        storage.add_memory("shared KEYWORD global", scope="global")
        storage.add_memory("shared KEYWORD other", scope="workspace:b")
        storage.add_memory(
            "shared KEYWORD expired",
            scope="workspace:a",
            expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )

        hits = storage.search_memories(
            "KEYWORD", scopes=["workspace:a", "global"], limit=10
        )
        contents = [row["content"] for row in hits]
        assert contents[:2] == ["shared KEYWORD workspace", "shared KEYWORD global"]
        assert all("other" not in content and "expired" not in content for content in contents)
    finally:
        storage.close()


def test_memory_ranking_combines_bm25_with_freshness(data_dir) -> None:
    storage = Storage(data_dir)
    try:
        older = storage.add_memory("ranking keyword alpha", scope="workspace:rank")
        newer = storage.add_memory("ranking keyword beta", scope="workspace:rank")
        with storage._lock:  # noqa: SLF001 - deterministic ranking fixture
            storage._conn.execute(  # noqa: SLF001
                "UPDATE memories SET last_used_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", older),
            )
            storage._conn.execute(  # noqa: SLF001
                "UPDATE memories SET last_used_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), newer),
            )

        hits = storage.search_memories(
            "ranking keyword", scopes=["workspace:rank"], limit=2
        )
        assert [row["id"] for row in hits] == [newer, older]
        assert hits[0]["freshness_score"] > hits[1]["freshness_score"]
        assert hits[0]["rank_score"] < hits[1]["rank_score"]
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_memory_write_requires_confirmation_even_in_auto_mode(
    data_dir, workspace
) -> None:
    harness = create_test_harness(data_dir=data_dir)
    try:
        result = await harness.run(
            RunRequest(
                message=(
                    '[fake:tools]memory_store\n'
                    '{"content":"MUST_REQUIRE_CONFIRMATION","scope":"workspace"}'
                ),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                tools=["memory_store"],
            )
        )
        scope = workspace_memory_scope(str(workspace))
        assert scope is not None
        assert result.status == RunStatus.completed
        assert "Approval denied" in result.output
        assert not harness.storage.search_memories(
            "MUST_REQUIRE_CONFIRMATION", scopes=[scope]
        )
        approvals = harness.storage.list_approvals(result.run_id)
        assert approvals and approvals[0]["decision"] == "deny"
    finally:
        await harness.aclose()


@pytest.mark.asyncio
async def test_memory_tool_contracts_cover_scope_version_conflicts_and_missing_storage(
    data_dir, workspace
) -> None:
    unavailable = ToolContext(
        run_id="memory-tool-unavailable",
        session_id="memory-session",
        cwd=str(workspace),
        data_dir=str(data_dir),
    )
    for tool, arguments in (
        (MemoryStoreTool(), {"content": "fact"}),
        (MemorySearchTool(), {"query": "fact"}),
        (MemoryUpdateTool(), {"id": "missing"}),
        (MemoryDeleteTool(), {"id": "missing"}),
    ):
        result = await tool.run(unavailable, arguments)
        assert result.is_error is True
        assert result.content == "storage unavailable"

    harness = create_test_harness(data_dir=data_dir)
    context = unavailable.model_copy(update={"harness": harness})
    store = MemoryStoreTool()
    search = MemorySearchTool()
    update = MemoryUpdateTool()
    delete = MemoryDeleteTool()
    try:
        empty = await store.run(context, {"content": "   "})
        assert empty.is_error is True
        assert empty.content == "empty content"

        stored = await store.run(
            context,
            {
                "content": "TOOL_CONTRACT_FACT blue",
                "scope": "workspace",
                "source": "test",
            },
        )
        assert stored.is_error is False
        assert "Stored memory" in stored.content
        scope = workspace_memory_scope(str(workspace))
        assert scope is not None
        row = harness.storage.search_memories(
            "TOOL_CONTRACT_FACT", scopes=[scope]
        )[0]

        default_hits = await search.run(context, {"query": "TOOL_CONTRACT_FACT"})
        assert "TOOL_CONTRACT_FACT blue" in default_hits.content
        explicit_hits = await search.run(
            context,
            {"query": "TOOL_CONTRACT_FACT", "scope": "workspace", "limit": 1},
        )
        assert f"[{scope}]" in explicit_hits.content
        no_hits = await search.run(context, {"query": "NO_SUCH_MEMORY"})
        assert no_hits.content == "No memories found"

        conflict = await update.run(
            context,
            {
                "id": row["id"],
                "content": "TOOL_CONTRACT_FACT green",
                "expected_hash": "sha256:stale",
            },
        )
        assert conflict.error_code == "memory_update_conflict"
        assert conflict.retryable is True

        updated = await update.run(
            context,
            {
                "id": row["id"],
                "content": "TOOL_CONTRACT_FACT green",
                "scope": "workspace",
                "expected_hash": row["content_hash"],
            },
        )
        assert "Updated memory" in updated.content
        current = harness.storage.get_memory(row["id"])
        assert current is not None

        delete_conflict = await delete.run(
            context,
            {"id": row["id"], "expected_hash": "sha256:stale"},
        )
        assert delete_conflict.error_code == "memory_delete_conflict"

        deleted = await delete.run(
            context,
            {"id": row["id"], "expected_hash": current["content_hash"]},
        )
        assert deleted.content == "Deleted memory"
        missing = await delete.run(context, {"id": row["id"]})
        assert missing.is_error is True
        assert missing.content == "Memory not found"
    finally:
        await harness.aclose()
