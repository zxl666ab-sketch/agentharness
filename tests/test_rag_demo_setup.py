from __future__ import annotations

from pathlib import Path

from scripts.setup_rag_demo import build_demo, build_demo_async

from agentharness.api.execution import PendingApprovalBroker
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent, _fake_run_profile
from agentharness.procurement.service import ProcurementService


def test_demo_matching_run_is_require_human_with_checkpoint(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    info = build_demo(data_dir, force=True)
    harness = Harness(data_dir=data_dir)
    try:
        assert info["chunk_count"] == 5
        matching = harness.storage.get_run(info["matching_run_id"])
        assert matching is not None
        assert matching["status"] == "require_human"
        assert matching["finished_at"] is None
        assert harness.storage.load_checkpoint(info["matching_run_id"]) is not None
        service = ProcurementService(harness)
        detail = service.get_request(info["matching_id"])
        assert detail["knowledge_references"]
        assert detail["comparison"] is not None
    finally:
        harness.close()


async def test_demo_approval_resume_works(tmp_path: Path) -> None:
    """The UI 提交供应商审批 path (agent.approve -> resume) must not raise
    'not resumable from status completed' on the demo's analyzed request."""
    data_dir = tmp_path / "demo"
    info = await build_demo_async(data_dir, force=True)
    harness = Harness(data_dir=data_dir)
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(
        harness,
        service,
        run_profile=_fake_run_profile(),
        approval_broker=broker,
    )
    try:
        comparison = service.get_request(info["matching_id"])["comparison"]
        assert comparison is not None
        detail = await agent.approve(
            info["matching_id"],
            snapshot_id=comparison["id"],
            input_sha256=comparison["input_sha256"],
            quote_id=comparison["result"]["recommended_quote_id"],
            note="回归验收",
            actor="测试员",
        )
        assert detail["status"] == "approved"
    finally:
        broker.close()
        await agent.aclose()
        await harness.aclose()
