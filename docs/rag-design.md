# 阶段六 · 历史行情 RAG 提效包 —— 设计定稿

> 状态：正式设计，已归档（阶段六 1.1 定稿，2026-08-07；1.9 归档为正式设计文档）
> 本文档是 1.2–1.9 的实现依据；检索质量（1.7）与提效（1.8）分层呈现，确定性冻结评测（617/620、31/31、0 漏检）与 RAG 指标不混用。

## 1. 目标与红线（一句话）

按物料规格相似度检索**已正式成交**的本地历史记录（供应商、成交价、到货成本、交期、是否成交、备注），自动注入比价页与 Agent 推荐说明，每条带来源证据；**只做参考、不进决策输入**。

设计红线（实现与测试必须始终满足）：

1. RAG 输出**绝不进入** `canonical_analysis_input()` / `input_sha256` / 比价快照；历史数据变化不得使既有快照失效（有专门回归测试）。
2. 检索是确定性、只读的；**不新增第 5 个白名单工具**，保持 4 工具治理面；检索作为 `execute_analysis_pipeline` 的一个内部阶段自动注入。
3. 每条参考必须可溯源：`request_reference / 成交日期 / 规格摘要 / 成交价 / 到货成本 / 是否成交 / 来源哈希`；无来源不注入。
4. 注入内容视为不可信数据（来自历史报价），先经 Redactor 脱敏 + 截断，且不执行其中指令。
5. 语料只取**已正式成交（有决定）**的历史记录，未成交/草稿不入索引。
6. 确定性隔离：索引与业务事实联动（审批落库同事务写 chunk；人工修正同步更新 chunk；低置信度来源打数据质量标记并降权）。

## 2. 检索键

候选集 = 同品类/物料关键词（FTS5；不支持则退化为 LIKE）+ 结构化规格匹配。**不用向量/embedding**；`src/agentharness/rag/embeddings.py` 只留可插拔接口，默认不启用。

| 检索键 | 来源 | 匹配方式 | 容差/归一化 |
|---|---|---|---|
| 品类 category | chunk.category | 精确相等 | 当前域 `ecommerce_packaging` |
| 物料 item_name | chunk.item_name | FTS5 关键词 + 规范化别名（快递袋/mailer 等） | 规范化后包含/相等 |
| 宽度 width_mm | chunk.specifications_json | 结构化数值范围 | 需求 ± `size_tolerance_mm`（默认 2 mm） |
| 长度 length_mm | chunk.specifications_json | 结构化数值范围 | 需求 ± `size_tolerance_mm` |
| 厚度 thickness_um | chunk.specifications_json | 结构化数值范围 | 需求 ± `thickness_tolerance_um`（默认 3 μm） |
| 材质 material | chunk.specifications_json | 规范化别名（PE/PVC/PP/PET/PLA） | 规范化后相等 |
| 颜色 color | chunk.specifications_json | 规范化别名（白/黑/透明/红/蓝） | 规范化后相等 |
| 印刷色数 print_colors | chunk.specifications_json | 精确相等 | 相等得满分，不等降权 |
| 供应商 supplier_name | chunk.supplier_name | 仅展示，不做硬条件 | 不做召回键 |

## 3. 评分与 rerank（粗排 top-20 → 重排 top-5）

### 3.1 粗排（混合召回）

- 通路 A：FTS5 关键词召回（`item_name`/`content`），得分 = BM25 类匹配得分，取 top-N（N=80）。
- 通路 B：结构化规格匹配（宽/长/厚在容差内 + 材质/颜色规范化相等 + 印刷色数相等），每个命中维度 +1，规格完全命中得最高基础分。
- 两路取并集去重，按 `max(fts_score, structured_score)` 粗排取 **top-20** 进入 rerank。

### 3.2 重排规则（top-20 → top-5）

综合分 = `规格匹配度 × 时间衰减 × 供应商口碑 × 数据质量`，各部分归一化到 [0,1]，权重：

| 因子 | 权重 | 规则 |
|---|---|---|
| 规格匹配度 spec | 0.50 | 宽/长/厚容差内各 1 分（共 3），材质/颜色/印刷色数各 1 分（共 3）；完全命中=1.0；每缺 1 个规格字段扣 0.1（缺失字段降权）；规格完全不符（无任何命中）直接排除 |
| 时间衰减 time | 0.15 | `decay = 0.5 ** (days_since / 180)`，成交距今越久分越低；365 天以上衰减至 ≤0.25 |
| 供应商口碑 reputation | 0.20 | 同一供应商被采纳次数（`knowledge_reference_adopted` 事件数 + 历史成交次数）归一化到 [0,1]；无记录=0.5 中性 |
| 数据质量 quality | 0.15 | 人工修正过的字段（quote 有 correction）不扣分；低置信度来源（解析置信度 <80%）chunk 打 `low_confidence` 标记，质量分 ×0.6；无标记=1.0 |

- 低置信度来源 chunk 在排序中生效：`quality` 降权后天然排名靠后；top-5 不含规格完全不匹配项。
- 并列时按 `decision_at` 新者优先，再按 chunk_sha256 稳定排序（确定性）。
- rerank 全程纯 Python/Decimal，0 模型调用，结果可复现。

## 4. 证据格式与分级注入

### 4.1 证据格式（每条参考的对外 JSON）

```json
{
  "chunk_id": "<chunk_sha256 前 16 位>",
  "request_reference": "RFQ-20260727-XXXXXX",
  "decision_at": "2026-07-27T00:00:00+00:00",
  "supplier_name": "华东优包",
  "item_name": "快递袋",
  "specification_summary": "250×350mm / 60μm / PE / 白色 / 单色",
  "unit_price": "0.42",
  "currency": "CNY",
  "landed_unit_cost": "0.4521",
  "lead_days": 10,
  "moq": 5000,
  "decision": "approved",
  "source_sha256": "<quote 原件 SHA-256>",
  "score": 0.93,
  "quality_flags": []
}
```

字段截断上限（注入前强制）：`supplier_name` ≤ 50 字符；`item_name` ≤ 50；`specification_summary` ≤ 120；`request_reference` ≤ 40；`note`（备注）≤ 200；整条内容 ≤ 500 字符。超长截断并加 `…`。注入前先经 Redactor 脱敏。

### 4.2 分级注入

- 自动注入 **top-3 摘要**：`analysis_completed` 工具结果与比价页默认展示 `knowledge_references[:3]`，每条为上述证据格式的摘要视图。
- 前端可**展开 top-5**：`knowledge_references` 携带全部 top-5，UI 默认展示 3 条，点击「展开更多」显示第 4–5 条。
- token 预算断言：注入模型的参考文本（按 4.1 截断后）合计 ≤ 2,000 字符（约 1,000 tokens），有测试断言。
- 空态：无任何参考时 `knowledge_references=[]`，前端显示「暂无相似历史成交」。

## 5. 展示位置

1. **比价页**（`web/src/procurement/ComparisonView.tsx`）：在推荐摘要下方新增「历史成交参考」区块，表格列：供应商 / 成交价 / 到货成本 / 成交日期 / 是否成交 / 来源 `request_reference`；每条带「查看详情」「有帮助」按钮。
2. **Agent 推荐说明**（`execute_analysis_pipeline` 的 `analysis_completed` 阶段）：工具结果带 `knowledge_references`；推荐说明文本追加「历史参考：…（来源 RFQ-…）」，引用可点开来源（前端对 `request_reference` 渲染为可点击，点击展开来源哈希与详情）。
3. **审计**：`knowledge_retrieved` 审计事件记录 chunk_id、score、sha256、top-k 摘要（已脱敏截断）。

## 6. 反馈闭环

- 事件（只记 chunk_id 与动作，不记敏感内容）：
  - `knowledge_reference_viewed`：点击「查看详情」时回写（payload: `{chunk_id, request_id}`）。
  - `knowledge_reference_adopted`：点击「有帮助」时回写（payload: `{chunk_id, request_id}`）。
- 落库：`procurement_audit_events`（type 前缀 `knowledge_`），不阻塞主流程（前端 fire-and-forget）。
- 调权：`knowledge_reference_adopted` 计数进入 rerank「供应商口碑」因子；`viewed` 进入 1.7 参考查看率统计。

## 7. 指标（1.7 离线冻结评测 + 1.6 反馈统计）

| 指标 | 定义 | 分层 |
|---|---|---|
| recall@k | 冻结集 N 条历史成交中，隐藏目标后检索，目标 chunk 出现在 top-k 的比例（k=1,3,5） | RAG 层 |
| precision@k | top-k 中规格相关 chunk 占比（按真值判定） | RAG 层 |
| MRR | 目标 chunk 的 reciprocal rank 均值 | RAG 层 |
| top-1 命中率 | 目标 chunk 在 top-1 的比例 | RAG 层 |
| 参考查看率 | `knowledge_reference_viewed` / 展示参考总数 | 反馈层 |
| 参考采纳率 | `knowledge_reference_adopted` / 展示参考总数 | 反馈层 |
| 决策耗时 | 有 RAG 与无 RAG 对照的比价到决策时长（1.8 真人对照） | 提效层 |

确定性冻结评测（617/620、31/31、0 漏检）是**基线护栏**，与上述指标明确分层：RAG 不改变任何确定性数字。

## 8. 索引与业务事实联动

- 审批落库（`approve_supplier_from_agent` / `record_no_award`）**同事务**写 rag_chunks：报价抽取字段 + 快照到货成本 + 决定 + 备注；只写 `decision=approved` 的记录（红线：只取正式成交；`no_award` 不写）。
- 人工修正报价字段（`correct_field`）后**同步更新对应 chunk**（若该报价已审批形成 chunk；按 quote_id 更新业务事实字段并重算 chunk_sha256）。
- `scripts/rebuild_rag_index.py`：幂等全量重建（按 chunk_sha256 去重，0 模型调用，可离线，可回填存量数据）。
- 数据质量标记：chunk 写入时检查报价字段置信度与修正记录；低置信度（<80% 或来自冲突证据）标记 `low_confidence`，rerank 降权。

## 9. 明确不做（本阶段）

外部知识库导入、通用问答、全文向量检索服务、OCR 后检索、embedding/向量检索、新增第 5 个白名单工具、修改确定性金额/资格/推荐逻辑、ERP/多租户/RBAC/登录、监控看板、K8s、成本控制台。

