from __future__ import annotations

import json
from pathlib import Path

from agentharness.contracts import RunStatus
from agentharness.security.redaction import Redactor
from agentharness.storage.sqlite import Storage


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


def test_personal_absolute_paths_are_redacted_without_hiding_relative_paths() -> None:
    redactor = Redactor()
    text = (
        r"Windows C:\Users\private\project\notes.txt and D:\work\private-project "
        "plus /home/private/project/config.json and relative notes/readme.md"
    )
    redacted = redactor.redact_public_text(text)
    assert "C:\\Users\\private" not in redacted
    assert "D:\\work\\private-project" not in redacted
    assert "/home/private/project" not in redacted
    assert redacted.count("[REDACTED_PATH]") == 3
    assert "notes/readme.md" in redacted


def test_persisted_approval_scope_is_redacted(data_dir: Path) -> None:
    secret = "SECRET_APPROVAL_SCOPE_TOKEN_24680"
    storage = Storage(data_dir, redactor=Redactor(extra_sentinels=[secret]))
    try:
        session_id = storage.create_session("approval-session")
        storage.create_run(
            run_id="approval-run",
            session_id=session_id,
            root_run_id="approval-run",
            status=RunStatus.waiting_approval,
        )
        storage.save_approval(
            {
                "id": "approval",
                "run_id": "approval-run",
                "tool_call_id": "call",
            "tool_name": "procurement_approve_supplier",
            "effect": "destructive",
            "invocation_id": "invocation",
            "arguments_sha256": "a" * 64,
            "approval_scope": (
                "procurement_approve_supplier:destructive:"
                f"token={secret}"
            ),
            }
        )
        scope = storage.list_approvals("approval-run")[0]["approval_scope"]
        assert secret not in scope
        assert "REDACTED" in scope
    finally:
        storage.close()

# Redaction coverage ends at the procurement approval boundary.


def test_compound_sensitive_key_names_are_redacted():
    """Normalized containment matching must catch compound key names."""
    redactor = Redactor()
    redacted = redactor.redact_obj(
        {
            "openai_api_key": "sk-openai-secret-1234",
            "github_token": "ghp_github_secret_1234",
            "aws_secret_access_key": "AKIAIOSFODNN7EXAMPLE",
            "x-api-key": "custom-x-api-key-value-123",
            "keywords": "plain search keyword - keep me",
            "max_tokens": 1234,
        }
    )
    assert redacted["openai_api_key"] == "[REDACTED]"
    assert redacted["github_token"] == "[REDACTED]"
    assert redacted["aws_secret_access_key"] == "[REDACTED]"
    assert redacted["x-api-key"] == "[REDACTED]"
    assert redacted["keywords"] == "plain search keyword - keep me"
    assert redacted["max_tokens"] == 1234


def test_sk_prefixed_keys_with_vendor_prefixes_are_redacted():
    redactor = Redactor()
    proj = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    ant = "sk-ant-abcdefghijklmnopqrstuvwxyz123456"
    out = redactor.redact_text(proj)
    assert proj not in out
    assert "[REDACTED_API_KEY]" in out
    out = redactor.redact_text(ant)
    assert ant not in out
    assert "[REDACTED_API_KEY]" in out
    out = redactor.redact_text(f"Bearer {proj}")
    assert proj not in out
    assert "[REDACTED_API_KEY]" in out
