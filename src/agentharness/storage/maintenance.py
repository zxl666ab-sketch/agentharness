"""Explicit maintenance: stats, garbage collection, and compaction."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentharness.storage.artifacts import ArtifactStore
from agentharness.storage.core import StorageCore, _utcnow


class MaintenanceOps:
    def __init__(self, core: StorageCore, *, artifacts: ArtifactStore) -> None:
        self._core = core
        self._lock = core.lock
        self._conn = core.conn
        self._reader = core.reader
        self.db_path = core.db_path
        self.artifacts = artifacts

    def maintenance_stats(self) -> dict[str, Any]:
        reader = self._reader()
        counts = {
            table: int(reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "sessions",
                "runs",
                "events",
                "messages",
                "artifacts",
                "memories",
                "run_pins",
                "run_leases",
                "tool_invocations",
                "tool_attempts",
            )
        }
        artifact_bytes = int(
            reader.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts").fetchone()[0]
        )
        return {
            **counts,
            "artifact_bytes": artifact_bytes,
            "database_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "wal_bytes": self.db_path.with_name(self.db_path.name + "-wal").stat().st_size
            if self.db_path.with_name(self.db_path.name + "-wal").exists()
            else 0,
        }

    def _orphan_artifacts_unlocked(self) -> list[dict[str, Any]]:
        reference_rows: list[sqlite3.Row] = []
        for query in (
            "SELECT metadata_json, output_summary, error FROM runs",
            "SELECT content, tool_calls_json, tool_result_json FROM messages",
            "SELECT payload_json FROM events",
            "SELECT data_json FROM checkpoints",
            "SELECT result_json FROM tool_invocations",
        ):
            reference_rows.extend(self._conn.execute(query).fetchall())
        corpus = "\n".join(
            str(value)
            for row in reference_rows
            for value in row
            if value is not None
        )
        artifacts = self._conn.execute("SELECT * FROM artifacts").fetchall()
        return [
            dict(row)
            for row in artifacts
            if str(row["id"]) not in corpus and str(row["sha256"]) not in corpus
        ]

    def plan_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        if older_than_days < 0:
            raise ValueError("older_than_days must be non-negative")
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self._lock:
            run_rows = self._conn.execute(
                """SELECT id FROM runs
                   WHERE status IN ('completed', 'failed', 'cancelled', 'interrupted')
                     AND COALESCE(finished_at, updated_at, created_at) < ?
                     AND id NOT IN (SELECT run_id FROM run_pins)
                     AND id NOT IN (
                         SELECT run_id FROM run_leases WHERE expires_at > ?
                     )
                   ORDER BY created_at ASC""",
                (cutoff, _utcnow()),
            ).fetchall()
            orphan_artifacts = self._orphan_artifacts_unlocked()
        return {
            "dry_run": True,
            "older_than_days": older_than_days,
            "cutoff": cutoff,
            "run_ids": [str(row[0]) for row in run_rows],
            "run_count": len(run_rows),
            "orphan_artifact_ids": [str(row["id"]) for row in orphan_artifacts],
            "orphan_artifact_count": len(orphan_artifacts),
            "orphan_artifact_bytes": sum(
                int(row.get("size_bytes") or 0) for row in orphan_artifacts
            ),
        }

    def apply_gc(self, *, older_than_days: int = 30) -> dict[str, Any]:
        plan = self.plan_gc(older_than_days=older_than_days)
        run_ids = list(plan["run_ids"])
        artifact_paths: list[str] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if run_ids:
                    placeholders = ",".join("?" for _ in run_ids)
                    self._conn.execute(
                        f"""DELETE FROM tool_attempts
                            WHERE invocation_id IN (
                                SELECT id FROM tool_invocations
                                WHERE run_id IN ({placeholders})
                            )""",
                        run_ids,
                    )
                    self._conn.execute(
                        f"DELETE FROM tool_invocations WHERE run_id IN ({placeholders})",
                        run_ids,
                    )
                    for table in (
                        "approvals",
                        "checkpoints",
                        "messages",
                        "events",
                        "stop_requests",
                        "run_leases",
                        "run_pins",
                    ):
                        self._conn.execute(
                            f"DELETE FROM {table} WHERE run_id IN ({placeholders})",
                            run_ids,
                        )
                    self._conn.execute(
                        f"DELETE FROM runs WHERE id IN ({placeholders})", run_ids
                    )
                orphan_artifacts = self._orphan_artifacts_unlocked()
                artifact_ids = [str(row["id"]) for row in orphan_artifacts]
                artifact_paths = [str(row["path"]) for row in orphan_artifacts]
                if artifact_ids:
                    placeholders = ",".join("?" for _ in artifact_ids)
                    self._conn.execute(
                        f"DELETE FROM artifacts WHERE id IN ({placeholders})",
                        artifact_ids,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        root = self.artifacts.root.resolve()
        removed_files = 0
        for raw_path in artifact_paths:
            path = Path(raw_path).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                path.unlink()
                removed_files += 1
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        return {
            **plan,
            "dry_run": False,
            "deleted_runs": len(run_ids),
            "deleted_artifacts": len(artifact_paths),
            "deleted_artifact_files": removed_files,
        }

    def compact(self) -> dict[str, int]:
        now = _utcnow()
        with self._lock:
            active = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM run_leases WHERE expires_at > ?", (now,)
                ).fetchone()[0]
            )
            if active:
                raise RuntimeError("cannot compact while active run leases exist")
        self._core.reset_readers()
        before = self.db_path.stat().st_size if self.db_path.exists() else 0
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
        after = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {"before_bytes": before, "after_bytes": after}
