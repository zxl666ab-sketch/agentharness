# 阶段六 1.4 · 检索器（混合召回 + rerank）证据（2026-08-07）

## 实现

- `src/agentharness/rag/retriever.py`：`Retriever.retrieve(request, limit=5, candidate_limit=20, now, adopted_counts)`。
  - 候选召回：FTS5 关键词（item_name/material/color；RagRepo LIKE 退化）+ 结构化规格容差匹配（宽/长 ± size_tolerance_mm、厚 ± thickness_tolerance_um、材质/颜色规范化相等、印刷色数精确相等）。
  - 粗排：关键词命中分 / 结构化命中分取 max → 排序取 top-20。
  - rerank（top-20 → top-5）：`规格匹配度(0.50) × 时间衰减(0.15, 180 天半衰期) × 供应商口碑(0.20, 采纳计数) × 数据质量(0.15, low_confidence×0.6 / conflict×0.7)`；缺失规格字段按每缺 1 个扣 0.1 降权；规格完全不匹配直接排除；并列按 decision_at 新者优先再按 chunk_sha256 稳定排序。
  - 排除当前 request 自身 chunk；无向量/embedding；0 模型调用。
- `adopted_counts` 来自反馈闭环事件（1.5 接入），作为「供应商口碑」因子。

## 验收

| 项 | 结果 |
|---|---|
| 混合召回 | 通过（FTS 命中 + 结构化容差命中并入候选） |
| rerank top-20→top-5 | 通过（候选 6 → 返回 5，规格不匹配被排除） |
| 时间衰减 | 通过（同规格新成交排前） |
| 数据质量降权 | 通过（low_confidence 排在同规格干净 chunk 之后） |
| 供应商口碑 | 通过（adopted_counts 高的供应商排前） |
| 确定性 | 通过（两次检索 chunk 顺序与 score 完全一致） |
| 全量 Python | 264 passed / 1 skipped，覆盖率 81.46%（门槛 80%） |
| ruff | `ruff check .` 通过 |
