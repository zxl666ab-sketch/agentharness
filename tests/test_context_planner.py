from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalMode,
    Message,
    MessageRole,
    RunRequest,
    RunStatus,
    ToolCall,
    ToolSpec,
)
from agentharness.engine.context import ContextBudgetError, ContextPlanner
from tests.fake_provider import FakeModelAdapter


def _planner(harness) -> ContextPlanner:
    return ContextPlanner(
        storage=harness.storage,
        artifacts=harness.storage.artifacts,
        redactor=harness.redactor,
    )


def test_plan_discovers_rules_root_to_cwd_and_keeps_the_selected_context(
    harness, tmp_path: Path
) -> None:
    root = tmp_path / "workspace"
    cwd = root / "packages" / "app"
    cwd.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root safety rule", encoding="utf-8")
    (cwd / "WORKBUDDY.md").write_text("app-local rule", encoding="utf-8")
    request = RunRequest(
        message="complete the alpha workflow",
        cwd=str(cwd),
        extra_dirs=[str(root)],
    )

    first = _planner(harness).plan(
        run_id="run-context",
        request=request,
        messages=[Message(role=MessageRole.user, content=request.message)],
        tools=[],
        model_turn=0,
    )

    assert first.system is not None
    assert first.system.index("root safety rule") < first.system.index("app-local rule")
    assert first.manifest.total_tokens <= first.manifest.budget_tokens
    assert first.manifest.prefix_fingerprint
    assert {item.section for item in first.manifest.items if item.included} >= {
        "system",
        "workspace_rules",
        "messages",
    }

    # A run keeps the selected bytes even if rule files change later.
    (root / "AGENTS.md").write_text("changed after the run started", encoding="utf-8")
    second = _planner(harness).plan(
        run_id="run-context",
        request=request,
        messages=[Message(role=MessageRole.user, content=request.message), Message(role=MessageRole.assistant, content="working")],
        tools=[],
        model_turn=1,
        state=first.state,
    )

    assert second.system == first.system
    assert second.manifest.prefix_fingerprint == first.manifest.prefix_fingerprint
    assert "changed after" not in second.system


def test_plan_records_excluded_oversized_rule_and_redacts_manifest(harness, tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("x" * 70_000, encoding="utf-8")
    (cwd / "WORKBUDDY.md").write_text(
        "Authorization: Bearer super-secret-token", encoding="utf-8"
    )
    request = RunRequest(message="inspect", cwd=str(cwd))

    bundle = _planner(harness).plan(
        run_id="redacted",
        request=request,
        messages=[Message(role=MessageRole.user, content="inspect")],
        tools=[],
        model_turn=0,
    )

    oversized = next(item for item in bundle.manifest.items if item.source.endswith("AGENTS.md"))
    assert not oversized.included
    assert oversized.compression == "excluded"
    assert "size" in oversized.reason
    serialized = bundle.manifest.model_dump_json() + bundle.state.model_dump_json()
    assert "super-secret-token" not in serialized


def test_plan_excludes_workspace_rule_symlinks(
    harness, tmp_path: Path, monkeypatch
) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    candidate = cwd / "AGENTS.md"
    candidate.write_text("must never be followed", encoding="utf-8")
    original_is_symlink = Path.is_symlink

    def reports_test_rule_as_symlink(path: Path) -> bool:
        return path == candidate or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", reports_test_rule_as_symlink)
    bundle = _planner(harness).plan(
        run_id="symlink-rule",
        request=RunRequest(message="inspect", cwd=str(cwd)),
        messages=[Message(role=MessageRole.user, content="inspect")],
        tools=[],
        model_turn=0,
    )

    rule = next(item for item in bundle.manifest.items if item.source.endswith("AGENTS.md"))
    assert rule.included is False
    assert rule.reason == "excluded: symbolic links are not allowed"
    assert "must never be followed" not in (bundle.system or "")


def test_plan_meets_budget_and_preserves_tool_call_result_pairs(harness, tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    tool_call = ToolCall(id="call-1", name="lookup", arguments={"q": "old"})
    messages = [
        Message(role=MessageRole.user, content="old request " * 80),
        Message(role=MessageRole.assistant, content="", tool_calls=[tool_call]),
        Message(role=MessageRole.tool, name="lookup", tool_call_id="call-1", content="old result " * 120),
        Message(role=MessageRole.assistant, content="old answer " * 80),
        Message(role=MessageRole.user, content="LATEST USER GOAL MUST STAY"),
    ]
    request = RunRequest(message="LATEST USER GOAL MUST STAY", cwd=str(cwd))
    tools = [ToolSpec(name="lookup", description="Read a lookup value", parameters={"type": "object"})]

    bundle = _planner(harness).plan(
        run_id="budgeted",
        request=request,
        messages=messages,
        tools=tools,
        model_turn=0,
        max_tokens=90,
    )

    assert bundle.manifest.total_tokens <= 90
    assert any(m.content == "LATEST USER GOAL MUST STAY" for m in bundle.messages)
    call_ids = {
        tc.id
        for message in bundle.messages
        for tc in (message.tool_calls or [])
    }
    result_ids = {
        message.tool_call_id
        for message in bundle.messages
        if message.role == MessageRole.tool
    }
    assert result_ids <= call_ids
    assert call_ids <= result_ids
    assert any(not item.included for item in bundle.manifest.items if item.section == "messages")


def test_plan_raises_instead_of_returning_an_over_budget_bundle(harness, tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    request = RunRequest(message="essential latest goal " * 100, cwd=str(cwd))

    with pytest.raises(ContextBudgetError):
        _planner(harness).plan(
            run_id="impossible",
            request=request,
            messages=[Message(role=MessageRole.user, content=request.message)],
            tools=[],
            model_turn=0,
            max_tokens=10,
        )


@pytest.mark.asyncio
async def test_run_engine_uses_bundle_and_persists_each_turn_manifest(harness, workspace) -> None:
    (workspace / "AGENTS.md").write_text("stable runtime rule", encoding="utf-8")
    provider = FakeModelAdapter(
        script=[
            {"kind": "tools", "tools": [{"name": "read_file", "arguments": {"path": "README.md"}}]},
            {"kind": "text", "text": "finished after tool"},
        ]
    )
    harness.register_provider("context-script", provider)

    result = await harness.run(
        RunRequest(
            message="read the workspace",
            provider="context-script",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )

    assert result.status == RunStatus.completed
    assert len(provider.calls) == 2
    assert provider.calls[0].system == provider.calls[1].system
    assert "stable runtime rule" in (provider.calls[0].system or "")
    manifests = harness.get_context_manifests(result.run_id)
    assert len(manifests) == 2
    assert manifests[0]["prefix_fingerprint"] == manifests[1]["prefix_fingerprint"]
    assert all(item["total_tokens"] <= item["budget_tokens"] for item in manifests)
    assert all(item.get("artifact_id") for item in manifests)
    checkpoint = harness.get_checkpoint(result.run_id)
    assert checkpoint is not None
    assert checkpoint.metadata.get("context_state")


@pytest.mark.asyncio
async def test_context_selection_survives_interrupt_resume(harness, workspace) -> None:
    rule = workspace / "AGENTS.md"
    rule.write_text("rule selected before interruption", encoding="utf-8")
    interrupted = await harness.run(
        RunRequest(
            message="[fake:error:timeout]",
            provider="fake",
            approval=ApprovalMode.auto,
            cwd=str(workspace),
        )
    )
    assert interrupted.status == RunStatus.interrupted
    before = harness.get_context_manifests(interrupted.run_id)
    assert len(before) == 1

    rule.write_text("rule changed after interruption", encoding="utf-8")
    resumed_provider = FakeModelAdapter(script=[{"kind": "text", "text": "resumed"}])
    harness.register_provider("fake", resumed_provider)
    resumed = await harness.resume(interrupted.run_id)

    assert resumed.status == RunStatus.completed
    assert "rule selected before interruption" in (resumed_provider.calls[0].system or "")
    assert "rule changed after interruption" not in (resumed_provider.calls[0].system or "")
    after = harness.get_context_manifests(interrupted.run_id)
    assert len(after) == 2
    assert before[0]["prefix_fingerprint"] == after[1]["prefix_fingerprint"]
