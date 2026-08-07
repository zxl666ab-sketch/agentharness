# 阶段六 1.5 · 服务集成（流水线阶段 + 审计 + 反馈 + 确定性隔离）证据（2026-08-07）

## 实现

- `src/agentharness/procurement/service.py`：
  - `execute_analysis_pipeline` 新增 `knowledge` 阶段：`Retriever` top-5 → `sanitize_reference`（Redactor 脱敏 + 字段截断上限）→ 自动注入 top-3（`injected_text` token 预算断言 ≤2,000 字符）→ `knowledge_retrieved` 审计（chunk_id/score/sha256/top-k 摘要）。
  - `get_request` 从最新 `knowledge_retrieved` 审计事件返回 `knowledge_references`（不进快照）。
  - `record_knowledge_feedback`：`knowledge_reference_viewed/adopted` 事件（只记 chunk_id 与 action）；`_knowledge_adopted_counts` 把采纳计数回喂 rerank「供应商口碑」因子（反馈闭环）。
- `src/agentharness/procurement/agent.py`：`pipeline_payload` 携带 `knowledge_references`；`analysis_completed` 回复追加历史参考提示（不影响确定性结论）。
- `src/agentharness/api/procurement.py`：`POST /requests/{id}/knowledge/feedback`（chunk_id + action，非阻塞）。
- `src/agentharness/storage/procurement.py`：`list_knowledge_feedback_events`。
- `src/agentharness/rag/reference.py`：脱敏/截断/证据投影/注入文本与预算常量。

## 验收

| 项 | 结果 |
|---|---|
| 分析结果含 knowledge_references | 通过（有历史→1 条，无历史→[]；字段含 request_reference/成交日期/规格摘要/成交价/到货成本/是否成交/来源哈希） |
| 审计事件可查 | 通过（knowledge_retrieved 含 count/injected_count/references） |
| 分级注入 token 预算断言 | 通过（top-3 文本 ≤2000 字符；6 条历史 → 返回 top-5、注入 top-3） |
| 反馈事件可查 | 通过（viewed/adopted 落库，payload 仅 chunk_id+action；非法 action/ID 拒绝） |
| 确定性隔离回归 | 通过（历史 chunk 增删后 `analysis_input_sha256` 与快照哈希不变；带知识注入的完整流水线哈希不变、verified=True） |
| 全量 Python | 269 passed / 1 skipped，覆盖率 81.15%（门槛 80%） |
| ruff | `ruff check .` 通过 |
| 确定性冻结评测 | 617/620、31/31、0 漏检、0 错误入选（`output/procurement-evaluation-stage6-15/`） |
