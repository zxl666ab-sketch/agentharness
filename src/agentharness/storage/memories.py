"""Long-term memories with FTS5 search and content-hash dedup."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from agentharness.contracts import new_id
from agentharness.security.redaction import Redactor
from agentharness.storage.core import StorageCore, _utcnow


class MemoryRepo:
    def __init__(self, core: StorageCore, redactor: Redactor) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.redactor = redactor

    @staticmethod
    def _memory_content_hash(content: str) -> str:
        normalized = " ".join(content.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _backfill_memory_metadata_unlocked(self) -> None:
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM memories WHERE content_hash = ''"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                """UPDATE memories
                   SET content_hash = ?, updated_at = COALESCE(updated_at, created_at)
                   WHERE id = ?""",
                (self._memory_content_hash(str(row["content"])), row["id"]),
            )

    def _delete_memory_fts_unlocked(self, row: sqlite3.Row) -> None:
        self._conn.execute(
            """INSERT INTO memories_fts(
                   memories_fts, rowid, content, source, scope
               ) VALUES('delete', ?, ?, ?, ?)""",
            (row["rowid"], row["content"], row["source"], row["scope"]),
        )

    def add_memory(
        self,
        content: str,
        *,
        source: str = "tool",
        scope: str = "global",
        memory_id: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        mid = memory_id or new_id()
        now = _utcnow()
        content = self.redactor.redact_text(content)
        source = self.redactor.redact_text(source)
        scope = self.redactor.redact_text(scope).strip() or "global"
        content_hash = self._memory_content_hash(content)
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                existing = self._conn.execute(
                    """SELECT id FROM memories
                       WHERE scope = ? AND content_hash = ?
                       ORDER BY created_at ASC LIMIT 1""",
                    (scope, content_hash),
                ).fetchone()
                if existing:
                    self._conn.execute("COMMIT")
                    return str(existing["id"])
                self._conn.execute(
                    """INSERT INTO memories(
                           id, content, source, scope, created_at, last_used_at,
                           content_hash, updated_at, expires_at, use_count
                       ) VALUES(?,?,?,?,?,?,?,?,?,0)""",
                    (
                        mid,
                        content,
                        source,
                        scope,
                        now,
                        now,
                        content_hash,
                        now,
                        expires_at,
                    ),
                )
                # FTS content sync (external content table)
                self._conn.execute(
                    """INSERT INTO memories_fts(rowid, content, source, scope)
                       SELECT rowid, content, source, scope FROM memories WHERE id = ?""",
                    (mid,),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return mid

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        row = self._reader().execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_memories(
        self,
        *,
        scope: str | None = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        values: list[Any] = []
        if scope is not None:
            where.append("scope = ?")
            values.append(scope)
        if not include_expired:
            where.append("(expires_at IS NULL OR expires_at > ?)")
            values.append(_utcnow())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        values.append(max(1, min(limit, 1000)))
        rows = self._reader().execute(
            f"""SELECT * FROM memories {clause}
                ORDER BY updated_at DESC, created_at DESC LIMIT ?""",
            values,
        ).fetchall()
        return [dict(row) for row in rows]

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        source: str | None = None,
        scope: str | None = None,
        expires_at: str | None = None,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT rowid, * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(memory_id)
                if expected_hash and str(row["content_hash"]) != expected_hash.removeprefix(
                    "sha256:"
                ):
                    raise ValueError("memory version conflict")
                new_content = self.redactor.redact_text(content or str(row["content"]))
                new_source = self.redactor.redact_text(source or str(row["source"] or "tool"))
                new_scope = self.redactor.redact_text(scope or str(row["scope"] or "global"))
                new_hash = self._memory_content_hash(new_content)
                new_expiry = row["expires_at"] if expires_at is None else (expires_at or None)
                duplicate = self._conn.execute(
                    """SELECT id FROM memories
                       WHERE scope = ? AND content_hash = ? AND id <> ? LIMIT 1""",
                    (new_scope, new_hash, memory_id),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"duplicate memory: {duplicate['id']}")
                self._delete_memory_fts_unlocked(row)
                self._conn.execute(
                    """UPDATE memories SET
                           content = ?, source = ?, scope = ?, content_hash = ?,
                           updated_at = ?, expires_at = ?
                       WHERE id = ?""",
                    (
                        new_content,
                        new_source,
                        new_scope,
                        new_hash,
                        _utcnow(),
                        new_expiry,
                        memory_id,
                    ),
                )
                self._conn.execute(
                    """INSERT INTO memories_fts(rowid, content, source, scope)
                       SELECT rowid, content, source, scope FROM memories WHERE id = ?""",
                    (memory_id,),
                )
                updated = self._conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        assert updated is not None
        return dict(updated)

    def delete_memory(self, memory_id: str, *, expected_hash: str | None = None) -> bool:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                row = self._conn.execute(
                    "SELECT rowid, * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return False
                if expected_hash and str(row["content_hash"]) != expected_hash.removeprefix(
                    "sha256:"
                ):
                    raise ValueError("memory version conflict")
                self._delete_memory_fts_unlocked(row)
                self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def search_memories(
        self,
        query: str,
        limit: int = 5,
        *,
        scopes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        requested_scopes = list(dict.fromkeys(scopes or ["global"]))
        if not requested_scopes:
            return []
        max_results = max(1, min(limit, 100))
        found: list[sqlite3.Row] = []
        with self._lock:
            for scope_name in requested_scopes:
                remaining = max_results - len(found)
                if remaining <= 0:
                    break
                try:
                    rows = self._conn.execute(
                        """SELECT m.*, bm25(memories_fts) AS bm25_score,
                                  (1.0 / (1.0 + MAX(
                                      0.0, julianday('now') - julianday(m.last_used_at)
                                  ))) AS freshness_score,
                                  (bm25(memories_fts) - 0.1 * (1.0 / (1.0 + MAX(
                                      0.0, julianday('now') - julianday(m.last_used_at)
                                  )))) AS rank_score
                           FROM memories AS m
                           JOIN memories_fts ON m.rowid = memories_fts.rowid
                           WHERE memories_fts MATCH ? AND m.scope = ?
                             AND (m.expires_at IS NULL OR m.expires_at > ?)
                           ORDER BY rank_score ASC, m.updated_at DESC
                           LIMIT ?""",
                        (query, scope_name, _utcnow(), remaining),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = self._conn.execute(
                        """SELECT *, NULL AS bm25_score, 0.0 AS freshness_score,
                                  0.0 AS rank_score
                           FROM memories
                           WHERE content LIKE ? AND scope = ?
                             AND (expires_at IS NULL OR expires_at > ?)
                           ORDER BY updated_at DESC LIMIT ?""",
                        (f"%{query}%", scope_name, _utcnow(), remaining),
                    ).fetchall()
                found.extend(rows)
            for row in found:
                self._conn.execute(
                    """UPDATE memories
                       SET last_used_at = ?, use_count = use_count + 1
                       WHERE id = ?""",
                    (_utcnow(), row["id"]),
                )
        return [dict(row) for row in found]
