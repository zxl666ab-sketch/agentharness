from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor
from agentharness.storage.artifacts import ArtifactStore


def test_artifact_summary_redacts_caller_supplied_secret(tmp_path):
    secret = "SECRET_SENTINEL_artifact_sum_99"
    r = Redactor()
    r.add_sentinel(secret)
    store = ArtifactStore(tmp_path / "arts", redactor=r)
    body = f"large body with {secret} " + ("x" * 5000)
    meta = store.put(body, summary=f"preview {secret} leaked")
    assert secret not in meta["summary"]
    assert "REDACTED" in meta["summary"]
    # Body file also redacted
    text = store.get_text(meta["sha256"])
    assert text is not None
    assert secret not in text


@pytest.mark.asyncio
async def test_large_tool_result_uses_redacted_artifact_through_api(
    data_dir, workspace
):
    secret = "SECRET_ENGINE_ARTIFACT_SENTINEL_44556"
    source = workspace / "large-secret.txt"
    source.write_text((f"line {secret}\n" + "x" * 300) * 30, encoding="utf-8")
    harness = Harness(
        data_dir=data_dir,
        redactor=Redactor(extra_sentinels=[secret]),
    )
    try:
        result = await harness.run(
            RunRequest(
                message="[fake:tools]read_file\n"
                + json.dumps({"path": "large-secret.txt"}),
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
        artifact_ids = [
            event.payload.get("artifact_id")
            for event in harness.get_events(result.run_id)
            if event.type == "tool_result" and event.payload.get("artifact_id")
        ]
        assert artifact_ids
        app = create_app(harness=harness)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/artifacts/{artifact_ids[0]}")
    finally:
        await harness.aclose()

    assert response.status_code == 200
    assert secret not in response.text
    assert "REDACTED" in response.text
    for path in (data_dir / "artifacts").rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
