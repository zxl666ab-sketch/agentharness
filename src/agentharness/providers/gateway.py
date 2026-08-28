"""Provider gateway: concurrency quota, QPS token bucket, circuit breaker, degradation.

P2-1 治理补强：在 LLM 网关层（Python 侧，Java 不动调用协议）叠加
- 并发配额：``asyncio.Semaphore`` 按 provider 全局限并发，超限排队；
- QPS 限流：令牌桶（provider 维度），等待超过 ``bucket_wait_s`` 即拒绝并产生事件；
- 熔断：30s 滑动窗口失败率超过阈值（默认 50%，最少样本 5）→ 熔断 60s，
  熔断期过半开后允许探测请求（half-open），成功即恢复；
- 降级：熔断/限流期间，带 ``gateway_degradable`` 标记的请求返回确定性模板文本
  （注明「模型不可用，展示确定性摘要」），其余请求抛 ``GatewayBlockedError``
  （解析类任务 → 结构化错误 → Java AiTask FAILED 走既有恢复路径）。

熔断/限流/降级事件通过 ``emit`` 回调发布（agent_service 写入 Kafka runtime 事件，
Java ``/api/procurement/platform`` 暴露脱敏状态）。配置走环境变量，见
``gateway_config_from_env``。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

from agentharness.contracts import (
    ModelRequest,
    ModelStreamItem,
    StreamItemType,
    Usage,
)

GatewayEventCallback = Callable[[str, str, dict[str, Any]], None]
"""Args: provider name, event name, sanitized detail."""

DEGRADED_MARKER = "（模型服务暂不可用，展示确定性摘要）"


class GatewayBlockedError(ValueError):
    """The provider gateway refused the call before reaching the provider."""

    def __init__(
        self,
        code: str,
        *,
        provider: str = "",
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"provider gateway {code}" + (f" ({provider})" if provider else ""))
        self.code = code  # "rate_limited" | "circuit_open"
        self.provider = provider
        self.retry_after_s = retry_after_s


def gateway_config_from_env(provider: str) -> dict[str, Any]:
    """Provider gateway tuning; all values come from environment (.env)."""
    return {
        "provider": provider,
        "max_concurrency": max(1, int(os.environ.get("AGENTHARNESS_PROVIDER_MAX_CONCURRENCY", "4"))),
        "qps": max(0.1, float(os.environ.get("AGENTHARNESS_PROVIDER_QPS", "10"))),
        "window_s": max(1.0, float(os.environ.get("AGENTHARNESS_PROVIDER_CIRCUIT_WINDOW_S", "30"))),
        "failure_rate": min(
            1.0, max(0.05, float(os.environ.get("AGENTHARNESS_PROVIDER_CIRCUIT_FAILURE_RATE", "0.5")))
        ),
        "min_samples": max(1, int(os.environ.get("AGENTHARNESS_PROVIDER_CIRCUIT_MIN_SAMPLES", "5"))),
        "open_s": max(1.0, float(os.environ.get("AGENTHARNESS_PROVIDER_CIRCUIT_OPEN_S", "60"))),
        "bucket_wait_s": max(0.0, float(os.environ.get("AGENTHARNESS_PROVIDER_BUCKET_WAIT_S", "0.5"))),
    }


class TokenBucket:
    """Leaky-token bucket; ``take()`` returns the seconds a caller must wait."""

    def __init__(self, rate: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = max(0.0, rate)
        self._tokens = self._rate
        self._last = clock()
        self._clock = clock

    def take(self, cost: float = 1.0) -> float:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last = now
        if self._tokens >= cost:
            self._tokens -= cost
            return 0.0
        return (cost - self._tokens) / self._rate


class CircuitBreaker:
    """Sliding-window failure-rate circuit breaker with half-open probing."""

    def __init__(
        self,
        *,
        window_s: float,
        failure_rate: float,
        min_samples: int,
        open_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_s = window_s
        self._failure_rate = failure_rate
        self._min_samples = min_samples
        self._open_s = open_s
        self._clock = clock
        self._outcomes: deque[tuple[float, bool]] = deque()
        self._open_until = 0.0
        self._probe_inflight = False  # M7：half-open 单飞行探测标志

    def state(self) -> str:
        now = self._clock()
        if now < self._open_until:
            return "open"
        if self._open_until > 0:
            return "half_open"  # 熔断窗口已过，下一次结果即探测
        return "closed"

    def allow(self) -> bool:
        return self.state() != "open"

    def try_probe(self) -> bool:
        """half-open 阶段仅放行一个在途探测请求（单飞行）；其余返回 False 继续拒绝。

        修复（审核 M7）：避免并发请求同时冲击未恢复的 provider，破坏熔断恢复语义。
        """
        if self.state() != "half_open":
            return True
        if self._probe_inflight:
            return False
        self._probe_inflight = True
        return True

    def record(self, ok: bool) -> str:
        """Record an outcome; returns the resulting state (for event emission)."""
        now = self._clock()
        if self._open_until > 0:
            if now >= self._open_until:
                # 探测结果：成功恢复，失败重新熔断一个完整窗口
                self._probe_inflight = False
                if ok:
                    self._open_until = 0.0
                    self._outcomes.clear()
                    return "closed"
                self._open_until = now + self._open_s
                return "open"
            # 仍在熔断窗口内的迟到结果，只计入滑动窗口，不改变熔断状态
        self._prune(now)
        self._outcomes.append((now, ok))
        self._prune(now)
        if len(self._outcomes) < self._min_samples:
            return "closed"
        failures = sum(1 for _ts, outcome in self._outcomes if not outcome)
        if failures / len(self._outcomes) >= self._failure_rate:
            self._open_until = now + self._open_s
            self._probe_inflight = False
            return "open"
        return "closed"

    def _prune(self, now: float) -> None:
        while self._outcomes and now - self._outcomes[0][0] > self._window_s:
            self._outcomes.popleft()

    def abort_probe(self) -> None:
        """取消的请求若持有 half-open 探测位，立即释放（避免探测位死锁）。"""
        if self.state() == "half_open":
            self._probe_inflight = False

    def remaining_open_s(self) -> float:
        return max(0.0, self._open_until - self._clock())


class ProviderGateway:
    """Per-provider gateway state: concurrency + QPS + breaker + sanitized snapshot."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        emit: GatewayEventCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = str(config["provider"])
        self._config = config
        self._semaphore = asyncio.Semaphore(int(config["max_concurrency"]))
        self._bucket = TokenBucket(float(config["qps"]), clock=clock)
        self._breaker = CircuitBreaker(
            window_s=float(config["window_s"]),
            failure_rate=float(config["failure_rate"]),
            min_samples=int(config["min_samples"]),
            open_s=float(config["open_s"]),
            clock=clock,
        )
        self._bucket_wait_s = float(config["bucket_wait_s"])
        self._emit = emit
        self._stats: dict[str, int] = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "rate_limited": 0,
            "circuit_blocked": 0,
            "degraded": 0,
        }
        self._last_event: dict[str, Any] | None = None
        # M9：心跳线程（agent_service daemon 线程）跨线程读快照的互斥
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # gating
    # ------------------------------------------------------------------

    async def acquire(self) -> None:
        """Wait for concurrency quota + QPS token; raise GatewayBlockedError on refusal.

        P-H1: whenever this call refuses the request *before* the stream starts, a
        half-open probe slot it consumed is released on the way out. Without that,
        a local QPS refusal (or a cancellation while queueing on the concurrency
        semaphore) left ``_probe_inflight`` stuck, so every later request was
        rejected as ``circuit_open`` until the process restarted.
        """
        state = self._breaker.state()
        probe_held = False
        if state == "open":
            blocked = True
        elif state == "half_open":
            probe_held = self._breaker.try_probe()
            blocked = not probe_held
        else:
            blocked = False
        if blocked:
            with self._lock:
                self._stats["circuit_blocked"] += 1
            detail = {
                "retry_after_s": round(self._breaker.remaining_open_s(), 3) if state == "open" else 0.5,
                "provider": self.provider,
            }
            self._notify("circuit_open", detail)
            raise GatewayBlockedError(
                "circuit_open",
                provider=self.provider,
                retry_after_s=detail["retry_after_s"],
            )
        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
            wait = self._bucket.take()
            if wait > 0:
                if wait > self._bucket_wait_s:
                    with self._lock:
                        self._stats["rate_limited"] += 1
                    detail = {
                        "retry_after_s": round(wait, 3),
                        "qps": self._config["qps"],
                        "provider": self.provider,
                    }
                    self._notify("rate_limited", detail)
                    raise GatewayBlockedError(
                        "rate_limited",
                        provider=self.provider,
                        retry_after_s=detail["retry_after_s"],
                    )
                await asyncio.sleep(wait)
                if self._bucket.take() > 0:
                    with self._lock:
                        self._stats["rate_limited"] += 1
                    detail = {
                        "retry_after_s": 0.0,
                        "qps": self._config["qps"],
                        "provider": self.provider,
                    }
                    self._notify("rate_limited", detail)
                    raise GatewayBlockedError("rate_limited", provider=self.provider, retry_after_s=0.0)
        except BaseException:
            if acquired:
                # Only release the quota slot this call actually took.
                self._semaphore.release()
            if probe_held:
                # The probe never reached the provider: free the single-flight slot.
                self._breaker.abort_probe()
            raise

    def release(self) -> None:
        self._semaphore.release()

    def record(self, ok: bool) -> None:
        with self._lock:
            self._stats["requests"] += 1
            if ok:
                self._stats["successes"] += 1
            else:
                self._stats["failures"] += 1
        # M8：事件只在状态迁移时发射（open/half_open ↔ closed），稳态流量不刷屏
        before = self._breaker.state()
        state = self._breaker.record(ok)
        if state == "open" and before != "open":
            self._notify("circuit_opened", {"provider": self.provider, "open_s": self._config["open_s"]})
        elif state == "closed" and before in ("open", "half_open"):
            self._notify("circuit_closed", {"provider": self.provider})

    def record_degraded(self) -> None:
        with self._lock:
            self._stats["degraded"] += 1
        self._notify("degraded", {"provider": self.provider})

    def abort_probe(self) -> None:
        """取消的请求若持有 half-open 探测位，立即释放（避免探测位死锁）。"""
        self._breaker.abort_probe()

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Sanitized gateway state for the Java platform endpoint (no secrets)."""
        with self._lock:
            stats = dict(self._stats)
            state = self._breaker.state()
            remaining = round(self._breaker.remaining_open_s(), 3)
            last_event = self._last_event
        return {
            "provider": self.provider,
            "state": state,
            "remaining_open_s": remaining,
            "stats": stats,
            "limits": {
                "max_concurrency": self._config["max_concurrency"],
                "qps": self._config["qps"],
                "window_s": self._config["window_s"],
                "failure_rate": self._config["failure_rate"],
                "min_samples": self._config["min_samples"],
                "open_s": self._config["open_s"],
            },
            "last_event": last_event,
        }

    def _notify(self, event: str, detail: dict[str, Any]) -> None:
        self._last_event = {"event": event, **detail}
        if self._emit is not None:
            try:
                self._emit(self.provider, event, detail)
            except Exception:  # noqa: BLE001 - observer failure must not break gating
                pass


def degraded_summary(request: ModelRequest) -> str:
    """确定性模板摘要：熔断/限流期间替代 LLM 文本，并注明模型不可用。"""
    metadata = request.metadata or {}
    stage = str(metadata.get("procurement_stage") or "")
    if stage == "decision":
        body = "Java 控制面已确认正式采购决定，确定性比价证据与审批报告仍然有效。"
    elif stage == "comparison":
        body = "已请求 Java 执行确定性比价，请等待人工审批结果。"
    elif stage == "capture":
        body = "采购需求将由确定性规则结构化，请人工复核字段后继续。"
    else:
        body = "本轮处理结果以确定性业务状态为准，请查看采购任务详情。"
    return f"模型服务暂不可用，展示确定性摘要：{body}{DEGRADED_MARKER}"


class GatewayAdapter:
    """Wraps a provider adapter: stream() 经网关限流/熔断/降级后再到达内层适配器。"""

    def __init__(self, inner: Any, gateway: ProviderGateway) -> None:
        self.inner = inner
        self.gateway = gateway
        self.name = getattr(inner, "name", "gated")

    async def aclose(self) -> None:
        closer = getattr(self.inner, "aclose", None) or getattr(self.inner, "close", None)
        if callable(closer):
            result = closer()
            if hasattr(result, "__await__"):
                await result

    def close(self) -> None:
        closer = getattr(self.inner, "close", None)
        if callable(closer):
            closer()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        degradable = bool((request.metadata or {}).get("gateway_degradable"))
        if degradable and self.gateway._breaker.state() == "open":
            # 解释类任务降级：确定性模板文本 + 明确「模型不可用」标记
            self.gateway.record_degraded()
            text = degraded_summary(request)
            yield ModelStreamItem(type=StreamItemType.text_delta, text=text)
            yield ModelStreamItem(
                type=StreamItemType.usage,
                usage=Usage(input_tokens=0, output_tokens=0, total_tokens=0),
            )
            yield ModelStreamItem(type=StreamItemType.done)
            return

        await self.gateway.acquire()
        outcome: str | None = None
        try:
            async for item in self.inner.stream(request):
                if item.type == StreamItemType.error:
                    outcome = "error"
                elif item.type == StreamItemType.done:
                    outcome = "ok"
                yield item
        except asyncio.CancelledError:
            outcome = None
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            self.gateway.release()
            if outcome is not None:
                self.gateway.record(outcome == "ok")
            else:
                # 取消路径不产生结果：释放可能持有的 half-open 探测位
                self.gateway.abort_probe()


__all__ = [
    "CircuitBreaker",
    "DEGRADED_MARKER",
    "GatewayAdapter",
    "GatewayBlockedError",
    "GatewayEventCallback",
    "ProviderGateway",
    "TokenBucket",
    "degraded_summary",
    "gateway_config_from_env",
]
