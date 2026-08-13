"""Embedded Python Agent microservice for the 0.5.0 Java/Python split.

Consumes commands from Kafka (caijiatai.commands), keeps runtime state in the
MySQL runtime schema (caijiatai_runtime), and publishes command results and
runtime events back to Kafka (caijiatai.results / caijiatai.events).

AI analysis produces an observable extraction/explanation lifecycle. The
deterministic comparison itself still runs exclusively in Java.
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from typing import Any

import pymysql

from agentharness.config import load_project_env

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

class AgentContextCache:
    """Optional Redis run-context cache; degrades to no-op when unavailable."""

    def __init__(self, config: dict[str, str]) -> None:
        url = (config.get("REDIS_URL") or "").strip()
        self.client = None
        if url:
            try:
                import redis  # type: ignore[import-not-found]
                self.client = redis.Redis.from_url(url, decode_responses=True)
                self.client.ping()
            except Exception:  # noqa: BLE001 - Redis unavailable: degrade
                self.client = None

    def put_run(self, run_id: str, value: dict[str, Any]) -> None:
        if self.client is None:
            return
        try:
            self.client.setex(f"ctx:run:{run_id}", 60, _canonical_json(value).decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if self.client is None:
            return None
        try:
            raw = self.client.get(f"ctx:run:{run_id}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None


SUPPORTED_OPERATIONS = frozenset({
    "analyze", "create_structured", "approve_decision", "reopen_task", "resume_run",
    "import_quote", "start_conversation",
})


def _failure_details(error: BaseException | str) -> dict[str, Any]:
    """Normalize execution failures for Java-owned retry and recovery state."""
    if isinstance(error, BaseException):
        error_type = type(error).__name__
        message = str(error) or error_type
    else:
        error_type = "AgentError"
        message = str(error) or "Agent execution failed"

    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", error_type)
    code = re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_").upper()
    type_name = error_type.lower()
    message_lower = message.lower()
    if type_name in {"quoteparseerror", "valueerror", "jsondecodeerror"}:
        category, retryable = "VALIDATION", False
    elif type_name == "requirementmodelerror" or any(
        token in message_lower for token in ("provider", "model unavailable", "模型调用失败")
    ):
        category, retryable = "PROVIDER", True
    elif isinstance(error, (TimeoutError, ConnectionError)) or any(
        token in message_lower for token in ("rpc_", "timeout", "timed out", "connection refused", "unavailable")
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
    """
    CREATE TABLE IF NOT EXISTS agent_sequence (
        id int NOT NULL PRIMARY KEY,
        global_seq bigint NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """,
]


def _canonical_json(value: Any) -> bytes:
    """Canonical JSON matching the Java golden contract (sorted keys, compact)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sign_envelope(key: str, envelope: dict[str, Any]) -> str:
    """Sign the complete canonical envelope except the detached signature."""
    unsigned = {name: value for name, value in envelope.items() if name != "signature"}
    return hmac.new(key.encode("utf-8"), _canonical_json(unsigned), hashlib.sha256).hexdigest()


def _verify_envelope(key: str, envelope: dict[str, Any]) -> bool:
    signature = str(envelope.get("signature") or "")
    return bool(signature) and hmac.compare_digest(signature, _sign_envelope(key, envelope))


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_REQUIREMENT_PROMPT = """你是采购需求结构化助手。把用户的采购目标转成 JSON，字段：
schema_version(1), title, category(ecommerce_packaging), item_name, quantity(整数), unit(piece),
specifications(width_mm,length_mm,thickness_um,height_mm(纸箱必填),material,color,print_colors),
constraints(base_currency(CNY),fx_rates({"CNY":"1"}),max_lead_days,invoice_required(true),
size_tolerance_mm,thickness_tolerance_um,max_landed_unit_cost(可选),destination(可选))。
尺寸按“宽×长×高”顺序拆分：400 × 300 × 250 mm 输出 width_mm=400、length_mm=300、height_mm=250；
只有两个数时 250 × 350 mm 输出 width_mm=250、length_mm=350。
print_colors 必须是 0-12 的整数（单色印刷=1、双色=2），不要输出文字或单位。
只输出 JSON，不要解释。"""


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON")
    return text[start:end + 1]


def _normalize_print_colors(value: Any) -> int:
    """Accept integer strings, Chinese color words and compact forms like 1色."""
    text = str(value or "").strip().lower()
    if re.fullmatch(r"\d+", text):
        return int(text)
    cleaned = re.sub(r"[\s_\-]+", "", text)
    mapping = {
        "单色": 1, "一色": 1, "单色印刷": 1, "一色印刷": 1,
        "双色": 2, "二色": 2, "双色印刷": 2, "二色印刷": 2,
        "onecolor": 1, "onecolour": 1, "twocolors": 2, "twocolour": 2,
    }
    if cleaned in mapping:
        return mapping[cleaned]
    match = re.fullmatch(r"(\d+)\s*色", cleaned)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(one|two|single|double)\s*colou?rs?", text)
    if match:
        return 1 if match.group(1) in {"one", "single"} else 2
    raise ValueError(f"模型返回印刷色数无效: {value!r}")


def _coerce_requirement(data: dict[str, Any], message: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("item_name"):
        raise ValueError("模型返回缺少 item_name")
    specs = {str(k): v for k, v in (data.get("specifications") or {}).items() if v not in (None, "")}
    if "print_colors" in specs:
        specs["print_colors"] = _normalize_print_colors(specs["print_colors"])
    raw_constraints = data.get("constraints") or {}
    constraints: dict[str, Any] = {
        "base_currency": str(raw_constraints.get("base_currency") or "CNY").upper(),
        "fx_rates": {"CNY": "1"},
        "max_lead_days": max(1, int(float(raw_constraints.get("max_lead_days") or 15))),
        "invoice_required": bool(raw_constraints.get("invoice_required", True)),
        "size_tolerance_mm": str(raw_constraints.get("size_tolerance_mm") or "2"),
        "thickness_tolerance_um": str(raw_constraints.get("thickness_tolerance_um") or "3"),
    }
    if raw_constraints.get("max_landed_unit_cost") not in (None, ""):
        constraints["max_landed_unit_cost"] = str(raw_constraints["max_landed_unit_cost"])
    if raw_constraints.get("destination"):
        constraints["destination"] = str(raw_constraints["destination"])
    quantity = int(float(data.get("quantity") or 0))
    if quantity <= 0:
        raise ValueError("模型返回数量无效")
    return {
        "schema_version": int(data.get("schema_version") or 1),
        "title": str(data.get("title") or "采购询价"),
        "category": str(data.get("category") or "ecommerce_packaging"),
        "item_name": str(data["item_name"]),
        "quantity": quantity,
        "unit": str(data.get("unit") or "piece"),
        "specifications": specs,
        "constraints": constraints,
    }


def _llm_requirement(message: str, config: dict[str, str]) -> dict[str, Any]:
    """Call the configured OpenAI-compatible provider; fall back to offline rules on failure."""
    provider = config.get("AGENTHARNESS_PROCUREMENT_PROVIDER") or os.environ.get("AGENTHARNESS_PROCUREMENT_PROVIDER", "procurement_fake")
    if provider != "openai":
        return _fake_requirement(message)
    api_key = config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("AGENTHARNESS_PROCUREMENT_PROVIDER=openai 但未配置 OPENAI_API_KEY，回退离线规则")
        return _fake_requirement(message)
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=config.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or None,
        )
        model = config.get("OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _REQUIREMENT_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0,
        )
        text = response.choices[0].message.content or ""
        return _coerce_requirement(json.loads(_extract_json(text)), message)
    except Exception:  # noqa: BLE001 - provider failure must not break the flow
        logger.warning("LLM 需求抽取失败，回退离线规则", exc_info=True)
        return _fake_requirement(message)


def _fake_requirement(message: str) -> dict[str, Any]:
    """Deterministic NL-to-structure extraction for the offline demo provider."""
    quantity = 10000
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(万|千)?", message)
    if match:
        number = float(match.group(1).replace(",", ""))
        factor = {"万": 10000, "千": 1000}.get(match.group(2) or "", 1)
        quantity = int(number * factor)
    width = _first_number(message, "宽", "width")
    length = _first_number(message, "长", "length")
    thickness = _first_number(message, "厚", "thickness")
    material = "瓦楞纸" if "瓦楞" in message else "PE" if re.search(r"(?<![A-Za-z0-9])PE(?![A-Za-z0-9])", message, re.IGNORECASE) else "未说明"
    color = "牛皮色" if "牛皮" in message else "白色" if "白色" in message else "未说明"
    max_lead = 15
    match = re.search(r"(\d+)\s*天.*?(?:交期|交货)", message) or re.search(r"交期(?:不超过|≤|<=|不高于)?\s*(\d+)\s*天", message)
    if match:
        max_lead = int(match.group(1))
    specifications = {
        "width_mm": str(width or 250),
        "length_mm": str(length or 350),
        "thickness_um": str(thickness or 60),
        "material": material,
        "color": color,
        "print_colors": 1 if "单色" in message else 0,
    }
    if "纸箱" in message or "箱" in message:
        specifications["height_mm"] = str(_first_number(message, "高", "height") or 250)
    constraints = {
        "base_currency": "CNY",
        "fx_rates": {"CNY": "1"},
        "max_lead_days": max(1, max_lead),
        "invoice_required": True,
        "size_tolerance_mm": "2",
        "thickness_tolerance_um": "3",
    }
    match = re.search(r"预算(?:不超过|≤|<=|不高于)?\s*([0-9.]+)", message)
    if match:
        constraints["max_landed_unit_cost"] = match.group(1)
    return {
        "schema_version": 1,
        "title": "采购询价",
        "category": "ecommerce_packaging",
        "item_name": "五层瓦楞纸箱" if "纸箱" in message else "快递袋" if "快递袋" in message else "包装耗材",
        "quantity": quantity,
        "unit": "piece",
        "specifications": specifications,
        "constraints": constraints,
    }


def _first_number(message: str, *keywords: str) -> float | int | None:
    for keyword in keywords:
        match = re.search(keyword + r"(?:\s*[为:：]?)\s*(\d+(?:\.\d+)?)", message)
        if match:
            value = float(match.group(1))
            return int(value) if value.is_integer() else value
    return None


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


class RpcClient:
    """Synchronous request/reply RPC over Kafka (10s timeout, one retry)."""

    def __init__(self, config: dict[str, str], hmac_key: str) -> None:
        self.config = config
        self.hmac_key = hmac_key
        self._futures: dict[str, tuple[Future, str]] = {}
        self._lock = threading.Lock()
        self.producer = None
        self.consumer = None
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
            key_serializer=lambda value: value.encode("utf-8") if isinstance(value, str) else value,
            value_serializer=lambda value: value if isinstance(value, bytes) else _canonical_json(value),
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
        assert self.consumer is not None
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

    def call(self, kind: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        for attempt in range(2):
            correlation_id = uuid.uuid4().hex
            request_sha = _sha256(payload)
            future: Future = Future()
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
            assert self.producer is not None
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

    def close(self) -> None:
        self._closed = True
        if self.consumer is not None:
            self.consumer.close()
        if self.producer is not None:
            self.producer.close()


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
        if len(self.hmac_key.encode("utf-8")) < 32:
            raise ValueError("AGENT_INTERNAL_HMAC_KEY 必须至少 32 字节")
        database_url = self.config.get("AGENTHARNESS_DATABASE_URL") or ""
        if not database_url:
            raise ValueError(
                "AGENTHARNESS_DATABASE_URL 未配置（本地开发示例见 .env.example，Docker Compose 自动注入）")
        self._db_kwargs = _parse_database_url(database_url)
        self._ensure_schema()
        self._seq_lock = threading.Lock()
        self._global_seq = max(self._max_global_seq(), self._topic_max_global_seq())
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE agent_sequence SET global_seq = %s WHERE id = 1", (self._global_seq,))
            conn.commit()
        finally:
            conn.close()
        self.producer = None
        self.consumer = None
        self._closed = False
        self.rpc = RpcClient(self.config, self.hmac_key)
        self.cache = AgentContextCache(self.config)

    # ---- MySQL runtime ----
    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(**self._db_kwargs)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                for statement in RUNTIME_SCHEMA_SQL:
                    cursor.execute(statement)
                cursor.execute(
                    "INSERT IGNORE INTO agent_sequence (id, global_seq) VALUES (1, 0)")
            conn.commit()
        finally:
            conn.close()

    def _max_global_seq(self) -> int:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COALESCE(MAX(global_seq), 0) FROM ("
                    "SELECT global_seq FROM runtime_event "
                    "UNION ALL SELECT global_seq FROM agent_sequence) AS seqs")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        finally:
            conn.close()

    def _topic_max_global_seq(self) -> int:
        """Recover the highest global_seq ever published from the events topic."""
        if KafkaConsumer is None:
            return 0
        consumer = KafkaConsumer(
            EVENTS_TOPIC,
            group_id="python-agent-seq-boot",
            bootstrap_servers=self._bootstrap(),
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            consumer_timeout_ms=4000,
            key_deserializer=lambda value: value.decode("utf-8") if value else "",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            **self._producer_sasl_only(),
        )
        maximum = 0
        try:
            for record in consumer:
                value = record.value
                if isinstance(value, dict):
                    try:
                        maximum = max(maximum, int(value.get("global_seq") or 0))
                    except (TypeError, ValueError):
                        pass
        finally:
            consumer.close()
        return maximum

    def _producer_sasl_only(self) -> dict[str, Any]:
        if not self.config.get("AGENT_KAFKA_SASL_USERNAME"):
            return {}
        return {
            "security_protocol": "SASL_PLAINTEXT",
            "sasl_mechanism": "SCRAM-SHA-256",
            "sasl_plain_username": self.config["AGENT_KAFKA_SASL_USERNAME"],
            "sasl_plain_password": self.config["AGENT_KAFKA_SASL_PASSWORD"],
        }

    def _next_global_seq(self) -> int:
        with self._seq_lock:
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE agent_sequence SET global_seq = LAST_INSERT_ID(global_seq + 1) WHERE id = 1")
                    cursor.execute("SELECT LAST_INSERT_ID()")
                    row = cursor.fetchone()
                    value = int(row[0]) if row else self._global_seq + 1
                conn.commit()
            finally:
                conn.close()
            self._global_seq = max(self._global_seq, value)
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
        if not operation_id or not operation_type:
            logger.warning("命令缺少 operation_id/operation_type")
            return
        if not _verify_envelope(self.hmac_key, envelope):
            logger.warning("命令签名校验失败：%s", operation_id)
            return
        if operation_type not in SUPPORTED_OPERATIONS:
            logger.warning("暂不支持的命令类型 %s（2a 最小切片仅 analyze）", operation_type)
            return
        if operation_type == "analyze" and envelope.get("message_type") == "ai_task.command":
            if not self._valid_ai_command(envelope):
                logger.warning("AI 任务命令字段无效：%s", operation_id)
                return
        payload = envelope.get("payload") or {}
        actual_sha = _sha256(payload)
        if payload_sha256 != actual_sha:
            logger.warning("命令 payload_sha256 不匹配：%s", operation_id)
            return
        existing = self._load_operation(operation_id)
        if existing is not None:
            if existing["payload_sha256"] != actual_sha:
                logger.warning("命令 payload 冲突（409）：%s", operation_id)
                self._publish_result(
                    operation_id,
                    envelope,
                    "failed",
                    {
                        "error_category": "VALIDATION",
                        "error_code": "OPERATION_PAYLOAD_CONFLICT",
                        "error_message": "operation_payload_conflict",
                        "retryable": False,
                    },
                    "operation_payload_conflict",
                )
                return
            if existing["result_published_at"] is not None:
                return  # 已发布，幂等跳过
            if existing["status"] == "accepted" and not existing["result"]:
                # 上次处理中断：以幂等方式重跑
                pass
            else:
                result = json.loads(existing["result"]) if existing["result"] else None
                status = existing["status"]
                error = existing["error"]
                self._publish_result(operation_id, envelope, status, result, error)
                self._persist_result(existing, status, result, error, published=True)
                return
        try:
            result, status, error = self._execute(operation_id, operation_type, envelope, payload, actual_sha)
        except Exception as exc:  # noqa: BLE001 - processing failures must surface, never leave a stuck command
            logger.exception("命令处理失败：%s", operation_id)
            failure = _failure_details(exc)
            result, status = failure, "failed"
            error = f"{failure['error_code']}: {failure['error_message']}"
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
                    "VALUES (%s, %s, %s, %s, %s, %s, 'accepted', NULL, NULL, NULL, %s, %s) "
                    "ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)",
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
        if operation_type == "start_conversation":
            session_id = hashlib.sha256((operation_id + ":session").encode("utf-8")).hexdigest()[:32]
            requirement = _llm_requirement(str(payload.get("message") or ""), self.config)
            quotes = []
            for attachment in payload.get("attachments") or []:
                quotes.append(self._import_quote(attachment, task_id, run_id))
            return {
                "requirement": requirement,
                "session_id": session_id,
                "run_id": run_id,
                "quotes": quotes,
            }, "completed", None
        if operation_type == "import_quote":
            quote = self._import_quote(payload, task_id, run_id)
            return {"quote": quote}, "completed", None
        if operation_type in ("reopen_task", "resume_run"):
            return {"run_id": run_id}, "completed", None
        if operation_type == "analyze":
            self.cache.put_run(
                run_id,
                {
                    "task_id": task_id,
                    "ai_task_id": envelope.get("ai_task_id"),
                    "operation_id": operation_id,
                    "status": "running",
                },
            )
            result = self._analyze_ai_task(operation_id, envelope, payload, task_id, run_id)
            self.cache.put_run(run_id, {"task_id": task_id, "operation_id": operation_id, "status": "completed"})
            return result, "completed", None
        return {}, "completed", None

    def _valid_ai_command(self, envelope: dict[str, Any]) -> bool:
        business_id = str(envelope.get("business_id") or "")
        aggregate_id = str(envelope.get("aggregate_id") or "")
        file_ids = envelope.get("file_ids")
        return bool(
            re.fullmatch(r"[0-9a-f]{32}", str(envelope.get("ai_task_id") or ""))
            and re.fullmatch(r"[0-9a-f]{32}", business_id)
            and business_id == aggregate_id
            and re.fullmatch(r"[0-9a-f]{32}", str(envelope.get("trace_id") or ""))
            and envelope.get("task_type") == "QUOTE_ANALYSIS"
            and isinstance(file_ids, list)
            and all(re.fullmatch(r"jb[0-9a-f]{32}", str(item)) for item in file_ids)
        )

    def _analyze_ai_task(
        self,
        operation_id: str,
        envelope: dict[str, Any],
        payload: dict[str, Any],
        task_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        steps = (
            ("INPUT_VALIDATE", 0.02, 0.10, "校验任务版本、输入指纹和报价集合"),
            ("ARTIFACT_FETCH", 0.12, 0.22, "读取 Java-owned 报价上下文与 Artifact 引用"),
            ("QUOTE_PARSE", 0.25, 0.45, "核对已持久化的结构化报价字段和来源"),
            ("RULE_ANALYSIS", 0.48, 0.70, "准备确定性规则分析输入，金额与排名仍由 Java 计算"),
            ("EXPLANATION", 0.75, 0.92, "生成解释摘要、风险标记和来源清单"),
            ("RESULT_PUBLISH", 0.95, 1.00, "发布结构化 AI 结果供 Java 幂等落库"),
        )
        self._emit("run_started", task_id, run_id, {
            "operation_id": operation_id,
            "ai_task_id": envelope.get("ai_task_id"),
            "trace_id": envelope.get("trace_id"),
        })
        sequence = 1
        context: dict[str, Any] = {}
        reference_interval: dict[str, Any] | None = None
        for step, started_progress, finished_progress, summary in steps:
            self._emit_ai_step(
                envelope, task_id, run_id, step, "RUNNING", started_progress, sequence, summary
            )
            sequence += 1
            if step == "ARTIFACT_FETCH":
                fetched = self.rpc.call("get_task_context", {"task_id": task_id})
                context = fetched if isinstance(fetched, dict) else {}
                # K5 历史报价 RAG：参考区间为软提示（冻结设计 4.10）——
                # RPC 失败/样本不足时静默降级，不参与排序、不排除报价、不影响评测。
                try:
                    reference = self.rpc.call(
                        "get_reference_prices",
                        {
                            "task_id": task_id,
                            "item_name": str(context.get("item_name") or ""),
                            "category": str(context.get("category") or ""),
                        },
                    )
                    interval = reference.get("interval") if isinstance(reference, dict) else None
                    reference_interval = interval if isinstance(interval, dict) else None
                except Exception:
                    reference_interval = None
            self._emit_ai_step(
                envelope, task_id, run_id, step, "SUCCEEDED", finished_progress, sequence, summary
            )
            sequence += 1
        result = self._explanation_result(envelope, payload, context, run_id, reference_interval)
        self._emit("run_completed", task_id, run_id, {
            "operation_id": operation_id,
            "ai_task_id": envelope.get("ai_task_id"),
            "trace_id": envelope.get("trace_id"),
        })
        return result

    def _explanation_result(
        self,
        envelope: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
        run_id: str,
        reference_interval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from agentharness.procurement.parsing import PARSER_VERSION
        from agentharness.procurement.reference_prices import apply_reference_interval

        quotes = context.get("quotes") if isinstance(context.get("quotes"), list) else []
        unresolved = sum(
            len(quote.get("review_fields") or [])
            for quote in quotes
            if isinstance(quote, dict)
        )
        risk_flags: list[str] = []
        if unresolved:
            risk_flags.append("UNRESOLVED_FIELDS")
        if len(quotes) < 2:
            risk_flags.append("INSUFFICIENT_QUOTES")
        sources = self._analysis_sources(quotes)
        provider = (
            self.config.get("AGENTHARNESS_PROCUREMENT_PROVIDER")
            or os.environ.get("AGENTHARNESS_PROCUREMENT_PROVIDER")
            or "procurement_fake"
        )
        model = (
            self.config.get("AGENTHARNESS_PROCUREMENT_MODEL")
            or self.config.get("OPENAI_MODEL")
            or os.environ.get("AGENTHARNESS_PROCUREMENT_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "deterministic"
        )
        return {
            "run_id": run_id,
            "ai_task_id": envelope.get("ai_task_id"),
            "business_id": envelope.get("business_id") or envelope.get("aggregate_id"),
            "generation": envelope.get("generation"),
            "input_sha256": payload.get("input_sha256"),
            "raw_result": {
                "quote_count": len(quotes),
                "source_count": len(sources),
                "unresolved_field_count": unresolved,
            },
            "structured_result": apply_reference_interval(
                {
                    "schema_version": 1,
                    "summary": (
                        f"已核对 {len(quotes)} 份报价的结构化输入与来源；"
                        "金额、资格与排序由 Java 确定性规则计算。"
                    ),
                    "risk_flags": risk_flags,
                    "quote_count": len(quotes),
                    "unresolved_field_count": unresolved,
                },
                reference_interval,
                quotes,
            ),
            "sources": sources,
            "provider": provider,
            "model": model,
            "prompt_version": "quote-analysis-v1",
            "parser_version": PARSER_VERSION,
        }

    def _analysis_sources(self, quotes: list[Any]) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for quote in quotes:
            if not isinstance(quote, dict):
                continue
            artifact_id = str(quote.get("source_artifact_id") or "")
            if not re.fullmatch(r"jb[0-9a-f]{32}", artifact_id):
                continue
            locator = str(quote.get("source_filename") or "原始报价")
            excerpt = str(quote.get("supplier_name") or "")
            confidence = 1.0
            method = str(quote.get("parser_version") or "structured_quote")
            fields = (quote.get("extracted") or {}).get("fields")
            if isinstance(fields, dict):
                for raw in fields.values():
                    if not isinstance(raw, dict) or not isinstance(raw.get("source"), dict):
                        continue
                    source = raw["source"]
                    locator = str(source.get("locator") or locator)
                    excerpt = str(source.get("excerpt") or excerpt)
                    method = str(source.get("method") or method)
                    try:
                        confidence = max(0.0, min(1.0, float(raw.get("confidence", 1))))
                    except (TypeError, ValueError):
                        confidence = 0.0
                    break
            sources.append({
                "artifact_id": artifact_id,
                "locator": locator,
                "excerpt": excerpt,
                "confidence": confidence,
                "method": method,
            })
        return sources

    def _import_quote(self, payload: dict[str, Any], task_id: str, run_id: str) -> dict[str, Any]:
        artifact_id = str(payload.get("artifact_id") or "")
        filename = str(payload.get("filename") or "")
        artifact = self.rpc.call("get_artifact", {"artifact_id": artifact_id})
        data = base64.b64decode(artifact.get("base64") or "")
        from agentharness.procurement.parsing import (
            PARSER_VERSION,
            fields_requiring_review,
            parse_quote,
        )
        self._emit("tool_call_start", task_id, run_id,
                   {"tool": "parse_quote", "filename": filename, "artifact_id": artifact_id})
        extracted = parse_quote(filename, data)
        extracted["review_fields"] = fields_requiring_review(extracted)
        status = "needs_review" if extracted["review_fields"] else "ready"
        self._emit("tool_result", task_id, run_id,
                   {"tool": "parse_quote", "filename": filename, "artifact_id": artifact_id,
                    "status": "ok", "review_fields": len(extracted["review_fields"])})
        supplier = None
        fields = extracted.get("fields") or {}
        supplier_entry = fields.get("supplier_name") if isinstance(fields, dict) else None
        if isinstance(supplier_entry, dict):
            supplier = supplier_entry.get("value")
        return {
            "artifact_id": artifact_id,
            "task_id": task_id,
            "supplier_name": supplier,
            "extracted": extracted,
            "status": status,
            "parser_version": PARSER_VERSION,
            "processing_ms": "0",
        }

    def _publish_result(self, operation_id: str, envelope: dict[str, Any], status: str,
                        result: dict[str, Any] | None, error: str | None) -> None:
        payload_sha256 = str(envelope.get("payload_sha256") or "")
        failure = result if status == "failed" and isinstance(result, dict) else {}
        if status == "failed" and not failure.get("error_category"):
            failure = _failure_details(error or "Agent execution failed")
        message = {
            "schema_version": 1,
            "message_type": "ai_task.result" if envelope.get("ai_task_id") else "agent.result",
            "operation_id": operation_id,
            "aggregate_id": envelope.get("aggregate_id"),
            "ai_task_id": envelope.get("ai_task_id"),
            "business_id": envelope.get("business_id") or envelope.get("aggregate_id"),
            "trace_id": envelope.get("trace_id"),
            "task_type": envelope.get("task_type"),
            "generation": envelope.get("generation"),
            "expected_task_version": envelope.get("expected_task_version"),
            "payload_sha256": payload_sha256,
            "status": status,
            "result": result or {},
            "error": error,
            "processed_at": _utcnow().isoformat() + "Z",
        }
        if status == "failed":
            message.update({
                "error_category": failure["error_category"],
                "error_code": failure["error_code"],
                "error_message": failure["error_message"],
                "retryable": bool(failure["retryable"]),
            })
        message["signature"] = _sign_envelope(self.hmac_key, message)
        assert self.producer is not None
        self.producer.send(RESULTS_TOPIC, key=operation_id, value=message)
        self.producer.flush()

    def _emit_ai_step(
        self,
        command: dict[str, Any],
        task_id: str,
        run_id: str,
        step: str,
        step_status: str,
        progress: float,
        sequence: int,
        summary: str,
    ) -> None:
        payload = {
            "step": step,
            "step_status": step_status,
            "progress": progress,
            "summary": summary,
        }
        occurred_at = _utcnow().isoformat() + "Z"
        message = {
            "schema_version": 1,
            "message_type": "ai_task.event",
            "type": "ai_task.step",
            "event_id": _new_id(),
            "operation_id": command.get("operation_id"),
            "ai_task_id": command.get("ai_task_id"),
            "business_id": command.get("business_id") or command.get("aggregate_id"),
            "trace_id": command.get("trace_id"),
            "generation": command.get("generation"),
            "event_type": "STEP_STARTED" if step_status == "RUNNING" else "STEP_SUCCEEDED",
            "step": step,
            "step_status": step_status,
            "status": "RUNNING",
            "progress": progress,
            "attempt": 1,
            "sequence": sequence,
            "summary": summary,
            "payload": payload,
            "occurred_at": occurred_at,
            "global_seq": self._next_global_seq(),
            "task_id": task_id,
            "run_id": run_id,
            "payload_sha256": _sha256(payload),
        }
        message["signature"] = _sign_envelope(self.hmac_key, message)
        assert self.producer is not None
        self.producer.send(EVENTS_TOPIC, key=task_id, value=message)
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO runtime_event (global_seq, task_id, run_id, type, payload, occurred_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        message["global_seq"],
                        task_id,
                        run_id,
                        "ai_task.step",
                        json.dumps(payload, ensure_ascii=False),
                        _utcnow(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

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
        }
        message["signature"] = _sign_envelope(self.hmac_key, message)
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
                    heartbeat = {
                        "type": "heartbeat.ping",
                        "task_id": "",
                        "run_id": "",
                        "global_seq": self._next_global_seq(),
                        "payload": payload,
                        "occurred_at": _utcnow().isoformat() + "Z",
                        "payload_sha256": payload_sha256,
                    }
                    heartbeat["signature"] = _sign_envelope(self.hmac_key, heartbeat)
                    self.producer.send(EVENTS_TOPIC, key="agent", value=heartbeat)
                    self.producer.flush()
            except Exception:  # noqa: BLE001
                logger.exception("心跳发布失败")
            time.sleep(5)

    # ---- lifecycle ----
    def start_health_server(self) -> None:
        """Minimal internal HTTP health endpoint (AGENT_PORT, default 8742)."""
        import http.server

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/api/health"):
                    body = b'{"status":"ok","service":"procurement_agent","kafka":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args) -> None:
                return

        port = int(self.config.get("AGENT_PORT") or os.environ.get("AGENT_PORT", "8742"))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info("Agent 健康端点已启动：127.0.0.1:%s/api/health", port)

    # ---- lifecycle ----

    def start(self) -> None:
        if KafkaConsumer is None or KafkaProducer is None:
            raise RuntimeError("kafka-python 未安装")
        self.producer = KafkaProducer(**self._producer_config())
        self.consumer = KafkaConsumer(COMMANDS_TOPIC, **self._consumer_config())
        self.rpc.start()
        logger.info("Python Agent 服务已启动（Kafka=%s, db=%s）", self._bootstrap(), self._db_kwargs["database"])
        self.start_health_server()
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
    # Local dev (README): `uv run python -m agentharness.agent_service` relies on the
    # repository .env for AGENTHARNESS_DATABASE_URL / AGENT_INTERNAL_HMAC_KEY / Kafka.
    # Docker Compose sets the same variables explicitly and wins via setdefault.
    load_project_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    service = AgentService()
    service.start()


if __name__ == "__main__":
    main()
