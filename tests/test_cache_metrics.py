"""Prompt-cache metrics: adapter parsing, engine accumulation, cache-aware cost."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentharness.contracts import (
    ApprovalMode,
    PricingConfig,
    RunRequest,
    RunStatus,
    Usage,
)
from agentharness.harness import Harness
from agentharness.providers.openai_adapter import _usage_item
from tests.fake_provider import FakeModelAdapter


def test_usage_item_parses_chat_cached_tokens() -> None:
    raw = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        prompt_tokens_details=SimpleNamespace(cached_tokens=80),
    )
    usage = _usage_item(raw)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 80


def test_usage_item_parses_responses_cached_tokens_dict_shape() -> None:
    raw = SimpleNamespace(
        input_tokens=200,
        output_tokens=20,
        total_tokens=220,
        input_tokens_details={"cached_tokens": 150},
    )
    usage = _usage_item(raw)
    assert usage is not None
    assert usage.cached_input_tokens == 150


def test_usage_item_clamps_and_defaults_cached_tokens() -> None:
    over_reporting = SimpleNamespace(
        prompt_tokens=50,
        completion_tokens=5,
        total_tokens=55,
        prompt_tokens_details=SimpleNamespace(cached_tokens=999),
    )
    usage = _usage_item(over_reporting)
    assert usage is not None
    assert usage.cached_input_tokens == 50

    plain = SimpleNamespace(prompt_tokens=50, completion_tokens=5, total_tokens=55)
    usage = _usage_item(plain)
    assert usage is not None
    assert usage.cached_input_tokens == 0

    broken = SimpleNamespace(
        prompt_tokens=50,
        completion_tokens=5,
        total_tokens=55,
        prompt_tokens_details=SimpleNamespace(cached_tokens="not-a-number"),
    )
    usage = _usage_item(broken)
    assert usage is not None
    assert usage.cached_input_tokens == 0


def test_cache_hit_rate_is_computed_and_serialized() -> None:
    usage = Usage(input_tokens=1000, cached_input_tokens=750)
    assert usage.cache_hit_rate == 0.75
    assert Usage().cache_hit_rate == 0.0
    # Clamped even if a gateway over-reports.
    assert Usage(input_tokens=10, cached_input_tokens=99).cache_hit_rate == 1.0
    dumped = usage.model_dump(mode="json")
    assert dumped["cache_hit_rate"] == 0.75
    assert dumped["cached_input_tokens"] == 750


@pytest.mark.asyncio
async def test_engine_accumulates_cached_tokens_across_turns(
    data_dir, workspace
) -> None:
    adapter = FakeModelAdapter(
        script=[
            {
                "kind": "text",
                "text": "answer",
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "cached_input_tokens": 60,
            }
        ]
    )
    harness = Harness(data_dir=data_dir, providers={"fake": adapter})
    try:
        result = await harness.run(
            RunRequest(
                message="hello",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert result.usage.cached_input_tokens == 60
    assert result.usage.last_cached_input_tokens == 60
    assert result.usage.cache_hit_rate == pytest.approx(0.6)
    assert result.usage.provider_attempts[-1].cached_input_tokens == 60


@pytest.mark.asyncio
async def test_cached_pricing_discounts_estimated_cost(data_dir, workspace) -> None:
    adapter = FakeModelAdapter(
        script=[
            {
                "kind": "text",
                "text": "answer",
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "cached_input_tokens": 60,
            }
        ]
    )
    pricing = PricingConfig(
        input_per_million_usd=10.0,
        output_per_million_usd=20.0,
        cached_input_per_million_usd=1.0,
    )
    harness = Harness(data_dir=data_dir, providers={"fake": adapter})
    try:
        result = await harness.run(
            RunRequest(
                message="hello",
                provider="fake",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                pricing=pricing,
            )
        )
    finally:
        await harness.aclose()

    # (40 uncached × $10 + 60 cached × $1 + 10 out × $20) / 1e6
    assert result.usage.estimated_cost_usd == pytest.approx(0.00066)
    # Without a cached rate the same run costs full input price.
    full = (100 * 10.0 + 10 * 20.0) / 1_000_000
    assert result.usage.estimated_cost_usd < full
