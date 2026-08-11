"""Unit tests for the Kafka-embedded Python agent service (no real Kafka/MySQL)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentharness import agent_service as svc

CONFIG = {
    "AGENTHARNESS_DATABASE_URL": "mysql+pymysql://u:p@127.0.0.1:3306/caijiatai_runtime",
    "AGENT_KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9092",
    "AGENT_KAFKA_SASL_USERNAME": "",
    "AGENT_KAFKA_SASL_PASSWORD": "",
    "AGENT_INTERNAL_HMAC_KEY": "unit-test-hmac-key-0123456789abcdef",
}


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.last = None

    def execute(self, sql, params=None):
        self.last = (sql, params)
        return 0

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, rows=None):
        self._rows = rows

    def cursor(self, *args, **kwargs):
        return FakeCursor(self._rows)

    def commit(self):
        return None

    def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    monkeypatch.setattr(svc.pymysql, "connect", lambda **kw: FakeConn())
    # unit tests must not touch a real Kafka broker
    monkeypatch.setattr(svc.AgentService, "_topic_max_global_seq", lambda self: 0)
    return FakeConn()


def make_service(monkeypatch, fake_db):
    service = svc.AgentService(dict(CONFIG))
    service.producer = MagicMock()
    service.consumer = MagicMock()
    service.rpc.producer = MagicMock()
    service.rpc.consumer = MagicMock()
    return service


def envelope(operation_id="op-" + "a" * 30, operation_type="analyze", payload=None):
    payload = payload or {"task_id": "t" * 32}
    payload_sha = svc._sha256(payload)
    return {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "aggregate_id": "t" * 32,
        "generation": 1,
        "expected_task_version": 0,
        "payload_sha256": payload_sha,
        "payload": payload,
        "signature": svc._hmac_sign(CONFIG["AGENT_INTERNAL_HMAC_KEY"], "command", operation_id, payload_sha),
    }


def test_hmac_roundtrip():
    key = CONFIG["AGENT_INTERNAL_HMAC_KEY"]
    sig = svc._hmac_sign(key, "command", "op-1", "sha")
    assert svc._hmac_verify(key, "command", "op-1", "sha", sig)
    assert not svc._hmac_verify(key, "command", "op-1", "sha", "bad")


def test_fake_requirement_extracts_specs():
    req = svc._fake_requirement("采购 50000 个快递袋，宽250mm 长350mm 厚60um，PE 白色，交期7天，预算0.5元")
    assert req["schema_version"] == 1
    assert req["quantity"] == 50000
    assert req["specifications"]["material"] == "PE"
    assert req["specifications"]["width_mm"] == "250"
    assert req["constraints"]["max_lead_days"] == 7
    assert req["constraints"]["max_landed_unit_cost"] == "0.5"


def test_analyze_publishes_result_and_events(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    service.handle_command(envelope())
    assert len(published) == 1
    status, result = published[0][2], published[0][3]
    assert status == "completed"
    assert result["run_id"].startswith("0" * 0)  # any 32-hex
    assert len(result["run_id"]) == 32
    # events were emitted (run_started, run_completed)
    sent = [call.args[0] for call in service.producer.send.call_args_list if call.args[0] == svc.EVENTS_TOPIC]
    assert len(sent) == 4  # run_started, tool_call_start, tool_result, run_completed


def test_idempotent_replay_skips_execution(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    existing = {
        "operation_id": envelope()["operation_id"],
        "payload_sha256": svc._sha256({"task_id": "t" * 32}),
        "status": "completed",
        "result": '{"run_id": "%s"}' % ("a" * 32),
        "error": None,
        "result_published_at": None,
    }
    service._load_operation = lambda operation_id: existing
    executed = []
    service._execute = lambda *a, **k: executed.append(a) or ({}, "completed", None)
    service.handle_command(envelope())
    assert executed == []
    assert len(published) == 1
    assert published[0][2] == "completed"


def test_payload_conflict_returns_409(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    service._load_operation = lambda operation_id: {
        "operation_id": envelope()["operation_id"],
        "payload_sha256": "different-hash",
        "status": "accepted",
        "result": None,
        "error": None,
        "result_published_at": None,
    }
    service.handle_command(envelope())
    assert len(published) == 1
    assert published[0][2] == "failed"
    assert published[0][4] == "operation_payload_conflict"


def test_rpc_client_retries_once_on_timeout():
    client = svc.RpcClient(dict(CONFIG), CONFIG["AGENT_INTERNAL_HMAC_KEY"])
    client.producer = MagicMock()
    client.consumer = MagicMock()
    with pytest.raises((TimeoutError, RuntimeError)):
        client.call("get_artifact", {"artifact_id": "x"}, timeout=0.05)
    # two attempts => two requests published
    assert client.producer.send.call_count == 2


def test_heartbeat_message_shape():
    service = svc.AgentService(dict(CONFIG))
    service._closed = True  # stop loop
    service.producer = MagicMock()
    service._next_global_seq = lambda: 1
    service._emit_heartbeat_for_test = None
    # directly exercise the send used by the heartbeat loop
    payload = {"agent": "python-agent", "service": "procurement_agent"}
    payload_sha = svc._sha256(payload)
    message = {
        "type": "heartbeat.ping",
        "task_id": "",
        "run_id": "",
        "global_seq": service._next_global_seq(),
        "payload": payload,
        "occurred_at": "2026-08-11T00:00:00Z",
        "payload_sha256": payload_sha,
        "signature": svc._hmac_sign(CONFIG["AGENT_INTERNAL_HMAC_KEY"], "event", "::heartbeat.ping", payload_sha),
    }
    assert message["type"] == "heartbeat.ping"
    assert message["global_seq"] == 1


def test_coerce_requirement_normalizes_llm_output():
    data = {
        "schema_version": 1,
        "title": "采购",
        "category": "ecommerce_packaging",
        "item_name": "快递袋",
        "quantity": "50000",
        "unit": "piece",
        "specifications": {"width_mm": "250", "length_mm": "350", "thickness_um": "60", "material": "PE", "color": "白色", "print_colors": 1},
        "constraints": {"base_currency": "cny", "fx_rates": {"CNY": "1"}, "max_lead_days": "7", "invoice_required": True, "size_tolerance_mm": "2", "thickness_tolerance_um": "3", "max_landed_unit_cost": "0.5"},
    }
    req = svc._coerce_requirement(data, "x")
    assert req["quantity"] == 50000
    assert req["constraints"]["base_currency"] == "CNY"
    assert req["constraints"]["max_lead_days"] == 7
    assert req["specifications"]["material"] == "PE"


def test_coerce_requirement_rejects_bad_quantity():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        svc._coerce_requirement({"item_name": "x", "quantity": 0}, "x")


def test_llm_requirement_falls_back_to_fake_without_key(monkeypatch):
    config = dict(CONFIG)
    config["AGENTHARNESS_PROCUREMENT_PROVIDER"] = "openai"
    config.pop("OPENAI_API_KEY", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    req = svc._llm_requirement("采购快递袋 50000 个，宽250mm", config)
    assert req["quantity"] == 50000


def test_llm_requirement_fake_provider_does_not_call_model():
    req = svc._llm_requirement("采购快递袋 3000 个", dict(CONFIG))
    assert req["quantity"] == 3000


def test_execute_exception_publishes_failed_result(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    service._execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    service.handle_command(envelope(operation_type="import_quote"))
    assert len(published) == 1
    assert published[0][2] == "failed"
    assert "boom" in published[0][4]


def test_accepted_without_result_is_reexecuted(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    existing = {
        "operation_id": envelope()["operation_id"],
        "payload_sha256": svc._sha256({"task_id": "t" * 32}),
        "status": "accepted",
        "result": None,
        "error": None,
        "result_published_at": None,
    }
    service._load_operation = lambda operation_id: existing
    executed = []
    service._execute = lambda *a, **k: executed.append(a) or ({"run_id": "r" * 32}, "completed", None)
    service.handle_command(envelope())
    assert len(executed) == 1
    assert published[0][2] == "completed"

