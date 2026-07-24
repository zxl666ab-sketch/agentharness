"""Usage breakdown: cumulative vs last turn vs local estimate."""

from __future__ import annotations

from agentharness.contracts import Usage, format_usage_brief


def test_format_usage_brief_shows_last_and_estimate() -> None:
    usage = Usage(
        input_tokens=315_516,
        output_tokens=5_155,
        total_tokens=320_671,
        last_input_tokens=102_000,
        last_output_tokens=800,
        last_local_estimate=4_200,
        model_turns=4,
    )
    line = format_usage_brief(usage, budget_max=200_000)
    assert "tokens=315516/5155" in line
    assert "last=102000/800" in line
    assert "est≈4200" in line
    assert "turns=4" in line
    assert "budget=320671/200000" in line


def test_format_usage_brief_empty_when_zero() -> None:
    assert format_usage_brief(Usage()) == ""
    assert format_usage_brief(None) == ""


def test_usage_extra_fields_default_for_old_payloads() -> None:
    # Storage / API may load older dumps without breakdown keys.
    usage = Usage.model_validate({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    assert usage.last_input_tokens == 0
    assert usage.model_turns == 0
    line = format_usage_brief(usage)
    assert "tokens=10/2" in line
