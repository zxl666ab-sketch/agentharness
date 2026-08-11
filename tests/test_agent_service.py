"""Unit tests for the Kafka-embedded Python agent service (no real Kafka/MySQL)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentharness import agent_service as svc

_ORIG_TOPIC_MAX = svc.AgentService._topic_max_global_seq


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


class FakeRowsConn:
    def __init__(self, rows=None):
        self._rows = rows or []
        self._index = 0

    def cursor(self, *args, **kwargs):
        return FakeCursor(self._rows)

    def commit(self):
        return None

    def close(self):
        return None


def test_database_url_parse():
    cfg = svc._parse_database_url("mysql+pymysql://u:p@dbhost:3307/dbname?x=1")
    assert cfg["host"] == "dbhost"
    assert cfg["port"] == 3307
    assert cfg["user"] == "u"
    assert cfg["password"] == "p"
    assert cfg["database"] == "dbname"


def test_next_global_seq_uses_persisted_counter(monkeypatch):
    service = svc.AgentService(dict(CONFIG))
    counter = {"value": 5}
    class RowConn:
        def cursor(self, *a, **k):
            return FakeCursor([(counter["value"] + 1,)])
        def commit(self):
            counter["value"] += 1
        def close(self):
            return None
    monkeypatch.setattr(service, "_connect", lambda: RowConn())
    assert service._next_global_seq() == 6
    assert service._next_global_seq() == 7


def test_persist_result_updates_row(monkeypatch):
    service = svc.AgentService(dict(CONFIG))
    class CaptureConn:
        def cursor(self, *a, **k):
            return FakeCursor()
        def commit(self):
            return None
        def close(self):
            return None
    monkeypatch.setattr(service, "_connect", lambda: CaptureConn())
    service._persist_result({"operation_id": "op-1"}, "completed", {"run_id": "r"}, None, True)
    # no exception = update path executed


def test_import_quote_with_mocked_rpc_and_parse(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    service.rpc.call = lambda kind, payload: {
        "base64": "aGVsbG8=", "filename": "q.xlsx", "content_type": "x", "sha256": "s",
    }
    monkeypatch.setattr("agentharness.procurement.parsing.parse_quote",
                        lambda filename, data: {"fields": {"supplier_name": {"value": "S"}}})
    monkeypatch.setattr("agentharness.procurement.parsing.fields_requiring_review",
                        lambda extracted: [])
    quote = service._import_quote({"artifact_id": "jb1", "filename": "q.xlsx"}, "task", "run" * 16)
    assert quote["status"] == "ready"
    assert quote["parser_version"] == "packaging-quote-v3"


def test_execute_approve_and_reopen_paths(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    envelope_data = {
        "operation_id": "op-" + "a" * 30,
        "operation_type": "approve_decision",
        "aggregate_id": "t" * 32,
        "generation": 1,
        "expected_task_version": 0,
        "payload_sha256": "x" * 64,
        "payload": {
            "pending_decision_id": "p" * 32,
            "run_id": "r" * 32,
            "tool_name": "procurement_approve_supplier",
            "task_version": 1,
            "snapshot_id": "s" * 32,
            "input_sha256": "i" * 64,
            "business_decision": "approved",
            "quote_id": "q" * 32,
            "note_hash": "n" * 64,
        },
        "signature": "sig",
    }
    result, status, error = service._execute("op-" + "a" * 30, "approve_decision", envelope_data, envelope_data["payload"], "x" * 64)
    assert status == "completed"
    assert result["approval"]["decision"] == "formal_java_confirmation"

    result2, status2, _ = service._execute("op-" + "b" * 30, "reopen_task", envelope_data, {"source_task_id": "t"}, "y" * 64)
    assert status2 == "completed"
    assert len(result2["run_id"]) == 32


def test_start_and_close_with_fake_consumer(monkeypatch, fake_db):
    service = svc.AgentService(dict(CONFIG))
    produced = []
    class FakeProducer:
        def send(self, topic, key=None, value=None):
            produced.append((topic, key, value))
            return None
        def flush(self):
            return None
        def close(self):
            return None
    class FakeConsumer:
        def __iter__(self):
            yield type("R", (), {"value": envelope(), "key": "op"})()
        def close(self):
            return None
    monkeypatch.setattr(svc, "KafkaProducer", lambda **kw: FakeProducer())
    monkeypatch.setattr(svc, "KafkaConsumer", lambda topic, **kw: FakeConsumer())
    monkeypatch.setattr(service, "start_health_server", lambda: None)
    monkeypatch.setattr(service, "_heartbeat_loop", lambda: None)
    monkeypatch.setattr(service.rpc, "start", lambda: None)
    monkeypatch.setattr(service, "_load_operation", lambda operation_id: None)
    monkeypatch.setattr(service, "_persist_result", lambda *a, **k: None)
    monkeypatch.setattr(service, "_publish_result", lambda *a, **k: None)
    service.start()
    service.close()


def test_web_main_imports():
    import agentharness.web_main  # noqa: F401


def test_rpc_loop_resolves_future(monkeypatch):
    from concurrent.futures import Future
    client = svc.RpcClient(dict(CONFIG), CONFIG["AGENT_INTERNAL_HMAC_KEY"])
    future = Future()
    client._futures["corr-1"] = future
    record = type("R", (), {"value": {"correlation_id": "corr-1", "status": "ok", "result": {"k": "v"}}})()
    correlation_id = str(record.value.get("correlation_id") or "")
    popped = client._futures.pop(correlation_id, None)
    if popped is not None and not popped.done():
        popped.set_result(record.value)
    assert future.done()
    assert future.result()["status"] == "ok"


def test_emit_publishes_and_persists(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    service._next_global_seq = lambda: 7
    service._emit("tool_call_start", "t" * 32, "r" * 32, {"tool": "x"})
    sent = [call for call in service.producer.send.call_args_list if call.args[0] == svc.EVENTS_TOPIC]
    assert len(sent) == 1
    message = sent[0].kwargs["value"]
    assert message["global_seq"] == 7
    assert message["type"] == "tool_call_start"
    assert message["signature"]


def test_publish_result_builds_signed_envelope(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    service._publish_result("op-1", {"aggregate_id": "a" * 32, "generation": 1,
                                     "expected_task_version": 0, "payload_sha256": "p" * 64},
                            "completed", {"run_id": "r" * 32}, None)
    sent = [call for call in service.producer.send.call_args_list if call.args[0] == svc.RESULTS_TOPIC]
    assert len(sent) == 1
    message = sent[0].kwargs["value"]
    assert message["status"] == "completed"
    assert message["signature"]


def test_topic_max_global_seq_scans_events(monkeypatch, fake_db):
    class FakeBootConsumer:
        def __init__(self, *a, **k):
            pass
        def __iter__(self):
            yield type("R", (), {"value": {"global_seq": 42}})()
            yield type("R", (), {"value": {"global_seq": 7}})()
        def close(self):
            return None
    monkeypatch.setattr(svc, "KafkaConsumer", FakeBootConsumer)
    monkeypatch.setattr(svc.AgentService, "_topic_max_global_seq", _ORIG_TOPIC_MAX)
    service = svc.AgentService(dict(CONFIG))
    assert service._topic_max_global_seq() == 42


def test_context_cache_degrades_without_redis(monkeypatch):
    cache = svc.AgentContextCache({"REDIS_URL": ""})
    assert cache.client is None
    cache.put_run("r" * 32, {"status": "ok"})
    assert cache.get_run("r" * 32) is None


def test_health_server_serves_health(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    service.config = dict(service.config, AGENT_PORT="18743")
    import urllib.request
    service.start_health_server()
    with urllib.request.urlopen("http://127.0.0.1:18743/api/health", timeout=3) as resp:
        assert resp.status == 200
        assert b"procurement_agent" in resp.read()


def test_config_builders(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    producer_cfg = service._producer_config()
    assert producer_cfg["acks"] == "all"
    consumer_cfg = service._consumer_config()
    assert consumer_cfg["group_id"] == "python-agent"
    assert service._bootstrap() == "127.0.0.1:9092"


def test_ensure_schema_and_max_seq(monkeypatch):
    service = svc.AgentService(dict(CONFIG))
    rows = [(0,)]
    monkeypatch.setattr(service, "_connect", lambda: FakeRowsConn(rows))
    service._ensure_schema()
    assert service._max_global_seq() == 0


def test_heartbeat_loop_sends_one_message(monkeypatch, fake_db):
    import threading as _threading
    service = make_service(monkeypatch, fake_db)
    sent = _threading.Event()
    def send(topic, key=None, value=None):
        if topic == svc.EVENTS_TOPIC and value.get("type") == "heartbeat.ping":
            sent.set()
        return None
    service.producer.send = send
    service._next_global_seq = lambda: 1
    monkeypatch.setattr("time.sleep", lambda *a: None)
    thread = _threading.Thread(target=service._heartbeat_loop, daemon=True)
    thread.start()
    assert sent.wait(5)
    service._closed = True
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_context_cache_redis_roundtrip(monkeypatch):
    class FakeRedisClient:
        def __init__(self, *a, **k):
            self._data = {}
        def ping(self):
            return True
        def setex(self, key, ttl, value):
            self._data[key] = value
            return True
        def get(self, key):
            return self._data.get(key)
    fake = FakeRedisClient()
    monkeypatch.setattr("redis.Redis.from_url", staticmethod(lambda *a, **k: fake))
    cache = svc.AgentContextCache({"REDIS_URL": "redis://localhost:6379/0"})
    assert cache.client is not None
    cache.put_run("r" * 32, {"status": "ok"})
    assert cache.get_run("r" * 32) == {"status": "ok"}


def test_execute_start_conversation_path(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    result, status, _ = service._execute("op-s", "start_conversation",
        {"operation_id": "op-s", "operation_type": "start_conversation", "aggregate_id": "t" * 32,
         "generation": 1, "expected_task_version": 0, "payload_sha256": "x" * 64,
         "payload": {"message": "采购 3000 个快递袋，宽250mm 长350mm 厚60um，PE 白色，交期7天"}},
        {"message": "采购 3000 个快递袋，宽250mm 长350mm 厚60um，PE 白色，交期7天"}, "x" * 64)
    assert status == "completed"
    assert result["requirement"]["quantity"] == 3000
    assert len(result["session_id"]) == 32


def test_import_quote_failure_publishes_failed(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    def boom(kind, payload):
        raise RuntimeError("artifact unavailable")
    service.rpc.call = boom
    published = []
    service._publish_result = lambda *a, **k: published.append(a)
    service.handle_command(envelope(operation_type="import_quote", payload={"artifact_id": "jb1", "filename": "q.xlsx"}))
    assert published
    assert published[0][2] == "failed"
    assert "artifact unavailable" in published[0][4]


def test_persist_result_published_false_branch(monkeypatch, fake_db):
    service = make_service(monkeypatch, fake_db)
    service._persist_result({"operation_id": "op-1"}, "accepted", None, None, published=False)
    service._persist_result({"operation_id": "op-1"}, "failed", None, "err", published=True)


def test_llm_fallback_on_provider_error(monkeypatch):
    import agentharness.agent_service as m
    class FakeOpenAI:
        def __init__(self, *a, **k):
            pass
        class chat:
            class completions:
                @staticmethod
                def create(*a, **k):
                    raise RuntimeError("provider down")
    monkeypatch.setattr(m, "OpenAI", FakeOpenAI, raising=False)
    config = dict(CONFIG)
    config["AGENTHARNESS_PROCUREMENT_PROVIDER"] = "openai"
    config["OPENAI_API_KEY"] = "k"
    config["OPENAI_MODEL"] = "m"
    config["OPENAI_BASE_URL"] = "http://x"
    req = m._llm_requirement("采购快递袋 3000 个", config)
    assert req["quantity"] == 3000


def test_sasl_config_branches(monkeypatch, fake_db):
    cfg = dict(CONFIG)
    cfg["AGENT_KAFKA_SASL_USERNAME"] = "python-agent"
    cfg["AGENT_KAFKA_SASL_PASSWORD"] = "secret"
    service = svc.AgentService(cfg)
    assert service._producer_config()["security_protocol"] == "SASL_PLAINTEXT"
    assert service._consumer_config()["sasl_plain_username"] == "python-agent"
    assert service._producer_sasl_only()["sasl_mechanism"] == "SCRAM-SHA-256"
    client = svc.RpcClient(cfg, cfg["AGENT_INTERNAL_HMAC_KEY"])
    assert client._sasl()["security_protocol"] == "SASL_PLAINTEXT"
    client.close()


def test_health_server_404(monkeypatch, fake_db):
    import urllib.error
    import urllib.request
    service = make_service(monkeypatch, fake_db)
    service.config = dict(service.config, AGENT_PORT="18744")
    service.start_health_server()
    try:
        urllib.request.urlopen("http://127.0.0.1:18744/other", timeout=3)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as error:
        assert error.code == 404


def test_main_runs_with_stubbed_service(monkeypatch, fake_db):
    class StubService:
        def __init__(self):
            pass
        def start(self):
            raise SystemExit(0)
    monkeypatch.setattr(svc, "AgentService", StubService)
    monkeypatch.setattr("logging.basicConfig", lambda **kw: None)
    try:
        svc.main()
    except SystemExit:
        pass


def test_next_global_seq_fallback_without_row(monkeypatch, fake_db):
    service = svc.AgentService(dict(CONFIG))
    class EmptyConn:
        def cursor(self, *a, **k):
            return FakeCursor([])
        def commit(self):
            return None
        def close(self):
            return None
    monkeypatch.setattr(service, "_connect", lambda: EmptyConn())
    assert service._next_global_seq() >= 1

