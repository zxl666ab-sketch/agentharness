"""Versioned contracts shared by trace, evaluation, diagnosis, replay, judge, and CI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentharness.contracts import Message, ToolResult, Usage, new_id

CONTRACT_SCHEMA_VERSION = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TraceVersions(BaseModel):
    """Fingerprints needed to explain which inputs shaped a run."""

    schema_version: int = CONTRACT_SCHEMA_VERSION
    trace_schema_version: int = CONTRACT_SCHEMA_VERSION
    event_schema_versions: list[int] = Field(default_factory=list)
    prompt_fingerprint: str = ""
    context_fingerprint: str = ""
    tool_schema_fingerprints: dict[str, str] = Field(default_factory=dict)
    skill_fingerprints: dict[str, str] = Field(default_factory=dict)
    workspace_rule_fingerprints: dict[str, str] = Field(default_factory=dict)
    runtime_config_fingerprint: str = ""


class TraceArtifactRef(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    artifact_id: str
    sha256: str
    content_type: str = "application/json"
    size_bytes: int = 0


class TraceSpan(BaseModel):
    """OTel-shaped span projection with agent-specific typed facts."""

    schema_version: int = CONTRACT_SCHEMA_VERSION
    trace_id: str
    span_id: str
    run_id: str
    parent_span_id: str | None = None
    kind: Literal[
        "run",
        "model",
        "tool",
        "tool_call",
        "approval",
        "verification",
        "delegate",
        "checkpoint",
        "control",
        "unknown",
    ] = "unknown"
    name: str = ""
    status: Literal["unset", "running", "completed", "failed", "interrupted"] = "unset"
    sequence_start: int = 0
    sequence_end: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    step: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    tool_result: ToolResult | None = None
    context_manifest_artifact_id: str | None = None
    input: Any = None
    output: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    event_ids: list[str] = Field(default_factory=list)


class AgentTrace(BaseModel):
    """Canonical, redacted projection of one run's persisted facts."""

    schema_version: int = CONTRACT_SCHEMA_VERSION
    trace_id: str = Field(default_factory=new_id)
    run_id: str
    session_id: str = ""
    root_run_id: str = ""
    parent_run_id: str | None = None
    status: str = "unknown"
    completeness: Literal["complete", "partial", "legacy"] = "partial"
    partial_reasons: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    usage: Usage = Field(default_factory=Usage)
    steps: int = 0
    final_output: str = ""
    error: str | None = None
    messages: list[Message] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    versions: TraceVersions = Field(default_factory=TraceVersions)
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    event_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)

    def span(self, span_id: str | None) -> TraceSpan | None:
        if not span_id:
            return None
        return next((item for item in self.spans if item.span_id == span_id), None)


class EvidenceRef(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    trace_id: str = ""
    run_id: str = ""
    span_id: str | None = None
    event_id: str | None = None
    artifact_id: str | None = None
    message_id: str | None = None
    source: str = "trace"
    path: str | None = None
    excerpt: str = ""
    sequence: int | None = None


class ToolExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    min_calls: int | None = Field(default=None, ge=0)
    max_calls: int | None = Field(default=None, ge=0)
    exact_calls: int | None = Field(default=None, ge=0)
    arguments: dict[str, Any] | None = None
    argument_match: Literal["exact", "subset"] = "subset"
    arguments_schema: dict[str, Any] | None = None
    result_status: Literal["success", "error"] | None = None
    result_error_code: str | None = None
    result_contains: list[str] = Field(default_factory=list)


class FileExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    exists: bool = True
    contains: list[str] = Field(default_factory=list)
    sha256: str | None = None
    json_schema: dict[str, Any] | None = None


class ArtifactExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str | None = None
    sha256: str | None = None
    content_type: str | None = None
    contains: list[str] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)
    max_output_tokens: int | None = Field(default=None, ge=0)
    max_model_turns: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_verifications: int | None = Field(default=None, ge=0)
    max_duration_ms: float | None = Field(default=None, ge=0)


class SafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbid_secret_patterns: bool = True
    forbid_workspace_escape: bool = True
    forbid_unapproved_destructive: bool = True


class EvaluationPolicy(BaseModel):
    """One versioned policy interpreted by every deterministic evaluation path."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CONTRACT_SCHEMA_VERSION
    policy_id: str = Field(default_factory=new_id)
    version: str = "1"
    name: str = "evaluation"
    match_mode: Literal["exact", "strict", "subset", "unordered"] = "subset"
    expected_status: str | None = None
    output_contains: list[str] = Field(default_factory=list)
    output_contains_any: list[str] = Field(default_factory=list)
    output_forbidden: list[str] = Field(default_factory=list)
    output_regex: str | None = None
    output_exact: str | None = None
    output_normalized: str | None = None
    output_json: bool = False
    output_json_schema: dict[str, Any] | None = None
    output_jsonpath: dict[str, Any] = Field(default_factory=dict)
    output_numeric_min: float | None = None
    output_numeric_max: float | None = None
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    tool_sequence: list[str] = Field(default_factory=list)
    tools: list[ToolExpectation] = Field(default_factory=list)
    files: list[FileExpectation] = Field(default_factory=list)
    artifacts: list[ArtifactExpectation] = Field(default_factory=list)
    require_tool_pairing: bool = True
    require_verification_before_completed: bool = False
    min_retries: int | None = Field(default=None, ge=0)
    max_retries: int | None = Field(default=None, ge=0)
    min_approvals: int | None = Field(default=None, ge=0)
    min_delegates: int | None = Field(default=None, ge=0)
    min_checkpoints: int | None = Field(default=None, ge=0)
    budgets: BudgetPolicy = Field(default_factory=BudgetPolicy)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_quality_assertions(self) -> bool:
        return any(
            (
                self.expected_status is not None,
                bool(self.output_contains),
                bool(self.output_contains_any),
                bool(self.output_forbidden),
                self.output_regex is not None,
                self.output_exact is not None,
                self.output_normalized is not None,
                self.output_json,
                self.output_json_schema is not None,
                bool(self.output_jsonpath),
                self.output_numeric_min is not None,
                self.output_numeric_max is not None,
                bool(self.required_tools),
                bool(self.forbidden_tools),
                bool(self.tool_sequence),
                bool(self.tools),
                bool(self.files),
                bool(self.artifacts),
                self.require_verification_before_completed,
                self.min_retries is not None,
                self.max_retries is not None,
                self.min_approvals is not None,
                self.min_delegates is not None,
                self.min_checkpoints is not None,
                any(value is not None for value in self.budgets.model_dump().values()),
            )
        )


class CheckResult(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    id: str
    category: str
    status: Literal["passed", "failed", "not_configured", "error"]
    expected: Any = None
    actual: Any = None
    hard: bool = True
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    weight: float = Field(default=1.0, ge=0.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    failure_category: str | None = None
    recovery_hint: str | None = None
    message: str = ""


class EvaluationReport(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    report_id: str = Field(default_factory=new_id)
    trace_id: str
    run_id: str
    policy_id: str
    policy_version: str = "1"
    mode: Literal["scored", "health_only", "unscored"] = "unscored"
    passed: bool | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    checks: list[CheckResult] = Field(default_factory=list)
    first_divergence: EvidenceRef | None = None
    hard_failures: int = 0
    passed_count: int = 0
    failed_count: int = 0
    not_configured_count: int = 0
    deterministic: bool = True
    semantic: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=_utcnow)


class ProbeFinding(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    probe: str
    summary: str
    affected_configuration: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DiagnosisReport(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    diagnosis_id: str = Field(default_factory=new_id)
    trace_id: str
    report_id: str
    root_cause: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    first_divergence: EvidenceRef | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    affected_configuration: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    probes: list[ProbeFinding] = Field(default_factory=list)
    read_only: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class ReplaySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = CONTRACT_SCHEMA_VERSION
    snapshot_id: str = Field(default_factory=new_id)
    trace: AgentTrace
    trace_artifact: TraceArtifactRef | None = None
    provider: str | None = None
    model: str | None = None
    prompt_fingerprint: str = ""
    tool_schema_fingerprints: dict[str, str] = Field(default_factory=dict)
    skill_fingerprints: dict[str, str] = Field(default_factory=dict)
    workspace_rule_fingerprints: dict[str, str] = Field(default_factory=dict)
    context_fingerprint: str = ""
    runtime_config_fingerprint: str = ""
    evaluation_policy_version: str = ""
    seed: int | None = None
    temperature: float | None = None
    provider_parameters: dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def copy_trace_identity(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_trace = data.get("trace")
        trace = (
            raw_trace
            if isinstance(raw_trace, AgentTrace)
            else AgentTrace.model_validate(raw_trace)
            if raw_trace is not None
            else None
        )
        if trace is None:
            return data
        versions = trace.versions
        data["trace"] = trace
        data["provider"] = data.get("provider") or trace.provider
        data["model"] = data.get("model") or trace.model
        data["prompt_fingerprint"] = (
            data.get("prompt_fingerprint") or versions.prompt_fingerprint
        )
        data["tool_schema_fingerprints"] = data.get(
            "tool_schema_fingerprints"
        ) or dict(versions.tool_schema_fingerprints)
        data["skill_fingerprints"] = data.get("skill_fingerprints") or dict(
            versions.skill_fingerprints
        )
        data["workspace_rule_fingerprints"] = data.get(
            "workspace_rule_fingerprints"
        ) or dict(versions.workspace_rule_fingerprints)
        data["context_fingerprint"] = (
            data.get("context_fingerprint") or versions.context_fingerprint
        )
        data["runtime_config_fingerprint"] = data.get(
            "runtime_config_fingerprint"
        ) or versions.runtime_config_fingerprint
        return data


class JudgeRubric(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    rubric_id: str
    version: str
    task_type: str = "general"
    text: str
    pass_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class JudgeSample(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    sample_id: str = Field(default_factory=new_id)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    passed: bool | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    dimensions: dict[str, Any] = Field(default_factory=dict)
    failure_category: str = "none"
    improvements: list[str] = Field(default_factory=list)
    abstained: bool = False
    error: str | None = None


class SemanticEvaluation(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    rubric_id: str
    rubric_version: str
    status: Literal["trusted", "unverified", "degraded", "abstained"] = "unverified"
    samples: list[JudgeSample] = Field(default_factory=list)
    mean_score: float | None = None
    median_score: float | None = None
    variance: float | None = None
    consistency: float | None = None
    passed: bool | None = None
    fallback_score: float | None = Field(default=None, ge=0.0, le=1.0)
    fallback_report_id: str | None = None
    attack_resistant: bool | None = None


class CalibrationExample(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    example_id: str
    task_type: str = "general"
    human_score: float = Field(ge=0.0, le=1.0)
    human_passed: bool
    judge_scores: list[float] = Field(default_factory=list)
    synthetic: bool = False


class CalibrationReport(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    calibration_id: str = Field(default_factory=new_id)
    sample_count: int = 0
    synthetic_only: bool = False
    trust_status: Literal["trusted", "unverified"] = "unverified"
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    cohens_kappa: float | None = None
    spearman: float | None = None
    mean_absolute_error: float | None = None
    internal_consistency: float | None = None
    task_type_bias: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class RegressionFinding(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    gate: str
    triggered: bool
    message: str
    baseline: Any = None
    current: Any = None
    delta: Any = None


class RegressionPolicy(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    min_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    min_mean_score: float | None = Field(default=None, ge=0.0, le=1.0)
    max_score_drop: float | None = Field(default=0.0, ge=0.0, le=1.0)
    min_trajectory_compliance: float | None = Field(default=None, ge=0.0, le=1.0)
    min_tool_argument_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    max_latency_ratio_increase: float | None = Field(default=None, ge=0.0)
    max_token_ratio_increase: float | None = Field(default=None, ge=0.0)
    max_cost_ratio_increase: float | None = Field(default=None, ge=0.0)


class RegressionCase(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    case_id: str
    tags: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str | None = None
    evaluation: EvaluationReport
    diagnosis: DiagnosisReport | None = None
    snapshot_id: str | None = None
    web_report_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    total_tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)


class RegressionSet(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    set_id: str
    golden: bool = False
    cases: list[RegressionCase] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerunStatistics(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    sample_count: int = 0
    success_rate: float | None = None
    wilson_low: float | None = None
    wilson_high: float | None = None
    mean_score: float | None = None
    score_variance: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None


class RegressionReport(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    regression_id: str = Field(default_factory=new_id)
    baseline_path: str = ""
    baseline_id: str = ""
    candidate_id: str = ""
    gates: list[RegressionFinding] = Field(default_factory=list)
    new_failures: list[str] = Field(default_factory=list)
    score_drops: list[dict[str, Any]] = Field(default_factory=list)
    token_delta: dict[str, Any] = Field(default_factory=dict)
    latency_delta: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    case_metrics: dict[str, Any] = Field(default_factory=dict)
    tag_metrics: dict[str, Any] = Field(default_factory=dict)
    provider_model_metrics: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any] = Field(default_factory=dict)
    tokens: dict[str, Any] = Field(default_factory=dict)
    costs: dict[str, Any] = Field(default_factory=dict)
    first_divergence_distribution: dict[str, int] = Field(default_factory=dict)
    findings: list[CheckResult] = Field(default_factory=list)
    failed_cases: list[dict[str, Any]] = Field(default_factory=list)
    rerun_statistics: RerunStatistics | None = None

    @property
    def failed(self) -> bool:
        return bool(
            self.new_failures
            or any(item.triggered for item in self.gates)
            or any(item.status in {"failed", "error"} for item in self.findings)
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["failed"] = self.failed
        return payload


class GateDecision(BaseModel):
    schema_version: int = CONTRACT_SCHEMA_VERSION
    decision_id: str = Field(default_factory=new_id)
    passed: bool
    reason: str = ""
    regression: RegressionReport
    failed_case_ids: list[str] = Field(default_factory=list)
    exit_code: int = 0
