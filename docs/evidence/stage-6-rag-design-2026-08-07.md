# 阶段六 1.1 · 设计定稿证据（2026-08-07）

## 交付物

- `docs/rag-design.md`：正式设计文档，覆盖检索键、粗排+rerank 规则（top-20→top-5）、证据格式与字段截断上限、分级注入（自动 top-3 / 展开 top-5）、展示位置、指标定义、索引与业务事实联动、明确不做清单。
- 锁定设计决策落地检查：
  - 混合召回 FTS + 结构化规格，**不用向量**（`src/agentharness/rag/embeddings.py` 只留可插拔接口，默认不启用）。
  - rerank：规格匹配度 × 时间衰减 × 供应商口碑 × 数据质量，低置信度来源降权。
  - 分级注入：自动 top-3 摘要（token 预算断言 ≤ 2,000 字符），前端可展开 top-5。
  - 反馈闭环：`knowledge_reference_viewed` / `knowledge_reference_adopted` 事件（只记 chunk_id 与动作）。
  - 确定性隔离红线：RAG 绝不进入 `canonical_analysis_input()` / `input_sha256` / 比价快照。
  - 治理面：不新增第 5 个白名单工具，检索是 `execute_analysis_pipeline` 的一个内部阶段。
  - 提示词约束：`src/agentharness/procurement/agent.py` system prompt 已含「不使用 Markdown 符号」规则（本阶段 1.1 提交内固话）。

## 基线（进入阶段六执行前复算，2026-08-07）

| 项 | 结果 |
|---|---|
| Python 测试 | 248 passed / 1 skipped |
| 覆盖率 | 81.12%（门槛 80%） |
| ruff | `ruff check .` 通过 |
| 确定性冻结评测 | 字段抽取 617/620（99.52%）、成本计算 31/31、硬约束漏检 0/17、错误入选 0 |
| 评测证据 | `output/procurement-evaluation-baseline-stage6/`（本次运行，命令见下） |

复算命令：

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q
ruff check .
.\.venv\Scripts\python.exe scripts\evaluate_procurement.py run --output output\procurement-evaluation-baseline-stage6
```

## 分层声明

RAG 检索质量（recall@k / precision@k / MRR / top-1 命中率）与确定性冻结评测（617/620、31/31、0 漏检）分层呈现：RAG 只进解释/参考上下文，不改变任何确定性数字；提效以 1.8 真人对照为准。
