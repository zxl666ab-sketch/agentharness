"""Embedded Python Agent microservice for the 0.5.0 Java/Python split.

Consumes commands from Kafka (caijiatai.commands), keeps runtime state in the
MySQL runtime schema (caijiatai_runtime), and publishes command results and
runtime events back to Kafka (caijiatai.results / caijiatai.events).

This is the 2a minimal slice: analyze commands are processed deterministically
(fake run lifecycle); the deterministic comparison itself still runs in Java.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pymysql

try:
    from kafka import KafkaConsumer, KafkaProducer
except ImportError:  # pragma: no cover - optional local dependency
    KafkaConsumer = None  # type: ignore[assignment]
    KafkaProducer = None  # type: ignore[assignment]

logger = logging.getLogger("agentharness.agent_service")

COMMANDS_TOPIC = "caijiatai.commands"
RESULTS_TOPIC = "caijiatai.results"
EVENTS_TOPIC = "caijiatai.events"

SUPPORTED_OPERATIONS = frozenset({"analyze", "create_structured", "approve_decision", "reopen_task", "resume_run"})

RUNTIME_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS internal_operations (
        operation_id varchar(36) PRIMARY KEY,
        operation_type varchar(60) NOT NULL,
        aggregate_id varchar(32) NOT NULL,
        generation int NOT NULL,
        expected_task_version bigint NOT NULL,
        payload_sha256 varchar(64) NOT NULL,
        status varchar(30) NOT NULL,
        result json NULL,
        error varchar(1000) NULL,
        result_published_at datetime(6) NULL,
        created_at datetime(6) NOT NULL,
        updated_at datetime(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_event (
        id bigint NOT NULL AUTO_INCREMENT,
        global_seq bigint NOT NULL,
        task_id varchar(32) NULL,
        run_id varchar(32) NULL,
        type varchar(100) NOT NULL,
        payload json NOT NULL,
        occurred_at datetime(6) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_runtime_event_global_seq (global_seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """,
]


def _canonical_json(value: Any) -> bytes:
    """Canonical JSON matching the Java golden contract (sorted keys, compact)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _hmac_sign(key: str, kind: str, operation_id: str, payload_sha256: str) -> str:
    content = f"{kind}\n{operation_id}\n{payload_sha256}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), content, hashlib.sha256).hexdigest()


def _hmac_verify(key: str, kind: str, operation_id: str, payload_sha256: str, signature: str) -> bool:
    if not signature:
        return False
    expected = _hmac_sign(key, kind, operation_id, payload_sha256)
    return hmac.compare_digest(expected, signature)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_database_url(url: str) -> dict[str, Any]:
    # mysql+pymysql://user:pass@host:port/db
    rest = url.split("://", 1)[1]
    credentials, _, host_part = rest.partition("@")
    user, _, password = credentials.partition(":")
    host_port, _, database = host_part.partition("/")
    host, _, port = host_port.partition(":")
    database = database.split("?")[0]
    return {
        "host": host or "127.0.0.1",
        "port": int(port or 3306),
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "autocommit": True,
    }


class AgentService:
    def __init__(self, config: dict[str, str] | None = None) -> None:
        self.config = config or {
            key: os.environ.get(key, "") for key in (
                "AGENTHARNESS_DATABASE_URL",
                "AGENT_KAFKA_BOOTSTRAP_SERVERS",
                "AGENT_KAFKA_SASL_USERNAME",
                "AGENT_KAFKA_SASL_PASSWORD",
                "AGENT_INTERNAL_HMAC_KEY",
            )
        }
        self.hmac_key = self.config["AGENT_INTERNAL_HMAC_KEY"]
        if not self.hmac_key:
            raise ValueError("AGENT_INTERNAL_HMAC_KEY 必须配置")
        self._db_kwargs = _parse_database_url(self.config["AGENTHARNESS_DATABASE_URL"])
        self._ensure_schema()
        self._global_seq = self._max_global_seq()
        self._seq_lock = threading.Lock()
        self.producer = None
        self.consumer = None
        self._closed = False

    # ---- MySQL runtime ----
    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(**self._db_kwargs)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                for statement in RUNTIME_SCHEMA_SQL:
                    cursor.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def _max_global_seq(self) -> int:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COALESCE(MAX(global_seq), 0) FROM runtime_event")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        finally:
            conn.close()

    def _next_global_seq(self) -> int:
        with self._seq_lock:
            self._global_seq += 1
            return self._global_seq

    def _load_operation(self, operation_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT operation_id, payload_sha256, status, result, error, result_published_at "
                    "FROM internal_operations WHERE operation_id = %s",
                    (operation_id,),
                )
                row = cursor.fetchone()
            return row
        finally:
            conn.close()

    def _persist_result(self, operation: dict[str, Any], status: str, result: dict[str, Any] | None,
                        error: str | None, published: bool) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE internal_operations SET status = %s, result = %s, error = %s, "
                    "result_published_at = %s, updated_at = %s WHERE operation_id = %s",
                    (
                        status,
                        json.dumps(result or {}, ensure_ascii=False),
                        error,
                        _utcnow() if published else None,
                        _utcnow(),
                        operation["operation_id"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    # ---- Kafka ----
    def _bootstrap(self) -> str:
        return self.config["AGENT_KAFKA_BOOTSTRAP_SERVERS"] or "127.0.0.1:9092"

    def _producer_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "bootstrap_servers": self._bootstrap(),
            "key_serializer": lambda value: value.encode("utf-8") if isinstance(value, str) else value,
            "value_serializer": lambda value: value if isinstance(value, bytes) else _canonical_json(value),
            "acks": "all",
            "enable_idempotence": True,
            "max_request_size": 16777216,
        }
        if self.config.get("AGENT_KAFKA_SASL_USERNAME"):
            config.update({
                "security_protocol": "SASL_PLAINTEXT",
                "sasl_mechanism": "SCRAM-SHA-256",
                "sasl_plain_username": self.config["AGENT_KAFKA_SASL_USERNAME"],
                "sasl_plain_password": self.config["AGENT_KAFKA_SASL_PASSWORD"],
            })
        return config

    def _consumer_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "bootstrap_servers": self._bootstrap(),
            "group_id": "python-agent",
            "auto_offset_reset": "earliest",
            "enable_auto_commit": True,
            "key_deserializer": lambda value: value.decode("utf-8") if value else "",
            "value_deserializer": lambda value: json.loads(value.decode("utf-8")),
            "max_poll_records": 10,
        }
        if self.config.get("AGENT_KAFKA_SASL_USERNAME"):
            config.update({
                "security_protocol": "SASL_PLAINTEXT",
                "sasl_mechanism": "SCRAM-SHA-256",
                "sasl_plain_username": self.config["AGENT_KAFKA_SASL_USERNAME"],
                "sasl_plain_password": self.config["AGENT_KAFKA_SASL_PASSWORD"],
            })
        return config

    # ---- command handling ----
    def handle_command(self, envelope: dict[str, Any]) -> None:
        operation_id = str(envelope.get("operation_id") or "")
        operation_type = str(envelope.get("operation_type") or "")
        payload_sha256 = str(envelope.get("payload_sha256") or "")
        signature = str(envelope.get("signature") or "")
        if not operation_id or not operation_type:
            logger.warning("命令缺少 operation_id/operation_type")
            return
        if not _hmac_verify(self.hmac_key, "command", operation_id, payload_sha256, signature):
            logger.warning("命令签名校验失败：%s", operation_id)
            return
        if operation_type not in SUPPORTED_OPERATIONS:
            logger.warning("暂不支持的命令类型 %s（2a 最小切片仅 analyze）", operation_type)
            return
        payload = envelope.get("payload") or {}
        actual_sha = _sha256(payload)
        existing = self._load_operation(operation_id)
        if existing is not None:
            if existing["payload_sha256"] != actual_sha:
                logger.warning("命令 payload 冲突（409）：%s", operation_id)
                self._publish_result(operation_id, envelope, "failed", None, "operation_payload_conflict")
                return
            if existing["result_published_at"] is not None:
                return  # 已发布，幂等跳过
            result = json.loads(existing["result"]) if existing["result"] else None
            status = existing["status"]
            error = existing["error"]
            self._publish_result(operation_id, envelope, status, result, error)
            self._persist_result(existing, status, result, error, published=True)
            return
        result, status, error = self._execute(operation_id, operation_type, envelope, payload, actual_sha)
        self._publish_result(operation_id, envelope, status, result, error)
        self._persist_result(
            {
                "operation_id": operation_id,
                "payload_sha256": actual_sha,
            },
            status, result, error, published=True,
        )

    def _execute(self, operation_id: str, operation_type: str, envelope: dict[str, Any],
                 payload: dict[str, Any], payload_sha256: str) -> tuple[dict[str, Any], str, str | None]:
        task_id = str(envelope.get("aggregate_id") or "")
        run_id = hashlib.sha256((operation_id + ":run").encode("utf-8")).hexdigest()[:32]
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO internal_operations "
                    "(operation_id, operation_type, aggregate_id, generation, expected_task_version, "
                    "payload_sha256, status, result, error, result_published_at, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'accepted', NULL, NULL, NULL, %s, %s)",
                    (
                        operation_id,
                        operation_type,
                        task_id,
                        int(envelope.get("generation") or 1),
                        int(envelope.get("expected_task_version") or 0),
                        payload_sha256,
                        _utcnow(),
                        _utcnow(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        if operation_type == "approve_decision":
            approval = self._approval_evidence(envelope, run_id)
            return {"approval": approval}, "completed", None
        if operation_type == "create_structured":
            session_id = hashlib.sha256((operation_id + ":session").encode("utf-8")).hexdigest()[:32]
            return {"session_id": session_id, "run_id": run_id}, "completed", None
        if operation_type in ("reopen_task", "resume_run"):
            return {"run_id": run_id}, "completed", None
        if operation_type == "analyze":
            self._emit("run_started", task_id, run_id, {"operation_id": operation_id})
            self._emit("run_completed", task_id, run_id, {"operation_id": operation_id})
            return {"run_id": run_id}, "completed", None
        return {}, "completed", None

    def _publish_result(self, operation_id: str, envelope: dict[str, Any], status: str,
                        result: dict[str, Any] | None, error: str | None) -> None:
        payload_sha256 = str(envelope.get("payload_sha256") or "")
        message = {
            "operation_id": operation_id,
            "aggregate_id": envelope.get("aggregate_id"),
            "generation": envelope.get("generation"),
            "expected_task_version": envelope.get("expected_task_version"),
            "payload_sha256": payload_sha256,
            "status": status,
            "result": result or {},
            "error": error,
            "processed_at": _utcnow().isoformat() + "Z",
            "signature": _hmac_sign(self.hmac_key, "result", operation_id, payload_sha256),
        }
        assert self.producer is not None
        self.producer.send(RESULTS_TOPIC, key=operation_id, value=message)
        self.producer.flush()

    def _emit(self, event_type: str, task_id: str, run_id: str, payload: dict[str, Any]) -> None:
        global_seq = self._next_global_seq()
        payload_sha256 = _sha256(payload)
        message = {
            "type": event_type,
            "task_id": task_id,
            "run_id": run_id,
            "global_seq": global_seq,
            "payload": payload,
            "occurred_at": _utcnow().isoformat() + "Z",
            "payload_sha256": payload_sha256,
            "signature": _hmac_sign(
                self.hmac_key, "event", f"{task_id}:{run_id}:{event_type}", payload_sha256),
        }
        assert self.producer is not None
        self.producer.send(EVENTS_TOPIC, key=task_id, value=message)
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_event (global_seq, task_id, run_id, type, payload, occurred_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        global_seq,
                        task_id or None,
                        run_id or None,
                        event_type,
                        json.dumps(payload, ensure_ascii=False),
                        _utcnow(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _approval_evidence(self, envelope: dict[str, Any], run_id: str) -> dict[str, Any]:
        payload = envelope.get("payload") or {}
        binding = {
            "pending_decision_id": str(payload.get("pending_decision_id") or ""),
            "run_id": str(payload.get("run_id") or run_id),
            "tool_name": "procurement_approve_supplier",
            "task_version": payload.get("task_version"),
            "snapshot_id": str(payload.get("snapshot_id") or ""),
            "input_sha256": str(payload.get("input_sha256") or ""),
            "business_decision": str(payload.get("business_decision") or ""),
            "quote_id": payload.get("quote_id"),
            "note_hash": str(payload.get("note_hash") or ""),
        }
        operation_id = str(envelope.get("operation_id") or "")
        approval = dict(binding)
        approval["id"] = hashlib.sha256((operation_id + ":approval").encode("utf-8")).hexdigest()[:32]
        approval["decision"] = "formal_java_confirmation"
        approval["confirmation_source"] = "java_control_plane"
        approval["arguments_sha256"] = _sha256(binding)
        approval["created_at"] = _utcnow().isoformat() + "Z"
        return approval

    def _heartbeat_loop(self) -> None:
        while not self._closed:
            try:
                if self.producer is not None:
                    payload = {"agent": "python-agent", "service": "procurement_agent"}
                    payload_sha256 = _sha256(payload)
                    self.producer.send(
                        EVENTS_TOPIC,
                        key="agent",
                        value={
                            "type": "heartbeat.ping",
                            "task_id": "",
                            "run_id": "",
                            "global_seq": self._next_global_seq(),
                            "payload": payload,
                            "occurred_at": _utcnow().isoformat() + "Z",
                            "payload_sha256": payload_sha256,
                            "signature": _hmac_sign(
                                self.hmac_key, "event", "::heartbeat.ping", payload_sha256),
                        },
                    )
                    self.producer.flush()
            except Exception:  # noqa: BLE001
                logger.exception("心跳发布失败")
            time.sleep(5)

    # ---- lifecycle ----

    def start(self) -> None:
        if KafkaConsumer is None or KafkaProducer is None:
            raise RuntimeError("kafka-python 未安装")
        self.producer = KafkaProducer(**self._producer_config())
        self.consumer = KafkaConsumer(COMMANDS_TOPIC, **self._consumer_config())
        logger.info("Python Agent 服务已启动（Kafka=%s, db=%s）", self._bootstrap(), self._db_kwargs["database"])
        heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat.start()
        try:
            for record in self.consumer:
                if self._closed:
                    break
                try:
                    self.handle_command(record.value)
                except Exception:  # noqa: BLE001 - keep consuming
                    logger.exception("处理命令失败：%s", record.key)
        finally:
            if self.producer is not None:
                self.producer.close()
            if self.consumer is not None:
                self.consumer.close()

    def close(self) -> None:
        self._closed = True
        if self.consumer is not None:
            self.consumer.close()
        if self.producer is not None:
            self.producer.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    service = AgentService()
    service.start()


if __name__ == "__main__":
    main()
