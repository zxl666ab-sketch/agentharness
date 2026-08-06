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
from agentharness.harness import Harness
from agentharness.providers.openai_adapter import _classify_error


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


def test_classify_error_detects_context_length() -> None:
    class _HttpError(RuntimeError):
        def __init__(self, message: str, status: int) -> None:
            super().__init__(message)
            self.status_code = status

    message, kind = _classify_error(
        _HttpError("This model's maximum context length is 128000 tokens", 400)
    )
    assert kind == "context_length"
    assert "context length" in message

    _, generic = _classify_error(_HttpError("unexpected upstream failure", 500))
    assert generic == "server_error"


@pytest.mark.asyncio
async def test_context_length_error_shrinks_budget_and_retries_once(
    data_dir, workspace
) -> None:
    class RecordingProvider(_SequenceProvider):
        def __init__(self, name: str, attempts: list[list[ModelStreamItem]]) -> None:
            super().__init__(name, attempts)
            self.requests: list[ModelRequest] = []

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
            self.requests.append(request)
            async for item in super().stream(request):
                yield item

    provider = RecordingProvider(
        "long-context",
        [_error("context_length"), _success("OK")],
    )
    harness = Harness(data_dir=data_dir, providers={"long-context": provider})
    try:
        result = await harness.run(
            RunRequest(
                message="shrink me",
                provider="long-context",
                cwd=str(workspace),
                provider_retry=ProviderRetryConfig(max_retries=0),
            )
        )
        events = harness.get_events(run_id=result.run_id, limit=1000)
    finally:
        await harness.aclose()

    assert result.status == RunStatus.completed
    assert result.output == "OK"
    assert provider.calls == 2
    assert [attempt.status for attempt in result.usage.provider_attempts] == [
        "error",
        "completed",
    ]
    retry = next(event for event in events if str(event.type) == "provider_retry")
    assert retry.payload["error_kind"] == "context_length"
    # Default BudgetConfig max_context_tokens=100_000 is halved on the one retry.
    assert retry.payload["context_shrunk_to"] == 50_000
    assert len(provider.requests) == 2
