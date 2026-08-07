# 采价台 Agent 实习项目升级计划（2026-08 修订版）

> 项目定位（一句话）：**受治理、人在环路的采购比价 Agent 系统**——4 个白名单工具的薄 Agent 编排 + 确定性报价流水线 + 可靠性栈（checkpoint/resume、审批绑精确选择、不可变快照、审计 SHA-256）。
> 本文档按「阶段」组织实习项目待办：每个阶段一个叙事主题，每条含目标、做法、加分点、工作量、验收标准。原 2026-08-05/06 审查清单已合并进本版；已闭环阶段（一至五）的证据保留在 docs/evidence/。
> 状态标记：`[ ]` 未开始；`[x]` 已完成（标题后追加 `【已完成 · 日期 / 实现 commit / 证据 commit / 证据】`）。每完成一条待办，先提交代码与测试结果，再更新本文件的勾选、实现 commit、证据路径并创建新的文档证据 commit；不得修改或覆盖已有 commit。真实模型阶段证据可在阶段收尾验证后作为同阶段的后续文档证据 commit 提交。

## 执行约定（全局）

> 适用于所有阶段的通用规则，优先级低于各待办自身验收，但任何阶段都不得违反。

### 阶段验证约定（关键）

**每个阶段全部条目完成后，用真实模型路径（deepseek-v4-flash，`.env` key，受预算约束）复测该阶段改动确实生效**，而不是只靠 fake 测试通过。确定性故障/边界行为必须另外用 fake provider 或 provider 故障注入验证；真实模型不要求稳定触发不可控故障，只验证真实路径的实际行为：

- 阶段六：真实模型下完整任务跑通，比价页与推荐说明出现可溯源历史参考；回合数与预算不劣化；检索质量以 1.7 离线冻结评测为准，提效以 1.8 真人对照为准。

每次阶段验证：先记录基线/预期 → 在 `max_cost_usd` 和 `max_tokens` 上限内跑真实模型路径 → 记录 run_id/状态/回合/工具调用/Token 成本 → 与预期对比 → 写入 `docs/evidence/stage-<N>-real-model-<日期>.md`。若未达到目标，须区分模型、网关、场景和编排原因并如实记录；不得用 fake 结果掩盖真实模型失败。

### 真实模型预算前置条件

真实模型验证前，先通过 `/api/procurement/config` 或环境变量（`AGENTHARNESS_PROCUREMENT_INPUT/OUTPUT_PER_MILLION_USD`）配置 input/output 价格；否则 runtime 不执行成本上限检查，`max_cost_usd` 形同虚设。

### 外部阻塞规则

没有 API key、余额/预算不足、网关不可用或真实模型无法稳定复现指定边界时，可继续完成不依赖外部服务的代码、测试和文档工作，但对应真实验证不得虚假标记为成功，也不得将该待办或阶段标记为 `[x]`，除非文档验收已明确允许仅离线完成。必须在阶段 evidence 中记录 `blocked`/`failed`、阻塞原因、已尝试命令、run_id（如有）和成本；不得无限重试或用 fake 结果代替真实模型证据。恢复外部条件后，应优先补做该验证，再继续收尾审计。

## 1. 阶段六 · 历史行情 RAG 提效包（叙事：有记忆、能提效）

> 目标：给采购员「这个价合不合理」的决策依据——按**物料规格相似度**检索本地历史成交记录（供应商、成交价、到货成本、交期、是否成交、备注），自动注入比价页与 Agent 推荐说明，每条带来源证据。只做参考，不进决策输入，不改变确定性结果与审计链。
> 设计红线（本阶段全局约束，任何待办不得违反）：
> 1. RAG 输出只进「解释/参考上下文」，**绝不进入** `canonical_analysis_input()` / `input_sha256` / 比价快照；历史数据变化不得使既有快照失效。
> 2. 检索是确定性、只读的；**不新增第 5 个白名单工具**，保持 4 工具治理面不变（检索作为 `execute_analysis_pipeline` 的一个阶段自动注入）。
> 3. 每条参考必须可溯源：`request_reference / 成交日期 / 规格摘要 / 成交价 / 到货成本 / 是否成交 / 来源哈希`；无来源不注入。
> 4. 注入内容视为不可信数据（来自历史报价），先经 Redactor 脱敏 + 截断，且不执行其中指令。
> 5. 检索质量用离线冻结评测衡量（0 模型调用），提效用真人对照衡量；两者分层呈现，不混用。
> 6. 语料只取**已正式成交（有决定）**的历史记录，未成交/草稿不入索引，避免噪声与半成品污染。
> 最终交付形态（本阶段目标，不交付最小可用版）：**混合召回（FTS + 结构化规格）→ rerank（top-20 → top-5）→ 分级注入（自动 top-3 摘要 / 展开 top-5）→ 反馈闭环（查看/采纳回写调权）**；索引与业务事实联动（人工修正即更新 chunk），低置信度来源 chunk 降权标记。

### 1.1 [x] 设计定稿：检索键、证据格式与展示形态【已完成 · 2026-08-07 / b4d11d4 / f3db6a3 / docs/rag-design.md + docs/evidence/stage-6-rag-design-2026-08-07.md】
- 目标/做法：产出 `docs/rag-design.md`，确定：①检索键 = 物料规格相似度（宽/长/厚 ± 公差、材质、颜色、印刷色数、品类）+ 关键词（`item_name`）；②相似度评分与 rerank 规则（粗排 FTS + 结构化 → 重排 top-20→top-5：规格匹配度 × 时间衰减 × 供应商口碑 × 数据质量）；③证据格式与分级注入（自动 top-3 摘要 / 展开 top-5）及字段截断上限；④展示位置（比价页「历史成交参考」栏 + Agent 推荐说明尾部引用）；⑤衡量指标定义（recall@k、precision@k、MRR、top-1 命中率、参考查看率、参考采纳率、决策耗时）。
- 加分点：先定指标再写代码，避免「做出来不知道好不好」；面试可展示「先产品后工程」。
- 工作量：0.5 天
- 验收：设计文档定稿，检索键 / 评分 / 证据格式 / 指标全部可执行；本阶段后续待办引用该文档。

### 1.2 [x] 语料与索引存储（schema v15）【已完成 · 2026-08-07 / 66ead65 / 072fda8 / docs/evidence/stage-6-schema-v15-rag-2026-08-07.md】
- 目标/做法：`src/agentharness/storage/rag.py` 新增 `RagRepo`（唯一 SQL 所有者）；`src/agentharness/storage/migrations.py` `SCHEMA_VERSION 14 → 15`：新增 `rag_chunks`（`chunk_sha256` 唯一、`request_id/quote_id/artifact_id/artifact_sha256/request_reference/supplier_name/item_name/category/specifications_json/unit_price/currency/landed_unit_cost/lead_days/moq/decision/decision_at/content/embedding BLOB NULL/created_at`）+ 必要索引（supplier_name、item_name、category、decision_at）；`src/agentharness/storage/sqlite.py` 挂载 `self.rag` 委托。
- 加分点：延续「每个域唯一 SQL 所有者 + 版本化迁移」约定；迁移本身可测。
- 工作量：0.5–1 天
- 验收：迁移 14→15 沿用现有迁移测试模式（含降级）；RagRepo 增删查单测全绿；`agentharness.db` 在旧库上升级不丢数据。

### 1.3 [x] 索引填充：写时更新 + 全量重建脚本【已完成 · 2026-08-07 / 9e1a2b6 / ac7ee40 / docs/evidence/stage-6-rag-indexing-2026-08-07.md】
- 目标/做法：`approve_supplier_from_agent` 决定落库时**同事务**写入 `rag_chunks`（报价抽取字段 + 快照中的到货成本 + 决定 + 备注）；报价字段经人工修正后**同步更新对应 chunk**（修正联动，避免索引与业务事实漂移）；`scripts/rebuild_rag_index.py` 幂等全量重建（按 `chunk_sha256` 去重、0 模型调用、可离线）。低置信度来源的 chunk 写入数据质量标记，检索时降权。
- 加分点：索引永远跟得上业务事实，且与审批同事务保证一致性；重建可复现。
- 工作量：1 天
- 验收：审批后 chunk 立即可查；人工修正后 chunk 与业务事实一致；数据质量标记在检索排序中生效；重建脚本对同一数据两次运行结果一致；历史存量数据可回填；测试覆盖幂等与原子性（审批失败不写索引）。

### 1.4 [x] 检索器：混合召回 + rerank【已完成 · 2026-08-07 / f787769 / 44dce5b / docs/evidence/stage-6-rag-retriever-2026-08-07.md】
- 目标/做法：`src/agentharness/rag/retriever.py`：候选集 = 同品类/物料关键词（SQLite FTS5；若运行环境 sqlite 不支持 FTS5，先退化为精确词 LIKE）+ 结构化规格匹配 → 粗排取 top-20 → 按 1.1 评分规则 rerank（规格容差、材质/颜色/印刷色数、缺失字段降权、时间衰减、数据质量）→ 排除当前 `request_id` → 返回 top-5（默认）带分数与证据；`src/agentharness/rag/embeddings.py` 预留可插拔接口，本阶段默认不启用向量。
- 加分点：**结构化优先于向量**——可解释、零成本、离线可测；rerank 证明「粗召回 + 精重排」的工程分层；面试能讲清「为什么这里不用 embedding」。
- 工作量：1–1.5 天
- 验收：冻结场景库上 top-5 命中已知历史成交；相似规格能召回、不相似不召回；无历史时返回空且不报错；rerank 后 top-5 命中率不劣于粗排 top-20，top-1 命中率记录在案；单次检索耗时与上下文预算受控（有上限断言）。

### 1.5 [x] 服务集成：流水线阶段 + 审计 + 确定性隔离【已完成 · 2026-08-07 / 7156c96 / 79c54d8 / docs/evidence/stage-6-rag-service-2026-08-07.md】
- 目标/做法：`src/agentharness/procurement/service.py` 的 `execute_analysis_pipeline` 在 `supplier_history` 之后新增 `retrieve_knowledge` 阶段；`src/agentharness/procurement/agent.py` 的 `pipeline_payload` 带 `knowledge_references`（脱敏、截断）；审计事件 `knowledge_retrieved`（chunk_id、score、sha256、top-k 摘要）；**分级注入**：流水线自动注入 top-3 摘要（token 预算断言），前端可展开 top-5；记录 `knowledge_reference_viewed` / `knowledge_reference_adopted` 反馈事件（只记 chunk_id 与动作，不记敏感内容）；新增回归测试：**历史数据变化不影响 `analysis_input_sha256`**。
- 加分点：与现有「阶段化 + 审计」完全同构，治理叙事不断层；确定性隔离有专门测试背书。
- 工作量：1 天
- 验收：分析结果含 `knowledge_references`；审计事件可查；分级注入的 token 预算断言通过；查看/采纳反馈事件可查；确定性冻结评测（617/620、31/31、0 漏检）不回归；「历史变更不改 input_sha256」回归测试全绿。

### 1.6 [x] 前端呈现：比价页历史成交参考栏【已完成 · 2026-08-07 / c4832ec / d8320b7 / docs/evidence/stage-6-rag-ui-2026-08-07.md + 截图】
- 目标/做法：`web/src/procurement/ComparisonView.tsx`（或报告视图）新增「历史成交参考」区块：供应商、成交价、到货成本、日期、是否成交、来源 `request_reference`；默认展示 top-3 摘要，可展开 top-5；空态文案「暂无相似历史成交」；Agent 推荐说明中的引用可点开来源；每条参考带「查看详情 / 有帮助」轻量交互，回写反馈事件（不阻塞流程）；`web/src/procurement/types.ts` 同步类型。
- 加分点：提效感最强的界面变化，demo 一屏讲清。
- 工作量：1 天
- 验收：web 测试 / lint / build 全绿；浏览器走通「有历史 / 无历史 / 展开 top-5 / 反馈点击」各态；反馈点击后事件可查；重建 `web_dist`。

### 1.7 [x] 离线评测：检索质量冻结集【已完成 · 2026-08-07 / b13b499 / 4a17f43 / docs/evidence/rag-retrieval-2026-08-07.md（recall@1 0.0714 / precision@1 0.9286 / MRR 0.9464 / top-1 0.9286；反馈查看率 1/3、采纳率 1/3）】
- 目标/做法：`scripts/evaluate_knowledge.py`：用现有场景库构造冻结集（N 条历史成交，隐藏目标后检索），输出 recall@k、precision@k、MRR、top-1 命中率；结合 1.6 反馈事件评估**参考查看率 / 参考采纳率**，作为评分权重调整依据（反馈闭环）；0 模型调用；结果写入 `docs/evidence/rag-retrieval-<日期>.md`，含失败案例分析。
- 加分点：RAG 也有「确定性评测」，面试被问「RAG 准不准」直接翻数字；与 617/620 分层呈现。
- 工作量：1–1.5 天
- 验收：脚本可复现；报告含指标、失败案例与调权建议；确定性指标与 RAG 指标明确分层，不混用。

### 1.8 [ ] 提效验证：真人对照（assisted vs assisted+RAG）
- 目标/做法：复用 `scripts/evaluate_procurement.py human-trial` 测量装置，新增对照：**无 RAG 的 assisted** vs **带 RAG 的 assisted**；指标：任务总耗时、比价到决策时长、翻历史/外部记录次数、参考采纳率；诚实分层，不把「assisted 本身提效」算成 RAG 增量功劳。
- 怎么做：先跑无 RAG 的 assisted 记录基线，再用带 RAG 的版本跑同一批任务，两边都用脚本自动计时与取证；详细操作步骤按 README 的 human-trial 说明，到这一步时也可回主会话让我逐步教。
- 加分点：这是「提效类型」功能的验收证据，README「提效比例待实测」终于能填数。
- 工作量：1–2 天（含真人执行时间）
- 验收：至少 1 组有效对照记录，报告含差异与局限；样本不足时如实标记「待实测」，不得虚构比例。

### 1.9 [ ] 真实模型验证 + README/面试叙事更新
- 目标/做法：受预算约束跑真实模型完整链路，验证：比价页出现历史参考、Agent 推荐说明带引用、回合数未增加、成本在预算内；README 健康度表 + 面试叙事更新；`docs/rag-design.md` 归档为正式设计。
- 工作量：1 天
- 验收：真实模型 run_id / 回合 / 成本记录入 `docs/evidence/stage-6-real-model-<日期>.md`；README 更新；面试叙事含「有记忆、能提效」章节。

## 2. 每条待办的硬门槛

除待办自身的验收标准外，每条待办完成前必须满足：

- Python：`.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q` 全绿，覆盖率不低于 80%；
- Python 质量：`ruff check .` 通过；
- Web 改动：在 `web` 目录运行 `npm test`、`npm run lint`，并重建 `web_dist`；
- UI 改动：启动开发服务（`uv run agentharness --workspace . --data-dir output/dev-run --no-open`，默认 http://127.0.0.1:8741），用浏览器验证主路径和相关边界路径；可用 Playwright 自动化冒烟或手动走通主路径；无法启动或验证时，必须记录阻塞，不得声称 UI 验收完成；
- 每条待办必须更新本文件对应标题的 `[ ]`/`[x]` 和完成证据；未满足验收或硬门槛时保持 `[ ]`。

## 3. 阶段完成门槛

阶段内全部待办满足自身验收和每条硬门槛后，才可进入该阶段真实模型验证。阶段验证成功、或已按外部阻塞规则如实记录为 `blocked`/`failed` 后，才能进入下一阶段；外部阻塞不得被伪装成成功。















