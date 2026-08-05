from __future__ import annotations

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
