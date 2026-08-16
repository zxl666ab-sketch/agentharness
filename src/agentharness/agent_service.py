"""Kafka transport for the durable procurement Agent Runtime.

Java owns procurement business state. This process validates signed Kafka
commands, delegates them to the canonical Harness/RunEngine implementation,
and publishes durable Run events and command results back to Java.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from agentharness.api.internal_agent import AgentCommandBody, InternalAgentCommands
from agentharness.config import load_project_env
from agentharness.contracts import EventEnvelope
from agentharness.harness import Harness

try:
    from kafka import KafkaConsumer, KafkaProducer
except ImportError:  # pragma: no cover - optional local dependency
    KafkaConsumer = None  # type: ignore[assignment]
    KafkaProducer = None  # type: ignore[assignment]

logger = logging.getLogger("agentharness.agent_service")

COMMANDS_TOPIC = "caijiatai.commands"
RESULTS_TOPIC = "caijiatai.results"
EVENTS_TOPIC = "caijiatai.events"
RPC_REQUESTS_TOPIC = "caijiatai.rpc.requests"
RPC_RESPONSES_TOPIC = "caijiatai.rpc.responses"

SUPPORTED_OPERATIONS = frozenset(
    {
        "start_conversation",
        "import_quote",
        "resume_run",
        "analyze",
        "approve_decision",
        "create_structured",
        "reopen_task",
        "parse_invoice",
        "explain_invoice_diff",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sign_envelope(key: str, envelope: dict[str, Any]) -> str:
    unsigned = {name: value for name, value in envelope.items() if name != "signature"}
    return hmac.new(
        key.encode("utf-8"), _canonical_json(unsigned), hashlib.sha256
    ).hexdigest()


def _verify_envelope(key: str, envelope: dict[str, Any]) -> bool:
    signature = str(envelope.get("signature") or "")
    return bool(signature) and hmac.compare_digest(
        signature, _sign_envelope(key, envelope)
    )


def _failure_details(error: BaseException | str) -> dict[str, Any]:
    if isinstance(error, BaseException):
        error_type = type(error).__name__
        message = str(error) or error_type
    else:
        error_type = "AgentError"
        message = str(error) or "Agent execution failed"
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", error_type)
    code = re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_").upper()
    low = message.lower()
    if error_type.lower() in {"quoteparseerror", "valueerror", "jsondecodeerror"}:
        category, retryable = "VALIDATION", False
    elif error_type.lower() == "requirementmodelerror" or any(
        token in low for token in ("provider", "model unavailable", "模型调用失败")
    ):
        category, retryable = "PROVIDER", True
    elif isinstance(error, (TimeoutError, ConnectionError)) or any(
        token in low
        for token in ("rpc_", "timeout", "timed out", "connection refused", "unavailable")
    ):
        category, retryable = "TRANSPORT", True
    else:
        category, retryable = "INTERNAL", False
    return {
        "error_category": category,
        "error_code": code[:100] or "AGENT_ERROR",
        "error_message": message[:1000],
        "retryable": retryable,
    }


class RpcClient:
    """Synchronous request/reply RPC over Kafka with one bounded retry."""

    def __init__(self, config: dict[str, str], hmac_key: str) -> None:
        self.config = config
        self.hmac_key = hmac_key
        self._futures: dict[str, tuple[Future[Any], str]] = {}
        self._lock = threading.Lock()
        self.producer: Any = None
        self.consumer: Any = None
        self._closed = False

    def _bootstrap(self) -> str:
        return self.config.get("AGENT_KAFKA_BOOTSTRAP_SERVERS") or "127.0.0.1:9092"

    def _sasl(self) -> dict[str, Any]:
        if not self.config.get("AGENT_KAFKA_SASL_USERNAME"):
            return {}
        return {
            "security_protocol": "SASL_PLAINTEXT",
            "sasl_mechanism": "SCRAM-SHA-256",
            "sasl_plain_username": self.config["AGENT_KAFKA_SASL_USERNAME"],
            "sasl_plain_password": self.config["AGENT_KAFKA_SASL_PASSWORD"],
        }

    def start(self) -> None:
        if KafkaConsumer is None or KafkaProducer is None:
            raise RuntimeError("kafka-python 未安装")
        self.producer = KafkaProducer(
            bootstrap_servers=self._bootstrap(),
            key_serializer=lambda value: value.encode("utf-8")
            if isinstance(value, str)
            else value,
            value_serializer=lambda value: value
            if isinstance(value, bytes)
            else _canonical_json(value),
            acks="all",
            enable_idempotence=True,
            **self._sasl(),
        )
        self.consumer = KafkaConsumer(
            RPC_RESPONSES_TOPIC,
            group_id="python-agent-rpc",
            bootstrap_servers=self._bootstrap(),
            auto_offset_reset="latest",
            enable_auto_commit=True,
            key_deserializer=lambda value: value.decode("utf-8") if value else "",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            **self._sasl(),
        )
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        for record in self.consumer:
            if self._closed:
                break
            value = record.value
            correlation_id = str(value.get("correlation_id") or "")
            if not _verify_envelope(self.hmac_key, value):
                logger.warning("RPC 响应签名校验失败：%s", correlation_id)
                continue
            with self._lock:
                pending = self._futures.get(correlation_id)
                if pending is None:
                    continue
                future, expected_sha = pending
                if value.get("request_sha256") != expected_sha:
                    logger.warning("RPC 响应 request_sha256 不匹配：%s", correlation_id)
                    continue
                self._futures.pop(correlation_id, None)
            if not future.done():
                future.set_result(value)

    def call(
        self, kind: str, payload: dict[str, Any], timeout: float = 10.0
    ) -> dict[str, Any]:
        for attempt in range(2):
            correlation_id = uuid.uuid4().hex
            request_sha = _sha256(payload)
            future: Future[Any] = Future()
            with self._lock:
                self._futures[correlation_id] = (future, request_sha)
            request = {
                "correlation_id": correlation_id,
                "kind": kind,
                "payload": payload,
                "reply_to": RPC_RESPONSES_TOPIC,
                "request_sha256": request_sha,
            }
            request["signature"] = _sign_envelope(self.hmac_key, request)
            self.producer.send(RPC_REQUESTS_TOPIC, key=correlation_id, value=request)
            self.producer.flush()
            try:
                response = future.result(timeout=timeout)
                if response.get("status") != "ok":
                    raise RuntimeError(str(response.get("error") or "rpc_error"))
                return response.get("result") or {}
            except Exception:
                with self._lock:
                    self._futures.pop(correlation_id, None)
                if attempt == 0:
                    logger.warning("RPC %s 超时/失败，重试一次", kind)
                    continue
                raise
        raise RuntimeError("RPC retry loop exhausted")

    def close(self) -> None:
        self._closed = True
        if self.consumer is not None:
            self.consumer.close()
        if self.producer is not None:
            self.producer.close()


class AgentService:
    def __init__(self, config: dict[str, str] | None = None) -> None:
        self.config = config or {
            key: os.environ.get(key, "")
            for key in (
                "AGENT_KAFKA_BOOTSTRAP_SERVERS",
                "AGENT_KAFKA_SASL_USERNAME",
                "AGENT_KAFKA_SASL_PASSWORD",
                "AGENT_INTERNAL_HMAC_KEY",
                "AGENTHARNESS_DATA_DIR",
                "AGENT_PORT",
            )
        }
        self.hmac_key = self.config["AGENT_INTERNAL_HMAC_KEY"]
        if len(self.hmac_key.encode("utf-8")) < 32:
            raise ValueError("AGENT_INTERNAL_HMAC_KEY 必须至少 32 字节")
        data_dir = Path(
            self.config.get("AGENTHARNESS_DATA_DIR")
            or os.environ.get("AGENTHARNESS_DATA_DIR")
            or "/data/runtime"
        )
        self.producer: Any = None
        self.consumer: Any = None
        self._closed = False
        self._seq_lock = threading.Lock()
        self._global_seq = 0
        self.rpc = RpcClient(self.config, self.hmac_key)
        self.harness = Harness(
            data_dir=data_dir,
            on_gateway_event=self._publish_gateway_event,
        )
        self.commands = InternalAgentCommands(
            self.harness,
            fetch_context=self._fetch_context_rpc,
            fetch_artifact=self._fetch_artifact_rpc,
        )
        self._unsubscribe_events = self.harness.subscribe_events(
            self._publish_harness_event
        )

    def _bootstrap(self) -> str:
        return self.config.get("AGENT_KAFKA_BOOTSTRAP_SERVERS") or "127.0.0.1:9092"

    def _sasl(self) -> dict[str, Any]:
        if not self.config.get("AGENT_KAFKA_SASL_USERNAME"):
            return {}
        return {
            "security_protocol": "SASL_PLAINTEXT",
            "sasl_mechanism": "SCRAM-SHA-256",
            "sasl_plain_username": self.config["AGENT_KAFKA_SASL_USERNAME"],
            "sasl_plain_password": self.config["AGENT_KAFKA_SASL_PASSWORD"],
        }

    def _producer_config(self) -> dict[str, Any]:
        return {
            "bootstrap_servers": self._bootstrap(),
            "key_serializer": lambda value: value.encode("utf-8")
            if isinstance(value, str)
            else value,
            "value_serializer": lambda value: value
            if isinstance(value, bytes)
            else _canonical_json(value),
            "acks": "all",
            "enable_idempotence": True,
            "max_request_size": 16777216,
            **self._sasl(),
        }

    def _consumer_config(self) -> dict[str, Any]:
        return {
            "bootstrap_servers": self._bootstrap(),
            "group_id": "python-agent",
            "auto_offset_reset": "earliest",
            "enable_auto_commit": True,
            "key_deserializer": lambda value: value.decode("utf-8") if value else "",
            "value_deserializer": lambda value: json.loads(value.decode("utf-8")),
            "max_poll_records": 10,
            **self._sasl(),
        }

    def _topic_max_global_seq(self) -> int:
        if KafkaConsumer is None:
            return self.harness.storage.max_global_seq()
        consumer = KafkaConsumer(
            EVENTS_TOPIC,
            group_id="python-agent-seq-boot",
            bootstrap_servers=self._bootstrap(),
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            consumer_timeout_ms=4000,
            key_deserializer=lambda value: value.decode("utf-8") if value else "",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            **self._sasl(),
        )
        maximum = self.harness.storage.max_global_seq()
        try:
            for record in consumer:
                try:
                    maximum = max(maximum, int(record.value.get("global_seq") or 0))
                except (AttributeError, TypeError, ValueError):
                    continue
        finally:
            consumer.close()
        return maximum

    def _next_global_seq(self) -> int:
        with self._seq_lock:
            self._global_seq += 1
            return self._global_seq

    def handle_command(self, envelope: dict[str, Any]) -> None:
        operation_id = str(envelope.get("operation_id") or "")
        operation_type = str(envelope.get("operation_type") or "")
        payload = envelope.get("payload") or {}
        payload_sha = str(envelope.get("payload_sha256") or "")
        if not operation_id or operation_type not in SUPPORTED_OPERATIONS:
            logger.warning("命令类型或 operation_id 无效：%s", operation_id)
            return
        if not _verify_envelope(self.hmac_key, envelope):
            logger.warning("命令签名校验失败：%s", operation_id)
            return
        if payload_sha != _sha256(payload):
            logger.warning("命令 payload_sha256 不匹配：%s", operation_id)
            return
        try:
            body = AgentCommandBody.model_validate(
                {
                    "operation_id": operation_id,
                    "operation_type": operation_type,
                    "aggregate_id": envelope.get("aggregate_id"),
                    "generation": envelope.get("generation"),
                    "expected_task_version": envelope.get("expected_task_version"),
                    "payload_sha256": payload_sha,
                    "payload": payload,
                }
            )
            response = asyncio.run(self.commands.execute(body))
            status = str(response.get("status") or "failed")
            result = response.get("result")
            result = result if isinstance(result, dict) else None
            error = str(response.get("error") or "") or None
        except Exception as exc:  # noqa: BLE001 - failure must reach Java
            logger.exception("命令处理失败：%s", operation_id)
            result = _failure_details(exc)
            status = "failed"
            error = f"{result['error_code']}: {result['error_message']}"
        self._publish_result(operation_id, envelope, status, result, error)

    async def _fetch_context_rpc(self, path: str) -> dict[str, Any]:
        match = re.fullmatch(r"/internal/v1/tasks/([0-9a-f]{32})/context", path)
        if not match:
            raise ValueError(f"unsupported Java context path: {path}")
        return await asyncio.to_thread(
            self.rpc.call, "get_task_context", {"task_id": match.group(1)}
        )

    async def _fetch_artifact_rpc(self, path: str) -> bytes:
        match = re.fullmatch(r"/internal/v1/artifacts/(jb[0-9a-f]{32})/raw", path)
        if not match:
            raise ValueError(f"unsupported Java artifact path: {path}")
        value = await asyncio.to_thread(
            self.rpc.call, "get_artifact", {"artifact_id": match.group(1)}
        )
        return base64.b64decode(str(value.get("base64") or ""), validate=True)

    def _publish_harness_event(self, event: EventEnvelope) -> None:
        if self.producer is None:
            return
        run = self.harness.storage.get_run(event.run_id) or {}
        try:
            metadata = json.loads(str(run.get("metadata_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        self._emit(
            event_type,
            str(metadata.get("purchase_request_id") or ""),
            event.run_id,
            {
                **event.payload,
                "session_id": event.session_id,
                "root_run_id": event.root_run_id,
                "run_seq": event.run_seq,
            },
        )

    def _publish_gateway_event(self, provider: str, event: str, detail: dict[str, Any]) -> None:
        """P2-1：熔断/限流/降级事件 → Kafka runtime 事件 → Java 平台接口可见。"""
        if self.producer is None:
            return
        self._emit(
            f"provider_gateway.{event}",
            "",
            "",
            {"provider": provider, **detail},
        )

    def _publish_result(
        self,
        operation_id: str,
        envelope: dict[str, Any],
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        failure = result if status == "failed" and isinstance(result, dict) else {}
        if status == "failed" and not failure.get("error_category"):
            failure = _failure_details(error or "Agent execution failed")
        message = {
            "schema_version": 1,
            "message_type": "ai_task.result"
            if envelope.get("ai_task_id")
            else "agent.result",
            "operation_id": operation_id,
            "aggregate_id": envelope.get("aggregate_id"),
            "ai_task_id": envelope.get("ai_task_id"),
            "business_id": envelope.get("business_id") or envelope.get("aggregate_id"),
            "trace_id": envelope.get("trace_id"),
            "task_type": envelope.get("task_type"),
            "generation": envelope.get("generation"),
            "expected_task_version": envelope.get("expected_task_version"),
            "payload_sha256": envelope.get("payload_sha256"),
            "status": status,
            "result": result or {},
            "error": error,
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if status == "failed":
            message.update(
                {
                    "error_category": failure["error_category"],
                    "error_code": failure["error_code"],
                    "error_message": failure["error_message"],
                    "retryable": bool(failure["retryable"]),
                }
            )
        message["signature"] = _sign_envelope(self.hmac_key, message)
        self.producer.send(RESULTS_TOPIC, key=operation_id, value=message)
        self.producer.flush()

    def _emit(
        self, event_type: str, task_id: str, run_id: str, payload: dict[str, Any]
    ) -> None:
        message = {
            "type": event_type,
            "task_id": task_id,
            "run_id": run_id,
            "global_seq": self._next_global_seq(),
            "payload": payload,
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload_sha256": _sha256(payload),
        }
        message["signature"] = _sign_envelope(self.hmac_key, message)
        self.producer.send(EVENTS_TOPIC, key=task_id or "agent", value=message)

    def _heartbeat_loop(self) -> None:
        while not self._closed:
            try:
                if self.producer is not None:
                    self._emit(
                        "heartbeat.ping",
                        "",
                        "",
                        {
                            "agent": "python-agent",
                            "service": "procurement_agent",
                            # P2-1：网关脱敏状态随心跳上送，Java 平台接口可读
                            "gateway": self.harness.gateway_snapshots(),
                        },
                    )
                    self.producer.flush()
            except Exception:  # noqa: BLE001
                logger.exception("心跳发布失败")
            time.sleep(5)

    def start_health_server(self) -> None:
        import http.server

        data_dir = str(self.harness.data_dir)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/api/health"):
                    body = _canonical_json(
                        {
                            "status": "ok",
                            "service": "procurement_agent",
                            "kafka": True,
                            "runtime": "harness",
                            "data_dir": data_dir,
                        }
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_args: Any) -> None:
                return

        port = int(self.config.get("AGENT_PORT") or os.environ.get("AGENT_PORT", "8742"))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def start(self) -> None:
        if KafkaConsumer is None or KafkaProducer is None:
            raise RuntimeError("kafka-python 未安装")
        self.producer = KafkaProducer(**self._producer_config())
        self.consumer = KafkaConsumer(COMMANDS_TOPIC, **self._consumer_config())
        self.rpc.start()
        self._global_seq = self._topic_max_global_seq()
        logger.info(
            "Python Agent Runtime 已启动（Kafka=%s, data=%s）",
            self._bootstrap(),
            self.harness.data_dir,
        )
        self.start_health_server()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        try:
            for record in self.consumer:
                if self._closed:
                    break
                try:
                    self.handle_command(record.value)
                except Exception:  # noqa: BLE001 - keep consuming
                    logger.exception("处理命令失败：%s", record.key)
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._unsubscribe_events()
        if self.consumer is not None:
            self.consumer.close()
        if self.producer is not None:
            self.producer.close()
        self.rpc.close()
        self.harness.close()


def main() -> None:
    load_project_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    AgentService().start()


if __name__ == "__main__":
    main()
