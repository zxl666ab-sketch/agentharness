"""Auto-compaction: selection, planner integration, and engine end-to-end."""

from __future__ import annotations

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    ContextState,
    Message,
    MessageRole,
    RunRequest,
    RunStatus,
    ToolCall,
)
from agentharness.engine.compaction import plan_compaction, render_transcript
from agentharness.engine.context import ContextBudgetError, ContextPlanner, estimate_tokens
from agentharness.harness import Harness
from tests.fake_provider import FakeModelAdapter

BIG_TEXT = ("alpha beta gamma delta " * 700) + "END_MARKER_XYZ"


def test_estimate_tokens_is_cjk_aware() -> None:
    # ASCII stays at ~4 chars/token (previous default).
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0
    # CJK characters cost ~1 token each, so Chinese-heavy text no longer
    # undercounts by ~4x (previously 8 CJK chars estimated as 2 tokens).
    assert estimate_tokens("中文中文") == 4
    assert estimate_tokens("采购10000个PE白色快递袋") == 10  # 8 CJK + ceil(7 ascii/4)
    # Mixed text: CJK tokens + ceil(ascii/4).
    assert estimate_tokens("中文 abcdef") == 4
    # CJK punctuation and fullwidth forms count too.
    assert estimate_tokens("，。！？") == 4


def _long_history() -> list[Message]:
    messages = [Message(role=MessageRole.user, content="original goal: build the report")]
    for index in range(6):
        call = ToolCall(id=f"call-{index}", name="read_file", arguments={"path": f"f{index}.txt"})
        messages.append(
            Message(role=MessageRole.assistant, content=f"working {index}", tool_calls=[call])
        )
        messages.append(
            Message(
                role=MessageRole.tool,
                tool_call_id=f"call-{index}",
                content=f"file {index} content " + ("x" * 900),
            )
        )
    messages.append(Message(role=MessageRole.user, content="now finish the summary"))
    return messages


def test_plan_compaction_protects_goal_and_recent_tail() -> None:
    messages = _long_history()
    budget = BudgetConfig(
        max_context_tokens=2000,
        context_compact_ratio=0.5,
        context_compact_keep_recent=2,
    )
    plan = plan_compaction(messages, None, budget)
    assert plan is not None
    covered = set(plan.cover_ids)
    # The latest user goal stays verbatim.
    assert messages[-1].id not in covered
    # The two newest groups before it stay verbatim (tool pairs are atomic).
    assert messages[-2].id not in covered and messages[-3].id not in covered
    # Old groups are covered, whole-group only.
    assert messages[1].id in covered and messages[2].id in covered
    assert plan.covered_tokens >= 512
    assert plan.groups_covered > 0


def test_plan_compaction_absent_below_threshold_or_disabled() -> None:
    messages = _long_history()
    assert plan_compaction(messages, None, BudgetConfig()) is None  # far below 80% of 100k
    assert (
        plan_compaction(
            messages,
            None,
            BudgetConfig(
                max_context_tokens=2000,
                context_compact_ratio=0.5,
                context_compact_enabled=False,
            ),
        )
        is None
    )


def test_plan_compaction_ignores_already_covered_groups() -> None:
    messages = _long_history()
    budget = BudgetConfig(max_context_tokens=2000, context_compact_ratio=0.5)
    first = plan_compaction(messages, None, budget)
    assert first is not None
    state = ContextState(summarized_message_ids=list(first.cover_ids))
    second = plan_compaction(messages, state, budget)
    # Everything coverable is already covered; remaining live tail is protected.
    assert second is None


def test_apply_compaction_renders_summary_and_excludes_covered_messages() -> None:
    planner = ContextPlanner()
    messages = _long_history()
    request = RunRequest(message="now finish the summary", cwd=".")
    base_state = planner.select_state(request)
    plan = plan_compaction(
        messages,
        None,
        BudgetConfig(max_context_tokens=2000, context_compact_ratio=0.5),
    )
    assert plan is not None
    state = planner.apply_compaction(
        base_state,
        summary_text="SUMMARY: report goal, six files read, ready to finish.",
        covered_ids=plan.cover_ids,
        artifact_id="artifact-1",
    )
    # State round-trips through JSON (checkpoint persistence contract).
    restored = ContextState.model_validate(state.model_dump(mode="json"))
    assert restored.compaction_count == 1
    assert set(restored.summarized_message_ids) == set(plan.cover_ids)

    bundle = planner.plan(
        run_id="run-1",
        request=request,
        messages=messages,
        tools=[],
        model_turn=3,
        state=restored,
        max_tokens=50_000,
    )
    assert "Conversation summary" in (bundle.system or "")
    assert "SUMMARY: report goal" in (bundle.system or "")
    planned_ids = {message.id for message in bundle.messages}
    assert planned_ids.isdisjoint(set(plan.cover_ids))
    assert messages[-1].id in planned_ids
    summarized_items = [
        item for item in bundle.manifest.items if item.compression == "summarized"
    ]
    assert summarized_items and all(not item.included for item in summarized_items)
    assert bundle.manifest.compacted is True
    summary_items = [
        item for item in bundle.manifest.items if item.section == "history_summary"
    ]
    assert summary_items and summary_items[0].artifact_id == "artifact-1"


def test_render_transcript_truncates_and_chains_prior_summary() -> None:
    messages = _long_history()
    text = render_transcript(
        messages, prior_summary="OLD SUMMARY", goal="build the report"
    )
    assert "Task goal:" in text
    assert "OLD SUMMARY" in text
    assert "read_file" in text
    # Big tool payloads are truncated per message.
    assert "x" * 901 not in text


def test_failed_tool_result_externalization_preserves_budget_failure() -> None:
    class FailingArtifacts:
        def put_json(self, *args, **kwargs):
            del args, kwargs
            raise OSError("artifact store unavailable")

    call = ToolCall(id="call-1", name="read_file", arguments={"path": "large.txt"})
    messages = [
        Message(role=MessageRole.user, content="read the file"),
        Message(role=MessageRole.assistant, content="reading", tool_calls=[call]),
        Message(role=MessageRole.tool, tool_call_id=call.id, content="x" * 4000),
    ]
    planner = ContextPlanner(artifacts=FailingArtifacts())

    with pytest.raises(ContextBudgetError):
        planner.plan(
            run_id="run-1",
            request=RunRequest(message="read the file", cwd="."),
            messages=messages,
            tools=[],
            model_turn=1,
            max_tokens=100,
        )

    assert messages[-1].content == "x" * 4000


@pytest.mark.asyncio
async def test_engine_auto_compacts_and_continues(data_dir, workspace) -> None:
    adapter = FakeModelAdapter(
        script=[
            {"kind": "text", "text": BIG_TEXT},
            {"kind": "text", "text": "SUMMARY: user asked for data, big output produced."},
            {"kind": "text", "text": "final answer"},
        ]
    )
    harness = Harness(data_dir=data_dir, providers={"fake": adapter})
    try:
        first = await harness.run(
            RunRequest(
                message="produce the big dataset",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        assert first.status == RunStatus.completed
        second = await harness.run(
            RunRequest(
                message="what did we produce?",
                provider="fake",
                session_id=first.session_id,
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(
                    max_context_tokens=4000,
                    context_compact_ratio=0.5,
                    context_compact_keep_recent=1,
                ),
            )
        )
        events = harness.get_events(run_id=second.run_id, limit=1000)
        checkpoint = harness.storage.load_checkpoint(second.run_id)
    finally:
        await harness.aclose()

    assert second.status == RunStatus.completed
    assert second.output == "final answer"

    compaction_events = [e for e in events if str(e.type) == "context_compacted"]
    assert len(compaction_events) == 1
    payload = compaction_events[0].payload
    assert payload["status"] == "applied"
    assert payload["messages_covered"] >= 2
    assert payload["tokens_before"] > payload["threshold_tokens"]
    assert payload["artifact_id"]

    # Final model call: summary in the stable prefix, originals out of messages.
    final_call = adapter.calls[-1]
    assert "Conversation summary" in (final_call.system or "")
    assert "SUMMARY: user asked for data" in (final_call.system or "")
    assert all("END_MARKER_XYZ" not in m.content for m in final_call.messages)
    # Summarizer call saw the (truncated) original history.
    summarize_call = adapter.calls[-2]
    assert "alpha beta gamma" in summarize_call.messages[0].content

    # The summarization spend is charged to the run.
    assert second.usage.input_tokens > 0 and second.usage.model_turns == 1

    # Compacted view survives restart: context state is checkpointed.
    assert checkpoint is not None
    state = ContextState.model_validate(checkpoint.metadata["context_state"])
    assert state.compaction_count == 1
    assert state.summarized_message_ids


@pytest.mark.asyncio
async def test_engine_compaction_failure_degrades_to_uncompacted(
    data_dir, workspace
) -> None:
    adapter = FakeModelAdapter(
        script=[
            {"kind": "text", "text": BIG_TEXT},
            {"kind": "error", "error": "summarizer down", "error_kind": "provider"},
            {"kind": "text", "text": "still finished"},
        ]
    )
    harness = Harness(data_dir=data_dir, providers={"fake": adapter})
    try:
        first = await harness.run(
            RunRequest(
                message="produce the big dataset",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        second = await harness.run(
            RunRequest(
                message="what did we produce?",
                provider="fake",
                session_id=first.session_id,
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(
                    max_context_tokens=4000,
                    context_compact_ratio=0.5,
                    context_compact_keep_recent=1,
                ),
            )
        )
        events = harness.get_events(run_id=second.run_id, limit=1000)
    finally:
        await harness.aclose()

    assert second.status == RunStatus.completed
    assert second.output == "still finished"
    compaction_events = [e for e in events if str(e.type) == "context_compacted"]
    assert len(compaction_events) == 1
    assert compaction_events[0].payload["status"] == "skipped"
    assert "summarizer down" in compaction_events[0].payload["reason"]
    final_call = adapter.calls[-1]
    assert "Conversation summary" not in (final_call.system or "")
