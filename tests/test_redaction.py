from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.contracts import ApprovalMode, RunRequest
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor


def test_sentinel_redaction():
    r = Redactor()
    secret = "SECRET_SENTINEL_9f3a2b1c0d"
    r.add_sentinel(secret)
    text = f"token is {secret} and more"
    out = r.redact_text(text)
    assert secret not in out
    assert "REDACTED" in out


def test_api_key_patterns():
    r = Redactor()
    s = "api_key=sk-abcdefghijklmnopqrstuvwxyz123456"
    out = r.redact_text(s)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out


def test_recursive_redaction_covers_keys_sets_and_bytes():
    secret = "SECRET_RECURSIVE_SENTINEL_86420"
    redactor = Redactor(extra_sentinels=[secret])

    redacted = redactor.redact_obj(
        {
            secret: [{"nested": {secret}}, (secret.encode("utf-8"),)],
        }
    )
    serialized = json.dumps(redacted, ensure_ascii=False, default=str)

    assert secret not in serialized
    assert "REDACTED" in serialized


def test_recursive_redaction_masks_values_under_sensitive_keys():
    redactor = Redactor()
    secrets = ["arbitrary-bearer-value-12345", "custom-api-key-value-67890"]

    redacted = redactor.redact_obj(
        {
            "headers": {
                "Authorization": f"Bearer {secrets[0]}",
                "x-api-key": secrets[1],
            },
            "budget": {"max_tokens": 1234},
        }
    )
    serialized = json.dumps(redacted, ensure_ascii=False)

    assert all(secret not in serialized for secret in secrets)
    assert redacted["budget"]["max_tokens"] == 1234
    assert serialized.count("[REDACTED]") == 2


@pytest.mark.asyncio
async def test_harness_storage_and_readonly_api_recursively_redact_all_run_fields(
    data_dir: Path, tmp_path: Path
):
    secret = "SECRET_RUN_FIELD_SENTINEL_97531"
    redactor = Redactor(extra_sentinels=[secret])
    workspace = tmp_path / f"workspace-{secret}"
    workspace.mkdir()
    harness = Harness(data_dir=data_dir, redactor=redactor)
    try:
        result = await harness.run(
            RunRequest(
                message=f"[fake:text]answer {secret}",
                provider="fake",
                model=f"model-{secret}",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                metadata={secret: {"nested": [{secret}]}},
            )
        )
        app = create_app(harness=harness)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [
                await client.get("/api/sessions"),
                await client.get(f"/api/sessions/{result.session_id}"),
                await client.get(f"/api/sessions/{result.session_id}/transcript"),
                await client.get("/api/runs"),
                await client.get(f"/api/runs/{result.run_id}"),
                await client.get(f"/api/runs/{result.run_id}/events"),
                await client.get(f"/api/runs/{result.run_id}/tree"),
            ]
        public_blob = "\n".join(response.text for response in responses)
        stored_blob = json.dumps(
            {
                "session": harness.get_session(result.session_id),
                "run": harness.get_run(result.run_id),
                "transcript": [
                    turn.model_dump(mode="json")
                    for turn in harness.get_session_transcript(result.session_id)
                ],
                "events": [
                    event.model_dump(mode="json")
                    for event in harness.get_events(run_id=result.run_id)
                ],
            },
            ensure_ascii=False,
            default=str,
        )
    finally:
        harness.close()

    assert secret not in stored_blob
    assert secret not in public_blob
    assert "REDACTED" in stored_blob
