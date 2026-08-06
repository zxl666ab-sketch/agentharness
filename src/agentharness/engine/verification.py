"""Deep candidate-verification module with bounded corrective decisions."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentharness.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    StreamItemType,
    VerificationCandidate,
    VerificationCheck,
    VerificationDecision,
    VerificationFailure,
    VerificationPolicy,
)
from agentharness.security.redaction import Redactor, default_redactor
from agentharness.security.sandbox import SandboxError, assert_in_workspace

EvaluatorResolver = Callable[[str], Any | None]


class VerificationLoop:
    """Dispatch validators and collapse their outcomes into one explicit decision."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        evaluator_resolver: EvaluatorResolver | None = None,
    ) -> None:
        self.redactor = redactor or default_redactor
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
        if check.kind == "output":
            return self._output_check(candidate, check)
        if check.kind == "file":
            return self._file_check(candidate, check)
        return await self._ai_check(candidate, policy, check)

    def _output_check(
        self, candidate: VerificationCandidate, check: VerificationCheck
    ) -> tuple[VerificationFailure | None, dict[str, Any]]:
        raw = check.assertions or candidate.output_assertions or {}
        if not isinstance(raw, dict):
            failure = VerificationFailure(
                validator="output",
                error_code="invalid_assertion",
                message="Deterministic assertions must be an object.",
                retryable=False,
                recovery_hint="Fix the Verification Policy assertion schema.",
            )
            return failure, {"assertions": raw}

        def string_list(name: str) -> list[str]:
            value = raw.get(name, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} must be a list of strings")
            return value

        try:
            required = string_list("contains")
            forbidden = string_list("not_contains")
            expected_tools = string_list("tools_ordered")
            required_successful_tools = string_list("tools_succeeded")
        except ValueError as exc:
            return (
                VerificationFailure(
                    validator="output",
                    error_code="invalid_assertion",
                    message=str(exc),
                    retryable=False,
                    recovery_hint="Fix the Verification Policy assertion schema.",
                ),
                {"assertions": raw},
            )

        missing = [needle for needle in required if needle not in candidate.output]
        present_forbidden = [needle for needle in forbidden if needle in candidate.output]
        reasons: list[str] = []
        if missing:
            reasons.append(f"output is missing required text: {missing}")
        if present_forbidden:
            reasons.append(f"output contains forbidden text: {present_forbidden}")
        if expected_tools and candidate.tools_ordered != expected_tools:
            reasons.append(
                f"expected tools {expected_tools!r}, actual {candidate.tools_ordered!r}"
            )
        missing_successful_tools = [
            name for name in required_successful_tools if name not in candidate.tools_succeeded
        ]
        if missing_successful_tools:
            reasons.append(
                "required successful tools are missing: "
                f"{missing_successful_tools!r}; actual {candidate.tools_succeeded!r}"
            )
        max_steps = raw.get("max_steps")
        if max_steps is not None:
            if not isinstance(max_steps, int) or max_steps < 0:
                return (
                    VerificationFailure(
                        validator="output",
                        error_code="invalid_assertion",
                        message="max_steps must be a non-negative integer",
                        retryable=False,
                    ),
                    {"assertions": raw},
                )
            if candidate.steps > max_steps:
                reasons.append(f"steps {candidate.steps} exceed maximum {max_steps}")
        evidence = {
            "passed": not reasons,
            "reasons": reasons,
            "contains": {needle: needle not in missing for needle in required},
            "not_contains": {
                needle: needle not in present_forbidden for needle in forbidden
            },
            "tools_ordered": candidate.tools_ordered,
            "tools_succeeded": candidate.tools_succeeded,
            "steps": candidate.steps,
        }
        if not reasons:
            return None, evidence
        return (
            VerificationFailure(
                validator="output",
                error_code="assertion_failed",
                message="; ".join(reasons) or "Deterministic assertion failed.",
                evidence=self.redactor.redact_obj(evidence),
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
        judge_prompt = json.dumps(
            {
                "goal": candidate.goal,
                "candidate_output": candidate.output,
                "steps": candidate.steps,
                "tools_ordered": candidate.tools_ordered,
                "instruction": (
                    "Return JSON with dimensions.task_completion/correctness/completeness "
                    "objects containing score 0..1, plus confidence, "
                    "hard_safety_violation, failure_category, evidence and improvements."
                ),
            },
            ensure_ascii=False,
        )
        try:
            chunks: list[str] = []
            async for item in adapter.stream(
                ModelRequest(
                    model=policy.evaluator_model,
                    system="You are an independent read-only verifier. Return JSON only.",
                    messages=[Message(role=MessageRole.user, content=judge_prompt)],
                    tools=[],
                    temperature=0,
                    max_tokens=2_000,
                )
            ):
                if item.type == StreamItemType.text_delta and item.text:
                    chunks.append(item.text)
                elif item.type == StreamItemType.error:
                    raise RuntimeError(item.error or "evaluator provider error")
            raw_verdict = "".join(chunks).strip()
            if raw_verdict.startswith("```"):
                raw_verdict = raw_verdict.strip("`")
                if raw_verdict.startswith("json"):
                    raw_verdict = raw_verdict[4:].lstrip()
            verdict = json.loads(raw_verdict)
            dimensions = verdict.get("dimensions") or {}
            scores: list[float] = []
            for name in ("task_completion", "correctness", "completeness"):
                raw_score = (dimensions.get(name) or {}).get("score")
                if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                    raise ValueError(f"evaluator dimension {name} has no numeric score")
                scores.append(float(raw_score))
            if any(score < 0 or score > 1 for score in scores):
                raise ValueError("evaluator scores must be between 0 and 1")
            core = sum(scores) / len(scores)
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
        hard_safety = bool(verdict.get("hard_safety_violation"))
        improvements = [str(item) for item in (verdict.get("improvements") or [])]
        evidence = {
            "provider": provider_name,
            "score": core,
            "confidence": verdict.get("confidence"),
            "hard_safety_violation": hard_safety,
            "failure_category": verdict.get("failure_category"),
            "evidence": verdict.get("evidence") or [],
            "improvements": improvements,
        }
        if core >= check.min_score and not hard_safety:
            return None, evidence
        return (
            VerificationFailure(
                validator="ai",
                error_code=(
                    "hard_safety_violation"
                    if hard_safety
                    else "ai_score_below_threshold"
                ),
                message=f"Independent evaluator score {core:.3f} is below {check.min_score:.3f}.",
                evidence=self.redactor.redact_obj(evidence),
                retryable=not hard_safety,
                recovery_hint=(improvements[0] if improvements else "Correct the cited gaps."),
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
