"""Budget anti-inflation and session prefix resolution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from agentharness.contracts import (
    ApprovalMode,
    BudgetConfig,
    ModelRequest,
    ModelStreamItem,
    RunRequest,
    RunStatus,
    StreamItemType,
    Usage,
)
from agentharness.engine.context import billable_turn_usage, estimate_tokens
from agentharness.harness import Harness
from agentharness.session_history import session_title_from_message


def test_billable_turn_usage_deflates_gateway_prompt_tokens() -> None:
    provider = Usage(input_tokens=304_466, output_tokens=7_027, total_tokens=311_493)
    local = 1_890
    text = "\u91c7\u8d2d B \u4ef7\u683c UP \u63d0\u5347 12.9 \u5143"
    billable = billable_turn_usage(
        provider_usage=provider,
        local_input_estimate=local,
        output_text=text,
    )
    assert billable.input_tokens == local
    assert billable.output_tokens == estimate_tokens(text)
    assert billable.total_tokens == billable.input_tokens + billable.output_tokens
    assert billable.estimated is True
    assert billable.total_tokens < 5_000


def test_billable_turn_usage_keeps_plausible_provider_counts() -> None:
    provider = Usage(input_tokens=900, output_tokens=40, total_tokens=940)
    billable = billable_turn_usage(
        provider_usage=provider,
        local_input_estimate=850,
        output_text="ok",
    )
    assert billable.input_tokens == 900
    assert billable.output_tokens == 40
    assert billable.estimated is False


class _InflatedFinalProvider:
    """One tool-free turn with gateway-inflated usage and a real final answer."""

    name = "inflated"

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        yield ModelStreamItem(type=StreamItemType.text_delta, text="CLI_OK final answer")
        yield ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=300_000, output_tokens=50_000, total_tokens=350_000),
        )
        yield ModelStreamItem(type=StreamItemType.done)


@pytest.mark.asyncio
async def test_inflated_provider_usage_does_not_fail_final_answer(
    data_dir: Path, workspace: Path
) -> None:
    harness = Harness(data_dir=data_dir, providers={"inflated": _InflatedFinalProvider()})
    try:
        result = await harness.run(
            RunRequest(
                message="say ok",
                provider="inflated",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                budget=BudgetConfig(max_tokens=200_000),
            )
        )
    finally:
        harness.close()

    assert result.status == RunStatus.completed
    assert "CLI_OK" in (result.output or "")
    assert result.error is None
    # Budget counters must stay under max_tokens after de-inflation.
    assert result.usage is not None
    assert result.usage.total_tokens < 200_000
    # Raw provider last_* still visible for diagnostics.
    assert result.usage.last_input_tokens == 300_000
    assert result.usage.last_local_estimate > 0


@pytest.mark.asyncio
async def test_session_prefix_resolves_to_full_id(data_dir: Path, workspace: Path) -> None:
    harness = Harness(data_dir=data_dir)
    try:
        first = await harness.run(
            RunRequest(
                message="remember codeword BANANA42",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        full = first.session_id
        assert len(full) == 32
        prefix = full[:12]
        resolved = harness.resolve_session_id(prefix)
        assert resolved == full

        second = await harness.run(
            RunRequest(
                message="what was the codeword?",
                session_id=resolved,
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        assert second.session_id == full
        # Must not create a 12-char fake session for the prefix.
        assert harness.get_session(prefix) is None or harness.get_session(prefix)["id"] == full
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_session_prefix_ambiguous_when_short_and_long_share_prefix(
    data_dir: Path, workspace: Path
) -> None:
    harness = Harness(data_dir=data_dir)
    try:
        long = await harness.run(
            RunRequest(
                message="long session",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        # Create a short fake session that is a pure prefix of the long one.
        short_id = long.session_id[:12]
        harness.storage.create_session(short_id, title="fake-short")
        with pytest.raises(ValueError, match="ambiguous"):
            harness.resolve_session_id(short_id)
    finally:
        harness.close()



def test_session_title_is_truncated_to_120_chars() -> None:
    """P3 regression: long first messages must not create unbounded titles."""
    long_text = "\u91c7\u8d2d10000\u4e2aPE\u5feb\u9012\u888b " * 20 + " \u7ed3\u675f"
    title = session_title_from_message(long_text)
    assert len(title) <= 120
    assert title.endswith("...")
    assert title.startswith("\u91c7\u8d2d10000\u4e2aPE\u5feb\u9012\u888b")

    short = session_title_from_message("  \u91c7\u8d2d  10000 \u4e2a \u888b  ")
    assert short == "\u91c7\u8d2d 10000 \u4e2a \u888b"
    assert session_title_from_message("   ") == "session"
