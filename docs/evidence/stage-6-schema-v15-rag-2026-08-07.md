# 阶段六 1.2 · 语料与索引存储（schema v15）证据（2026-08-07）

## 实现

- `src/agentharness/storage/migrations.py`：`SCHEMA_VERSION 14 → 15`，新增 `rag_chunks` 表（`chunk_sha256` 主键唯一，含 request_id/quote_id/artifact_id/artifact_sha256/request_reference/supplier_name/item_name/category/specifications_json/unit_price/currency/landed_unit_cost/lead_days/moq/decision/decision_at/content/quality_flags_json/embedding BLOB NULL/created_at/updated_at）；索引 supplier_name、item_name、category、decision_at、quote_id；FTS5 外部内容表 `rag_chunks_fts` + 插入/删除/更新同步触发器。
- `src/agentharness/storage/rag.py`：`RagRepo`（唯一 SQL 所有者）：`upsert_chunk`（按 chunk_sha256 幂等去重）、`get_chunk`、`delete_chunk`、`delete_chunks_for_quote/request`、`count_chunks`、`list_chunks`、`list_chunks_by_quote`、`fts_search`（FTS5 前缀关键词，异常/不可用时退化为 LIKE）。
- `src/agentharness/storage/sqlite.py`：挂载 `self.rag`。

## 验收

| 项 | 结果 |
|---|---|
| 迁移 14→15 升级 | 通过（`test_v14_upgrade_gains_rag_index_and_preserves_legacy_data`） |
| 旧库升级不丢数据 | 通过（v14 预置 legacy request/decision，升级后仍可读，规格 JSON 正确） |
| RagRepo 增删查单测 | 通过（upsert/get/delete/count/list、幂等重建、按 quote/request 删除） |
| FTS5 + LIKE 退化 | 通过（关键词命中 item_name/supplier_name/content；无命中返回空） |
| 全量 Python | 252 passed / 1 skipped，覆盖率 80.75%（门槛 80%） |
| ruff | `ruff check .` 通过 |

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q
ruff check .
```

## 说明

- 降级：沿用现有迁移测试模式（仓库无降级机制，只做旧版本→当前版本升级验证）。
- `embedding BLOB NULL` 按锁定设计保留为可插拔接口占位，默认不启用、不写入。
