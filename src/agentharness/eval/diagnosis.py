"""Deterministic root-cause diagnosis and read-only evidence probes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from agentharness.contracts import MessageRole
from agentharness.eval.contracts import (
    AgentTrace,
    CheckResult,
    DiagnosisReport,
    EvaluationReport,
    EvidenceRef,
    ProbeFinding,
    TraceSpan,
)

_ROOT_CAUSE_MAP = {
    "wrong_tool_selection": "wrong_tool_selection",
    "invalid_tool_arguments": "invalid_tool_arguments",
    "duplicate_tool_call": "duplicate_tool_call",
    "retry_loop": "retry_loop",
    "missing_required_step": "missing_required_step",
    "tool_result_missing": "tool_result_ignored",
    "tool_result_mismatch": "tool_result_ignored",
    "premature_completion": "premature_completion",
    "verification_missing": "verification_missing",
    "approval_deadlock": "approval_deadlock",
    "context_drift": "context_drift",
    "budget_exhaustion": "budget_exhaustion",
    "provider_failure": "provider_failure",
    "environment_failure": "environment_failure",
    "workspace_violation": "environment_failure",
    "tool_result_error": "environment_failure",
}

_PRIORITY = {
    "provider_failure": 0,
    "invalid_tool_arguments": 1,
    "wrong_tool_selection": 2,
    "approval_deadlock": 3,
    "environment_failure": 4,
    "retry_loop": 5,
    "duplicate_tool_call": 6,
    "tool_result_ignored": 7,
    "verification_missing": 8,
    "missing_required_step": 9,
    "premature_completion": 10,
    "context_drift": 11,
    "budget_exhaustion": 12,
}

_RECOMMENDATIONS = {
    "wrong_tool_selection": "Constrain tool selection with the task-specific ToolSpec and expected trajectory.",
    "invalid_tool_arguments": "Align the call arguments with the cited ToolSpec schema before retrying.",
    "duplicate_tool_call": "Record successful call results and suppress duplicate calls with identical arguments.",
    "retry_loop": "Stop retrying unchanged arguments; use the cited error and ToolSpec to change the next action.",
    "missing_required_step": "Add the missing required step before producing a terminal answer.",
    "tool_result_ignored": "Consume and cite the paired tool result before deciding or completing.",
    "premature_completion": "Require terminal evidence and successful verification before completion.",
    "verification_missing": "Run the configured verification policy before marking the run completed.",
    "approval_deadlock": "Resolve or explicitly deny the pending approval and expose that decision to the agent.",
    "context_drift": "Pin the cited context fingerprint and investigate the first turn where it changed.",
    "budget_exhaustion": "Reduce retries/context/tool calls or explicitly raise the relevant budget.",
    "provider_failure": "Retry only if the provider error is retryable; otherwise use the configured provider fallback.",
    "environment_failure": "Repair the cited workspace, tool runtime, or external dependency before rerunning.",
    "unknown": "Inspect the cited first divergence and add a deterministic check for this failure pattern.",
}


def _span_evidence(trace: AgentTrace, span: TraceSpan | None, source: str) -> EvidenceRef | None:
    if span is None:
        return None
    return EvidenceRef(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        span_id=span.span_id,
        event_id=span.event_ids[0] if span.event_ids else None,
        artifact_id=span.context_manifest_artifact_id,
        source=source,
        sequence=span.sequence_start,
    )


def _dedupe_evidence(items: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple[Any, ...]] = set()
    output: list[EvidenceRef] = []
    for item in items:
        key = (
            item.span_id,
            item.event_id,
            item.artifact_id,
            item.message_id,
            item.path,
            item.source,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


class Probe(Protocol):
    name: str

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None: ...


class ToolSpecProbe:
    name = "ToolSpecProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        tool = divergence.tool_name if divergence else None
        if not tool:
            failed = next(
                (
                    check
                    for check in evaluation.checks
                    if check.status == "failed" and check.category in {"tool", "tool_result"}
                ),
                None,
            )
            span_id = failed.evidence[0].span_id if failed and failed.evidence else None
            divergence = trace.span(span_id)
            tool = divergence.tool_name if divergence else None
        if not tool:
            return None
        ref = _span_evidence(trace, divergence, "tool_spec")
        if ref is None:
            return None
        assert divergence is not None
        fingerprint = trace.versions.tool_schema_fingerprints.get(tool, "")
        return ProbeFinding(
            probe=self.name,
            summary=(
                f"Tool {tool} was called with {divergence.tool_arguments!r}; "
                f"schema fingerprint={fingerprint or 'unavailable'}."
            ),
            affected_configuration=[f"tool:{tool}", f"tool_schema:{fingerprint or 'unknown'}"],
            evidence=[ref],
        )


class PromptProbe:
    name = "PromptProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        message = next((item for item in trace.messages if item.role == MessageRole.user), None)
        if message is None:
            return None
        span = divergence or next((item for item in trace.spans if item.kind == "model"), None)
        ref = _span_evidence(trace, span, "prompt")
        if ref is None:
            return None
        ref.message_id = message.id
        ref.excerpt = message.content[:240]
        return ProbeFinding(
            probe=self.name,
            summary=f"Original task prompt fingerprint={trace.versions.prompt_fingerprint}.",
            affected_configuration=[f"prompt:{trace.versions.prompt_fingerprint or 'unknown'}"],
            evidence=[ref],
        )


class ContextManifestProbe:
    name = "ContextManifestProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        span = divergence if divergence and divergence.context_manifest_artifact_id else None
        if span is None and divergence and divergence.parent_span_id:
            parent = trace.span(divergence.parent_span_id)
            span = parent if parent and parent.context_manifest_artifact_id else None
        if span is None:
            span = next(
                (item for item in trace.spans if item.context_manifest_artifact_id), None
            )
        ref = _span_evidence(trace, span, "context_manifest")
        if ref is None:
            return None
        return ProbeFinding(
            probe=self.name,
            summary=f"Context fingerprint={trace.versions.context_fingerprint}.",
            affected_configuration=[
                f"context:{trace.versions.context_fingerprint or 'unknown'}"
            ],
            evidence=[ref],
        )


class SkillProbe:
    name = "SkillProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        if not trace.versions.skill_fingerprints:
            return None
        span = divergence or next((item for item in trace.spans if item.kind == "model"), None)
        ref = _span_evidence(trace, span, "skill")
        if ref is None:
            return None
        return ProbeFinding(
            probe=self.name,
            summary="Selected skill fingerprints were captured in the context manifest.",
            affected_configuration=[
                f"skill:{name}:{fingerprint}"
                for name, fingerprint in sorted(trace.versions.skill_fingerprints.items())
            ],
            evidence=[ref],
        )


class WorkspaceRuleProbe:
    name = "WorkspaceRuleProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        if not trace.versions.workspace_rule_fingerprints:
            return None
        span = divergence or next((item for item in trace.spans if item.kind == "model"), None)
        base = _span_evidence(trace, span, "workspace_rule")
        if base is None:
            return None
        refs: list[EvidenceRef] = []
        for path in sorted(trace.versions.workspace_rule_fingerprints):
            refs.append(base.model_copy(update={"path": path}))
        return ProbeFinding(
            probe=self.name,
            summary="Workspace rules cited by the model context were inspected read-only.",
            affected_configuration=[
                f"workspace_rule:{path}:{fingerprint}"
                for path, fingerprint in sorted(
                    trace.versions.workspace_rule_fingerprints.items()
                )
            ],
            evidence=refs,
        )


class RuntimeConfigProbe:
    name = "RuntimeConfigProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        span = divergence or next((item for item in trace.spans if item.kind == "run"), None)
        ref = _span_evidence(trace, span, "runtime_config")
        if ref is None:
            return None
        path = trace.metadata.get("runtime_config_path")
        if isinstance(path, str) and path:
            ref.path = path
        return ProbeFinding(
            probe=self.name,
            summary=(
                f"Runtime configuration fingerprint={trace.versions.runtime_config_fingerprint}; "
                f"provider={trace.provider}, model={trace.model}, cwd={trace.metadata.get('cwd')}."
            ),
            affected_configuration=[
                f"runtime:{trace.versions.runtime_config_fingerprint or 'unknown'}",
                f"provider:{trace.provider or 'unknown'}",
                f"model:{trace.model or 'unknown'}",
            ],
            evidence=[ref],
        )


class VersionProbe:
    name = "VersionProbe"

    def inspect(
        self,
        trace: AgentTrace,
        evaluation: EvaluationReport,
        divergence: TraceSpan | None,
    ) -> ProbeFinding | None:
        span = divergence or next(iter(trace.spans), None)
        ref = _span_evidence(trace, span, "versions")
        if ref is None:
            return None
        versions = trace.versions
        return ProbeFinding(
            probe=self.name,
            summary=(
                f"Trace schema={trace.schema_version}; event schemas="
                f"{versions.event_schema_versions or ['unknown']}."
            ),
            affected_configuration=[
                f"trace_schema:{trace.schema_version}",
                *[f"event_schema:{item}" for item in versions.event_schema_versions],
            ],
            evidence=[ref],
        )


DEFAULT_PROBES: tuple[Probe, ...] = (
    ToolSpecProbe(),
    PromptProbe(),
    ContextManifestProbe(),
    SkillProbe(),
    WorkspaceRuleProbe(),
    RuntimeConfigProbe(),
    VersionProbe(),
)


class DiagnosisEngine:
    """Map deterministic failures to an evidence-backed root cause and probes."""

    def __init__(self, *, storage: Any | None = None, probes: Iterable[Probe] = DEFAULT_PROBES):
        self.storage = storage
        self.probes = tuple(probes)

    def diagnose(
        self, trace: AgentTrace, evaluation: EvaluationReport
    ) -> DiagnosisReport:
        failures = [
            check for check in evaluation.checks if check.status in {"failed", "error"}
        ]
        root, source_check = self._root_cause(failures)
        divergence = trace.span(
            evaluation.first_divergence.span_id if evaluation.first_divergence else None
        )
        findings = [
            finding
            for probe in self.probes
            if (finding := probe.inspect(trace, evaluation, divergence)) is not None
        ]
        evidence: list[EvidenceRef] = []
        if evaluation.first_divergence is not None:
            evidence.append(evaluation.first_divergence)
        if source_check is not None:
            evidence.extend(source_check.evidence)
        for finding in findings:
            evidence.extend(finding.evidence)
        evidence = _dedupe_evidence(evidence)
        affected = list(
            dict.fromkeys(
                item for finding in findings for item in finding.affected_configuration
            )
        )
        if root == "none":
            confidence = 1.0
            recommendations: list[str] = []
        elif source_check is not None and evidence:
            confidence = 0.9
            recommendations = [_RECOMMENDATIONS.get(root, _RECOMMENDATIONS["unknown"])]
            if source_check.recovery_hint:
                recommendations.append(source_check.recovery_hint)
        else:
            confidence = 0.5
            recommendations = [_RECOMMENDATIONS["unknown"]]
        return DiagnosisReport(
            trace_id=trace.trace_id,
            report_id=evaluation.report_id,
            root_cause=root,
            confidence=confidence,
            first_divergence=evaluation.first_divergence,
            evidence=evidence,
            affected_configuration=affected,
            recommendations=list(dict.fromkeys(recommendations)),
            probes=findings,
            read_only=True,
        )

    @staticmethod
    def _root_cause(failures: list[CheckResult]) -> tuple[str, CheckResult | None]:
        if not failures:
            return "none", None
        candidates: list[tuple[int, int, str, CheckResult]] = []
        for check in failures:
            mapped = _ROOT_CAUSE_MAP.get(check.failure_category or "", "unknown")
            sequence = min(
                (ref.sequence for ref in check.evidence if ref.sequence is not None),
                default=1_000_000_000,
            )
            candidates.append((_PRIORITY.get(mapped, 99), sequence, mapped, check))
        _priority, _sequence, root, check = min(candidates)
        return root, check
