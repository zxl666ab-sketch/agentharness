"""P2-1: provider gateway — concurrency quota, QPS token bucket, circuit breaker,
degradation and event emission (rate-limit / circuit / degrade paths)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentharness.contracts import ModelRequest, ModelStreamItem, StreamItemType
from agentharness.harness import Harness
from agentharness.providers.gateway import (
    DEGRADED_MARKER,
    CircuitBreaker,
    GatewayAdapter,
    GatewayBlockedError,
    ProviderGateway,
    TokenBucket,
    degraded_summary,
    gateway_config_from_env,
)


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeAdapter:
    name = "fake"

    def __init__(
        self,
        *,
        fail: bool = False,
        active: list[int] | None = None,
        slow: bool = False,
    ) -> None:
        self.fail = fail
        self.active = active
        self.slow = slow
        self.calls = 0

    async def stream(self, request: ModelRequest):
        self.calls += 1
        if self.active is not None:
            self.active.append(1)
        try:
            if self.slow:
                await asyncio.sleep(0.05)
            if self.fail:
                yield ModelStreamItem(type=StreamItemType.error, error="boom", error_kind="provider")
                return
            yield ModelStreamItem(type=StreamItemType.text_delta, text="ok")
            if self.slow:
                await asyncio.sleep(0.05)
            yield ModelStreamItem(type=StreamItemType.done)
        finally:
            if self.active is not None:
                self.active.pop()


def _gateway(
    clock: _FakeClock,
    events: list[tuple[str, str, dict[str, Any]]] | None = None,
    **overrides: Any,
) -> ProviderGateway:
    config = gateway_config_from_env("openai")
    config.update(overrides)
    return ProviderGateway(
        config=config,
        emit=(lambda provider, event, detail: events.append((provider, event, detail)))
        if events is not None
        else None,
        clock=clock,
    )


def _request(**metadata: Any) -> ModelRequest:
    return ModelRequest(messages=[], tools=[], model="fake", metadata=metadata)


async def _drain(adapter: GatewayAdapter, request: ModelRequest) -> list[ModelStreamItem]:
    return [item async for item in adapter.stream(request)]


class TestTokenBucket:
    def test_grants_within_rate_and_returns_wait_after(self) -> None:
        clock = _FakeClock()
        bucket = TokenBucket(rate=2.0, clock=clock)
        assert bucket.take() == 0.0
        assert bucket.take() == 0.0
        assert bucket.take() > 0.0
        clock.advance(0.5)
        assert bucket.take() == 0.0


class TestCircuitBreaker:
    def test_opens_after_failure_rate_with_min_samples(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            window_s=30.0, failure_rate=0.5, min_samples=5, open_s=60.0, clock=clock
        )
        for _ in range(4):
            assert breaker.record(False) == "closed"  # below min samples
        assert breaker.state() == "closed"
        assert breaker.record(False) == "open"
        assert breaker.state() == "open"
        assert breaker.allow() is False
        assert breaker.remaining_open_s() == pytest.approx(60.0)

    def test_successes_keep_closed(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            window_s=30.0, failure_rate=0.5, min_samples=5, open_s=60.0, clock=clock
        )
        for _ in range(5):
            breaker.record(True)
        assert breaker.state() == "closed"

    def test_half_open_probe_recovers_on_success(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            window_s=30.0, failure_rate=0.5, min_samples=5, open_s=60.0, clock=clock
        )
        for _ in range(5):
            breaker.record(False)
        assert breaker.state() == "open"
        assert breaker.allow() is False
        clock.advance(61.0)
        assert breaker.state() == "half_open"
        assert breaker.allow() is True
        assert breaker.record(True) == "closed"
        assert breaker.state() == "closed"

    def test_half_open_probe_failure_reopens(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            window_s=30.0, failure_rate=0.5, min_samples=5, open_s=60.0, clock=clock
        )
        for _ in range(5):
            breaker.record(False)
        clock.advance(61.0)
        assert breaker.record(False) == "open"
        assert breaker.state() == "open"
        assert breaker.remaining_open_s() > 0

    def test_window_prunes_old_outcomes(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            window_s=30.0, failure_rate=0.5, min_samples=5, open_s=60.0, clock=clock
        )
        for _ in range(4):
            breaker.record(False)  # below min samples → closed
        assert breaker.state() == "closed"
        clock.advance(31.0)  # 窗口过期 → 旧失败被剪枝
        breaker.record(True)
        breaker.record(True)
        breaker.record(True)
        # 若无剪枝：4 失败 + 3 成功 = 57% ≥ 50% → open；剪枝后 3 样本 → closed
        assert breaker.state() == "closed"


class TestGatewayAcquire:
    @pytest.mark.asyncio
    async def test_circuit_open_blocks_with_retry_after(self) -> None:
        clock = _FakeClock()
        gateway = _gateway(clock, window_s=30.0, failure_rate=0.5, min_samples=2, open_s=60.0)
        gateway.record(False)
        gateway.record(False)
        assert gateway.snapshot()["state"] == "open"
        with pytest.raises(GatewayBlockedError) as exc:
            await gateway.acquire()
        assert exc.value.code == "circuit_open"
        assert exc.value.retry_after_s == pytest.approx(60.0)
        assert gateway.snapshot()["stats"]["circuit_blocked"] == 1

    @pytest.mark.asyncio
    async def test_qps_limit_rejects_when_bucket_wait_too_long(self) -> None:
        clock = _FakeClock()
        gateway = _gateway(clock, qps=1.0, bucket_wait_s=0.0)
        await gateway.acquire()
        with pytest.raises(GatewayBlockedError) as exc:
            await gateway.acquire()
        assert exc.value.code == "rate_limited"
        assert gateway.snapshot()["stats"]["rate_limited"] == 1

    @pytest.mark.asyncio
    async def test_waits_within_bucket_cap(self) -> None:
        config = gateway_config_from_env("openai")
        config.update(qps=1000.0, bucket_wait_s=0.05)
        gateway = ProviderGateway(config=config)  # real clock: waits ~1ms and succeeds
        await gateway.acquire()
        await gateway.acquire()
        assert gateway.snapshot()["stats"]["rate_limited"] == 0
        assert gateway.snapshot()["stats"]["requests"] == 0  # record() only from adapter

    @pytest.mark.asyncio
    async def test_concurrency_quota_queues(self) -> None:
        clock = _FakeClock()
        active: list[int] = []
        inner = _FakeAdapter(active=active, slow=True)
        gateway = _gateway(clock, max_concurrency=1, qps=100.0)
        adapter = GatewayAdapter(inner, gateway)
        first = asyncio.create_task(_drain(adapter, _request()))
        await asyncio.sleep(0.01)
        assert active == [1]  # first stream in flight
        second = asyncio.create_task(_drain(adapter, _request()))
        await asyncio.sleep(0.01)
        assert len(active) == 1  # second waits for the concurrency quota
        await first
        await second
        assert inner.calls == 2


class TestGatewayAdapter:
    @pytest.mark.asyncio
    async def test_success_records_ok(self) -> None:
        clock = _FakeClock()
        events: list[tuple[str, str, dict[str, Any]]] = []
        gateway = _gateway(clock, events=events, qps=100.0)
        adapter = GatewayAdapter(_FakeAdapter(), gateway)
        items = await _drain(adapter, _request())
        assert [item.type for item in items] == [
            StreamItemType.text_delta,
            StreamItemType.done,
        ]
        assert gateway.snapshot()["stats"]["successes"] == 1
        assert gateway.snapshot()["stats"]["failures"] == 0

    @pytest.mark.asyncio
    async def test_provider_error_opens_breaker_and_emits_event(self) -> None:
        clock = _FakeClock()
        events: list[tuple[str, str, dict[str, Any]]] = []
        gateway = _gateway(
            clock, events=events, qps=100.0, window_s=30.0, failure_rate=0.5, min_samples=3, open_s=60.0
        )
        adapter = GatewayAdapter(_FakeAdapter(fail=True), gateway)
        for _ in range(3):
            items = await _drain(adapter, _request())
            assert items[-1].type == StreamItemType.error
        assert gateway.snapshot()["state"] == "open"
        assert any(event == "circuit_opened" for _p, event, _d in events)
        # 熔断后非降级请求直接结构化拒绝（解析类 → FAILED）
        with pytest.raises(GatewayBlockedError):
            await _drain(adapter, _request())

    @pytest.mark.asyncio
    async def test_degradation_returns_deterministic_template_when_open(self) -> None:
        clock = _FakeClock()
        events: list[tuple[str, str, dict[str, Any]]] = []
        gateway = _gateway(
            clock, events=events, qps=100.0, window_s=30.0, failure_rate=0.5, min_samples=2, open_s=60.0
        )
        adapter = GatewayAdapter(_FakeAdapter(fail=True), gateway)
        for _ in range(2):
            await _drain(adapter, _request())
        assert gateway.snapshot()["state"] == "open"
        items = await _drain(adapter, _request(procurement_stage="comparison", gateway_degradable=True))
        text = "".join(item.text or "" for item in items if item.text)
        assert "模型服务暂不可用" in text
        assert DEGRADED_MARKER in text
        assert "确定性比价" in text
        assert gateway.snapshot()["stats"]["degraded"] == 1
        assert any(event == "degraded" for _p, event, _d in events)

    @pytest.mark.asyncio
    async def test_degraded_summary_marks_model_unavailable(self) -> None:
        assert "模型服务暂不可用" in degraded_summary(_request(procurement_stage="decision"))
        assert "确定性摘要" in degraded_summary(_request())
        assert DEGRADED_MARKER in degraded_summary(_request())


class TestHarnessWiring:
    def test_harness_wraps_llm_providers_but_not_deterministic(self, tmp_path) -> None:
        harness = Harness(
            data_dir=tmp_path / "gateway-a",
            providers={"openai": _FakeAdapter(), "procurement_internal": _FakeAdapter()},
        )
        assert isinstance(harness.providers["openai"], GatewayAdapter)
        assert not isinstance(harness.providers["procurement_internal"], GatewayAdapter)
        assert "openai" in harness.gateways
        snapshots = harness.gateway_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["provider"] == "openai"
        assert "stats" in snapshots[0] and "limits" in snapshots[0]
        harness.close()

    def test_register_provider_wraps_new_llm_provider(self, tmp_path) -> None:
        harness = Harness(data_dir=tmp_path / "gateway-b", providers={})
        harness.register_provider("openai", _FakeAdapter())
        assert isinstance(harness.providers["openai"], GatewayAdapter)
        assert "openai" in harness.gateways
        harness.register_provider("procurement_internal", _FakeAdapter())
        assert not isinstance(harness.providers["procurement_internal"], GatewayAdapter)
        harness.close()

    def test_snapshot_contains_no_secrets(self, tmp_path) -> None:
        harness = Harness(data_dir=tmp_path / "gateway-c", providers={"openai": _FakeAdapter()})
        snapshot = harness.gateway_snapshots()[0]
        raw = str(snapshot)
        for secret in ("api_key", "token", "password", "secret"):
            assert secret not in raw.lower()
        harness.close()
