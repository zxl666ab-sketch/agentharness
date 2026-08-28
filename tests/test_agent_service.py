"""Kafka transport tests for the canonical Harness-backed Agent service."""

from __future__ import annotations

from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentharness import agent_service as svc
from agentharness.contracts import EventEnvelope

CONFIG = {
    "AGENT_KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9092",
    "AGENT_KAFKA_SASL_USERNAME": "",
    "AGENT_KAFKA_SASL_PASSWORD": "",
    "AGENT_INTERNAL_HMAC_KEY": "unit-test-hmac-key-0123456789abcdef",
}


@pytest.fixture
def service(tmp_path):  # type: ignore[no-untyped-def]
    runtime = svc.AgentService({**CONFIG, "AGENTHARNESS_DATA_DIR": str(tmp_path)})
    runtime.producer = MagicMock()
    runtime.consumer = MagicMock()
    runtime.rpc.producer = MagicMock()
    runtime.rpc.consumer = MagicMock()
    yield runtime
    runtime.close()


def envelope(
    operation_id: str = "11111111-1111-1111-1111-111111111111",
    operation_type: str = "analyze",
    payload: dict | None = None,
) -> dict:
    payload = payload or {
        "task_id": "a" * 32,
        "business_id": "a" * 32,
        "ai_task_id": "b" * 32,
        "trace_id": "c" * 32,
        "task_type": "QUOTE_ANALYSIS",
        "file_ids": [],
        "input_sha256": "d" * 64,
    }
    value = {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "schema_version": 1,
        "message_type": "ai_task.command" if operation_type == "analyze" else "agent.command",
        "aggregate_id": "a" * 32,
        "generation": 1,
        "expected_task_version": 0,
        "payload_sha256": svc._sha256(payload),
        "payload": payload,
    }
    if operation_type == "analyze":
        value.update(
            {
                "ai_task_id": "b" * 32,
                "business_id": "a" * 32,
                "trace_id": "c" * 32,
                "task_type": "QUOTE_ANALYSIS",
                "file_ids": [],
            }
        )
    value["signature"] = svc._sign_envelope(CONFIG["AGENT_INTERNAL_HMAC_KEY"], value)
    return value


def test_hmac_covers_the_complete_envelope() -> None:
    value = envelope()
    assert svc._verify_envelope(CONFIG["AGENT_INTERNAL_HMAC_KEY"], value)
    value["payload"]["task_id"] = "f" * 32
    assert not svc._verify_envelope(CONFIG["AGENT_INTERNAL_HMAC_KEY"], value)


def test_command_delegates_to_the_canonical_harness_processor(service) -> None:  # type: ignore[no-untyped-def]
    service.commands.execute = AsyncMock(
        return_value={
            "operation_id": envelope()["operation_id"],
            "status": "completed",
            "result": {"run_id": "r" * 32, "status": "waiting_approval"},
            "error": None,
        }
    )
    service.handle_command(envelope())
    service.commands.execute.assert_awaited_once()
    published = service.producer.send.call_args
    assert published.args[0] == svc.RESULTS_TOPIC
    assert published.kwargs["value"]["status"] == "completed"
    assert published.kwargs["value"]["result"]["status"] == "waiting_approval"


def test_invalid_signature_never_reaches_the_runtime(service) -> None:  # type: ignore[no-untyped-def]
    service.commands.execute = AsyncMock()
    value = envelope()
    value["signature"] = "0" * 64
    service.handle_command(value)
    service.commands.execute.assert_not_awaited()
    service.producer.send.assert_not_called()


def test_runtime_failure_is_published_for_java_retry_policy(service) -> None:  # type: ignore[no-untyped-def]
    service.commands.execute = AsyncMock(side_effect=TimeoutError("RPC timed out"))
    service.handle_command(envelope(operation_type="import_quote"))
    message = service.producer.send.call_args.kwargs["value"]
    assert message["status"] == "failed"
    assert message["error_category"] == "TRANSPORT"
    assert message["retryable"] is True


@pytest.mark.asyncio
async def test_java_context_and_artifact_are_loaded_through_kafka_rpc(service) -> None:  # type: ignore[no-untyped-def]
    service.rpc.call = MagicMock(
        side_effect=[{"task_version": 3}, {"base64": "aGVsbG8="}]
    )
    context = await service._fetch_context_rpc(
        f"/internal/v1/tasks/{'a' * 32}/context"
    )
    artifact = await service._fetch_artifact_rpc(
        f"/internal/v1/artifacts/jb{'b' * 32}/raw"
    )
    assert context == {"task_version": 3}
    assert artifact == b"hello"
    assert service.rpc.call.call_args_list[0].args[0] == "get_task_context"
    assert service.rpc.call.call_args_list[1].args[0] == "get_artifact"


def test_harness_events_are_projected_to_kafka(service) -> None:  # type: ignore[no-untyped-def]
    run_id = "r" * 32
    session_id = service.harness.storage.create_session(title="采购")
    service.harness.storage.create_run(
        run_id=run_id,
        session_id=session_id,
        root_run_id=run_id,
        metadata={"purchase_request_id": "a" * 32},
    )
    service._global_seq = 10
    service._publish_harness_event(
        EventEnvelope(
            session_id=session_id,
            root_run_id=run_id,
            run_id=run_id,
            type="run_status",
            payload={"status": "require_human"},
        )
    )
    message = service.producer.send.call_args.kwargs["value"]
    assert message["task_id"] == "a" * 32
    assert message["run_id"] == run_id
    assert message["payload"]["status"] == "require_human"
    assert message["global_seq"] == 11


def test_heartbeat_seq_is_durable_across_restart(tmp_path, monkeypatch) -> None:
    """LIVE-1: heartbeats only went to Kafka, so a trimmed topic regressed the seed.

    ``retention.ms`` deletes the high-seq messages after 7 days; the restart seed
    then fell back below the Java projection's high-water mark, which made
    ``/api/health`` permanently ``agent_status: down`` and started silently
    dropping real task events on the global-seq dedupe.
    """
    # No broker in unit tests: `_topic_max_global_seq` falls back to local
    # storage, i.e. exactly the "topic was trimmed to nothing" case.
    monkeypatch.setattr(svc, "KafkaConsumer", None)
    config = {**CONFIG, "AGENTHARNESS_DATA_DIR": str(tmp_path)}

    first = svc.AgentService(dict(config))
    try:
        first.producer = MagicMock()
        first._global_seq = 86_565  # the pre-trim era of the incident
        for _ in range(3):
            first._emit("heartbeat.ping", "", "", {"agent": "python-agent"})
        emitted = int(first.producer.send.call_args.kwargs["value"]["global_seq"])
        assert emitted == 86_568
        assert first.harness.storage.max_global_seq() == emitted
    finally:
        first.close()

    second = svc.AgentService(dict(config))
    try:
        second.producer = MagicMock()
        second._global_seq = second._topic_max_global_seq()
        assert second._global_seq == emitted, "restart seed lost the heartbeat seq"
        second._emit("heartbeat.ping", "", "", {"agent": "python-agent"})
        restarted = int(second.producer.send.call_args.kwargs["value"]["global_seq"])
        assert restarted > emitted
        assert second.harness.storage.max_global_seq() == restarted
    finally:
        second.close()


def test_rpc_client_retries_once() -> None:
    client = svc.RpcClient(dict(CONFIG), CONFIG["AGENT_INTERNAL_HMAC_KEY"])
    client.producer = MagicMock()
    client.consumer = MagicMock()
    with pytest.raises((TimeoutError, RuntimeError)):
        client.call("get_artifact", {"artifact_id": "x"}, timeout=0.01)
    assert client.producer.send.call_count == 2


def test_rpc_response_requires_matching_signature_and_request_hash() -> None:
    client = svc.RpcClient(dict(CONFIG), CONFIG["AGENT_INTERNAL_HMAC_KEY"])
    future: Future = Future()
    client._futures["corr"] = (future, "request-sha")
    value = {
        "correlation_id": "corr",
        "status": "ok",
        "result": {"k": "v"},
        "request_sha256": "request-sha",
    }
    value["signature"] = svc._sign_envelope(CONFIG["AGENT_INTERNAL_HMAC_KEY"], value)
    client.consumer = [type("Record", (), {"value": value})()]
    client._loop()
    assert future.result()["result"] == {"k": "v"}


def test_sasl_configuration_is_applied_to_command_and_rpc_clients(tmp_path) -> None:
    config = {
        **CONFIG,
        "AGENT_KAFKA_SASL_USERNAME": "python-agent",
        "AGENT_KAFKA_SASL_PASSWORD": "secret",
        "AGENTHARNESS_DATA_DIR": str(tmp_path),
    }
    service = svc.AgentService(config)
    try:
        assert service._producer_config()["security_protocol"] == "SASL_PLAINTEXT"
        assert service._consumer_config()["sasl_plain_username"] == "python-agent"
        assert service.rpc._sasl()["sasl_mechanism"] == "SCRAM-SHA-256"
    finally:
        service.close()


def test_failure_categories_remain_explicit() -> None:
    assert svc._failure_details(ValueError("bad input"))["error_category"] == "VALIDATION"
    assert svc._failure_details(TimeoutError("timed out"))["retryable"] is True
    assert svc._failure_details(RuntimeError("provider unavailable"))["error_category"] == "PROVIDER"


def test_start_consumes_commands_and_closes_runtime(monkeypatch, tmp_path) -> None:
    produced: list[tuple[str, str | None, dict | None]] = []

    class FakeProducer:
        def send(self, topic, key=None, value=None):  # type: ignore[no-untyped-def]
            produced.append((topic, key, value))

        def flush(self) -> None:
            return

        def close(self) -> None:
            return

    class FakeConsumer:
        def __iter__(self):  # type: ignore[no-untyped-def]
            yield type("Record", (), {"value": envelope(), "key": "op"})()

        def close(self) -> None:
            return

    service = svc.AgentService({**CONFIG, "AGENTHARNESS_DATA_DIR": str(tmp_path)})
    service.commands.execute = AsyncMock(
        return_value={"status": "completed", "result": {"run_id": "r" * 32}}
    )
    monkeypatch.setattr(svc, "KafkaProducer", lambda **_kwargs: FakeProducer())
    monkeypatch.setattr(svc, "KafkaConsumer", lambda *_args, **_kwargs: FakeConsumer())
    monkeypatch.setattr(service.rpc, "start", lambda: None)
    monkeypatch.setattr(service, "start_health_server", lambda: None)
    monkeypatch.setattr(service, "_heartbeat_loop", lambda: None)
    monkeypatch.setattr(service, "_topic_max_global_seq", lambda: 0)
    service.start()
    assert any(topic == svc.RESULTS_TOPIC for topic, _key, _value in produced)
    assert service._closed is True


def test_main_loads_project_environment_before_start(monkeypatch) -> None:
    calls: list[str] = []

    class StubService:
        def start(self) -> None:
            calls.append("started")

    monkeypatch.setattr(svc, "load_project_env", lambda: calls.append("loaded"))
    monkeypatch.setattr(svc, "AgentService", StubService)
    svc.main()
    assert calls == ["loaded", "started"]
