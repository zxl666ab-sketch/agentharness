"""P2-3 语义缓存：精确层（输入 SHA-256）+ TTL + 版本失效。

缓存 LLM/确定性解析结果与需求结构化结果，场景：相同报价文件重复上传解析、
相同需求消息重复结构化。key 结构：``semantic:v1:{scope}:{sha256}:{version}``
- scope: ``quote_parse`` | ``requirement``
- sha256: 输入内容 SHA-256（原件 SHA-256 / 需求消息 SHA-256）→ 原件更新即 miss
- version: 解析器版本或 schema 版本 → 版本变化即天然失效（key 不同）

纪律：
- 缓存命中 = 确定性返回，不产生审计事件（命中只记 stats）；
- 解析结果缓存只对已通过校验的结果生效（工具成功路径才 put）；
- Redis 不可用/未配置时 no-op（与 Java Noop 回退一致），失败不阻断业务。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

CacheStore = Callable[[], Any] | None


class SemanticCache:
    """Exact-match semantic result cache with TTL and version invalidation."""

    KEY_PREFIX = "semantic:v1"

    def __init__(
        self,
        store: Any = None,
        *,
        ttl_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl_s = (
            float(os.environ.get("AGENTHARNESS_SEMANTIC_CACHE_TTL_S", "86400"))
            if ttl_s is None
            else ttl_s
        )
        self._clock = clock
        self._stats = {"hits": 0, "misses": 0, "puts": 0, "errors": 0}

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> SemanticCache:
        """Redis URL 来自 AGENT_REDIS_URL；不可用/未配置 → no-op 缓存。"""
        url = (os.environ.get("AGENT_REDIS_URL") or "").strip()
        if not url:
            return cls(store=None)
        try:
            import redis  # noqa: PLC0415 - 延迟导入，未配置时不依赖

            client = redis.Redis.from_url(
                url, socket_timeout=1.0, socket_connect_timeout=1.0
            )
            client.ping()
            return cls(store=client)
        except Exception:  # noqa: BLE001 - Redis 故障必须回退 no-op
            return cls(store=None)

    # ------------------------------------------------------------------
    # 报价解析结果
    # ------------------------------------------------------------------

    def get_quote_parse(self, artifact_sha256: str, parser_version: str) -> dict[str, Any] | None:
        value = self._get("quote_parse", artifact_sha256, parser_version)
        return value if isinstance(value, dict) else None

    def put_quote_parse(
        self, artifact_sha256: str, parser_version: str, extracted: dict[str, Any]
    ) -> None:
        """只放已通过校验的解析结果（工具成功路径调用）。"""
        self._put("quote_parse", artifact_sha256, parser_version, extracted)

    # ------------------------------------------------------------------
    # 需求结构化结果
    # ------------------------------------------------------------------

    def get_requirement(
        self, message_sha256: str, schema_version: int
    ) -> dict[str, Any] | None:
        value = self._get("requirement", message_sha256, str(schema_version))
        return value if isinstance(value, dict) else None

    def put_requirement(
        self, message_sha256: str, schema_version: int, requirement: dict[str, Any]
    ) -> None:
        """只放已通过后端校验的需求（_validate_model_requirement 之后）。"""
        self._put("requirement", message_sha256, str(schema_version), requirement)

    # ------------------------------------------------------------------
    # 可观测
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def enabled(self) -> bool:
        return self._store is not None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get(self, scope: str, sha256: str, version: str) -> Any:
        if self._store is None:
            self._stats["misses"] += 1
            return None
        try:
            raw = self._store.get(self._key(scope, sha256, version))
            if raw is None:
                self._stats["misses"] += 1
                return None
            self._stats["hits"] += 1
            return json.loads(raw)
        except Exception:  # noqa: BLE001 - 缓存故障按 miss 处理
            self._stats["errors"] += 1
            self._stats["misses"] += 1
            return None

    def _put(self, scope: str, sha256: str, version: str, value: Any) -> None:
        if self._store is None:
            return
        try:
            self._store.set(
                self._key(scope, sha256, version),
                json.dumps(value, ensure_ascii=False, default=str),
                ex=self._ttl_s,
            )
            self._stats["puts"] += 1
        except Exception:  # noqa: BLE001 - 缓存故障不阻断业务
            self._stats["errors"] += 1

    def _key(self, scope: str, sha256: str, version: str) -> str:
        return f"{self.KEY_PREFIX}:{scope}:{sha256}:{version}"


__all__ = ["SemanticCache"]
