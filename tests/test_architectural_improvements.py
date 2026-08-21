"""Unit tests for AST sandbox, Self-Correction reflection, Cancellation, and Trace FinOps."""

from decimal import Decimal
import pytest
from pydantic import BaseModel, Field

from agentharness.security.ast_sandbox import validate_python_code, ASTSecurityError
from agentharness.procurement.reflection import extract_with_reflection, ParsingReflectionExhaustedError
from agentharness.engine.cancellation import CancellationToken, TaskCancelledError
from agentharness.observability.trace_finops import TraceContext, TokenFinOpsTracker


def test_ast_sandbox_blocks_banned_imports():
    with pytest.raises(ASTSecurityError, match="Import of banned module"):
        validate_python_code("import os\nos.system('dir')")


def test_ast_sandbox_blocks_dunder_reflection():
    with pytest.raises(ASTSecurityError, match="Access to private/dunder attribute"):
        validate_python_code("x = ().__class__.__subclasses__()")


def test_ast_sandbox_allows_safe_code():
    validate_python_code("a = [1, 2, 3]\nb = sum(a)\nprint(b)")


def test_cancellation_token():
    token = CancellationToken("task-123")
    assert not token.is_cancelled
    token.cancel()
    assert token.is_cancelled
    with pytest.raises(TaskCancelledError):
        token.throw_if_cancelled()


def test_trace_context_propagation():
    ctx = TraceContext.from_header("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx.span_id == "00f067aa0ba902b7"
    child = ctx.create_child_span()
    assert child.trace_id == ctx.trace_id
    assert child.span_id != ctx.span_id
    assert child.to_header().startswith(f"00-{ctx.trace_id}-")


def test_token_finops_tracker():
    tracker = TokenFinOpsTracker()
    entry = tracker.record_usage(
        task_id="task-001",
        model="deepseek-chat",
        prompt_tokens=1000,
        completion_tokens=500,
        duration_ms=450.0,
    )
    assert entry["total_tokens"] == 1500
    assert Decimal(entry["estimated_cost"]) > Decimal("0")


class DummySupplierQuote(BaseModel):
    supplier_name: str
    total_amount: float = Field(gt=0)


@pytest.mark.asyncio
async def test_reflection_loop_success():
    calls = []
    async def mock_llm(prompt: str):
        calls.append(prompt)
        if len(calls) == 1:
            # First attempt returns invalid JSON schema
            return "```json\n{\"supplier_name\": \"Acme Corp\", \"total_amount\": -10}\n```"
        # Second attempt fixes it
        return "```json\n{\"supplier_name\": \"Acme Corp\", \"total_amount\": 150.5}\n```"

    res = await extract_with_reflection("extract info", DummySupplierQuote, mock_llm, max_retries=2)
    assert res.supplier_name == "Acme Corp"
    assert res.total_amount == 150.5
    assert len(calls) == 2
