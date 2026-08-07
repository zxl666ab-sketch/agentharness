# 交接任务：实现「历史行情 RAG 提效包」（阶段六）

> 用法：在 Codex 新会话中整段粘贴本文件内容作为初始提示，或以目标模式创建目标后，指示其「阅读并执行 D:\个人通用agentharness\docs\rag-handoff-prompt.md」。新会话没有本项目的历史对话，本文件必须自洽。

## 目标（一句话，可用于目标模式 objective）
在 D:\个人通用agentharness（采价台）实现「历史行情 RAG 提效包」：按物料规格相似度检索本地历史成交记录，自动注入比价页与 Agent 推荐说明，每条带来源证据；只做参考、不进决策输入、不改变确定性结果与审计链；按最终优化版交付（混合召回 + rerank + 分级注入 + 反馈闭环）。

## 必读文档（先读再动手）
- 执行计划：`docs/agent-upgrade-2026-08-05.md` 的「1. 阶段六 · 历史行情 RAG 提效包」（1.1–1.9）
- 架构：`docs/architecture.md`
- 项目说明：`README.md`
- 证据目录：`docs/evidence/`

## 已锁定设计决策（全部保留，不得删除或改为不做）
1. **混合召回**：FTS5 关键词 + 结构化规格匹配。**不用向量/embedding**；`src/agentharness/rag/embeddings.py` 只留可插拔接口，默认不启用。
2. **rerank（要做）**：混合召回 → 粗排 top-20 → rerank（规格容差、材质/颜色/印刷色数、缺失字段降权、时间衰减、数据质量）→ top-5。
3. **分级注入（要做）**：流水线自动注入 top-3 摘要（token 预算断言），前端可展开 top-5。
4. **反馈闭环（要做）**：记录 `knowledge_reference_viewed` / `knowledge_reference_adopted` 事件（只记 chunk_id 与动作，不记敏感内容），用于调权。
5. **索引与业务事实联动**：审批落库同事务写 rag_chunks；人工修正报价字段后同步更新 chunk；低置信度来源 chunk 打数据质量标记并降权。
6. **确定性隔离红线（要做）**：RAG 输出**绝不进入** `canonical_analysis_input()` / `input_sha256` / 比价快照；历史数据变化不得使既有快照失效；新增回归测试证明。
7. **治理面不变**：**不新增第 5 个白名单工具**，保持 4 工具；检索作为 `execute_analysis_pipeline` 的一个阶段自动注入。
8. **可溯源**：每条参考必须含 `request_reference / 成交日期 / 规格摘要 / 成交价 / 到货成本 / 是否成交 / 来源哈希`；无来源不注入。注入内容视为不可信数据，先经 Redactor 脱敏 + 截断。
9. **语料范围**：只取已正式成交（有决定）的历史记录；未成交/草稿不入索引。
10. **提示词约束**：保留 `src/agentharness/procurement/agent.py` system prompt 已有的「不使用 Markdown 符号」规则（若无则补上），不得回退。

## 执行顺序（每项完成 = 代码 + 测试 + 证据，再进入下一项）
### 1.1 设计定稿
产出 `docs/rag-design.md`：检索键（宽/长/厚 ± 公差、材质、颜色、印刷色数、品类、item_name）；评分与 rerank 规则（粗排 FTS+结构化 → 重排 top-20→top-5：规格匹配度 × 时间衰减 × 供应商口碑 × 数据质量）；证据格式与分级注入（自动 top-3 / 展开 top-5）及字段截断上限；展示位置；指标（recall@k、precision@k、MRR、top-1 命中率、参考查看率、参考采纳率、决策耗时）。

### 1.2 语料与索引存储（schema v15）
`src/agentharness/storage/rag.py` 新增 `RagRepo`（唯一 SQL 所有者）；`src/agentharness/storage/migrations.py` `SCHEMA_VERSION 14 → 15`：新增 `rag_chunks`（`chunk_sha256` 唯一，含 `request_id/quote_id/artifact_id/artifact_sha256/request_reference/supplier_name/item_name/category/specifications_json/unit_price/currency/landed_unit_cost/lead_days/moq/decision/decision_at/content/embedding BLOB NULL/created_at`，索引：supplier_name、item_name、category、decision_at）；`src/agentharness/storage/sqlite.py` 挂载 `self.rag`。验收：迁移 14→15 可升级/降级（沿用现有迁移测试模式），旧库升级不丢数据，RagRepo 增删查单测全绿。

### 1.3 索引填充：写时更新 + 全量重建脚本
审批落库（`approve_supplier_from_agent`）**同事务**写 rag_chunks（报价抽取字段 + 快照中的到货成本 + 决定 + 备注）；人工修正报价字段后**同步更新对应 chunk**；`scripts/rebuild_rag_index.py` 幂等全量重建（按 chunk_sha256 去重、0 模型调用、可离线、可回填存量数据）。验收：审批后 chunk 立即可查；人工修正后 chunk 与业务事实一致；数据质量标记在排序中生效；重建两次结果一致；审批失败不写索引。

### 1.4 检索器：混合召回 + rerank
`src/agentharness/rag/retriever.py`：候选集 = 同品类/物料关键词（FTS5；不支持则退化为 LIKE）+ 结构化规格匹配 → 粗排 top-20 → 按 1.1 规则 rerank → 排除当前 request_id → top-5（默认）带分数与证据；`rag/embeddings.py` 只留接口。验收：冻结场景 top-5 命中已知历史成交；相似召回、不相似不召回；无历史返回空不报错；rerank 后 top-5 命中率不劣于粗排 top-20；检索耗时与上下文预算有上限断言。

### 1.5 服务集成：流水线阶段 + 审计 + 确定性隔离
`src/agentharness/procurement/service.py` 的 `execute_analysis_pipeline` 在 `supplier_history` 之后新增 `retrieve_knowledge` 阶段；`src/agentharness/procurement/agent.py` 的 `pipeline_payload` 带 `knowledge_references`（脱敏、截断）；审计事件 `knowledge_retrieved`（chunk_id、score、sha256、top-k 摘要）；分级注入 + `knowledge_reference_viewed/adopted` 反馈事件落库；新增回归测试：**历史数据变化不影响 `analysis_input_sha256`**。验收：分析结果含 knowledge_references；审计与反馈事件可查；确定性冻结评测（617/620、31/31、0 漏检）不回归。

### 1.6 前端呈现
`web/src/procurement/ComparisonView.tsx`（或报告视图）新增「历史成交参考」区块：供应商、成交价、到货成本、日期、是否成交、来源 `request_reference`；默认展示 top-3 摘要、可展开 top-5；空态文案「暂无相似历史成交」；Agent 推荐说明中的引用可点开来源；每条参考带「查看详情 / 有帮助」轻量交互，回写反馈事件（不阻塞流程）；`web/src/procurement/types.ts` 同步类型；重建 `web_dist`。验收：web 测试/lint/build 全绿；浏览器走通「有历史 / 无历史 / 展开 top-5 / 反馈点击」各态。

### 1.7 离线评测：检索质量冻结集
`scripts/evaluate_knowledge.py`：用现有场景库构造冻结集（N 条历史成交，隐藏目标后检索），输出 recall@k、precision@k、MRR、top-1 命中率；结合 1.6 反馈事件评估参考查看率/采纳率，作为调权依据（反馈闭环）；0 模型调用；结果写入 `docs/evidence/rag-retrieval-<日期>.md`（含失败案例与调权建议）。验收：脚本可复现；指标与确定性指标（617/620）明确分层。

### 1.8 提效验证：真人对照
复用 `scripts/evaluate_procurement.py human-trial` 测量装置，做 **无 RAG 的 assisted** vs **带 RAG 的 assisted** 对照；指标：任务总耗时、比价到决策时长、翻历史/外部记录次数、参考采纳率；诚实分层，不把「assisted 本身提效」算成 RAG 增量功劳。验收：至少 1 组有效对照；样本不足如实标「待实测」，不得虚构比例。做法细节以 README 的 human-trial 说明为准；到这一步时也可回主会话让我逐步教。

### 1.9 真实模型验证 + README/面试叙事更新
受预算约束跑真实模型完整链路，验证：比价页出现历史参考、Agent 推荐说明带引用、回合数未增加、成本在预算内；README 健康度表 + 面试叙事更新；`docs/rag-design.md` 归档为正式设计。验收：run_id/回合/成本记录入 `docs/evidence/stage-6-real-model-<日期>.md`。**无 API key 或外部不可用 → 按外部阻塞规则如实记录 blocked，不得伪装成功。**

## 硬门槛（每项必须满足）
- Python：`.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q` 全绿，覆盖率 ≥80%；
- `ruff check .` 通过；
- web 改动：在 `web` 目录运行 `npm test`、`npm run lint`，并重建 `web_dist`；
- UI 改动：`uv run agentharness --workspace . --data-dir output/dev-run --no-open` 启动（默认 http://127.0.0.1:8741），用浏览器验证主路径与边界路径（可用 Playwright 或手动）；无法启动时记录阻塞，不得声称 UI 验收完成；
- 确定性冻结评测不回归：617/620、31/31、0 漏检。

## 提交与文档约定
- 每完成 1.1–1.9 中一项：先提交代码与测试结果，再在 `docs/agent-upgrade-2026-08-05.md` 对应标题打 `[x]` 并追加 `【已完成 · 日期 / 实现 commit / 证据 commit / 证据】`；证据写入 `docs/evidence/`；不得改写历史 commit。
- 全部完成后输出：改动文件清单、每个 commit、测试/覆盖率/ruff/web 结果、评测数字与真实模型验证的如实记录（含 blocked 原因）。

## 外部阻塞规则
没有 API key、余额不足、网关不可用或真实模型无法稳定复现时，可继续完成不依赖外部服务的代码/测试/文档，但对应真实验证不得虚假标记成功，也不得把该待办标为 `[x]`；必须在 evidence 中记录 blocked 原因与已尝试命令。

## 明确不做
外部知识库导入、通用问答、全文向量检索服务、OCR 后检索、embedding/向量检索、新增第 5 个白名单工具、修改确定性金额/资格/推荐逻辑、ERP/多租户/RBAC/登录、监控看板、K8s、成本控制台。

## 最终交付清单
1. `docs/rag-design.md` 定稿；
2. schema v15 迁移 + RagRepo + 写时索引 + 重建脚本 + 修正联动 + 数据质量标记；
3. retriever（混合召回 + rerank，无向量）；
4. 流水线阶段 + 审计 + 反馈事件 + 确定性隔离回归测试；
5. 前端历史成交参考栏（top-3/展开 top-5/反馈交互）；
6. `scripts/evaluate_knowledge.py` 冻结评测报告；
7. 真人对照报告（或如实「待实测」）；
8. 真实模型验证报告（或如实 blocked）；
9. 计划文档 1.1–1.9 全部按约定勾选并附证据。
