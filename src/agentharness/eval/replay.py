"""Immutable trace snapshots and deterministic, side-effect-free re-evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agentharness.eval.contracts import (
    EvaluationPolicy,
    EvaluationReport,
    ReplaySnapshot,
    TraceArtifactRef,
)
from agentharness.eval.trajectory import TrajectoryEvaluator
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.trace import TraceProjector


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SnapshotStore:
    """Persist immutable snapshots in the existing Artifact store or a file adapter."""

    def __init__(self, storage: Any, *, redactor: Redactor | None = None) -> None:
        self.storage = storage
        self.directory: Path | None = None
        self.redactor = redactor or getattr(storage, "redactor", None) or default_redactor

    @classmethod
    def from_directory(
        cls, directory: str | Path, *, redactor: Redactor | None = None
    ) -> SnapshotStore:
        instance = cls.__new__(cls)
        instance.storage = None
        instance.directory = Path(directory)
        instance.directory.mkdir(parents=True, exist_ok=True)
        instance.redactor = redactor or default_redactor
        return instance

    def capture(
        self,
        run_id: str,
        *,
        evaluation_policy_version: str = "",
    ) -> tuple[ReplaySnapshot, TraceArtifactRef]:
        if self.storage is None:
            raise RuntimeError("capture requires the runtime Storage adapter")
        projector = TraceProjector(self.storage, redactor=self.redactor)
        trace = projector.project(run_id)
        trace_artifact = projector.persist(trace)
        run = self.storage.get_run(run_id) or {}
        metadata = _json_object(run.get("metadata_json"))
        parameters = metadata.get("provider_parameters")
        snapshot = ReplaySnapshot(
            snapshot_id="",
            trace=trace,
            trace_artifact=trace_artifact,
            evaluation_policy_version=evaluation_policy_version,
            seed=metadata.get("seed") if isinstance(metadata.get("seed"), int) else None,
            temperature=(
                float(metadata["temperature"])
                if isinstance(metadata.get("temperature"), (int, float))
                else None
            ),
            provider_parameters=(
                self.redactor.redact_obj(parameters) if isinstance(parameters, dict) else {}
            ),
        )
        safe_payload = self.redactor.redact_obj(snapshot.model_dump(mode="json"))
        meta = self.storage.artifacts.put_json(
            safe_payload,
            summary=f"Immutable ReplaySnapshot for run {run_id}",
        )
        meta["id"] = self.storage.register_artifact(meta)
        artifact = TraceArtifactRef(
            artifact_id=meta["id"],
            sha256=meta["sha256"],
            content_type=meta.get("content_type") or "application/json",
            size_bytes=int(meta.get("size_bytes") or 0),
        )
        return snapshot.model_copy(update={"snapshot_id": artifact.sha256}), artifact

    def save(self, snapshot: ReplaySnapshot) -> tuple[ReplaySnapshot, TraceArtifactRef]:
        """Save through the directory adapter for portable snapshot exchange."""
        if self.directory is None:
            raise RuntimeError("save is available only on the directory adapter")
        payload = self.redactor.redact_obj(
            snapshot.model_copy(update={"snapshot_id": ""}).model_dump(mode="json")
        )
        text = _stable_json(payload)
        snapshot_id = _digest(text)
        path = self.directory / f"{snapshot_id}.json"
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        artifact = TraceArtifactRef(
            artifact_id=snapshot_id,
            sha256=snapshot_id,
            content_type="application/json",
            size_bytes=len(text.encode("utf-8")),
        )
        return snapshot.model_copy(update={"snapshot_id": snapshot_id}), artifact

    def load(self, snapshot_id: str) -> ReplaySnapshot:
        if self.storage is not None:
            row = self.storage.get_artifact_by_sha(snapshot_id)
            if row is None:
                raise KeyError(f"snapshot not found: {snapshot_id}")
            text = self.storage.artifacts.get_text(snapshot_id)
            if text is None:
                raise KeyError(f"snapshot content not found: {snapshot_id}")
            actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        else:
            assert self.directory is not None
            path = self.directory / f"{snapshot_id}.json"
            if not path.is_file():
                raise KeyError(f"snapshot not found: {snapshot_id}")
            text = path.read_text(encoding="utf-8")
            actual = _digest(text)
        if actual != snapshot_id:
            raise ValueError(
                f"snapshot digest mismatch: expected {snapshot_id}, actual {actual}"
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"snapshot is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("snapshot JSON must be an object")
        stored_id = payload.get("snapshot_id")
        if stored_id not in {None, "", snapshot_id}:
            raise ValueError(
                f"snapshot digest identity mismatch: embedded {stored_id}, actual {snapshot_id}"
            )
        payload["snapshot_id"] = snapshot_id
        return ReplaySnapshot.model_validate(payload)


class OfflineReplay:
    """Re-evaluate only captured trace facts; never rerun the agent or its tools."""

    guarantee = "deterministic policy evaluation of an immutable trace"

    def evaluate(
        self, snapshot: ReplaySnapshot, policy: EvaluationPolicy
    ) -> EvaluationReport:
        evaluator = TrajectoryEvaluator()
        report = evaluator.evaluate(snapshot.trace, policy)
        identity = _digest(
            _stable_json(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "policy": policy.model_dump(mode="json"),
                }
            )
        )
        return report.model_copy(
            update={
                "report_id": identity,
                "evaluated_at": snapshot.captured_at,
                "metadata": {
                    **report.metadata,
                    "snapshot_id": snapshot.snapshot_id,
                    "replay": True,
                    "guarantee": self.guarantee,
                },
            }
        )

