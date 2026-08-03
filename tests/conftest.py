from __future__ import annotations

import os
from pathlib import Path

import pytest

# Keep offline tests isolated from any project-level .env credentials.
os.environ.setdefault("AGENTHARNESS_NO_DOTENV", "1")
os.environ.setdefault("AGENTHARNESS_PROCUREMENT_PROVIDER", "procurement_fake")

from agentharness.contracts import ApprovalDecision, ApprovalRequest
from agentharness.harness import Harness
from agentharness.security.redaction import Redactor
from tests.fake_provider import FakeModelAdapter


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def redactor() -> Redactor:
    return Redactor()


@pytest.fixture
def harness(data_dir: Path, redactor: Redactor) -> Harness:
    async def auto_approve(req: ApprovalRequest) -> ApprovalDecision:
        if req.effect.value == "destructive":
            return ApprovalDecision.deny
        return ApprovalDecision.allow_run

    h = Harness(data_dir=data_dir, redactor=redactor)
    h.register_provider("fake", FakeModelAdapter())
    h.set_approval_callback(auto_approve)
    yield h
    h.close()


@pytest.fixture
def ask_harness(data_dir: Path, redactor: Redactor) -> Harness:
    decisions: list[ApprovalDecision] = []

    async def cb(req: ApprovalRequest) -> ApprovalDecision:
        if decisions:
            return decisions.pop(0)
        return ApprovalDecision.allow_once

    h = Harness(data_dir=data_dir, redactor=redactor)
    h.register_provider("fake", FakeModelAdapter())
    h.set_approval_callback(cb)
    h._test_decisions = decisions  # type: ignore[attr-defined]
    yield h
    h.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# hello workspace\n", encoding="utf-8")
    (ws / "a.txt").write_text("alpha\n", encoding="utf-8")
    (ws / "b.txt").write_text("beta\n", encoding="utf-8")
    return ws
