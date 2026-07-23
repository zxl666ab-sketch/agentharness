"""Optional live smoke — skipped unless LIVE_SMOKE=1 and keys are present.

Run:
  LIVE_SMOKE=1 uv run pytest tests/live -v
  # or
  uv run python scripts/live_smoke.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

LIVE = os.environ.get("LIVE_SMOKE", "").strip() in ("1", "true", "yes")
HAS_OPENAI = bool(os.environ.get("OPENAI_API_KEY"))
HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(not LIVE, reason="Set LIVE_SMOKE=1 to enable live provider tests")
@pytest.mark.skipif(not HAS_OPENAI, reason="OPENAI_API_KEY not set")
@pytest.mark.asyncio
async def test_live_openai_text_and_tool(tmp_path):
    from agentharness.contracts import ApprovalDecision, ApprovalMode, RunRequest
    from agentharness.harness import Harness

    async def auto(req):  # noqa: ANN001
        return (
            ApprovalDecision.deny
            if req.effect.value == "destructive"
            else ApprovalDecision.allow_run
        )

    h = Harness(data_dir=tmp_path / "data")
    h.set_approval_callback(auto)
    try:
        result = await h.run(
            RunRequest(
                message="Reply with exactly: LIVE_OK. No tools.",
                provider="openai",
                model=os.environ.get("OPENAI_MODEL") or None,
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
                tools=[],
            )
        )
        assert result.status.value == "completed", result.error
        assert (result.output or "").strip(), "empty output"
    finally:
        await h.aclose()


@pytest.mark.skipif(not LIVE, reason="Set LIVE_SMOKE=1 to enable live provider tests")
@pytest.mark.skipif(not HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.asyncio
async def test_live_anthropic_text(tmp_path):
    from agentharness.contracts import ApprovalDecision, ApprovalMode, RunRequest
    from agentharness.harness import Harness

    async def auto(req):  # noqa: ANN001
        return (
            ApprovalDecision.deny
            if req.effect.value == "destructive"
            else ApprovalDecision.allow_run
        )

    h = Harness(data_dir=tmp_path / "data")
    h.set_approval_callback(auto)
    try:
        result = await h.run(
            RunRequest(
                message="Reply with exactly: LIVE_OK. No tools.",
                provider="anthropic",
                model=os.environ.get("ANTHROPIC_MODEL") or None,
                approval=ApprovalMode.auto,
                cwd=str(ROOT),
                tools=[],
            )
        )
        assert result.status.value == "completed", result.error
        assert (result.output or "").strip(), "empty output"
    finally:
        await h.aclose()
