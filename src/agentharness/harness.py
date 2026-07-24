"""Public Harness API — run / resume / cancel / register_tool / readonly queries."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.contracts import (
    Checkpoint,
    ConversationTurn,
    EventEnvelope,
    Message,
    MessageRole,
    RunRequest,
    RunResult,
    RunStatus,
    ToolSpec,
    Usage,
)
from agentharness.engine.runtime import ApprovalCallback, RunEngine
from agentharness.providers.anthropic_adapter import AnthropicMessagesAdapter
from agentharness.providers.fake import FakeModelAdapter
from agentharness.providers.openai_adapter import OpenAIResponsesAdapter
from agentharness.security.egress import EgressPolicy, default_policy
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.storage.sqlite import Storage
from agentharness.tools import create_default_tools
from agentharness.tools.mcp_tool import MCPBridge

logger = logging.getLogger(__name__)

EventCallback = Callable[[EventEnvelope], None]


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _model_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _elapsed_seconds(start: Any, end: Any) -> float:
    if not start or not end:
        return 0.0
    try:
        started = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        finished = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
        return max(0.0, (finished - started).total_seconds())
    except (TypeError, ValueError):
        return 0.0


class Harness:
    """Top-level agent harness facade."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        redactor: Redactor | None = None,
        approval_callback: ApprovalCallback | None = None,
        providers: dict[str, Any] | None = None,
        tools: dict[str, Any] | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".agentharness"
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or default_redactor
        # Default-secure egress policy shared by every outbound tool (http/browser/MCP).
        # Trusted hosts/CIDRs come only from injected config, never from model arguments.
        self.egress_policy = egress_policy or default_policy()
        self.storage = Storage(self.data_dir, redactor=self.redactor)
        self.mcp_bridge = MCPBridge(redactor=self.redactor, policy=self.egress_policy)
        self._process_registry: dict[str, list[Any]] = {}
        self.tools: dict[str, Any] = tools or create_default_tools(
            process_registry=self._process_registry,
            mcp_bridge=self.mcp_bridge,
            egress_policy=self.egress_policy,
        )
        self.providers: dict[str, Any] = providers or {
            "fake": FakeModelAdapter(),
            "openai": OpenAIResponsesAdapter(),
            "anthropic": AnthropicMessagesAdapter(),
        }
        self.engine = RunEngine(
            self.storage,
            self.providers,
            self.tools,
            redactor=self.redactor,
            approval_callback=approval_callback,
            harness=self,
        )
        # Share process registry with engine for cancel
        self.engine._active_processes = self._process_registry
        self._event_subs: list[EventCallback] = []
        self._event_subs_lock = threading.RLock()
        self._closed = False

    def set_approval_callback(self, cb: ApprovalCallback | None) -> None:
        self.engine.approval_callback = cb

    def register_tool(self, tool: Any) -> None:
        spec: ToolSpec = tool.spec
        self.tools[spec.name] = tool

    def register_provider(self, name: str, adapter: Any) -> None:
        self.providers[name] = adapter
        self.engine.providers = self.providers

    async def run(self, request: RunRequest) -> RunResult:
        started = time.monotonic()
        result = await self.engine.run(request)
        latency_s = time.monotonic() - started
        # Deterministic run-end grade only; never change terminal status on failure.
        self._maybe_grade_run(request, result, latency_s=latency_s)
        return result

    def _maybe_grade_run(
        self,
        request: RunRequest,
        result: RunResult,
        *,
        latency_s: float,
    ) -> None:
        """If ``request.metadata["eval_assert"]`` is set, grade and persist ``metadata.eval``.

        v1 rules:
        - deterministic graders only (no LLM judge / no network)
        - missing eval_assert: no-op (do not write eval)
        - exceptions are swallowed so run terminal semantics stay intact
        - re-grade overwrites the ``eval`` key (idempotent cover)
        """
        try:
            raw_assert = (request.metadata or {}).get("eval_assert")
            if raw_assert is None:
                return
            self._grade_and_persist(
                result.run_id,
                prompt=request.message,
                raw_assert=raw_assert,
                result=result,
                latency_s=latency_s,
                source="run_end",
            )
        except Exception as exc:  # noqa: BLE001 - never break run terminal path
            logger.warning(
                "run-end grade failed for run %s: %s",
                getattr(result, "run_id", None),
                exc,
                exc_info=True,
            )

    def _stored_grade_context(
        self, run_id: str
    ) -> tuple[dict[str, Any], str, Any, RunResult, float]:
        row = self.get_run(run_id)
        if row is None:
            raise KeyError(run_id)
        status = str(row.get("status") or "")
        if status not in {
            RunStatus.completed.value,
            RunStatus.failed.value,
            RunStatus.cancelled.value,
            RunStatus.interrupted.value,
        }:
            raise ValueError("run is not terminal")

        metadata = _json_object(row.get("metadata_json"))
        raw_assert = metadata.get("eval_assert", {})

        messages = self.get_run_messages(run_id)
        prompt = next(
            (
                message.content
                for message in messages
                if message.role == MessageRole.user and message.content
            ),
            str(row.get("user_summary") or ""),
        )
        output = "".join(
            message.content or ""
            for message in messages
            if message.role == MessageRole.assistant
        ) or str(row.get("output_summary") or "")
        usage = Usage.model_validate(_json_object(row.get("usage_json")))
        latency_s = _elapsed_seconds(row.get("created_at"), row.get("finished_at"))
        result = RunResult.model_validate(
            {
                "run_id": run_id,
                "session_id": row.get("session_id"),
                "status": status,
                "output": output,
                "error": row.get("error"),
                "usage": usage,
                "steps": row.get("steps") or 0,
                "parent_run_id": row.get("parent_run_id"),
                "root_run_id": row.get("root_run_id") or run_id,
                "created_at": row.get("created_at"),
                "finished_at": row.get("finished_at"),
                "metadata": metadata,
            }
        )
        return row, prompt, raw_assert, result, latency_s

    def grade_run(self, run_id: str) -> dict[str, Any]:
        """Run the free deterministic/manual-health grade for one terminal run."""
        _row, prompt, raw_assert, result, latency_s = self._stored_grade_context(run_id)
        return self._grade_and_persist(
            run_id,
            prompt=prompt,
            raw_assert=raw_assert,
            result=result,
            latency_s=latency_s,
            source="manual",
        )

    async def grade_run_async(
        self, run_id: str, *, mode: str = "deterministic"
    ) -> dict[str, Any]:
        """Manual grade entry point supporting deterministic or AI quality scoring."""
        if mode not in {"deterministic", "ai"}:
            raise ValueError("mode must be deterministic or ai")
        if mode == "deterministic":
            return self.grade_run(run_id)

        row, prompt, raw_assert, result, latency_s = self._stored_grade_context(run_id)
        deterministic = self._grade_and_persist(
            run_id,
            prompt=prompt,
            raw_assert=raw_assert,
            result=result,
            latency_s=latency_s,
            persist=False,
            source="manual",
        )

        from agentharness.cli.config_store import resolve_runtime_settings
        from agentharness.eval.contracts import EvaluationReport, JudgeRubric
        from agentharness.eval.runner import build_agent_trace
        from agentharness.eval.trusted_judge import (
            JudgeOrchestrator,
            ModelSemanticJudgeAdapter,
        )

        settings = resolve_runtime_settings(self.data_dir)
        adapter: Any
        owned_adapter = False
        if settings.provider == "openai":
            adapter = OpenAIResponsesAdapter(
                api_key=settings.api_key,
                base_url=settings.base_url,
                default_model=settings.model,
            )
            owned_adapter = True
        elif settings.provider == "anthropic":
            adapter = AnthropicMessagesAdapter(
                api_key=settings.api_key,
                base_url=settings.base_url,
                default_model=settings.model,
            )
            owned_adapter = True
        else:
            adapter = self.providers.get("fake") or FakeModelAdapter()

        deterministic_report = EvaluationReport.model_validate(
            deterministic["evaluation_report"]
        )
        trace = build_agent_trace(self, result).model_copy(
            update={"duration_ms": latency_s * 1000.0}
        )
        rubric_text = (
            str(raw_assert.get("rubric"))
            if isinstance(raw_assert, dict) and raw_assert.get("rubric")
            else (
                "Evaluate whether the assistant correctly and completely satisfies the "
                "user request using only evidence visible in the trace."
            )
        )
        rubric = JudgeRubric(
            rubric_id=f"manual:{run_id}",
            version="1",
            task_type="manual_run",
            text=rubric_text,
            pass_threshold=0.7,
        )
        try:
            semantic = await JudgeOrchestrator(
                ModelSemanticJudgeAdapter(
                    adapter,
                    model=settings.model,
                    redactor=self.redactor or default_redactor,
                ),
                sample_count=3,
                redactor=self.redactor or default_redactor,
            ).evaluate(
                trace,
                deterministic_report,
                rubric,
            )
        finally:
            if owned_adapter:
                await adapter.aclose()
        valid_samples = [
            sample
            for sample in semantic.samples
            if not sample.abstained and sample.error is None and sample.score is not None
        ]
        representative = valid_samples[0] if valid_samples else None
        semantic_score = semantic.mean_score
        deterministic_score = deterministic_report.score
        score = semantic_score if semantic_score is not None else deterministic_score
        if deterministic_report.hard_failures and deterministic_score is not None and score is not None:
            score = min(score, deterministic_score)
        reasons = list(deterministic.get("reasons") or [])
        if semantic_score is not None and semantic_score < rubric.pass_threshold:
            reasons.append(
                f"AI quality score {semantic_score:.2f} is below {rubric.pass_threshold:.2f}"
            )
        if semantic.status in {"degraded", "abstained"}:
            reasons.append(
                "AI judge was unavailable or abstained; deterministic rule score was used."
            )
        confidence = (
            sum(sample.confidence for sample in valid_samples) / len(valid_samples)
            if valid_samples
            else 0.0
        )
        payload = {
            **deterministic,
            "mode": "ai",
            "source": "manual",
            "passed": semantic.passed,
            "score": score,
            "reasons": reasons,
            "grader": "deterministic+ai",
            "graded_at": datetime.now(UTC).isoformat(),
            "dimensions": representative.dimensions if representative else {},
            "confidence": confidence,
            "variance": semantic.variance,
            "consistency": semantic.consistency,
            "judge_status": semantic.status,
            "failure_category": (
                representative.failure_category
                if representative
                else deterministic.get("failure_category", "execution_or_assertion")
            ),
            "hard_safety_violation": bool(
                representative and representative.failure_category == "safety_permission"
            ),
            "evidence": (self.redactor or default_redactor).redact_obj(
                [
                    evidence.model_dump(mode="json")
                    for sample in valid_samples
                    for evidence in sample.evidence
                ]
            ),
            "improvements": (self.redactor or default_redactor).redact_obj(
                representative.improvements if representative else []
            ),
            "judge_provider": settings.provider,
            "judge_model": settings.model,
            "judge_usage": {},
            "judge_samples": [sample.model_dump(mode="json") for sample in semantic.samples],
            "semantic_evaluation": semantic.model_dump(mode="json"),
        }
        canonical_report = deterministic_report.model_copy(
            update={
                "mode": (
                    "scored"
                    if semantic.mean_score is not None
                    else deterministic_report.mode
                ),
                "passed": semantic.passed,
                "score": score,
                "deterministic": False,
                "semantic": semantic.model_dump(mode="json"),
            }
        )
        from agentharness.eval.diagnosis import DiagnosisEngine
        from agentharness.eval.replay import SnapshotStore

        diagnosis = (
            DiagnosisEngine(storage=self.storage).diagnose(trace, deterministic_report)
            if deterministic_report.failed_count
            else None
        )
        snapshot, snapshot_artifact = SnapshotStore(
            self.storage, redactor=self.redactor or default_redactor
        ).capture(
            run_id,
            evaluation_policy_version=deterministic_report.policy_version,
        )
        payload.update(
            {
                "evaluation_report": canonical_report.model_dump(mode="json"),
                "evaluation_report_id": canonical_report.report_id,
                "evaluation_mode": canonical_report.mode,
                "diagnosis_id": diagnosis.diagnosis_id if diagnosis else None,
                "snapshot_id": snapshot.snapshot_id,
            }
        )
        self.retain_run_evaluation(
            run_id,
            report=canonical_report,
            diagnosis=diagnosis,
            snapshot=snapshot,
            snapshot_artifact=snapshot_artifact,
            semantic=semantic,
            legacy_eval=payload,
            source="manual",
        )
        return payload

    def _grade_and_persist(
        self,
        run_id: str,
        *,
        prompt: str,
        raw_assert: Any,
        result: RunResult,
        latency_s: float,
        persist: bool = True,
        source: str = "manual",
    ) -> dict[str, Any]:
        from agentharness.eval.dataset import AssertionSpec
        from agentharness.eval.runner import build_agent_trace
        from agentharness.eval.trajectory import TrajectoryEvaluator, policy_from_assertions

        try:
            assertions = AssertionSpec.model_validate(raw_assert)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid metadata.eval_assert: {exc}") from exc
        trace = build_agent_trace(self, result).model_copy(
            update={"duration_ms": latency_s * 1000.0}
        )
        policy_v2 = policy_from_assertions(assertions, policy_id=f"run:{run_id}")
        grade_started = time.monotonic()
        report = TrajectoryEvaluator(storage=self.storage).evaluate(trace, policy_v2)
        grade_latency = time.monotonic() - grade_started
        failed_checks = [
            check for check in report.checks if check.status in {"failed", "error"}
        ]
        reasons = [
            check.message
            or f"{check.id}: expected {check.expected!r}, actual {check.actual!r}"
            for check in failed_checks
        ]

        redactor = self.redactor or default_redactor
        eval_payload = {
            "schema_version": 1,
            "mode": "deterministic",
            "source": source,
            "passed": report.passed is True,
            "score": report.score,
            "reasons": [redactor.redact_text(r) for r in reasons],
            "grader": "composite",
            "graded_at": datetime.now(UTC).isoformat(),
            "latency_s": round(grade_latency, 6),
            "assertion_summary": redactor.redact_obj(
                {
                    "status": assertions.status,
                    "contains": list(assertions.contains),
                    "contains_any": list(assertions.contains_any),
                    "regex": assertions.regex,
                    "tools_used": list(assertions.tools_used),
                    "tools_order": list(assertions.tools_order),
                    "max_tokens": assertions.max_tokens,
                    "max_steps": assertions.max_steps,
                    "max_latency_s": assertions.max_latency_s,
                }
            ),
            "failure_category": (
                "none"
                if report.passed
                else next(
                    (
                        check.failure_category
                        for check in failed_checks
                        if check.failure_category
                    ),
                    "execution_or_assertion",
                )
            ),
            "evaluation_report": report.model_dump(mode="json"),
            "evaluation_report_id": report.report_id,
            "evaluation_mode": report.mode,
        }
        if persist:
            from agentharness.eval.diagnosis import DiagnosisEngine
            from agentharness.eval.replay import SnapshotStore

            diagnosis = (
                DiagnosisEngine(storage=self.storage).diagnose(trace, report)
                if report.failed_count
                else None
            )
            snapshot, snapshot_artifact = SnapshotStore(
                self.storage, redactor=redactor
            ).capture(
                run_id,
                evaluation_policy_version=policy_v2.version,
            )
            eval_payload.update(
                {
                    "diagnosis_id": diagnosis.diagnosis_id if diagnosis else None,
                    "snapshot_id": snapshot.snapshot_id,
                }
            )
            self.retain_run_evaluation(
                run_id,
                report=report,
                diagnosis=diagnosis,
                snapshot=snapshot,
                snapshot_artifact=snapshot_artifact,
                legacy_eval=eval_payload,
                source=source,
            )
        return eval_payload

    def retain_run_evaluation(
        self,
        run_id: str,
        *,
        report: Any,
        diagnosis: Any | None = None,
        snapshot: Any | None = None,
        snapshot_artifact: Any | None = None,
        semantic: Any | None = None,
        calibration: Any | None = None,
        legacy_eval: dict[str, Any] | None = None,
        source: str = "suite",
    ) -> dict[str, Any]:
        """Retain one canonical evaluation read model on the existing run row."""

        row = self.get_run(run_id)
        if row is None:
            raise KeyError(run_id)
        metadata = _json_object(row.get("metadata_json"))
        current = metadata.get("evaluation")
        retained = dict(current) if isinstance(current, dict) else {}

        report_payload = _model_payload(report)
        diagnosis_payload = _model_payload(diagnosis)
        semantic_payload = _model_payload(semantic)
        calibration_payload = _model_payload(calibration)
        retained.update(
            {
                "schema_version": 2,
                "source": source,
                "run_id": run_id,
                "trace_id": report_payload.get("trace_id"),
                "report_id": report_payload.get("report_id"),
                "report": report_payload,
                "diagnosis_id": (
                    diagnosis_payload.get("diagnosis_id") if diagnosis_payload else None
                ),
                "diagnosis": diagnosis_payload,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        if snapshot is not None:
            snapshot_payload = _model_payload(snapshot)
            artifact_payload = _model_payload(snapshot_artifact)
            retained["replay"] = {
                "snapshot_id": snapshot_payload.get("snapshot_id"),
                "artifact_id": artifact_payload.get("artifact_id"),
                "sha256": artifact_payload.get("sha256"),
                "evaluation_policy_version": snapshot_payload.get(
                    "evaluation_policy_version"
                ),
                "captured_at": snapshot_payload.get("captured_at"),
            }
        if semantic is not None or calibration is not None:
            retained["judge"] = {
                "status": (
                    semantic_payload.get("status")
                    if semantic_payload
                    else calibration_payload.get("trust_status", "unverified")
                ),
                "semantic_evaluation": semantic_payload,
                "calibration": calibration_payload,
            }

        patch: dict[str, Any] = {"evaluation": retained}
        if legacy_eval is not None:
            patch["eval"] = legacy_eval
        self.storage.merge_run_metadata(run_id, patch)
        return retained

    def retain_run_regression(
        self,
        run_id: str,
        *,
        regression: Any,
        gate_decision: Any,
        baseline_diff: dict[str, Any] | None = None,
        rerun_statistics: Any | None = None,
    ) -> dict[str, Any]:
        """Attach a suite-level Gate result without replacing retained evaluation data."""

        row = self.get_run(run_id)
        if row is None:
            raise KeyError(run_id)
        metadata = _json_object(row.get("metadata_json"))
        current = metadata.get("evaluation")
        retained = dict(current) if isinstance(current, dict) else {
            "schema_version": 2,
            "run_id": run_id,
        }
        regression_payload = _model_payload(regression)
        decision_payload = _model_payload(gate_decision)
        rerun_payload = _model_payload(rerun_statistics)
        retained["regression"] = {
            "regression_id": regression_payload.get("regression_id"),
            "decision_id": decision_payload.get("decision_id"),
            "baseline_diff": dict(baseline_diff or {}),
            "report": regression_payload,
            "gate_decision": decision_payload,
            "rerun_statistics": rerun_payload,
        }
        retained["updated_at"] = datetime.now(UTC).isoformat()
        self.storage.merge_run_metadata(run_id, {"evaluation": retained})
        return retained

    def get_run_evaluation(self, run_id: str) -> dict[str, Any]:
        """Build the read-only Trace-native evaluation payload for the Web API."""

        row = self.get_run(run_id)
        if row is None:
            raise KeyError(run_id)

        from agentharness.eval.contracts import DiagnosisReport, EvaluationReport
        from agentharness.eval.diagnosis import DiagnosisEngine

        metadata = _json_object(row.get("metadata_json"))
        raw_legacy = metadata.get("eval")
        legacy: dict[str, Any] = (
            dict(raw_legacy) if isinstance(raw_legacy, dict) else {}
        )
        raw_retained = metadata.get("evaluation")
        retained: dict[str, Any] = (
            dict(raw_retained) if isinstance(raw_retained, dict) else {}
        )
        raw_report = retained.get("report") or legacy.get("evaluation_report")
        report: EvaluationReport | None = None
        if isinstance(raw_report, dict):
            try:
                report = EvaluationReport.model_validate(raw_report)
            except ValueError:
                report = None

        trace = self.get_agent_trace(run_id)
        raw_diagnosis = retained.get("diagnosis")
        diagnosis: DiagnosisReport | None = None
        if isinstance(raw_diagnosis, dict):
            try:
                diagnosis = DiagnosisReport.model_validate(raw_diagnosis)
            except ValueError:
                diagnosis = None
        if diagnosis is None and report is not None and report.failed_count:
            diagnosis = DiagnosisEngine(storage=self.storage).diagnose(trace, report)

        raw_judge = retained.get("judge")
        retained_judge: dict[str, Any] = (
            dict(raw_judge) if isinstance(raw_judge, dict) else {}
        )
        raw_semantic = retained_judge.get("semantic_evaluation")
        if not isinstance(raw_semantic, dict):
            raw_semantic = legacy.get("semantic_evaluation")
        semantic: dict[str, Any] | None = (
            dict(raw_semantic) if isinstance(raw_semantic, dict) else None
        )
        calibration = retained_judge.get("calibration")
        judge_status = (
            semantic.get("status")
            if isinstance(semantic, dict)
            else legacy.get("judge_status") or retained_judge.get("status") or "unverified"
        )
        judge = {
            "status": judge_status,
            "semantic_evaluation": semantic,
            "calibration": calibration if isinstance(calibration, dict) else None,
            "samples": semantic.get("samples", []) if isinstance(semantic, dict) else [],
            "mean_score": (
                semantic.get("mean_score")
                if isinstance(semantic, dict)
                else legacy.get("score") if legacy.get("mode") == "ai" else None
            ),
            "median_score": semantic.get("median_score") if isinstance(semantic, dict) else None,
            "variance": (
                semantic.get("variance")
                if isinstance(semantic, dict)
                else legacy.get("variance")
            ),
            "consistency": (
                semantic.get("consistency")
                if isinstance(semantic, dict)
                else legacy.get("consistency")
            ),
            "attack_resistant": (
                semantic.get("attack_resistant") if isinstance(semantic, dict) else None
            ),
            "confidence": legacy.get("confidence"),
            "dimensions": legacy.get("dimensions", {}),
            "provider": legacy.get("judge_provider"),
            "model": legacy.get("judge_model"),
        }
        raw_replay = retained.get("replay")
        replay: dict[str, Any] = (
            dict(raw_replay) if isinstance(raw_replay, dict) else {}
        )
        raw_regression = retained.get("regression")
        regression: dict[str, Any] = (
            dict(raw_regression) if isinstance(raw_regression, dict) else {}
        )
        report_payload = report.model_dump(mode="json") if report is not None else None
        diagnosis_payload = (
            diagnosis.model_dump(mode="json") if diagnosis is not None else None
        )
        return {
            "schema_version": 2,
            "available": report is not None,
            "run_id": run_id,
            "trace": trace.model_dump(mode="json"),
            "report": report_payload,
            "diagnosis": diagnosis_payload,
            "judge": judge,
            "replay": replay,
            "regression": regression,
            "ids": {
                "run_id": run_id,
                "trace_id": trace.trace_id,
                "report_id": report.report_id if report is not None else None,
                "diagnosis_id": diagnosis.diagnosis_id if diagnosis is not None else None,
                "snapshot_id": replay.get("snapshot_id"),
                "regression_id": regression.get("regression_id"),
                "decision_id": regression.get("decision_id"),
            },
            "legacy_eval": legacy or None,
        }

    async def resume(self, run_id: str, input: str | None = None) -> RunResult:
        return await self.engine.resume(run_id, input=input)

    async def cancel(self, run_id: str) -> None:
        await self.engine.cancel(run_id)

    async def interrupt(self, run_id: str, reason: str = "interrupted") -> None:
        await self.engine.interrupt(run_id, reason)

    # -- readonly queries ---------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.storage.get_run(run_id)

    def list_runs(
        self, session_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self.storage.list_runs(session_id=session_id, limit=limit, offset=offset)

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List sessions with latest top-level run status for the observer UI."""
        sessions = self.storage.list_sessions(limit=limit)
        return [self._enrich_session(s) for s in sessions]

    def resolve_session_id(self, value: str, *, limit: int = 1000) -> str:
        """Resolve a unique session id by exact id or unambiguous prefix.

        Used by interactive ``/use`` and scriptable ``run --session`` so a
        truncated CLI display id continues the real session instead of creating
        a brand-new 12-char session. If both a short fake id and a longer id
        share the same prefix, raise ambiguous rather than picking the short one.
        """
        value = (value or "").strip()
        if not value:
            raise ValueError("session id is required")
        matches = [
            str(session["id"])
            for session in self.storage.list_sessions(limit=limit)
            if str(session.get("id", "")) == value
            or str(session.get("id", "")).startswith(value)
        ]
        # de-dupe while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for mid in matches:
            if mid not in seen:
                seen.add(mid)
                unique.append(mid)
        if len(unique) == 1:
            return unique[0]
        if not unique:
            raise KeyError(f"Session not found: {value}")
        raise ValueError(f"Session prefix is ambiguous: {value}")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        sess = self.storage.get_session(session_id)
        if not sess:
            return None
        return self._enrich_session(sess)

    def _enrich_session(self, sess: dict[str, Any]) -> dict[str, Any]:
        """Attach latest top-level run status / id / error for UI left column.

        list_sessions already resolves these in one SQL query. For get_session (a
        bare session row without the join), fall back to a single scoped list_runs.
        """
        out = dict(sess)
        sid = out.get("id")
        if not sid:
            return out
        if "latest_status" in out or "latest_run_id" in out:
            # Already enriched by list_sessions' join — normalize missing keys.
            out.setdefault("latest_status", None)
            out.setdefault("latest_run_id", None)
            out.setdefault("latest_error", None)
            return out
        runs = self.storage.list_runs(session_id=sid, limit=50)
        top = [r for r in runs if not r.get("parent_run_id")]
        latest = top[0] if top else None  # list_runs is DESC by created_at
        if latest:
            out["latest_status"] = latest.get("status")
            out["latest_run_id"] = latest.get("id")
            out["latest_error"] = latest.get("error")
        return out

    def get_events(
        self,
        run_id: str | None = None,
        after_global_seq: int = 0,
        limit: int = 500,
    ) -> list[EventEnvelope]:
        return self.storage.get_events(
            run_id=run_id, after_global_seq=after_global_seq, limit=limit
        )

    def get_run_tree(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.get_run_tree(run_id)

    def get_run_messages(self, run_id: str) -> list[Message]:
        return self.storage.get_messages(run_id)

    def get_agent_trace(self, run_id: str):
        """Project one run into the shared, redacted canonical trace contract."""
        from agentharness.trace import TraceProjector

        return TraceProjector(self.storage, redactor=self.redactor).project(run_id)

    def capture_agent_trace(self, run_id: str):
        """Project and persist a content-addressed canonical trace artifact."""
        from agentharness.trace import TraceProjector

        projector = TraceProjector(self.storage, redactor=self.redactor)
        trace = projector.project(run_id)
        return trace, projector.persist(trace)

    def get_context_manifests(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.get_context_manifests(run_id)

    def get_checkpoint(self, run_id: str) -> Checkpoint | None:
        return self.storage.load_checkpoint(run_id)

    def list_approvals(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.list_approvals(run_id)

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.storage.get_artifact(artifact_id)

    def get_session_transcript(self, session_id: str) -> list[ConversationTurn]:
        """Return all top-level turns for a session, including failed turns.

        Ordered by run created_at ascending. Delegate child runs are excluded.
        """
        runs = self.storage.list_top_level_runs(session_id)
        turns: list[ConversationTurn] = []
        for run in runs:
            messages = self.storage.get_messages(run["id"])
            user_content = ""
            assistant_parts: list[str] = []
            for m in messages:
                role = m.role.value if hasattr(m.role, "value") else str(m.role)
                if role == MessageRole.user.value and not user_content:
                    user_content = m.content or ""
                elif role == MessageRole.assistant.value:
                    if m.content:
                        assistant_parts.append(m.content)
            # Prefer stored output_summary when assistant messages empty
            assistant_content = "".join(assistant_parts)
            if not assistant_content and run.get("output_summary"):
                assistant_content = run["output_summary"] or ""
            status_raw = run.get("status") or RunStatus.pending.value
            try:
                status: RunStatus | str = RunStatus(status_raw)
            except ValueError:
                status = status_raw
            turns.append(
                ConversationTurn(
                    run_id=run["id"],
                    session_id=session_id,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    status=status,
                    error=run.get("error"),
                    provider=run.get("provider"),
                    model=run.get("model"),
                    started_at=run.get("created_at"),
                    finished_at=run.get("finished_at"),
                    evaluation=_json_object(run.get("metadata_json")).get("eval"),
                )
            )
        return turns

    def subscribe_events(self, callback: EventCallback) -> Callable[[], None]:
        """Subscribe to ordered redacted events (including text_delta).

        Returns an unsubscribe callable. Events are already redacted by storage.
        """
        with self._event_subs_lock:
            self._event_subs.append(callback)

        def unsubscribe() -> None:
            with self._event_subs_lock:
                try:
                    self._event_subs.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _notify_events(self, events: list[EventEnvelope]) -> None:
        with self._event_subs_lock:
            subs = list(self._event_subs)
        for ev in events:
            for cb in subs:
                try:
                    cb(ev)
                except Exception:  # noqa: BLE001
                    pass

    def doctor(self) -> dict[str, Any]:
        packaged_web = Path(__file__).resolve().parent / "web_dist" / "index.html"
        source_web = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
        web_ready = packaged_web.is_file() or source_web.is_file()
        browser_runtime = "missing"
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                if Path(playwright.chromium.executable_path).is_file():
                    browser_runtime = "ready"
        except Exception:  # noqa: BLE001
            browser_runtime = "missing"
        return {
            "data_dir": str(self.data_dir),
            "db": str(self.storage.db_path),
            "db_exists": self.storage.db_path.exists(),
            "sqlite_integrity": self.storage.integrity_check(),
            "schema_version": self.storage.schema_version(),
            "web_build": "ready" if web_ready else "missing",
            "browser_runtime": browser_runtime,
            "providers": list(self.providers.keys()),
            "tools": list(self.tools.keys()),
            "sessions": len(self.list_sessions()),
            "runs": len(self.list_runs()),
            "max_global_seq": self.storage.max_global_seq(),
        }

    async def aclose(self) -> None:
        """Close async tools and storage on their owning event loop."""
        if self._closed:
            return
        errors: list[Exception] = []
        for active_run_id in list(self.engine._active_run_ids):
            try:
                await self.engine.interrupt(active_run_id, "harness_shutdown")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        seen: set[int] = set()
        for tool in self.tools.values():
            if id(tool) in seen:
                continue
            seen.add(id(tool))
            close_all = getattr(tool, "close_all", None)
            if callable(close_all):
                try:
                    result = close_all()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        seen.clear()
        for provider in self.providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            closer = getattr(provider, "aclose", None) or getattr(provider, "close", None)
            if callable(closer):
                try:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
        try:
            await self.mcp_bridge.close_all()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

        with self._event_subs_lock:
            self._event_subs.clear()
        try:
            self.storage.close()
            self._closed = True
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        if errors:
            raise ExceptionGroup("Harness cleanup failed", errors)

    def _has_open_async_resources(self) -> bool:
        if self.engine._active_run_ids or any(self._process_registry.values()):
            return True
        if self.mcp_bridge._sessions:
            return True
        for tool in self.tools.values():
            if getattr(tool, "_playwright", None) is not None:
                return True
            if getattr(tool, "_browsers", None):
                return True
        for provider in self.providers.values():
            if getattr(provider, "_client", None) is not None:
                return True
        return False

    def close(self) -> None:
        """Synchronous close for callers without live async resources."""
        if self._closed:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        if self._has_open_async_resources():
            raise RuntimeError("Harness has live async resources; use `await harness.aclose()`")
        with self._event_subs_lock:
            self._event_subs.clear()
        self.storage.close()
        self._closed = True
