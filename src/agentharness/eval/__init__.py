"""Offline eval harness — run task suites and score success / cost / latency.

Reuses the public Harness surface only (RunRequest -> RunResult), so it works
against any provider (fake, openai, anthropic, or a local vLLM endpoint) with no
changes to the engine. Metrics are grouped by provider/model so a base model, a
LoRA-tuned model, and a frontier model can be compared on the same suite.
"""

from agentharness.eval.dataset import EvalCase, EvalSuite, load_suite
from agentharness.eval.runner import CaseResult, SuiteReport, run_suite

__all__ = [
    "EvalCase",
    "EvalSuite",
    "load_suite",
    "CaseResult",
    "SuiteReport",
    "run_suite",
]
