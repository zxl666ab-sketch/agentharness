from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from scripts.rebuild_rag_index import rebuild

from agentharness.harness import Harness
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.parsing import parse_quote
from agentharness.procurement.service import ProcurementError, ProcurementService


def _approved_request(
    data_dir: Path,
) -> tuple[Harness, ProcurementService, dict, dict, str, str]:
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
    for case in truth["quotes"][:2]:
        document = build_case_document(case)
        service.import_quote(
            str(request["id"]),
            filename=case["filename"],
            data=document,
            extracted=parse_quote(case["filename"], document),
        )
    run_id = "rag-indexing-run"
    harness.storage.create_run(
        run_id=run_id,
        session_id=str(request["session_id"]),
        root_run_id=run_id,
    )
    snapshot = service.compare_for_agent(str(request["id"]), run_id=run_id)
    selected_id = str(snapshot["result"]["recommended_quote_id"])
    return harness, service, request, snapshot, selected_id, run_id


def test_approval_writes_chunk_atomically(data_dir: Path) -> None:
    harness, service, request, snapshot, selected_id, run_id = _approved_request(
        data_dir
    )
    try:
        assert harness.storage.rag.count_chunks() == 0
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="approval-rag-1",
            note="同意",
            actor="采购员",
        )
        chunks = harness.storage.rag.list_chunks()
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk["decision"] == "approved"
        assert chunk["request_reference"] == request["reference"]
        assert chunk["item_name"] == "快递袋"
        assert chunk["supplier_name"] != ""
        assert chunk["supplier_name"] in {"Alpha Packaging", "Beta Packaging"}
        assert chunk["unit_price"] is not None
        assert chunk["landed_unit_cost"] is not None
        assert chunk["artifact_sha256"] != ""
        assert chunk["quality_flags"] == []
        assert chunk["lead_days"] is not None
        assert chunk["moq"] is not None
    finally:
        harness.close()


def test_approval_failure_does_not_write_index(data_dir: Path) -> None:
    harness, service, request, snapshot, selected_id, run_id = _approved_request(
        data_dir
    )
    try:
        with harness.storage._lock:  # noqa: SLF001 - transaction fault injection
            harness.storage._conn.execute(  # noqa: SLF001
                """CREATE TRIGGER fail_rag_audit
                   BEFORE INSERT ON procurement_audit_events
                   WHEN NEW.type = 'supplier_approved'
                   BEGIN
                       SELECT RAISE(ABORT, 'forced rag audit failure');
                   END"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="forced rag audit failure"):
            service.approve_supplier_from_agent(
                str(request["id"]),
                snapshot_id=str(snapshot["id"]),
                input_sha256=str(snapshot["input_sha256"]),
                quote_id=selected_id,
                run_id=run_id,
                approval_id="approval-rag-fail",
                note=None,
                actor="采购员",
            )
        assert harness.storage.rag.count_chunks() == 0
        assert service.get_request(str(request["id"]))["decision"] is None
    finally:
        harness.close()


def test_corrected_facts_flow_into_chunk(data_dir: Path) -> None:
    harness, service, request, snapshot, selected_id, run_id = _approved_request(
        data_dir
    )
    try:
        quote = next(
            item
            for item in service.get_request(str(request["id"]))["quotes"]
            if item["id"] == selected_id
        )
        corrected = service.correct_field(
            str(request["id"]),
            str(quote["id"]),
            field="supplier_name",
            value="星河包装",
            actor="采购员",
        )
        assert corrected["supplier_name"] == "星河包装"
        snapshot2 = service.compare_for_agent(str(request["id"]), run_id=run_id)
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot2["id"]),
            input_sha256=str(snapshot2["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="approval-rag-2",
            note=None,
            actor="采购员",
        )
        chunks = harness.storage.rag.list_chunks()
        assert len(chunks) == 1
        assert chunks[0]["supplier_name"] == "星河包装"
        assert "corrected" in chunks[0]["quality_flags"]
    finally:
        harness.close()


def test_sync_rag_chunk_after_quote_change(data_dir: Path) -> None:
    harness, service, request, snapshot, selected_id, run_id = _approved_request(
        data_dir
    )
    try:
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="approval-rag-3",
            note=None,
            actor="采购员",
        )
        assert harness.storage.rag.count_chunks() == 1
        quote = next(
            item
            for item in service.get_request(str(request["id"]))["quotes"]
            if item["id"] == selected_id
        )
        changed = dict(quote)
        changed["extracted"] = dict(quote["extracted"])
        changed["extracted"]["fields"] = dict(quote["extracted"]["fields"])
        price_entry = dict(changed["extracted"]["fields"]["unit_price"])
        price_entry["value"] = "0.99"
        changed["extracted"]["fields"]["unit_price"] = price_entry
        old_hash = harness.storage.rag.list_chunks()[0]["chunk_sha256"]

        # Same sync path used by correct_field on a business fact change.
        service._sync_rag_chunk_for_quote(  # noqa: SLF001 - white-box sync test
            service.repo.get_request(str(request["id"])),
            changed,
        )
        chunks = harness.storage.rag.list_chunks()
        assert len(chunks) == 1
        assert chunks[0]["unit_price"] == "0.99"
        assert chunks[0]["chunk_sha256"] != old_hash
    finally:
        harness.close()


def test_rebuild_script_is_idempotent_and_backfills(data_dir: Path) -> None:
    harness, service, request, snapshot, selected_id, run_id = _approved_request(
        data_dir
    )
    try:
        service.approve_supplier_from_agent(
            str(request["id"]),
            snapshot_id=str(snapshot["id"]),
            input_sha256=str(snapshot["input_sha256"]),
            quote_id=selected_id,
            run_id=run_id,
            approval_id="approval-rag-4",
            note=None,
            actor="采购员",
        )
        expected = {
            chunk["chunk_sha256"] for chunk in harness.storage.rag.list_chunks()
        }
        assert len(expected) == 1

        # Simulate a lost/partial index, then backfill with the rebuild script.
        harness.storage.rag.delete_chunks_for_request(str(request["id"]))
        assert harness.storage.rag.count_chunks() == 0
        first = rebuild(data_dir)
        second = rebuild(data_dir)
        assert first["indexed"] == 1
        assert second["indexed"] == 1
        chunks = harness.storage.rag.list_chunks()
        assert {chunk["chunk_sha256"] for chunk in chunks} == expected
        assert len(chunks) == 1
    finally:
        harness.close()


def test_no_award_does_not_write_index(data_dir: Path) -> None:
    harness, service, request, snapshot, _selected_id, _run_id = _approved_request(
        data_dir
    )
    try:
        # The snapshot has eligible quotes, so no_award is rejected; nothing is
        # ever indexed for a non-approved outcome.
        with pytest.raises(ProcurementError):
            service.record_no_award(
                str(request["id"]),
                snapshot_id=str(snapshot["id"]),
                input_sha256=str(snapshot["input_sha256"]),
                note=None,
                actor="采购员",
            )
        assert harness.storage.rag.count_chunks() == 0
    finally:
        harness.close()
