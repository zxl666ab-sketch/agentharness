# P2-3 语义缓存（精确层 + TTL + 版本失效）— 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §4 P2-3。commit 见 `git log -1`。
> 实现于 Python 侧直连 Redis（`AGENT_REDIS_URL`，独立 DB index 2，避免与 Java DB 0 干扰），
> 符合计划「或 Python 侧直连 Redis」分支。

## 实现（`src/agentharness/procurement/semantic_cache.py`）

| 要点 | 说明 |
|---|---|
| key 结构 | `semantic:v1:{scope}:{sha256}:{version}` — scope=quote_parse/requirement |
| 精确层 | sha256 = 原件 SHA-256（报价文件）/ 需求消息 SHA-256 → **原件更新即 miss**（不同 key） |
| 版本失效 | version = `PARSER_VERSION`（packaging-quote-v3）/ schema 版本 → **解析器/schema 升级即天然失效** |
| TTL | 默认 86400s，`AGENTHARNESS_SEMANTIC_CACHE_TTL_S` 可调 |
| 纪律 | 命中=确定性返回、不产生审计事件（只计 stats）；**只缓存工具成功路径（已通过校验）的解析结果**；Redis 不可用/未配置 → no-op 回退，故障不阻断业务 |

接线：
- `ProcurementAgentTools._parse_attachment`：解析前查缓存（命中→`cache_hit=true`、`processing_ms=0`，跳过重新解析）；未命中→解析成功后 put（已通过字段校验）。
- `ProcurementAgentTools.capture_requirement`：确定性需求提取按消息 SHA-256 + schema v2 缓存，命中时 `source="semantic_cache"`。
- `InternalAgentCommands` 构造 `SemanticCache.from_env()`；`compose.yaml` agent 服务新增 `AGENT_REDIS_URL: redis://redis:6379/2`。

## 验收

- ✅ 同一文件二次上传不重新解析（Web 侧可见 `cache_hit` + `processing_ms=0`；单测断言两次解析结果一致且 stats.hits=1）
- ✅ 单测覆盖 TTL 失效（60s TTL，61s 后 miss）与版本失效（v3→v4 miss；schema v2→v3 miss；原件 SHA 变更 miss）
- ✅ 命中确定性返回、不产生审计事件（命中仅计 stats，无事件写入路径）
- ✅ Redis 故障/未配置 no-op 不阻断业务（单测：broken store → miss，不抛错）

## 数字变化

- Python 测试：245 → **255**（+10：`test_semantic_cache.py`）
- 新增文件：`semantic_cache.py`（57 行核心）+ 测试 + compose env 一行 + `.env.example` 注释

## 面试话术更新点

- 「同一份报价文件重复上传直接命中语义缓存——key 是原件 SHA-256 + 解析器版本，TTL 之外还带版本失效：解析器升级、原件更新都会自动失效；缓存只落已通过校验的结果，命中是确定性返回、不产生审计事件」
