from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest

from agentharness.contracts import (
    ApprovalMode,
    ModelRequest,
    ModelStreamItem,
    ProviderRetryConfig,
    RunRequest,
    RunStatus,
    StreamItemType,
    Usage,
)
from agentharness.engine.runtime import _RETRYABLE_PROVIDER_ERRORS, _provider_exception_kind
from agentharness.harness import Harness
from agentharness.providers.gateway import GatewayBlockedError


def test_gateway_block_errors_are_classified_retryable() -> None:
    """H2 修复：网关限流/熔断异常必须进入可重试集合（P2-1 恢复路径）。"""
    blocked = GatewayBlockedError("rate_limited", provider="openai", retry_after_s=1.5)
    assert _provider_exception_kind(blocked) == "rate_limited"
    assert _provider_exception_kind(blocked) in _RETRYABLE_PROVIDER_ERRORS
    opened = GatewayBlockedError("circuit_open", provider="openai", retry_after_s=2.0)
    assert _provider_exception_kind(opened) == "circuit_open"
    assert _provider_exception_kind(opened) in _RETRYABLE_PROVIDER_ERRORS


class _SequenceProvider:
    def __init__(self, name: str, attempts: list[list[ModelStreamItem]]) -> None:
        self.name = name
        self.attempts = attempts
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        index = min(self.calls, len(self.attempts) - 1)
        self.calls += 1
        for item in self.attempts[index]:
            yield item


def _error(kind: str, *, retry_after_s: float | None = None) -> list[ModelStreamItem]:
    return [
        ModelStreamItem(
            type=StreamItemType.error,
            error=f"injected {kind}",
            error_kind=kind,
            retry_after_s=retry_after_s,
        )
    ]


def _success(text: str) -> list[ModelStreamItem]:
    return [
        ModelStreamItem(type=StreamItemType.text_delta, text=text),
        ModelStreamItem(
            type=StreamItemType.usage,
            usage=Usage(input_tokens=2, output_tokens=1, total_tokens=3),
        ),
        ModelStreamItem(type=StreamItemType.done),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "connection", "server_error"])
async def test_retryable_provider_errors_recover_before_output(
    data_dir, workspace, kind: str
) -> None:
    provider = _SequenceProvider("unstable", [_error(kind), _error(kind), _success("OK")])
    harness = Harness(data_dir=data_dir, providers={"unstable": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="retry",
                provider="unstable",
                approval=ApprovalMode.auto,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(base_delay_s=0),
            )
        )
        events = harness.get_events(run_id=result.run_id, limit=1000)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert result.output == "OK"
    assert provider.calls == 3
    assert [attempt.status for attempt in result.usage.provider_attempts] == [
        "error",
        "error",
        "completed",
    ]
    assert sum(str(event.type) == "provider_retry" for event in events) == 2


@pytest.mark.asyncio
async def test_retry_is_bounded_to_three_retries(data_dir, workspace) -> None:
    provider = _SequenceProvider("down", [_error("rate_limit")])
    harness = Harness(data_dir=data_dir, providers={"down": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="bounded retry",
                provider="down",
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(base_delay_s=0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert provider.calls == 4
    assert len(result.usage.provider_attempts) == 4


@pytest.mark.asyncio
async def test_rate_limit_honors_provider_retry_after(data_dir, workspace) -> None:
    provider = _SequenceProvider(
        "rate-limited",
        [_error("rate_limit", retry_after_s=0.03), _success("OK")],
    )
    harness = Harness(data_dir=data_dir, providers={provider.name: provider})
    started = time.monotonic()
    try:
        result = await harness.run(
            RunRequest(
                message="respect retry-after",
                provider=provider.name,
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(base_delay_s=0, jitter_ratio=0),
            )
        )
        events = harness.get_events(run_id=result.run_id, limit=1000)
    finally:
        await harness.aclose()

    elapsed = time.monotonic() - started
    retry = next(event for event in events if str(event.type) == "provider_retry")
    assert result.status == RunStatus.completed
    assert elapsed >= 0.025
    assert retry.payload["retry_after_s"] == 0.03
    assert retry.payload["delay_s"] == 0.03


@pytest.mark.asyncio
async def test_partial_text_stops_retry_to_prevent_duplicate_output(data_dir, workspace) -> None:
    provider = _SequenceProvider(
        "partial",
        [
            [
                ModelStreamItem(type=StreamItemType.text_delta, text="PARTIAL"),
                *_error("rate_limit"),
            ],
            _success("MUST_NOT_APPEAR"),
        ],
    )
    harness = Harness(data_dir=data_dir, providers={"partial": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="partial",
                provider="partial",
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(base_delay_s=0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert result.output == "PARTIAL"
    assert provider.calls == 1
    assert result.usage.provider_attempts[0].had_output is True


@pytest.mark.asyncio
async def test_partial_tool_delta_stops_retry_before_side_effect(data_dir, workspace) -> None:
    provider = _SequenceProvider(
        "partial-tool",
        [
            [
                ModelStreamItem(
                    type=StreamItemType.tool_call_start,
                    tool_call_id="call-1",
                    tool_name="write_file",
                ),
                ModelStreamItem(
                    type=StreamItemType.tool_call_delta,
                    tool_call_id="call-1",
                    tool_name="write_file",
                    arguments_delta='{"path":"side-effect.txt"',
                ),
                *_error("server_error"),
            ],
            _success("MUST_NOT_APPEAR"),
        ],
    )
    harness = Harness(data_dir=data_dir, providers={"partial-tool": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="partial tool",
                provider="partial-tool",
                cwd=str(workspace),
                approval=ApprovalMode.auto,
                tools=["write_file"],
                provider_retry=ProviderRetryConfig(base_delay_s=0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert provider.calls == 1
    assert not (workspace / "side-effect.txt").exists()


@pytest.mark.asyncio
async def test_no_implicit_fallback(data_dir, workspace) -> None:
    primary = _SequenceProvider("primary", [_error("provider")])
    secondary = _SequenceProvider("secondary", [_success("MUST_NOT_RUN")])
    harness = Harness(
        data_dir=data_dir,
        providers={"primary": primary, "secondary": secondary},
    )
    try:
        result = await harness.run(
            RunRequest(
                message="no fallback",
                provider="primary",
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
            )
        )
    finally:
        await harness.aclose()

    assert result.status == RunStatus.failed
    assert primary.calls == 1
    assert secondary.calls == 0
