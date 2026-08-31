"""P2-3: semantic cache — exact-match (SHA-256) keying, TTL expiry, version
invalidation, validated-only caching, no-op when Redis is unavailable."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from agentharness.procurement.agent_tools import ProcurementAgentTools
from agentharness.procurement.semantic_cache import SemanticCache
from agentharness.storage.sqlite import Storage


class _MemoryStore:
    """redis-py 兼容的最小内存实现（get/set(ex)/ping），支持时钟注入。"""

    def __init__(self, clock: Any) -> None:
        self._clock = clock
        self._data: dict[str, tuple[str, float | None]] = {}

    def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: str, ex: float | None = None) -> None:
        expires_at = self._clock() + ex if ex is not None else None
        self._data[key] = (value, expires_at)

    def ping(self) -> bool:
        return True


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _aresolve(value: bytes) -> bytes:
    return value


def _cache(clock: _FakeClock) -> SemanticCache:
    return SemanticCache(store=_MemoryStore(clock), ttl_s=60.0, clock=clock)


def test_default_ttl_is_int_for_redis_py8_compat(monkeypatch) -> None:
    """线上事故回归：redis-py>=8 拒绝 float ex（"ex must be timedelta or int"），
    曾经的 float 默认 TTL 让所有写入静默失败并计入 errors。"""
    monkeypatch.delenv("AGENTHARNESS_SEMANTIC_CACHE_TTL_S", raising=False)
    captured: dict = {}

    class StrictStore:
        def get(self, key: str):
            return None

        def set(self, key: str, value: str, ex=None) -> None:
            if not isinstance(ex, int):
                raise TypeError("ex must be datetime.timedelta or int")
            captured["ex"] = ex

        def ping(self) -> bool:
            return True

    cache = SemanticCache(store=StrictStore())
    cache.put_requirement("a" * 64, 2, {"ok": True})
    assert cache.stats()["errors"] == 0
    assert captured["ex"] == 86400


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quote"
    for row in [
        ["供应商", "解析测试供应商"],
        ["品名", "PE 白色快递袋 250x350mm 60um 单色印刷"],
        ["币种", "CNY"],
        ["单价", "500"],
        ["计价数量", "1000"],
        ["税率", "13%"],
        ["是否含税", "是"],
        ["是否包邮", "是"],
        ["MOQ", "1000"],
        ["交期", "7"],
        ["是否可开票", "是"],
    ]:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class TestSemanticCacheCore:
    def test_exact_match_round_trip(self) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        cache.put_quote_parse("sha-a", "v3", {"fields": {"unit_price": {"value": "500"}}})
        assert cache.get_quote_parse("sha-a", "v3") == {"fields": {"unit_price": {"value": "500"}}}
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 0
        assert cache.stats()["puts"] == 1

    def test_ttl_expiry_misses(self) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        cache.put_quote_parse("sha-a", "v3", {"fields": {}})
        assert cache.get_quote_parse("sha-a", "v3") is not None
        clock.advance(61.0)
        assert cache.get_quote_parse("sha-a", "v3") is None
        assert cache.stats()["misses"] == 1

    def test_version_invalidation_changes_key(self) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        cache.put_quote_parse("sha-a", "v3", {"fields": {}})
        # 解析器版本升级（v4）→ 不同 key → miss
        assert cache.get_quote_parse("sha-a", "v4") is None
        assert cache.get_quote_parse("sha-a", "v3") is not None

    def test_source_sha_change_misses(self) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        cache.put_quote_parse("sha-a", "v3", {"fields": {}})
        # 原件更新（新 SHA-256）→ miss
        assert cache.get_quote_parse("sha-b", "v3") is None

    def test_requirement_scope_separate_and_schema_versioned(self) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        cache.put_requirement("msg-1", 2, {"item_name": "快递袋"})
        assert cache.get_requirement("msg-1", 2) == {"item_name": "快递袋"}
        assert cache.get_requirement("msg-1", 3) is None  # schema 版本变化失效
        assert cache.get_quote_parse("msg-1", "2") is None  # scope 隔离

    def test_noop_when_redis_unavailable(self) -> None:
        cache = SemanticCache(store=None)
        assert cache.enabled() is False
        cache.put_quote_parse("sha-a", "v3", {"fields": {}})  # 不抛错
        assert cache.get_quote_parse("sha-a", "v3") is None
        assert cache.stats()["misses"] == 1
        assert cache.stats()["errors"] == 0

    def test_stats_are_exact_under_concurrent_access(self) -> None:
        """M3（旧报告）：`_stats` 计数器无锁 → 并发丢计数、快照读到半更新。"""
        clock = _FakeClock()
        cache = _cache(clock)
        threads = 8
        per_thread = 50

        def worker(index: int) -> None:
            for round_index in range(per_thread):
                sha = f"sha-{index}-{round_index}"
                cache.put_quote_parse(sha, "v3", {"fields": {}})
                cache.get_quote_parse(sha, "v3")
                cache.get_quote_parse(f"missing-{index}-{round_index}", "v3")

        pool = [threading.Thread(target=worker, args=(index,)) for index in range(threads)]
        previous_slice = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)  # 强制细粒度切换：无锁计数必定丢更新
        try:
            for item in pool:
                item.start()
            for item in pool:
                item.join()
        finally:
            sys.setswitchinterval(previous_slice)

        stats = cache.stats()
        assert stats["puts"] == threads * per_thread
        assert stats["hits"] == threads * per_thread
        assert stats["misses"] == threads * per_thread
        assert stats["errors"] == 0

    def test_broken_store_falls_back_to_miss(self) -> None:
        class _Broken:
            def get(self, _key: str) -> str:
                raise RuntimeError("redis down")

            def set(self, *_args: Any) -> None:
                raise RuntimeError("redis down")

        cache = SemanticCache(store=_Broken())
        assert cache.get_quote_parse("sha-a", "v3") is None
        cache.put_quote_parse("sha-a", "v3", {"fields": {}})  # 不抛错
        assert cache.stats()["errors"] == 2


class TestSemanticCacheWiring:
    @pytest.mark.asyncio
    async def test_quote_batch_parsing_is_parallel_but_bounded(
        self, tmp_path: Path
    ) -> None:
        """A large attachment batch must not create an unbounded worker burst."""

        storage = Storage(tmp_path / "runtime")
        session_id = storage.create_session("bounded-parse")
        run_id = "b" * 32
        storage.create_run(
            run_id=run_id,
            session_id=session_id,
            root_run_id=run_id,
            provider="fake",
            model="m",
        )
        attachments = [
            {"artifact_id": f"jb{index:032x}", "filename": f"quote-{index}.xlsx"}
            for index in range(5)
        ]
        storage.merge_run_metadata(
            run_id, {"procurement_pending_attachments": attachments}
        )
        tools = ProcurementAgentTools(
            storage,
            fetch_context=lambda _path: _aresolve(b""),
            fetch_artifact=lambda _path: _aresolve(b""),
            quote_parse_concurrency=2,
        )
        active = 0
        peak = 0

        async def parse(attachment: dict[str, Any]) -> dict[str, Any]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {
                "artifact_id": attachment["artifact_id"],
                "supplier_name": attachment["filename"],
                "status": "ready",
                "parser_version": "test",
                "processing_ms": "1",
                "cache_hit": False,
                "extracted": {"fields": {}},
            }

        tools._parse_attachment = parse  # type: ignore[method-assign]
        result = await tools.parse_uploaded_quotes(
            type("Context", (), {"run_id": run_id, "metadata": {}})(), {}
        )

        payload = json.loads(result.content)
        assert peak == 2
        assert [quote["artifact_id"] for quote in payload["quotes"]] == [
            attachment["artifact_id"] for attachment in attachments
        ]
        assert payload["parse_batch"]["attachment_count"] == 5
        assert payload["parse_batch"]["concurrency"] == 2
        assert payload["parse_batch"]["processing_ms"] >= 0
        storage.close()

    def test_quote_parse_cache_hit_skips_reparse(self, tmp_path: Path) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        storage = Storage(tmp_path / "runtime")
        content = _xlsx_bytes()
        import hashlib

        sha = hashlib.sha256(content).hexdigest()
        tools = ProcurementAgentTools(
            storage,
            fetch_context=lambda path: {"requirement_confirmed": False},
            fetch_artifact=lambda _path: _aresolve(content),
            semantic_cache=cache,
        )
        attachment = {"artifact_id": "jb" + "a" * 30, "filename": "quote.xlsx", "sha256": sha}

        import asyncio

        first = asyncio.run(tools._parse_attachment(attachment))
        assert first["cache_hit"] is False
        assert float(first["processing_ms"]) >= 0

        second = asyncio.run(tools._parse_attachment(attachment))
        assert second["cache_hit"] is True
        assert second["processing_ms"] == "0"
        assert second["extracted"]["fields"] == first["extracted"]["fields"]
        assert cache.stats()["hits"] == 1
        storage.close()

    def test_requirement_cache_hit_marks_source(self, tmp_path: Path) -> None:
        clock = _FakeClock()
        cache = _cache(clock)
        storage = Storage(tmp_path / "runtime")
        tools = ProcurementAgentTools(
            storage,
            fetch_context=lambda path: {},
            fetch_artifact=lambda _path: b"",
            semantic_cache=cache,
        )
        message = "采购 5000 个白色 PE 快递袋，宽度 250mm，交期 15 天"
        run_id = "r" * 32
        session_id = storage.create_session("semantic-demo")
        storage.create_run(
            run_id=run_id,
            session_id=session_id,
            root_run_id=run_id,
            provider="fake",
            model="m",
        )
        storage.merge_run_metadata(run_id, {"procurement_source_message": message})
        ctx = type("C", (), {"run_id": run_id, "metadata": {}})()

        import asyncio

        result = asyncio.run(
            tools.capture_requirement(ctx, {"requirement": None})  # type: ignore[arg-type]
        )
        assert '"source":"deterministic_offline_adapter"' in result.content
        result2 = asyncio.run(
            tools.capture_requirement(ctx, {"requirement": None})  # type: ignore[arg-type]
        )
        assert '"source":"semantic_cache"' in result2.content
        assert cache.stats()["hits"] == 1
        storage.close()

    def test_from_env_without_redis_url_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_REDIS_URL", raising=False)
        assert SemanticCache.from_env().enabled() is False
