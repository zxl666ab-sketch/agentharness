"""Durable RAG knowledge chunks for approved procurement history.

This module is the single SQL owner for ``rag_chunks`` and its FTS5 index.
Only approved (decided) historical records are allowed into the index; the
index is advisory reference material and never participates in deterministic
analysis inputs.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _dumps, _utcnow

_FTS_INDEX_COLUMNS = ("item_name", "supplier_name", "content")


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


_FTS_FALLBACK_MARKERS = (
    "fts5:",
    "no such module: fts5",
    "no such table: rag_chunks_fts",
    "syntax error",
    "malformed match",
    "unterminated string",
)


def _fts_terms(query: str) -> list[str]:
    """Split a free-text query into FTS5-safe prefix terms (CJK-aware)."""
    return [term for term in query.replace("，", " ").replace(",", " ").split() if term]


def _should_fallback_to_like(exc: sqlite3.OperationalError) -> bool:
    """Only FTS availability/syntax errors degrade to LIKE; real database
    errors (locked, disk I/O, …) must keep propagating instead of silently
    returning a wrong (or empty) result."""
    message = str(exc)
    return any(marker in message for marker in _FTS_FALLBACK_MARKERS)


class RagRepo:
    """CRUD + FTS/LIKE search over ``rag_chunks``."""

    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

    def upsert_chunk(self, chunk: dict[str, Any]) -> None:
        """Insert or replace a chunk by ``chunk_sha256`` (idempotent rebuild)."""
        safe = self.redactor.redact_obj(chunk)
        now = safe.get("updated_at") or safe.get("created_at") or _utcnow()
        with self._lock:
            self._conn.execute(
                """INSERT INTO rag_chunks(
                    chunk_sha256, request_id, quote_id, artifact_id, artifact_sha256,
                    request_reference, supplier_name, item_name, category,
                    specifications_json, unit_price, currency, landed_unit_cost,
                    lead_days, moq, decision, decision_at, content,
                    quality_flags_json, embedding, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chunk_sha256) DO UPDATE SET
                    request_id=excluded.request_id,
                    quote_id=excluded.quote_id,
                    artifact_id=excluded.artifact_id,
                    artifact_sha256=excluded.artifact_sha256,
                    request_reference=excluded.request_reference,
                    supplier_name=excluded.supplier_name,
                    item_name=excluded.item_name,
                    category=excluded.category,
                    specifications_json=excluded.specifications_json,
                    unit_price=excluded.unit_price,
                    currency=excluded.currency,
                    landed_unit_cost=excluded.landed_unit_cost,
                    lead_days=excluded.lead_days,
                    moq=excluded.moq,
                    decision=excluded.decision,
                    decision_at=excluded.decision_at,
                    content=excluded.content,
                    quality_flags_json=excluded.quality_flags_json,
                    embedding=excluded.embedding,
                    updated_at=excluded.updated_at""",
                (
                    safe["chunk_sha256"],
                    safe["request_id"],
                    safe.get("quote_id"),
                    safe["artifact_id"],
                    safe["artifact_sha256"],
                    safe["request_reference"],
                    safe["supplier_name"],
                    safe["item_name"],
                    safe["category"],
                    _dumps(safe.get("specifications", {})),
                    safe.get("unit_price"),
                    safe.get("currency"),
                    safe.get("landed_unit_cost"),
                    safe.get("lead_days"),
                    safe.get("moq"),
                    safe["decision"],
                    safe["decision_at"],
                    safe["content"],
                    _dumps(safe.get("quality_flags", [])),
                    safe.get("embedding"),
                    safe.get("created_at", now),
                    now,
                ),
            )

    def get_chunk(self, chunk_sha256: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM rag_chunks WHERE chunk_sha256 = ?", (chunk_sha256,)
        ).fetchone()
        return _decode_row(row, "specifications_json", "quality_flags_json")

    def delete_chunk(self, chunk_sha256: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rag_chunks WHERE chunk_sha256 = ?", (chunk_sha256,)
            )
        return cursor.rowcount == 1

    def delete_chunks_for_quote(self, quote_id: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rag_chunks WHERE quote_id = ?", (quote_id,)
            )
        return cursor.rowcount

    def delete_chunks_for_request(self, request_id: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rag_chunks WHERE request_id = ?", (request_id,)
            )
        return cursor.rowcount

    def count_chunks(self) -> int:
        row = self._reader().execute(
            "SELECT COUNT(*) AS count FROM rag_chunks"
        ).fetchone()
        return int(row["count"]) if row else 0

    def list_chunks(self, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            """SELECT * FROM rag_chunks
               ORDER BY decision_at DESC, chunk_sha256 ASC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [
            item
            for row in rows
            if (item := _decode_row(row, "specifications_json", "quality_flags_json"))
            is not None
        ]

    def list_chunks_by_quote(self, quote_id: str) -> list[dict[str, Any]]:
        rows = self._reader().execute(
            "SELECT * FROM rag_chunks WHERE quote_id = ? ORDER BY decision_at DESC",
            (quote_id,),
        ).fetchall()
        return [
            item
            for row in rows
            if (item := _decode_row(row, "specifications_json", "quality_flags_json"))
            is not None
        ]

    def fts_search(self, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """FTS5 keyword recall over item_name/supplier_name/content.

        Falls back to a LIKE scan when FTS5 is unavailable or the query cannot
        be tokenized (for example a single CJK phrase without exact token).
        """
        terms = _fts_terms(query)
        if not terms:
            # Empty/whitespace-only query must never match the whole table via
            # the LIKE fallback (LIKE '%%' matches every row).
            return []
        match = " AND ".join(f'"{term}"*' for term in terms)
        try:
            rows = self._reader().execute(
                """SELECT c.* FROM rag_chunks_fts AS f
                   JOIN rag_chunks AS c ON c.rowid = f.rowid
                   WHERE rag_chunks_fts MATCH ?
                   ORDER BY bm25(rag_chunks_fts) ASC, c.decision_at DESC
                   LIMIT ?""",
                (match, limit),
            ).fetchall()
            decoded = [
                item
                for row in rows
                if (
                    item := _decode_row(
                        row, "specifications_json", "quality_flags_json"
                    )
                )
                is not None
            ]
            if decoded:
                return decoded
        except sqlite3.OperationalError as exc:
            if not _should_fallback_to_like(exc):
                # e.g. "database is locked" / "disk I/O error": re-raise instead
                # of silently returning LIKE results.
                raise
            # FTS5 may be unavailable on unusual builds or reject the query
            # syntax; LIKE fallback below.
        return self._like_search(query, limit=limit)

    def _like_search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        escaped = (
            str(query or "")
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        rows = self._reader().execute(
            """SELECT * FROM rag_chunks
               WHERE item_name LIKE ? ESCAPE '\\' OR supplier_name LIKE ? ESCAPE '\\'
                  OR content LIKE ? ESCAPE '\\'
               ORDER BY decision_at DESC, chunk_sha256 ASC LIMIT ?""",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [
            item
            for row in rows
            if (item := _decode_row(row, "specifications_json", "quality_flags_json"))
            is not None
        ]


__all__ = ["RagRepo", "_FTS_INDEX_COLUMNS"]
