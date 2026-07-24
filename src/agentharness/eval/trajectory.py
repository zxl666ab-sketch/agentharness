"""Deterministic trace-native evaluator used by verification, replay, and CI."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from jsonpath_ng.ext import parse as parse_jsonpath
from jsonschema import SchemaError, ValidationError
from jsonschema import validate as validate_json_schema

from agentharness.eval.contracts import (
    AgentTrace,
    ArtifactExpectation,
    BudgetPolicy,
    CheckResult,
    EvaluationPolicy,
    EvaluationReport,
    EvidenceRef,
    FileExpectation,
    ToolExpectation,
    TraceSpan,
)
from agentharness.security.sandbox import SandboxError, assert_in_workspace

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+[^\s,]+"),
    re.compile(r"(?i)\b(?:password|api[_-]?key)\s*[:=]\s*[^\s,}\]]+"),
)


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _json_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for child in value.values():
            found = _json_number(child)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _json_number(child)
            if found is not None:
                return found
    return None


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(key in actual and _is_subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return all(any(_is_subset(item, candidate) for candidate in actual) for item in expected)
    return expected == actual


def _sequence_matches(expected: list[str], actual: list[str], mode: str) -> bool:
    if mode == "exact":
        return actual == expected
    if mode == "strict":
        return actual[: len(expected)] == expected
    if mode == "unordered":
        wanted = Counter(expected)
        observed = Counter(actual)
        return all(observed[name] >= count for name, count in wanted.items())
    iterator = iter(actual)
    return all(any(item == wanted for item in iterator) for wanted in expected)


class TrajectoryEvaluator:
    """Interpret an :class:`AgentTrace` under one versioned deterministic policy."""

    def __init__(self, *, storage: Any | None = None) -> None:
        self.storage = storage

    def evaluate(self, trace: AgentTrace, policy: EvaluationPolicy) -> EvaluationReport:
        checks: list[CheckResult] = []
        tool_spans = sorted(
            (span for span in trace.spans if span.kind == "tool"),
            key=lambda span: (span.sequence_start, span.span_id),
        )
        model_spans = sorted(
            (span for span in trace.spans if span.kind == "model"),
            key=lambda span: (span.sequence_start, span.span_id),
        )
        run_span = next((span for span in trace.spans if span.kind == "run"), None)
        fallback_span = tool_spans[-1] if tool_spans else (model_spans[-1] if model_spans else run_span)

        def add(
            check_id: str,
            category: str,
            passed: bool,
            *,
            expected: Any = None,
            actual: Any = None,
            span: TraceSpan | None = None,
            hard: bool = True,
            failure_category: str | None = None,
            recovery_hint: str | None = None,
            message: str = "",
            evidence: list[EvidenceRef] | None = None,
            error: bool = False,
        ) -> None:
            refs = evidence if evidence is not None else [self._evidence(trace, span or fallback_span)]
            checks.append(
                CheckResult(
                    id=check_id,
                    category=category,
                    status="error" if error else "passed" if passed else "failed",
                    expected=expected,
                    actual=actual,
                    hard=hard,
                    score=1.0 if passed else 0.0,
                    evidence=[item for item in refs if item is not None],
                    failure_category=None if passed else failure_category,
                    recovery_hint=None if passed else recovery_hint,
                    message=message,
                )
            )

        if policy.expected_status is not None:
            add(
                "run.status",
                "final_state",
                trace.status == policy.expected_status,
                expected=policy.expected_status,
                actual=trace.status,
                span=run_span,
                failure_category="premature_completion",
                recovery_hint="Finish the run in the required terminal state.",
            )

        self._evaluate_output(trace, policy, checks, add, model_spans[-1] if model_spans else None)
        self._evaluate_trajectory(trace, policy, tool_spans, checks, add, fallback_span)
        self._evaluate_tool_expectations(trace, policy.tools, tool_spans, add)
        self._evaluate_lifecycle(trace, policy, tool_spans, add, run_span)
        self._evaluate_budgets(trace, policy, tool_spans, add, run_span)
        self._evaluate_safety(trace, policy, tool_spans, add, run_span)
        self._evaluate_files(trace, policy.files, add)
        self._evaluate_artifacts(trace, policy.artifacts, add)

        mode: str
        if not policy.has_quality_assertions:
            if trace.event_count == 0 and trace.status in {"", "unknown"}:
                mode = "unscored"
            else:
                mode = "health_only"
                terminal_ok = trace.status in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                    "require_human",
                }
                add(
                    "health.terminal",
                    "health",
                    terminal_ok,
                    expected="terminal status",
                    actual=trace.status,
                    span=run_span,
                    failure_category="premature_completion",
                )
            checks.append(
                CheckResult(
                    id="policy.assertions",
                    category="policy",
                    status="not_configured",
                    hard=False,
                    score=None,
                    message="No quality assertion is configured.",
                )
            )
        else:
            mode = "scored"

        failed = [check for check in checks if check.status in {"failed", "error"}]
        hard_failures = sum(1 for check in failed if check.hard)
        scored = [check for check in checks if check.status in {"passed", "failed", "error"}]
        score = None
        if mode == "scored" and scored:
            total_weight = sum(check.weight for check in scored)
            score = round(
                sum(check.weight for check in scored if check.status == "passed") / total_weight
                if total_weight
                else 0.0,
                4,
            )
        first = self._first_divergence(failed)
        passed: bool | None
        if mode == "unscored":
            passed = None
        else:
            passed = hard_failures == 0 and not failed
        return EvaluationReport(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            mode=mode,  # type: ignore[arg-type]
            passed=passed,
            score=score,
            checks=checks,
            first_divergence=first,
            hard_failures=hard_failures,
            passed_count=sum(1 for check in checks if check.status == "passed"),
            failed_count=sum(1 for check in checks if check.status in {"failed", "error"}),
            not_configured_count=sum(1 for check in checks if check.status == "not_configured"),
            metadata={"trace_completeness": trace.completeness},
        )

    def _evaluate_output(
        self,
        trace: AgentTrace,
        policy: EvaluationPolicy,
        checks: list[CheckResult],
        add: Any,
        span: TraceSpan | None,
    ) -> None:
        output = trace.final_output or ""
        for index, needle in enumerate(policy.output_contains):
            add(
                f"output.contains.{index}",
                "output",
                needle in output,
                expected=needle,
                actual=output,
                span=span,
                failure_category="output_mismatch",
                recovery_hint="Include the required output content.",
                message=(f"missing substring: {needle!r}" if needle not in output else ""),
            )
        if policy.output_contains_any:
            add(
                "output.contains_any",
                "output",
                any(needle in output for needle in policy.output_contains_any),
                expected=policy.output_contains_any,
                actual=output,
                span=span,
                failure_category="output_mismatch",
            )
        for index, needle in enumerate(policy.output_forbidden):
            add(
                f"output.forbidden.{index}",
                "output",
                needle not in output,
                expected={"absent": needle},
                actual=output,
                span=span,
                failure_category="forbidden_output",
            )
        if policy.output_regex is not None:
            try:
                matched = re.search(policy.output_regex, output) is not None
                add(
                    "output.regex",
                    "output",
                    matched,
                    expected=policy.output_regex,
                    actual=output,
                    span=span,
                    failure_category="output_mismatch",
                )
            except re.error as exc:
                add(
                    "output.regex",
                    "output",
                    False,
                    expected=policy.output_regex,
                    actual=str(exc),
                    span=span,
                    failure_category="invalid_policy",
                    error=True,
                )
        if policy.output_exact is not None:
            add(
                "output.exact",
                "output",
                output == policy.output_exact,
                expected=policy.output_exact,
                actual=output,
                span=span,
                failure_category="output_mismatch",
            )
        if policy.output_normalized is not None:
            add(
                "output.normalized",
                "output",
                _normalize(output) == _normalize(policy.output_normalized),
                expected=_normalize(policy.output_normalized),
                actual=_normalize(output),
                span=span,
                failure_category="output_mismatch",
            )

        needs_json = any(
            (
                policy.output_json,
                policy.output_json_schema is not None,
                bool(policy.output_jsonpath),
                policy.output_numeric_min is not None,
                policy.output_numeric_max is not None,
            )
        )
        if not needs_json:
            return
        parsed: Any = None
        parse_error: str | None = None
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
        if policy.output_json or policy.output_json_schema is not None or policy.output_jsonpath:
            add(
                "output.json",
                "output",
                parse_error is None,
                expected="valid JSON",
                actual=parse_error or parsed,
                span=span,
                failure_category="invalid_output_json",
            )
        if parse_error is not None:
            return
        if policy.output_json_schema is not None:
            try:
                validate_json_schema(parsed, policy.output_json_schema)
                add(
                    "output.json_schema",
                    "output",
                    True,
                    expected=policy.output_json_schema,
                    actual=parsed,
                    span=span,
                )
            except (ValidationError, SchemaError) as exc:
                add(
                    "output.json_schema",
                    "output",
                    False,
                    expected=policy.output_json_schema,
                    actual=exc.message,
                    span=span,
                    failure_category="output_schema_mismatch",
                    error=isinstance(exc, SchemaError),
                )
        for index, (expression, expected) in enumerate(policy.output_jsonpath.items()):
            try:
                values = [match.value for match in parse_jsonpath(expression).find(parsed)]
                matched = any(value == expected for value in values)
                add(
                    f"output.jsonpath.{index}",
                    "output",
                    matched,
                    expected={expression: expected},
                    actual=values,
                    span=span,
                    failure_category="output_jsonpath_mismatch",
                )
            except Exception as exc:  # noqa: BLE001 - parser errors become policy findings
                add(
                    f"output.jsonpath.{index}",
                    "output",
                    False,
                    expected={expression: expected},
                    actual=str(exc),
                    span=span,
                    failure_category="invalid_policy",
                    error=True,
                )
        number = _json_number(parsed)
        if policy.output_numeric_min is not None:
            add(
                "output.numeric_min",
                "output",
                number is not None and number >= policy.output_numeric_min,
                expected={"min": policy.output_numeric_min},
                actual=number,
                span=span,
                failure_category="output_range_mismatch",
            )
        if policy.output_numeric_max is not None:
            add(
                "output.numeric_max",
                "output",
                number is not None and number <= policy.output_numeric_max,
                expected={"max": policy.output_numeric_max},
                actual=number,
                span=span,
                failure_category="output_range_mismatch",
            )

    def _evaluate_trajectory(
        self,
        trace: AgentTrace,
        policy: EvaluationPolicy,
        tool_spans: list[TraceSpan],
        checks: list[CheckResult],
        add: Any,
        fallback_span: TraceSpan | None,
    ) -> None:
        actual = [span.tool_name or span.name for span in tool_spans]
        if policy.tool_sequence:
            passed = _sequence_matches(policy.tool_sequence, actual, policy.match_mode)
            divergence = self._sequence_divergence_span(
                policy.tool_sequence, tool_spans, policy.match_mode
            )
            add(
                "trajectory.sequence",
                "trajectory",
                passed,
                expected={"mode": policy.match_mode, "tools": policy.tool_sequence},
                actual=actual,
                span=divergence or fallback_span,
                failure_category="wrong_tool_selection",
                recovery_hint="Follow the required tool trajectory and ordering.",
            )
        for index, required in enumerate(policy.required_tools):
            matching = next((span for span in tool_spans if span.tool_name == required), None)
            add(
                f"tool.required.{index}",
                "tool",
                matching is not None,
                expected=required,
                actual=actual,
                span=matching or fallback_span,
                failure_category="missing_required_step",
                recovery_hint=f"Call the required tool {required}.",
            )
        for index, forbidden in enumerate(policy.forbidden_tools):
            matching = next((span for span in tool_spans if span.tool_name == forbidden), None)
            add(
                f"tool.forbidden.{index}",
                "tool",
                matching is None,
                expected={"absent": forbidden},
                actual=actual,
                span=matching or fallback_span,
                failure_category="wrong_tool_selection",
            )

    def _evaluate_tool_expectations(
        self,
        trace: AgentTrace,
        expectations: Iterable[ToolExpectation],
        tool_spans: list[TraceSpan],
        add: Any,
    ) -> None:
        for expectation in expectations:
            matches = [span for span in tool_spans if span.tool_name == expectation.name]
            count = len(matches)
            if expectation.exact_calls is not None:
                add(
                    f"tool.{expectation.name}.count",
                    "tool",
                    count == expectation.exact_calls,
                    expected={"exact": expectation.exact_calls},
                    actual=count,
                    span=matches[expectation.exact_calls] if count > expectation.exact_calls else (matches[-1] if matches else None),
                    failure_category="duplicate_tool_call" if count > expectation.exact_calls else "missing_required_step",
                )
            if expectation.min_calls is not None:
                add(
                    f"tool.{expectation.name}.min_calls",
                    "tool",
                    count >= expectation.min_calls,
                    expected={"min": expectation.min_calls},
                    actual=count,
                    span=matches[-1] if matches else None,
                    failure_category="missing_required_step",
                )
            if expectation.max_calls is not None:
                add(
                    f"tool.{expectation.name}.max_calls",
                    "tool",
                    count <= expectation.max_calls,
                    expected={"max": expectation.max_calls},
                    actual=count,
                    span=matches[expectation.max_calls] if count > expectation.max_calls else (matches[-1] if matches else None),
                    failure_category="duplicate_tool_call",
                )
            if expectation.arguments is not None:
                argument_matches = [
                    span
                    for span in matches
                    if (
                        span.tool_arguments == expectation.arguments
                        if expectation.argument_match == "exact"
                        else _is_subset(expectation.arguments, span.tool_arguments)
                    )
                ]
                add(
                    f"tool.{expectation.name}.arguments",
                    "tool",
                    bool(argument_matches),
                    expected=expectation.arguments,
                    actual=[span.tool_arguments for span in matches],
                    span=argument_matches[0] if argument_matches else (matches[0] if matches else None),
                    failure_category="invalid_tool_arguments",
                    recovery_hint="Use arguments that match the tool contract.",
                )
            if expectation.arguments_schema is not None:
                errors: list[tuple[TraceSpan, str, bool]] = []
                for span in matches:
                    try:
                        validate_json_schema(span.tool_arguments, expectation.arguments_schema)
                    except (ValidationError, SchemaError) as exc:
                        errors.append((span, exc.message, isinstance(exc, SchemaError)))
                add(
                    f"tool.{expectation.name}.arguments_schema",
                    "tool",
                    bool(matches) and not errors,
                    expected=expectation.arguments_schema,
                    actual=[message for _span, message, _schema_error in errors]
                    or [span.tool_arguments for span in matches],
                    span=errors[0][0] if errors else (matches[0] if matches else None),
                    failure_category="invalid_tool_arguments",
                    error=bool(errors and errors[0][2]),
                )
            if expectation.result_status is not None:
                bad = [
                    span
                    for span in matches
                    if span.tool_result is None
                    or (expectation.result_status == "success" and span.tool_result.is_error)
                    or (expectation.result_status == "error" and not span.tool_result.is_error)
                ]
                add(
                    f"tool.{expectation.name}.result_status",
                    "tool_result",
                    bool(matches) and not bad,
                    expected=expectation.result_status,
                    actual=[
                        "missing" if span.tool_result is None else "error" if span.tool_result.is_error else "success"
                        for span in matches
                    ],
                    span=bad[0] if bad else (matches[0] if matches else None),
                    failure_category="tool_result_error",
                )
            if expectation.result_error_code is not None:
                matched = next(
                    (
                        span
                        for span in matches
                        if span.tool_result
                        and span.tool_result.error_code == expectation.result_error_code
                    ),
                    None,
                )
                add(
                    f"tool.{expectation.name}.error_code",
                    "tool_result",
                    matched is not None,
                    expected=expectation.result_error_code,
                    actual=[span.tool_result.error_code if span.tool_result else None for span in matches],
                    span=matched or (matches[0] if matches else None),
                    failure_category="tool_result_error",
                )
            for index, needle in enumerate(expectation.result_contains):
                matched = next(
                    (
                        span
                        for span in matches
                        if span.tool_result and needle in span.tool_result.content
                    ),
                    None,
                )
                add(
                    f"tool.{expectation.name}.result_contains.{index}",
                    "tool_result",
                    matched is not None,
                    expected=needle,
                    actual=[span.tool_result.content if span.tool_result else None for span in matches],
                    span=matched or (matches[0] if matches else None),
                    failure_category="tool_result_mismatch",
                )

    def _evaluate_lifecycle(
        self,
        trace: AgentTrace,
        policy: EvaluationPolicy,
        tool_spans: list[TraceSpan],
        add: Any,
        run_span: TraceSpan | None,
    ) -> None:
        if policy.require_tool_pairing:
            unpaired = next(
                (span for span in tool_spans if not span.tool_call_id or span.tool_result is None), None
            )
            if tool_spans:
                add(
                    "tool.pairing",
                    "tool_result",
                    unpaired is None,
                    expected="every tool call has one result",
                    actual={"tool_calls": len(tool_spans), "unpaired": 1 if unpaired else 0},
                    span=unpaired or tool_spans[-1],
                    failure_category="tool_result_missing",
                )
        verification_spans = [span for span in trace.spans if span.kind == "verification"]
        approval_spans = [
            span for span in trace.spans if span.kind == "approval" and span.name == "approval_resolved"
        ]
        delegate_spans = [
            span for span in trace.spans if span.kind == "delegate" and span.name == "child_run_started"
        ]
        checkpoint_spans = [span for span in trace.spans if span.kind == "checkpoint"]
        if policy.require_verification_before_completed:
            latest = max((span.sequence_end for span in verification_spans), default=-1)
            terminal = run_span.sequence_end if run_span else max(
                (span.sequence_end for span in trace.spans), default=0
            )
            add(
                "lifecycle.verification_before_completed",
                "verification",
                bool(verification_spans) and latest < terminal,
                expected="verification before completed",
                actual={"verification_sequence": latest, "terminal_sequence": terminal},
                span=verification_spans[-1] if verification_spans else run_span,
                failure_category="verification_missing",
            )
        retry_count = sum(max(0, count - 1) for count in Counter(
            (span.tool_name, json.dumps(span.tool_arguments, sort_keys=True, default=str))
            for span in tool_spans
        ).values())
        for check_id, minimum, maximum, actual, category, failure_category in (
            ("lifecycle.retries", policy.min_retries, policy.max_retries, retry_count, "retry", "retry_loop"),
            ("lifecycle.approvals", policy.min_approvals, None, len(approval_spans), "approval", "approval_deadlock"),
            ("lifecycle.delegates", policy.min_delegates, None, len(delegate_spans), "delegate", "missing_required_step"),
            ("lifecycle.checkpoints", policy.min_checkpoints, None, len(checkpoint_spans), "checkpoint", "missing_required_step"),
        ):
            if minimum is not None:
                add(
                    f"{check_id}.min",
                    category,
                    actual >= minimum,
                    expected={"min": minimum},
                    actual=actual,
                    span=run_span,
                    failure_category=failure_category,
                )
            if maximum is not None:
                add(
                    f"{check_id}.max",
                    category,
                    actual <= maximum,
                    expected={"max": maximum},
                    actual=actual,
                    span=run_span,
                    failure_category=failure_category,
                )

    def _evaluate_budgets(
        self,
        trace: AgentTrace,
        policy: EvaluationPolicy,
        tool_spans: list[TraceSpan],
        add: Any,
        span: TraceSpan | None,
    ) -> None:
        values = (
            ("tokens", policy.budgets.max_tokens, trace.usage.total_tokens),
            ("input_tokens", policy.budgets.max_input_tokens, trace.usage.input_tokens),
            ("output_tokens", policy.budgets.max_output_tokens, trace.usage.output_tokens),
            ("model_turns", policy.budgets.max_model_turns, trace.usage.model_turns),
            ("steps", policy.budgets.max_steps, trace.steps),
            ("tool_calls", policy.budgets.max_tool_calls, len(tool_spans)),
            (
                "verifications",
                policy.budgets.max_verifications,
                sum(1 for item in trace.spans if item.kind == "verification" and item.name == "verification_result"),
            ),
            ("duration_ms", policy.budgets.max_duration_ms, trace.duration_ms or 0.0),
        )
        for name, maximum, actual in values:
            if maximum is None:
                continue
            add(
                f"budget.{name}",
                "budget",
                actual <= maximum,
                expected={"max": maximum},
                actual=actual,
                span=span,
                failure_category="budget_exhaustion",
                recovery_hint="Reduce the trajectory cost or raise the explicit budget.",
            )

    def _evaluate_safety(
        self,
        trace: AgentTrace,
        policy: EvaluationPolicy,
        tool_spans: list[TraceSpan],
        add: Any,
        run_span: TraceSpan | None,
    ) -> None:
        if policy.safety.forbid_secret_patterns:
            serialized = trace.model_dump_json()
            leaked = next((pattern.pattern for pattern in _SECRET_PATTERNS if pattern.search(serialized)), None)
            add(
                "safety.redaction",
                "safety",
                leaked is None,
                expected="no raw secret pattern",
                actual=leaked,
                span=run_span,
                failure_category="secret_exposure",
                recovery_hint="Redact the value before it reaches trace persistence.",
            )
        if policy.safety.forbid_workspace_escape:
            escaped = next(
                (
                    span
                    for span in tool_spans
                    if any(
                        re.search(r"(^|[\\/\s])\.\.([\\/\s]|$)", value) is not None
                        for value in self._strings(span.tool_arguments)
                    )
                    or (
                        span.tool_result is not None
                        and span.tool_result.error_code in {"workspace_violation", "path_outside_workspace"}
                    )
                ),
                None,
            )
            add(
                "safety.workspace",
                "safety",
                escaped is None,
                expected="workspace-confined paths",
                actual=escaped.tool_arguments if escaped else None,
                span=escaped or run_span,
                failure_category="workspace_violation",
            )
        if policy.safety.forbid_unapproved_destructive:
            approvals = {
                str(span.attributes.get("tool_call_id"))
                for span in trace.spans
                if span.kind == "approval"
                and span.name == "approval_resolved"
                and str(span.attributes.get("decision")) in {"allow_once", "allow_run"}
            }
            destructive = next(
                (
                    span
                    for span in tool_spans
                    if span.tool_name == "shell" and (span.tool_call_id or "") not in approvals
                ),
                None,
            )
            add(
                "safety.approval",
                "safety",
                destructive is None,
                expected="approval before destructive tool",
                actual=destructive.tool_name if destructive else None,
                span=destructive or run_span,
                failure_category="unapproved_destructive_operation",
            )

    def _evaluate_files(
        self, trace: AgentTrace, expectations: Iterable[FileExpectation], add: Any
    ) -> None:
        cwd = str(trace.metadata.get("cwd") or ".")
        for index, expectation in enumerate(expectations):
            evidence = EvidenceRef(
                trace_id=trace.trace_id,
                run_id=trace.run_id,
                source="file",
                path=expectation.path,
            )
            try:
                target = assert_in_workspace(expectation.path, cwd=cwd, must_exist=False)
                exists = target.is_file()
                errors: list[str] = []
                content = ""
                if exists:
                    content = target.read_text(encoding="utf-8", errors="replace")
                if exists != expectation.exists:
                    errors.append("existence mismatch")
                if exists:
                    missing = [needle for needle in expectation.contains if needle not in content]
                    if missing:
                        errors.append(f"missing content: {missing}")
                    if expectation.sha256 is not None:
                        digest = hashlib.sha256(target.read_bytes()).hexdigest()
                        if digest != expectation.sha256:
                            errors.append(f"sha256={digest}")
                    if expectation.json_schema is not None:
                        try:
                            validate_json_schema(json.loads(content), expectation.json_schema)
                        except (json.JSONDecodeError, ValidationError, SchemaError) as exc:
                            errors.append(str(exc))
                add(
                    f"file.{index}",
                    "file",
                    not errors,
                    expected=expectation.model_dump(mode="json"),
                    actual={"exists": exists, "errors": errors},
                    failure_category="file_assertion_failed",
                    evidence=[evidence],
                )
            except (SandboxError, OSError) as exc:
                add(
                    f"file.{index}",
                    "file",
                    False,
                    expected=expectation.model_dump(mode="json"),
                    actual=str(exc),
                    failure_category="workspace_violation",
                    evidence=[evidence],
                )

    def _evaluate_artifacts(
        self, trace: AgentTrace, expectations: Iterable[ArtifactExpectation], add: Any
    ) -> None:
        for index, expectation in enumerate(expectations):
            row = None
            storage = self.storage
            if storage is not None:
                if expectation.artifact_id:
                    row = storage.get_artifact(expectation.artifact_id)
                elif expectation.sha256:
                    row = storage.get_artifact_by_sha(expectation.sha256)
            artifact_id = str((row or {}).get("id") or expectation.artifact_id or "")
            evidence = EvidenceRef(
                trace_id=trace.trace_id,
                run_id=trace.run_id,
                artifact_id=artifact_id or None,
                source="artifact",
            )
            errors: list[str] = []
            content = ""
            if row is None:
                errors.append("artifact not found")
            else:
                assert storage is not None
                sha = str(row.get("sha256") or "")
                content = storage.artifacts.get_text(sha) or ""
                if expectation.sha256 and sha != expectation.sha256:
                    errors.append(f"sha256={sha}")
                if expectation.content_type and row.get("content_type") != expectation.content_type:
                    errors.append(f"content_type={row.get('content_type')}")
                missing = [needle for needle in expectation.contains if needle not in content]
                if missing:
                    errors.append(f"missing content: {missing}")
                if expectation.json_schema is not None:
                    try:
                        validate_json_schema(json.loads(content), expectation.json_schema)
                    except (json.JSONDecodeError, ValidationError, SchemaError) as exc:
                        errors.append(str(exc))
            add(
                f"artifact.{index}",
                "artifact",
                not errors,
                expected=expectation.model_dump(mode="json"),
                actual={"artifact_id": artifact_id, "errors": errors},
                failure_category="artifact_assertion_failed",
                evidence=[evidence],
            )

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in TrajectoryEvaluator._strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in TrajectoryEvaluator._strings(child)]
        return []

    @staticmethod
    def _evidence(trace: AgentTrace, span: TraceSpan | None) -> EvidenceRef:
        return EvidenceRef(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            span_id=span.span_id if span else None,
            event_id=span.event_ids[0] if span and span.event_ids else None,
            source="trace_span" if span else "trace",
            sequence=span.sequence_start if span else None,
        )

    @staticmethod
    def _first_divergence(failures: list[CheckResult]) -> EvidenceRef | None:
        refs = [ref for check in failures for ref in check.evidence]
        if not refs:
            return None
        return min(refs, key=lambda ref: (ref.sequence is None, ref.sequence or 0))

    @staticmethod
    def _sequence_divergence_span(
        expected: list[str], actual: list[TraceSpan], mode: str
    ) -> TraceSpan | None:
        if not actual:
            return None
        actual_names = [span.tool_name or span.name for span in actual]
        if mode in {"exact", "strict"}:
            for index, wanted in enumerate(expected):
                if index >= len(actual) or actual_names[index] != wanted:
                    return actual[min(index, len(actual) - 1)]
            if mode == "exact" and len(actual) > len(expected):
                return actual[len(expected)]
            return None
        if mode == "unordered":
            missing = Counter(expected) - Counter(actual_names)
            return actual[-1] if missing else None
        cursor = 0
        for span in actual:
            if cursor < len(expected) and (span.tool_name or span.name) == expected[cursor]:
                cursor += 1
        return actual[-1] if cursor < len(expected) else None


def policy_from_assertions(assertions: Any, *, policy_id: str | None = None) -> EvaluationPolicy:
    """Convert the v1 flat assertion DSL into the shared v2 policy contract."""
    raw = (
        assertions.model_dump(mode="python", by_alias=True)
        if hasattr(assertions, "model_dump")
        else dict(assertions or {})
    )
    digest = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return EvaluationPolicy(
        policy_id=policy_id or f"assertions:{digest}",
        version=str(raw.get("schema_version") or "1"),
        match_mode=raw.get("match_mode") or "subset",
        expected_status=raw.get("status"),
        output_contains=list(raw.get("contains") or []),
        output_contains_any=list(raw.get("contains_any") or []),
        output_forbidden=list(raw.get("forbidden") or []),
        output_regex=raw.get("regex"),
        output_exact=raw.get("exact"),
        output_normalized=raw.get("normalized"),
        output_json=bool(raw.get("json") or raw.get("json_output")),
        output_json_schema=raw.get("json_schema"),
        output_jsonpath=dict(raw.get("jsonpath") or {}),
        output_numeric_min=raw.get("numeric_min"),
        output_numeric_max=raw.get("numeric_max"),
        required_tools=list(raw.get("tools_used") or []),
        forbidden_tools=list(raw.get("forbidden_tools") or []),
        tool_sequence=list(raw.get("tools_order") or []),
        tools=list(raw.get("tools") or []),
        files=list(raw.get("files") or []),
        artifacts=list(raw.get("artifacts") or []),
        require_tool_pairing=bool(raw.get("require_tool_pairing", True)),
        require_verification_before_completed=bool(
            raw.get("require_verification_before_completed", False)
        ),
        min_retries=raw.get("min_retries"),
        max_retries=raw.get("max_retries"),
        min_approvals=raw.get("min_approvals"),
        min_delegates=raw.get("min_delegates"),
        min_checkpoints=raw.get("min_checkpoints"),
        budgets=BudgetPolicy(
            max_tokens=raw.get("max_tokens"),
            max_input_tokens=raw.get("max_input_tokens"),
            max_output_tokens=raw.get("max_output_tokens"),
            max_model_turns=raw.get("max_model_turns"),
            max_steps=raw.get("max_steps"),
            max_tool_calls=raw.get("max_tool_calls"),
            max_verifications=raw.get("max_verifications"),
            max_duration_ms=(
                float(raw["max_latency_s"]) * 1000.0
                if raw.get("max_latency_s") is not None
                else None
            ),
        ),
    )
