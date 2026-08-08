from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.harness import Harness
from agentharness.procurement.costing import analysis_input_sha256
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import parse_quote
from agentharness.procurement.service import ProcurementError, ProcurementService
from agentharness.rag.reference import (
    EXPANDED_TOP_K,
    INJECTED_TOP_K,
    KNOWLEDGE_INJECTION_MAX_CHARS,
    injected_text,
)


def _service(data_dir: Path) -> tuple[Harness, ProcurementService, dict, list, str]:
    truth = load_frozen_truth()
    harness = Harness(data_dir=data_dir)
    service = ProcurementService(harness)
    request = service.create_request(
        {
            "title": "华东仓快递袋询价",
            "category": "ecommerce_packaging",
            "item_name": truth["request"]["item_name"],
            "quantity": truth["request"]["quantity"],
            "unit": truth["request"]["unit"],
            "specifications": truth["request"]["specifications"],
            "constraints": truth["request"]["constraints"],
        }
    )
    parsed = []
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        quote = service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
        parsed.append(quote)
    run_id = "rag-service-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    return harness, service, request, parsed, run_id


def _history_chunk(sha: str = "h" * 64, **overrides: object) -> dict:
    base = {
        "chunk_sha256": sha,
        "request_id": "history-request",
        "quote_id": "history-quote",
        "artifact_id": "history-art",
        "artifact_sha256": "9" * 64,
        "request_reference": "RFQ-20260601-HISTORY",
        "supplier_name": "华东优包",
        "item_name": "快递袋",
        "category": "ecommerce_packaging",
        "specifications": {
            "width_mm": "250",
            "length_mm": "350",
            "thickness_um": "60",
            "material": "PE",
            "color": "白色",
            "print_colors": 1,
        },
        "unit_price": "0.40",
        "currency": "CNY",
        "landed_unit_cost": "0.4300",
        "lead_days": 8,
        "moq": 5000,
        "decision": "approved",
        "decision_at": "2026-06-01T00:00:00+00:00",
        "content": "快递袋 250x350mm PE 白色 单色 0.40 CNY",
        "quality_flags": [],
        "created_at": "2026-06-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_pipeline_includes_knowledge_references_and_audit(data_dir: Path) -> None:
    harness, service, request, _quotes, run_id = _service(data_dir)
    try:
        harness.storage.rag.upsert_chunk(_history_chunk())
        result = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        references = result["knowledge_references"]
        assert len(references) == 1
        reference = references[0]
        for key in (
            "chunk_id",
            "request_reference",
            "decision_at",
            "supplier_name",
            "item_name",
            "specification_summary",
            "unit_price",
            "landed_unit_cost",
            "decision",
            "source_sha256",
            "score",
        ):
            assert reference.get(key) not in (None, "")
        assert reference["request_reference"] == "RFQ-20260601-HISTORY"
        assert reference["decision"] == "approved"
        assert reference["chunk_id"] == "h" * 16

        detail = service.get_request(str(request["id"]))
        assert detail["knowledge_references"] == references

        events = service.repo.list_audit_events(str(request["id"]))
        retrieved = [event for event in events if event["type"] == "knowledge_retrieved"]
        assert len(retrieved) == 1
        payload = retrieved[0]["payload"]
        assert payload["count"] == 1
        assert payload["injected_count"] == 1
        assert payload["references"][0]["chunk_id"] == "h" * 16
    finally:
        harness.close()


def test_pipeline_empty_history_gives_empty_references(data_dir: Path) -> None:
    harness, service, request, _quotes, run_id = _service(data_dir)
    try:
        result = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        assert result["knowledge_references"] == []
        assert service.get_request(str(request["id"]))["knowledge_references"] == []
    finally:
        harness.close()


def test_tiered_injection_top3_budget_assertion(data_dir: Path) -> None:
    harness, service, request, _quotes, run_id = _service(data_dir)
    try:
        for idx in range(6):
            harness.storage.rag.upsert_chunk(
                _history_chunk(
                    sha=f"{idx:064x}",
                    request_reference=f"RFQ-2026060{idx + 1}-HISTORY",
                    decision_at=f"2026-06-0{idx + 1}T00:00:00+00:00",
                )
            )
        result = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        references = result["knowledge_references"]
        assert len(references) == EXPANDED_TOP_K == 5
        injected = injected_text(references, top_k=INJECTED_TOP_K)
        assert len(injected) <= KNOWLEDGE_INJECTION_MAX_CHARS
        assert injected != ""
        # 模型注入必须是 top-3 紧凑文本，而不是把 top-5 全量参考发给模型。
        assert result["knowledge_injection"] == injected
        assert result["knowledge_injection"].startswith(references[0].get("text") or "")
        assert (references[4].get("text") or "") not in result["knowledge_injection"]
    finally:
        harness.close()


def test_feedback_events_recorded_and_validate(data_dir: Path) -> None:
    harness, service, request, _quotes, run_id = _service(data_dir)
    try:
        harness.storage.rag.upsert_chunk(_history_chunk())
        service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        chunk_id = "h" * 64
        viewed = service.record_knowledge_feedback(
            str(request["id"]), chunk_id=chunk_id, action="viewed"
        )
        assert viewed["ok"] is True
        adopted = service.record_knowledge_feedback(
            str(request["id"]), chunk_id=chunk_id, action="adopted"
        )
        assert adopted["ok"] is True
        events = [
            event
            for event in service.repo.list_audit_events(str(request["id"]))
            if event["type"].startswith("knowledge_reference_")
        ]
        assert {event["type"] for event in events} == {
            "knowledge_reference_viewed",
            "knowledge_reference_adopted",
        }
        for event in events:
            assert set(event["payload"].keys()) == {"chunk_id", "action"}

        with pytest.raises(ProcurementError):
            service.record_knowledge_feedback(
                str(request["id"]), chunk_id=chunk_id, action="favorite"
            )
        with pytest.raises(ProcurementError):
            service.record_knowledge_feedback(
                str(request["id"]), chunk_id="short", action="viewed"
            )
        with pytest.raises(ProcurementError):
            service.record_knowledge_feedback(
                str(request["id"]), chunk_id="0" * 64, action="viewed"
            )
        counts = service._knowledge_adopted_counts()  # noqa: SLF001 - white-box
        assert counts.get("华东优包") == 1
    finally:
        harness.close()


def test_history_changes_do_not_affect_input_sha256(data_dir: Path) -> None:
    harness, service, request, quotes, run_id = _service(data_dir)
    try:
        baseline = service.compare_for_agent(str(request["id"]), run_id=run_id)
        baseline_hash = str(baseline["input_sha256"])

        # Historical data changes after the snapshot must not invalidate it.
        harness.storage.rag.upsert_chunk(_history_chunk())
        recheck = analysis_input_sha256(
            service.repo.get_request(str(request["id"])),
            [dict(quote) for quote in service.repo.list_quotes(str(request["id"]))],
            analysis_as_of=baseline["result"]["analysis_as_of"],
        )
        assert recheck == baseline_hash

        # A full pipeline run that injects knowledge must keep the same hash.
        result = service.execute_analysis_pipeline(str(request["id"]), run_id=run_id)
        assert result["knowledge_references"]
        assert result["snapshot"]["input_sha256"] == baseline_hash
        assert result["verification"]["verified"] is True
        assert len(quotes) == 2
    finally:
        harness.close()
