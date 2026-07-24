"""Deep candidate-verification module with bounded corrective decisions."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agentharness.contracts import (
    Message,
    MessageRole,
    ToolResult,
    VerificationCandidate,
    VerificationCheck,
    VerificationDecision,
    VerificationFailure,
    VerificationPolicy,
)
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.security.sandbox import SandboxError, assert_in_workspace

CommandRunner = Callable[[VerificationCandidate, str], Awaitable[ToolResult]]
EvaluatorResolver = Callable[[str], Any | None]


class VerificationLoop:
    """Dispatch validators and collapse their outcomes into one explicit decision."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        command_runner: CommandRunner | None = None,
        evaluator_resolver: EvaluatorResolver | None = None,
    ) -> None:
        self.redactor = redactor or default_redactor
        self.command_runner = command_runner
        self.evaluator_resolver = evaluator_resolver

    async def evaluate(
        self,
        candidate: VerificationCandidate,
        policy: VerificationPolicy,
        *,
        attempt: int,
    ) -> VerificationDecision:
        """Validate a candidate once; retry scheduling remains an explicit decision."""
        if candidate.cancel_event is not None and candidate.cancel_event.is_set():
            failure = VerificationFailure(
                validator="loop",
                error_code="cancelled",
                message="Verification was cancelled.",
                retryable=False,
            )
            return VerificationDecision(action="stop", failures=[failure], attempt=attempt)

        failures: list[VerificationFailure] = []
        evidence: dict[str, Any] = {}
        for index, check in enumerate(policy.validators):
            if candidate.cancel_event is not None and candidate.cancel_event.is_set():
                failures.append(
                    VerificationFailure(
                        validator="loop",
                        error_code="cancelled",
                        message="Verification was cancelled.",
                        retryable=False,
                    )
                )
                break
            failure, check_evidence = await self._run_check(candidate, policy, check)
            evidence[f"{index}:{check.kind}"] = self.redactor.redact_obj(check_evidence)
            if failure is not None:
                failures.append(failure)

        if not failures:
            return VerificationDecision(
                action="pass",
                attempt=attempt,
                evidence=self.redactor.redact_obj(evidence),
            )

        if any(not failure.retryable for failure in failures):
            action = "require_human"
        elif attempt < policy.max_retries:
            action = "retry"
        elif policy.on_exhausted == "failed":
            action = "stop"
        else:
            action = "require_human"

        feedback = self._feedback(candidate, failures, attempt=attempt, action=action)
        return VerificationDecision(
            action=action,
            feedback=feedback,
            feedback_message=(
                Message(role=MessageRole.user, content=feedback) if action == "retry" else None
            ),
            failures=failures,
            attempt=attempt,
            evidence=self.redactor.redact_obj(evidence),
        )

    async def _run_check(
        self,
        candidate: VerificationCandidate,
        policy: VerificationPolicy,
        check: VerificationCheck,
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        if check.kind == "eval_assert":
            return self._eval_assert(candidate, check)
        if check.kind == "file":
            return self._file_check(candidate, check)
        if check.kind == "command":
            return await self._command_check(candidate, check)
        return await self._ai_check(candidate, policy, check)

    def _eval_assert(
        self, candidate: VerificationCandidate, check: VerificationCheck
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        # Lazy imports avoid evaluator -> Harness -> RunEngine import cycles.
        from agentharness.eval.contracts import AgentTrace, TraceSpan
        from agentharness.eval.dataset import AssertionSpec, EvalCase
        from agentharness.eval.trajectory import TrajectoryEvaluator, policy_from_assertions

        raw = check.assertions or candidate.eval_assert or {}
        try:
            assertions = AssertionSpec.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            failure = VerificationFailure(
                validator="eval_assert",
                error_code="invalid_assertion",
                message=f"Invalid eval assertion: {exc}",
                retryable=False,
                recovery_hint="Fix the Verification Policy assertion schema.",
            )
            return failure, {"assertions": raw}
        # Validate through the existing DSL contract, then interpret through the
        # canonical policy/evaluator used by offline replay and CI.
        EvalCase.model_validate(
            {
                "id": f"verify:{candidate.run_id}",
                "prompt": candidate.goal,
                "assert": assertions.model_dump(mode="json", by_alias=True),
            }
        )
        policy_v2 = policy_from_assertions(
            assertions, policy_id=f"verification:{candidate.run_id}"
        )
        if candidate.trace is not None:
            trace = AgentTrace.model_validate(candidate.trace)
        else:
            spans = [
                TraceSpan(
                    trace_id=f"candidate:{candidate.run_id}",
                    span_id=f"candidate-tool:{index}",
                    run_id=candidate.run_id,
                    kind="tool",
                    name=name,
                    status="completed",
                    sequence_start=index + 1,
                    sequence_end=index + 1,
                    tool_call_id=f"candidate-call:{index}",
                    tool_name=name,
                )
                for index, name in enumerate(candidate.tools_ordered)
            ]
            trace = AgentTrace(
                trace_id=f"candidate:{candidate.run_id}",
                run_id=candidate.run_id,
                status="completed",
                completeness="partial",
                partial_reasons=["verification_candidate_without_persisted_trace"],
                final_output=candidate.output,
                usage=candidate.usage,
                steps=candidate.steps,
                duration_ms=candidate.latency_s * 1000.0,
                messages=candidate.messages,
                spans=spans,
                event_count=1,
                metadata={"cwd": candidate.cwd},
            )
            policy_v2 = policy_v2.model_copy(
                update={
                    "require_tool_pairing": False,
                    "safety": policy_v2.safety.model_copy(
                        update={"forbid_unapproved_destructive": False}
                    ),
                }
            )
        report = TrajectoryEvaluator().evaluate(trace, policy_v2)
        failed = [check for check in report.checks if check.status in {"failed", "error"}]
        reasons = [
            check.message
            or f"{check.id}: expected {check.expected!r}, actual {check.actual!r}"
            for check in failed
        ]
        evidence = {
            "passed": report.passed,
            "score": report.score,
            "reasons": reasons,
            "report": report.model_dump(mode="json"),
        }
        if report.passed:
            return None, evidence
        return (
            VerificationFailure(
                validator="eval_assert",
                error_code="assertion_failed",
                message="; ".join(reasons) or "Deterministic assertion failed.",
                evidence=self.redactor.redact_obj(
                    report.first_divergence.model_dump(mode="json")
                    if report.first_divergence
                    else {}
                ),
                retryable=True,
                recovery_hint="Correct the candidate so every deterministic assertion passes.",
            ),
            evidence,
        )

    def _file_check(
        self, candidate: VerificationCandidate, check: VerificationCheck
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        if not check.path:
            return (
                VerificationFailure(
                    validator="file",
                    error_code="invalid_file_check",
                    message="File validator requires path.",
                    retryable=False,
                    recovery_hint="Add a workspace-relative path to the policy.",
                ),
                {},
            )
        try:
            target = assert_in_workspace(
                check.path,
                cwd=candidate.cwd,
                extra_dirs=candidate.extra_dirs,
                must_exist=False,
            )
        except (SandboxError, OSError) as exc:
            return (
                VerificationFailure(
                    validator="file",
                    error_code="workspace_violation",
                    message=str(exc),
                    retryable=True,
                    recovery_hint="Use a path inside the configured workspace.",
                ),
                {"path": check.path},
            )
        exists = target.is_file()
        evidence: dict[str, Any] = {"path": str(target), "exists": exists}
        if exists != check.exists:
            return (
                VerificationFailure(
                    validator="file",
                    error_code="file_condition_failed",
                    message=(
                        f"Expected {check.path} to {'exist' if check.exists else 'be absent'}, "
                        f"but it {'exists' if exists else 'is absent'}."
                    ),
                    evidence=evidence,
                    retryable=True,
                    recovery_hint=(
                        "Create or correct the required file."
                        if check.exists
                        else "Remove the file only if the user goal permits it."
                    ),
                ),
                evidence,
            )
        if not exists:
            return None, evidence
        try:
            content = target.read_text(encoding="utf-8", errors="replace")[:1_000_000]
        except OSError as exc:
            return (
                VerificationFailure(
                    validator="file",
                    error_code="file_read_failed",
                    message=str(exc),
                    retryable=True,
                    recovery_hint="Re-read the file and repair permissions or encoding.",
                ),
                evidence,
            )
        missing = [needle for needle in check.contains if needle not in content]
        evidence["contains"] = {needle: needle not in missing for needle in check.contains}
        if missing:
            return (
                VerificationFailure(
                    validator="file",
                    error_code="file_content_failed",
                    message=f"{check.path} is missing required content: {missing}",
                    evidence=evidence,
                    retryable=True,
                    recovery_hint="Update the file to include every required content condition.",
                ),
                evidence,
            )
        return None, evidence

    async def _command_check(
        self, candidate: VerificationCandidate, check: VerificationCheck
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        if not check.command:
            failure = VerificationFailure(
                validator="command",
                error_code="invalid_command_check",
                message="Command validator requires a command.",
                retryable=False,
                recovery_hint="Add a command to the Verification Policy.",
            )
            return failure, {}
        if self.command_runner is None:
            failure = VerificationFailure(
                validator="command",
                error_code="governed_runner_unavailable",
                message="Governed command runner is unavailable.",
                retryable=False,
                recovery_hint="Enable the shell tool and its Approval/Sandbox path.",
            )
            return failure, {"command": check.command}
        started = time.monotonic()
        result = await self.command_runner(candidate, check.command)
        evidence = {
            "command": check.command,
            "is_error": result.is_error,
            "output": self.redactor.redact_text(result.content[:2000]),
            "duration_ms": result.duration_ms or (time.monotonic() - started) * 1000,
            "error_code": result.error_code,
            "error_category": result.error_category,
        }
        missing = [needle for needle in check.contains if needle not in result.content]
        if not result.is_error and not missing:
            return None, evidence
        code = result.error_code or ("command_output_failed" if missing else "command_failed")
        return (
            VerificationFailure(
                validator="command",
                error_code=code,
                message=(
                    f"Command output is missing {missing}"
                    if missing
                    else self.redactor.redact_text(result.content[:1000]) or "Command failed."
                ),
                evidence=evidence,
                retryable=result.retryable if result.error_code else True,
                recovery_hint=result.recovery_hint or "Inspect the output and correct the failing code.",
            ),
            evidence,
        )

    async def _ai_check(
        self,
        candidate: VerificationCandidate,
        policy: VerificationPolicy,
        check: VerificationCheck,
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        provider_name = policy.evaluator_provider
        if not provider_name or self.evaluator_resolver is None:
            return self._evaluator_configuration_failure("Independent evaluator is not configured.")
        if provider_name == candidate.executor_provider:
            return self._evaluator_configuration_failure(
                "Evaluator provider must be independent from the executing provider.",
                code="evaluator_not_independent",
            )
        adapter = self.evaluator_resolver(provider_name)
        if adapter is None:
            return self._evaluator_configuration_failure(
                f"Evaluator provider {provider_name!r} is unavailable."
            )
        if adapter is candidate.executor_adapter:
            return self._evaluator_configuration_failure(
                "Evaluator adapter must be isolated from the executing adapter.",
                code="evaluator_not_independent",
            )
        try:
            from agentharness.eval.ai_judge import judge_trajectory

            judged = await judge_trajectory(
                adapter,
                model=policy.evaluator_model,
                trajectory={
                    "goal": candidate.goal,
                    "output": candidate.output,
                    "steps": candidate.steps,
                    "tools_ordered": candidate.tools_ordered,
                    "messages": [m.model_dump(mode="json") for m in candidate.messages],
                },
                redactor=self.redactor,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                VerificationFailure(
                    validator="ai",
                    error_code="evaluator_failed",
                    message=self.redactor.redact_text(str(exc)),
                    retryable=False,
                    recovery_hint="Ask a human to review or restore the independent evaluator.",
                ),
                {"provider": provider_name},
            )
        verdict = judged.verdict
        core = (
            verdict.dimensions.task_completion.score
            + verdict.dimensions.correctness.score
            + verdict.dimensions.completeness.score
        ) / 3
        evidence = {
            "provider": provider_name,
            "score": core,
            "confidence": verdict.confidence,
            "hard_safety_violation": verdict.hard_safety_violation,
            "failure_category": verdict.failure_category,
            "evidence": verdict.evidence,
            "improvements": verdict.improvements,
            "usage": judged.usage.model_dump(),
        }
        if core >= check.min_score and not verdict.hard_safety_violation:
            return None, evidence
        return (
            VerificationFailure(
                validator="ai",
                error_code=(
                    "hard_safety_violation"
                    if verdict.hard_safety_violation
                    else "ai_score_below_threshold"
                ),
                message=f"Independent evaluator score {core:.3f} is below {check.min_score:.3f}.",
                evidence=self.redactor.redact_obj(evidence),
                retryable=not verdict.hard_safety_violation,
                recovery_hint=(verdict.improvements[0] if verdict.improvements else "Correct the cited gaps."),
            ),
            evidence,
        )

    @staticmethod
    def _evaluator_configuration_failure(
        message: str, *, code: str = "evaluator_unavailable"
    ) -> tuple[VerificationFailure, dict[str, Any]]:
        return (
            VerificationFailure(
                validator="ai",
                error_code=code,
                message=message,
                retryable=False,
                recovery_hint="Configure a separate read-only evaluator or ask a human to review.",
            ),
            {},
        )

    def _feedback(
        self,
        candidate: VerificationCandidate,
        failures: list[VerificationFailure],
        *,
        attempt: int,
        action: str,
    ) -> str:
        payload = self.redactor.redact_obj(
            {
                "type": "verification_feedback",
                "original_goal": candidate.goal,
                "attempt": attempt,
                "decision": action,
                "failures": [failure.model_dump(mode="json") for failure in failures],
                "instruction": (
                    "Correct the failed items, gather fresh evidence, and return a new candidate."
                ),
            }
        )
        return "[verification_feedback]\n" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
