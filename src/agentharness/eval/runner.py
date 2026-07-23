"""Run an eval suite through the Harness and aggregate metrics.

Concurrency-bounded, provider-agnostic. Each case runs in a fresh session so
runs stay independent. Success is scored from RunResult plus (optionally) the
run tree for tool-usage assertions. Metrics are grouped by (provider, model).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from agentharness.contracts import RunRequest
from agentharness.eval.dataset import EvalCase, EvalSuite


@dataclass
class CaseResult:
    case_id: str
    provider: str
    model: str | None
    passed: bool
    status: str
    reasons: list[str]
    latency_s: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    steps: int
    run_id: str
    tags: list[str] = field(default_factory=list)


@dataclass
class GroupMetrics:
    provider: str
    model: str | None
    total: int = 0
    passed: int = 0
    latency_s: float = 0.0
    total_tokens: int = 0
    steps: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_latency_s(self) -> float:
        return self.latency_s / self.total if self.total else 0.0

    @property
    def avg_tokens(self) -> float:
        return self.total_tokens / self.total if self.total else 0.0

    @property
    def avg_steps(self) -> float:
        return self.steps / self.total if self.total else 0.0


@dataclass
class SuiteReport:
    suite: str
    results: list[CaseResult]
    groups: list[GroupMetrics]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "total": self.total,
            "passed": self.passed,
            "pass_rate": round(self.pass_rate, 4),
            "groups": [
                {
                    "provider": g.provider,
                    "model": g.model,
                    "total": g.total,
                    "passed": g.passed,
                    "pass_rate": round(g.pass_rate, 4),
                    "avg_latency_s": round(g.avg_latency_s, 3),
                    "avg_tokens": round(g.avg_tokens, 1),
                    "avg_steps": round(g.avg_steps, 2),
                }
                for g in self.groups
            ],
            "results": [
                {
                    "case_id": r.case_id,
                    "provider": r.provider,
                    "model": r.model,
                    "passed": r.passed,
                    "status": r.status,
                    "reasons": r.reasons,
                    "latency_s": round(r.latency_s, 3),
                    "total_tokens": r.total_tokens,
                    "steps": r.steps,
                    "run_id": r.run_id,
                }
                for r in self.results
            ],
        }


def _tools_used(harness: Any, run_id: str) -> set[str]:
    """Names of tools the run actually called, from persisted assistant messages."""
    used: set[str] = set()
    for msg in harness.get_messages(run_id):
        for tc in msg.tool_calls or []:
            if tc.name:
                used.add(tc.name)
    return used


def _score(case: EvalCase, result: Any, harness: Any) -> tuple[bool, list[str]]:
    """Return (passed, reasons-for-failure)."""
    reasons: list[str] = []
    if result.status.value != case.expect_status:
        reasons.append(f"status={result.status.value}, want {case.expect_status}")
    out = result.output or ""
    for needle in case.expect_output_contains:
        if needle not in out:
            reasons.append(f"missing substring: {needle!r}")
    if case.expect_output_contains_any:
        if not any(n in out for n in case.expect_output_contains_any):
            reasons.append(f"none of any-substrings present: {case.expect_output_contains_any}")
    if case.expect_tools_used:
        used = _tools_used(harness, result.run_id)
        for tool in case.expect_tools_used:
            if tool not in used:
                reasons.append(f"tool not used: {tool}")
    return (not reasons, reasons)


async def run_suite(
    harness: Any,
    suite: EvalSuite,
    *,
    provider: str | None = None,
    model: str | None = None,
    concurrency: int = 3,
) -> SuiteReport:
    """Run every case through harness.run and aggregate metrics by (provider, model)."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _run_one(case: EvalCase) -> CaseResult:
        prov = case.provider or suite.provider or provider or "fake"
        mdl = case.model or suite.model or model
        req = RunRequest(
            message=case.prompt,
            provider=prov,
            model=mdl,
            system=case.system or suite.system,
            cwd=case.cwd,
        )
        async with sem:
            t0 = time.monotonic()
            try:
                result = await harness.run(req)
            except Exception as exc:  # noqa: BLE001 — a crashed run is a failed case, not a crashed suite
                return CaseResult(
                    case_id=case.id, provider=prov, model=mdl, passed=False,
                    status="error", reasons=[f"exception: {exc}"], latency_s=time.monotonic() - t0,
                    input_tokens=0, output_tokens=0, total_tokens=0, steps=0,
                    run_id="", tags=case.tags,
                )
            latency = time.monotonic() - t0
        passed, reasons = _score(case, result, harness)
        u = result.usage
        return CaseResult(
            case_id=case.id, provider=prov, model=mdl, passed=passed,
            status=result.status.value, reasons=reasons, latency_s=latency,
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            total_tokens=u.total_tokens, steps=result.steps,
            run_id=result.run_id, tags=case.tags,
        )

    results = await asyncio.gather(*(_run_one(c) for c in suite.cases))

    grouped: dict[tuple[str, str | None], GroupMetrics] = {}
    for r in results:
        key = (r.provider, r.model)
        g = grouped.get(key)
        if g is None:
            g = GroupMetrics(provider=r.provider, model=r.model)
            grouped[key] = g
        g.total += 1
        g.passed += int(r.passed)
        g.latency_s += r.latency_s
        g.total_tokens += r.total_tokens
        g.steps += r.steps

    return SuiteReport(suite=suite.name, results=list(results), groups=list(grouped.values()))

