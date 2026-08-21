"""W3C Distributed Tracing (traceparent) and Token FinOps tracker for LLM Gateway."""

from __future__ import annotations

import random
import time
from decimal import Decimal
from typing import Any


class TraceContext:
    """W3C Trace Context propagator (traceparent format: version-trace_id-parent_id-flags)."""

    def __init__(self, trace_id: str | None = None, span_id: str | None = None, sampled: bool = True):
        self.version = "00"
        self.trace_id = trace_id or f"{random.getrandbits(128):032x}"
        self.span_id = span_id or f"{random.getrandbits(64):016x}"
        self.flags = "01" if sampled else "00"

    def to_header(self) -> str:
        """Format as standard W3C 'traceparent' header."""
        return f"{self.version}-{self.trace_id}-{self.span_id}-{self.flags}"

    @classmethod
    def from_header(cls, header: str | None) -> TraceContext:
        """Parse incoming W3C 'traceparent' or create fresh context."""
        if not header:
            return cls()
        parts = header.strip().split("-")
        if len(parts) >= 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
            return cls(trace_id=parts[1], span_id=parts[2], sampled=(parts[3] == "01"))
        return cls()

    def create_child_span(self) -> TraceContext:
        """Derive a new child span context sharing the same trace_id."""
        child_span_id = f"{random.getrandbits(64):016x}"
        return TraceContext(trace_id=self.trace_id, span_id=child_span_id, sampled=(self.flags == "01"))


class TokenFinOpsTracker:
    """FinOps cost tracking and token quota metrics for LLM Provider invocations."""

    # Approximate unit prices per 1K tokens (in USD or CNY normalized)
    MODEL_UNIT_PRICES: dict[str, dict[str, Decimal]] = {
        "deepseek-chat": {"prompt": Decimal("0.001"), "completion": Decimal("0.002")},
        "deepseek-reasoner": {"prompt": Decimal("0.004"), "completion": Decimal("0.016")},
        "gpt-4o": {"prompt": Decimal("0.015"), "completion": Decimal("0.060")},
        "gpt-4o-mini": {"prompt": Decimal("0.001"), "completion": Decimal("0.003")},
        "default": {"prompt": Decimal("0.002"), "completion": Decimal("0.004")},
    }

    def __init__(self):
        self._records: list[dict[str, Any]] = []

    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        pricing = self.MODEL_UNIT_PRICES.get(model, self.MODEL_UNIT_PRICES["default"])
        cost = (
            (Decimal(prompt_tokens) / Decimal(1000)) * pricing["prompt"]
            + (Decimal(completion_tokens) / Decimal(1000)) * pricing["completion"]
        )
        return cost.quantize(Decimal("0.000001"))

    def record_usage(
        self,
        task_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
        trace_context: TraceContext | None = None,
    ) -> dict[str, Any]:
        cost = self.calculate_cost(model, prompt_tokens, completion_tokens)
        entry = {
            "task_id": task_id,
            "trace_id": trace_context.trace_id if trace_context else None,
            "span_id": trace_context.span_id if trace_context else None,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost": str(cost),
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        self._records.append(entry)
        return entry
