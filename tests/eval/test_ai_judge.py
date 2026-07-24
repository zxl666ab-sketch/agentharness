from __future__ import annotations

import json

import pytest

from agentharness.eval.ai_judge import judge_trajectory
from agentharness.providers.fake import FakeModelAdapter
from agentharness.security.redaction import default_redactor


def _valid_verdict() -> str:
    scored = {"score": 0.8, "reason": "supported", "applicable": True}
    return json.dumps(
        {
            "dimensions": {
                "task_completion": scored,
                "correctness": scored,
                "completeness": scored,
                "planning_recovery": {**scored, "applicable": False},
                "tool_use": {**scored, "applicable": False},
                "execution_verification": {**scored, "applicable": False},
                "efficiency": scored,
                "safety_control": scored,
                "user_experience": scored,
            },
            "hard_safety_violation": False,
            "confidence": 0.75,
            "failure_category": "none",
            "evidence": ["direct answer"],
            "improvements": [],
        }
    )


@pytest.mark.asyncio
async def test_ai_judge_repairs_invalid_json_once() -> None:
    adapter = FakeModelAdapter(
        script=[
            {"kind": "text", "text": "not json"},
            {"kind": "text", "text": _valid_verdict()},
        ]
    )
    result = await judge_trajectory(
        adapter,
        model="fake",
        trajectory={"user_request": "hi", "assistant_output": "hello"},
        redactor=default_redactor,
        timeout_s=1,
    )
    assert result.verdict.confidence == 0.75
    assert len(adapter.calls) == 2
    assert adapter.calls[0].tools == []


@pytest.mark.asyncio
async def test_ai_judge_rejects_two_invalid_responses() -> None:
    adapter = FakeModelAdapter(
        script=[
            {"kind": "text", "text": "bad"},
            {"kind": "text", "text": "still bad"},
        ]
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        await judge_trajectory(
            adapter,
            model="fake",
            trajectory={},
            redactor=default_redactor,
            timeout_s=1,
        )
