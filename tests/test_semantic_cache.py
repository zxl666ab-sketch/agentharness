"""P2-3: semantic cache — exact-match (SHA-256) keying, TTL expiry, version
invalidation, validated-only caching, no-op when Redis is unavailable."""

from __future__ import annotations

import io
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
