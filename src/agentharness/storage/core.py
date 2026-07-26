"""Shared SQLite connection management for the storage repositories."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.storage.migrations import apply_migrations


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class StorageCore:
    """Owns the single writer connection, its lock, and per-thread read-only
    connections. WAL lets readers run concurrently with the single writer
    without acquiring the writer lock, so API reads never contend with
    child-run event writes.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # manual transactions
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with self.lock:
            apply_migrations(self.conn)
        # Each thread gets its own RO connection because a single sqlite3
        # connection is not safe for concurrent use.
        self._read_local = threading.local()
        self._read_conns: list[sqlite3.Connection] = []
        self._read_conns_lock = threading.Lock()

    def reader(self) -> sqlite3.Connection:
        """Return this thread's read-only connection, creating it on first use."""
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    def reset_readers(self) -> None:
        """Close every read-only connection (required before VACUUM/compaction)."""
        with self._read_conns_lock:
            for conn in self._read_conns:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            self._read_conns.clear()
            self._read_local = threading.local()

    def close(self) -> None:
        with self.lock:
            self.conn.close()
        self.reset_readers()

    def integrity_check(self) -> str:
        with self.lock:
            row = self.conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def schema_version(self) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row[0]) if row else 0
