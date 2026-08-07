# 阶段六 1.3 · 索引填充（写时更新 + 全量重建）证据（2026-08-07）

## 实现

- `src/agentharness/rag/chunking.py`：`build_chunk` 从「已审批决定 + 快照结果 + 报价抽取字段 + 备注」构建 chunk；`chunk_sha256` 对规范业务事实（request/quote/decision/价格/交期/MOQ/来源哈希）求哈希，业务变化即换哈希；`quality_flags_for_quote` 输出 `low_confidence`（置信度 <0.8）/ `conflict_evidence` / `corrected`；`specification_summary` 生成规格摘要。
- `src/agentharness/rag/embeddings.py`：可插拔接口 + `NoopEmbedder` 默认禁用（锁定设计，不用向量）。
- `src/agentharness/procurement/service.py`：
  - `approve_supplier_from_agent` **同事务**写 rag_chunks（`commit_decision(..., rag_chunks=[...])`）；审批失败整体回滚，不写索引。
  - `correct_field` 通过 `_sync_rag_chunk_for_quote` 同步更新对应 chunk（先删后建，仅在已 approved 且为该报价时重建）。
- `src/agentharness/storage/procurement.py`：`commit_decision` 增加 `rag_chunks` 参数，事务内经 `RagRepo.upsert_chunk` 写入；`delete_request_tree` 同时删除 rag_chunks。
- `scripts/rebuild_rag_index.py`：幂等全量重建（按 chunk_sha256 去重、0 模型调用、可离线、可回填存量已审批数据；每次重建先清该 request 旧 chunk 再重建）。

## 验收

| 项 | 结果 |
|---|---|
| 审批后 chunk 立即可查 | 通过（approve 后 count=1，字段完整：供应商/成交价/到货成本/交期/MOQ/决定/来源哈希） |
| 人工修正后 chunk 与业务事实一致 | 通过（修正 supplier_name 后审批，chunk 为修正值且带 corrected 标记；`_sync_rag_chunk_for_quote` 白盒同步测试通过） |
| 数据质量标记写入 | 通过（low_confidence/corrected 标记在 chunk.quality_flags；排序生效在 1.4 验证） |
| 重建两次结果一致 | 通过（两次 rebuild indexed=1，chunk_sha256 集合一致；清空后回填成功） |
| 审批失败不写索引 | 通过（触发器强制 supplier_approved 审计失败 → 整体回滚，rag count=0、无决定） |
| no_award 不写索引 | 通过（record_no_award 被拒/不产生 chunk） |
| 全量 Python | 258 passed / 1 skipped，覆盖率 81.24%（门槛 80%） |
| ruff | `ruff check .` 通过 |
| 确定性冻结评测 | 617/620、31/31、0 漏检、0 错误入选（`output/procurement-evaluation-stage6-13/`） |
