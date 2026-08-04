"""Durable procurement requests, quotes, comparisons, decisions, and audit events."""

from __future__ import annotations

import json
from typing import Any

from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps, _utcnow


def _decode_row(row: Any, *json_columns: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in json_columns:
        raw = result.pop(column, "{}")
        try:
            result[column.removesuffix("_json")] = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            result[column.removesuffix("_json")] = {}
    return result


def _decode_request_row(row: Any) -> dict[str, Any] | None:
    result = _decode_row(row, "specifications_json", "constraints_json")
    if result is None:
        return None
    schema_version = int(result.get("schema_version") or 1)
    if schema_version >= 2 and result.get("quantity_decimal") not in (None, ""):
        result["quantity"] = str(result["quantity_decimal"])
    result.pop("quantity_decimal", None)
    result["schema_version"] = schema_version
    return result


class ProcurementRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

    def create_request(self, request: dict[str, Any]) -> None:
        safe = self.redactor.redact_obj(request)
        now = safe.get("created_at") or _utcnow()
        schema_version = int(safe.get("schema_version") or 1)
        quantity_decimal = str(safe.get("quantity_decimal") or safe.get("quantity"))
        legacy_quantity = 0 if schema_version >= 2 else int(safe["quantity"])
        with self._lock:
            self._conn.execute(
                """INSERT INTO procurement_requests(
                    id, reference, title, category, item_name, quantity, unit,
                    specifications_json, constraints_json, status, session_id,
                    created_at, updated_at, schema_version, quantity_decimal
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    safe["id"],
                    safe["reference"],
                    safe["title"],
                    safe["category"],
                    safe["item_name"],
                    legacy_quantity,
                    safe["unit"],
                    _dumps(safe.get("specifications", {})),
                    _dumps(safe.get("constraints", {})),
                    safe.get("status", "draft"),
                    safe["session_id"],
                    now,
                    now,
                    schema_version,
                    quantity_decimal,
                ),
            )

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM procurement_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return _decode_request_row(row)

    def list_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            """SELECT * FROM procurement_requests
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            item
            for row in rows
            if (item := _decode_request_row(row))
            is not None
        ]

    def delete_request(self, request_id: str) -> bool:
        """Remove one procurement projection while preserving global run history."""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                exists = self._conn.execute(
                    "SELECT 1 FROM procurement_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                if exists is None:
                    self._conn.execute("COMMIT")
                    return False

                # Delete children first because the procurement tables use
                # foreign keys without cascade actions.
                for table in (
                    "procurement_decisions",
                    "procurement_audit_events",
                    "procurement_comparison_snapshots",
                    "procurement_quotes",
                ):
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE request_id = ?",
                        (request_id,),
                    )
                self._conn.execute(
                    "DELETE FROM procurement_requests WHERE id = ?",
                    (request_id,),
                )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def update_request(self, request_id: str, **changes: Any) -> bool:
        allowed = {
            "status",
            "analysis_run_id",
            "current_snapshot_id",
            "approved_quote_id",
            "schema_version",
            "title",
            "item_name",
            "quantity",
            "unit",
            "specifications",
            "constraints",
        }
        assignments: list[str] = []
        values: list[Any] = []
        schema_row = self._conn.execute(
            "SELECT schema_version FROM procurement_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        schema_version = int(changes.get("schema_version") or (schema_row[0] if schema_row else 1))
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"unsupported procurement request field: {key}")
            safe_value = self.redactor.redact_obj(value)
            if key == "quantity":
                assignments.extend(["quantity = ?", "quantity_decimal = ?"])
                values.extend([
                    0 if schema_version >= 2 else int(value),
                    str(value),
                ])
            else:
                column = f"{key}_json" if key in {"specifications", "constraints"} else key
                assignments.append(f"{column} = ?")
                values.append(_dumps(safe_value) if column.endswith("_json") else safe_value)
        if not assignments:
            return False
        assignments.append("updated_at = ?")
        values.extend([_utcnow(), request_id])
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE procurement_requests SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
        return cursor.rowcount == 1

    def create_quote(self, quote: dict[str, Any]) -> None:
        safe = self.redactor.redact_obj(quote)
        now = safe.get("created_at") or _utcnow()
        with self._lock:
            self._conn.execute(
                """INSERT INTO procurement_quotes(
                    id, request_id, supplier_name, source_filename, source_kind,
                    source_artifact_id, source_sha256, extracted_json, status,
                    review_count, parser_version, processing_ms, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    safe["id"],
                    safe["request_id"],
                    safe["supplier_name"],
                    safe["source_filename"],
                    safe["source_kind"],
                    safe["source_artifact_id"],
                    safe["source_sha256"],
                    _dumps(safe["extracted"]),
                    safe["status"],
                    safe.get("review_count", 0),
                    safe["parser_version"],
                    safe.get("processing_ms", 0),
                    now,
                    now,
                ),
            )

    def get_quote(self, quote_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM procurement_quotes WHERE id = ?", (quote_id,)
        ).fetchone()
        return _decode_row(row, "extracted_json")

    def list_quotes(self, request_id: str) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            """SELECT * FROM procurement_quotes
               WHERE request_id = ? ORDER BY created_at ASC""",
            (request_id,),
        ).fetchall()
        return [item for row in rows if (item := _decode_row(row, "extracted_json"))]

    def update_quote(
        self,
        quote_id: str,
        *,
        extracted: dict[str, Any],
        supplier_name: str,
        status: str,
        review_count: int,
    ) -> bool:
        safe = self.redactor.redact_obj(extracted)
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE procurement_quotes
                   SET extracted_json = ?, supplier_name = ?, status = ?,
                       review_count = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    _dumps(safe),
                    self.redactor.redact_text(supplier_name),
                    status,
                    review_count,
                    _utcnow(),
                    quote_id,
                ),
            )
        return cursor.rowcount == 1

    def create_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        safe = self.redactor.redact_obj(snapshot)
        with self._lock:
            if self._conn.in_transaction:
                return self._create_snapshot_unlocked(safe)
            self._conn.execute("BEGIN")
            try:
                result = self._create_snapshot_unlocked(safe)
                self._conn.execute("COMMIT")
                return result
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _create_snapshot_unlocked(self, safe: dict[str, Any]) -> dict[str, Any]:
        now = safe.get("created_at") or _utcnow()
        row = self._conn.execute(
            """SELECT COALESCE(MAX(version), 0) + 1
               FROM procurement_comparison_snapshots WHERE request_id = ?""",
            (safe["request_id"],),
        ).fetchone()
        version = int(row[0])
        self._conn.execute(
            """INSERT INTO procurement_comparison_snapshots(
                id, request_id, run_id, version, input_sha256,
                result_json, artifact_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                safe["id"],
                safe["request_id"],
                safe["run_id"],
                version,
                safe["input_sha256"],
                _dumps(safe["result"]),
                safe["artifact_id"],
                now,
            ),
        )
        return {**safe, "version": version, "created_at": now}

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM procurement_comparison_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return _decode_row(row, "result_json")

    def list_snapshots(self, request_id: str) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            """SELECT * FROM procurement_comparison_snapshots
               WHERE request_id = ? ORDER BY version DESC""",
            (request_id,),
        ).fetchall()
        return [item for row in rows if (item := _decode_row(row, "result_json"))]

    def create_decision(self, decision: dict[str, Any]) -> None:
        safe = self.redactor.redact_obj(decision)
        with self._lock:
            self._insert_decision(safe)

    def commit_decision(
        self,
        decision: dict[str, Any],
        audit_event: dict[str, Any],
    ) -> None:
        safe_decision = self.redactor.redact_obj(decision)
        safe_event = self.redactor.redact_obj(audit_event)
        decision_kind = str(safe_decision.get("decision") or "")
        if decision_kind not in {"approved", "no_award"}:
            raise ValueError("unsupported procurement decision")
        quote_id = safe_decision.get("quote_id")
        if decision_kind == "approved" and not quote_id:
            raise ValueError("approved procurement decision requires quote_id")
        if decision_kind == "no_award" and quote_id:
            raise ValueError("no_award procurement decision cannot contain quote_id")
        status = "approved" if decision_kind == "approved" else "no_award"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._insert_decision(safe_decision)
                cursor = self._conn.execute(
                    """UPDATE procurement_requests
                       SET status = ?, approved_quote_id = ?, updated_at = ?
                       WHERE id = ? AND status NOT IN ('approved', 'no_award')""",
                    (
                        status,
                        quote_id,
                        _utcnow(),
                        safe_decision["request_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("采购任务不存在或已经完成审批")
                self._insert_audit_event(safe_event)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _insert_decision(self, safe: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO procurement_decisions(
                id, request_id, snapshot_id, quote_id, run_id, approval_id,
                decision, note, actor, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                safe["id"],
                safe["request_id"],
                safe["snapshot_id"],
                safe.get("quote_id"),
                safe["run_id"],
                safe["approval_id"],
                safe["decision"],
                safe.get("note"),
                safe["actor"],
                safe.get("created_at", _utcnow()),
            ),
        )

    def get_decision(self, request_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM procurement_decisions WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def add_audit_event(self, event: dict[str, Any]) -> None:
        safe = self.redactor.redact_obj(event)
        with self._lock:
            self._insert_audit_event(safe)

    def _insert_audit_event(self, safe: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO procurement_audit_events(
                id, request_id, quote_id, run_id, type, actor,
                payload_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                safe["id"],
                safe["request_id"],
                safe.get("quote_id"),
                safe.get("run_id"),
                safe["type"],
                safe.get("actor", "system"),
                _dumps(safe.get("payload", {})),
                safe.get("created_at", _utcnow()),
            ),
        )

    def list_audit_events(self, request_id: str) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            """SELECT * FROM procurement_audit_events
               WHERE request_id = ? ORDER BY created_at ASC""",
            (request_id,),
        ).fetchall()
        return [item for row in rows if (item := _decode_row(row, "payload_json"))]
