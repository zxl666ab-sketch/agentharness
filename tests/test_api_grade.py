"""Narrow manual-grade API; all unrelated writes remain rejected."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.providers.fake import FakeModelAdapter


def _judge_json(score: float = 0.9) -> str:
    dimension = {"score": score, "reason": "good", "applicable": True}
    return json.dumps(
        {
            "dimensions": {
                "task_completion": dimension,
                "correctness": dimension,
                "completeness": dimension,
                "planning_recovery": {**dimension, "applicable": False},
                "tool_use": {**dimension, "applicable": False},
                "execution_verification": {**dimension, "applicable": False},
                "efficiency": dimension,
                "safety_control": dimension,
                "user_experience": dimension,
            },
            "hard_safety_violation": False,
            "confidence": 0.88,
            "failure_category": "none",
            "evidence": ["answer addresses request"],
            "improvements": ["none"],
        }
    )


@pytest.mark.asyncio
async def test_manual_grade_endpoint(harness) -> None:
    graded = await harness.run(
        RunRequest(
            message="[fake:text]grade me",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"contains": ["grade me"]}},
        )
    )
    no_rule = await harness.run(
        RunRequest(
            message="[fake:text]plain",
            provider="fake",
            approval=ApprovalMode.never,
        )
    )
    hard_fail = await harness.run(
        RunRequest(
            message="[fake:text]actual output",
            provider="fake",
            approval=ApprovalMode.never,
            metadata={"eval_assert": {"contains": ["required but absent"]}},
        )
    )
    app = create_app(harness=harness)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/runs/{graded.run_id}/grade")
        assert response.status_code == 200
        body = response.json()
        assert body["eval"]["passed"] is True
        assert body["run"]["id"] == graded.run_id

        health_grade = await client.post(f"/api/runs/{no_rule.run_id}/grade")
        assert health_grade.status_code == 200
        assert health_grade.json()["eval"]["mode"] == "deterministic"
        health_detail = await client.get(
            f"/api/runs/{no_rule.run_id}/evaluation"
        )
        assert health_detail.status_code == 200
        health_payload = health_detail.json()
        assert health_payload["schema_version"] == 2
        assert health_payload["report"]["mode"] == "health_only"
        assert health_payload["report"]["score"] is None
        assert health_payload["trace"]["run_id"] == no_rule.run_id
        assert health_payload["ids"]["trace_id"] == health_payload["trace"]["trace_id"]
        assert health_payload["ids"]["report_id"] == health_payload["report"]["report_id"]
        assert health_payload["ids"]["snapshot_id"]
        assert health_payload["judge"]["status"] == "unverified"

        harness.register_provider(
            "fake", FakeModelAdapter(script=[{"kind": "text", "text": _judge_json()}])
        )
        ai_grade = await client.post(
            f"/api/runs/{no_rule.run_id}/grade", json={"mode": "ai"}
        )
        assert ai_grade.status_code == 200
        evaluation = ai_grade.json()["eval"]
        assert evaluation["mode"] == "ai"
        assert evaluation["passed"] is True
        assert evaluation["score"] == 0.9
        assert evaluation["failure_category"] == "none"
        assert evaluation["confidence"] == 0.88

        harness.register_provider(
            "fake", FakeModelAdapter(script=[{"kind": "text", "text": _judge_json()}])
        )
        hard_grade = await client.post(
            f"/api/runs/{hard_fail.run_id}/grade", json={"mode": "ai"}
        )
        assert hard_grade.status_code == 200
        assert hard_grade.json()["eval"]["passed"] is False
        hard_detail = await client.get(
            f"/api/runs/{hard_fail.run_id}/evaluation"
        )
        assert hard_detail.status_code == 200
        hard_payload = hard_detail.json()
        assert hard_payload["diagnosis"]["root_cause"] != "none"
        assert hard_payload["diagnosis"]["read_only"] is True
        assert hard_payload["ids"]["diagnosis_id"]
        assert hard_payload["judge"]["status"] == "unverified"

        missing_run = await client.post("/api/runs/does-not-exist/grade")
        assert missing_run.status_code == 404
        missing_evaluation = await client.get(
            "/api/runs/does-not-exist/evaluation"
        )
        assert missing_evaluation.status_code == 404

        unrelated = await client.post(f"/api/runs/{graded.run_id}/messages")
        assert unrelated.status_code == 405
