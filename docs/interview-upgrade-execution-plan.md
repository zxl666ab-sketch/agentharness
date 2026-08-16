# AI 智能采购平台 · 面试升级执行计划 v2.0（goal 模式施工文档）

> 文档版本：2.0（2026-08）
> 读者：**新会话 goal 执行 Agent（施工方）** + 用户（审核人）
> 定位：本仓库**唯一的执行总控**。开工前必须通读：`README.md`、`docs/architecture.md`、`docs/recruitment-value-upgrade.md`（Track B，本计划不执行）、本文档。
> Track B 分工：`docs/recruitment-value-upgrade.md` 的 P0-1 压测、P0-2 可观测性、P0-3 虚拟线程、P0-4 LLM-as-Judge、P1-1 MCP、P1-2 向量 RAG 属于**另一个 goal 会话**的范围，本计划一律不做、不重复、不引用其产出为前提。
> 更新规则：每完成一个阶段只更新本文档对应状态；不另建平行路线图。

---

## 0. 一句话目标与总验收

**目标**：在不动冻结资产的前提下，把项目从"技术强但业务单薄、前端难用"升级为"业财闭环完整（新增发票三单匹配/合同/供应商准入）、AI 参与点翻倍、前端可用、面试可讲出数字与故障故事"。

**总验收**（每个阶段都必须满足）：

1. 全套验证通过：Python `ruff + pytest --cov-fail-under=80`、Java `mvnw test`、Web `npm test + lint + build`、`check_web_build_determinism.py`；
2. 每个阶段一个 commit，message 写明阶段与要点；
3. 证据落 `docs/evidence/`（截图/JSON/报告），README 与 contracts 同步更新；
4. 冻结资产零改动（见 §6 禁止清单）。

---

## 1. 开工基线（第 0 天，必做，不完成不许开工）

1. `git status`：当前有未提交改动（README、compose、若干 Java/Python/Web 文件 + 未跟踪的 `V12__reconcile_final_decisions_with_reviews.sql`、`KafkaRpcServerTest.java`、`roles.test.ts`、`docs/recruitment-value-upgrade.md`）。**先向用户确认处置方式（收尾提交/保持不动），默认建议收尾提交**（commit message：`chore: baseline WIP before interview-upgrade execution`）。
2. 跑全套验证（README「验证」节），记录基线数字（Java 测试数、Python 测试数、Web 测试数、评测 617/620）。
3. 通读代码地图：Java `platform/statemachine`、`agent/`、`approval/`、`comparison/`、`order/`、`settlement/`、`supplier/`、`ai/`、`review/`；Python `agentharness/procurement/`、`engine/`、`providers/`、`storage/`；Web `web/src/procurement/`。**重点读 `docs/procurement-workbench-refactor-plan.md`（阶段 0-10 已完成，避免重复改造）**。

---

## 2. 已有能力清单（禁止重复造轮子）

施工前先确认这些**已存在**，新功能必须复用，不得另起炉灶：

| 能力 | 位置 | 说明 |
|---|---|---|
| outbox 可靠消息 | `agent/AgentOutboxWorker` + `agent_command` | 命令状态机 pending→published→accepted→completed/failed |
| DLQ + HMAC | `agent/KafkaDlqConfiguration`、`MessageCodec` | 双侧幂等，已有 DLQ 重放路径 |
| 注册式状态机引擎 | `platform/statemachine` | 新业务（发票/合同/准入）**必须注册进 `StateMachineRegistry`** |
| 三层并发防护 | 幂等表 + 乐观锁 + `cache/DecisionLock` | 新接口沿用 |
| AI 任务中心 | `ai/AiTask*` + `AiTaskCenter.tsx` + `AiTaskRecovery.tsx` | 任务状态/重试/取消已有，新 Agent 任务复用 |
| 人工审核中心 | `review/` + `ReviewCenter.tsx` | allow-once 审批已有 |
| 冲突显示 + 人工修正 | 报价字段 `conflicts` + corrections API + `QuoteWorkspace` FieldEditor | 校验链雏形已有 |
| 供应商档案/绩效/黑名单 | `supplier/` + `SupplierCenter.tsx` | 缺"准入流程"，不重写档案 |
| 订单/对账状态机 + 超时调度 | `order/`、`settlement/` | 三单匹配建立其上 |
| RAG 软提示 | `agent/ReferencePriceService` + Python `reference_prices.py` | 新 RAG 场景（合同条款库）参照此边界 |
| 冻结评测 | `frozen-evaluation.json` + `frozen-evaluation-ext.json` | 新评测**必须**独立新文件 |
| 成本保护 | `max_cost_usd` + 429 Retry-After 退避 | LLM 网关在此基础上加"限流/熔断" |

---

## 3. P1 前端可用性与结构修复（2–3 天，先做，收益最快）

> 背景：面试 demo 的唯一界面。以下问题均已读码确认，**并已通过无头 Playwright 实测复现（2026-08-16，见下"实测证据"）**。

### 实测证据（2026-08-16 无头 Playwright 走查，截图在 `output/ui-walk/`）

1. **状态文案（实锤）**：同一任务列表中「待审批」×2 与「等待审批」×1 同时出现；首页"最近任务"表格直出英文枚举（`approved`×3、`no_award`×1、`cancelled`×1、`collecting`×3），与侧边栏中文标签（"已批准"等）不一致。
2. **闭环断裂（实锤）**：任务详情进度条仅 5 步、止于"人工审批"；数据中存在 10 个订单、12 个已结束任务，但从任务详情页无任何入口抵达订单/对账/付款。
3. **隐藏门槛（新建任务实测）**：新建采购对话的"开始分析"按钮在 `files.length < 2` 时禁用（`ProcurementConversation.tsx` L218），页面无任何前置说明——只传 1 家报价时按钮莫名灰置。
4. **门禁原因半可见**："开始比价"置灰时完整原因仅在 `title`（hover 可见）；分析栏虽有一行小字（"需求待人工确认，请先保存需求…"），但"保存需求确认"动作位于页面上方另一面板，用户需自行发现先后顺序。
5. **信息密度（实锤）**：对话面板（Agent 会话 + SESSION/RUN ID + 工具进度）常驻详情页；证据条默认显示"原件 SHA-256 / 解析用时 11 ms"；报价复核默认全展开（20 字段行）；比价表 11 列。
6. **正向结论**：全流程无 JS console 错误；新建 → Agent 解析 → 待复核在离线 Provider 下约 9 秒走通，后端链路健康。
7. **已知瑕疵（演示话术项，不修解析核心）**：需求"采购 5000 个白色 PE 快递袋"被抽取为"采购量 5000 个白色"（颜色词并入数量，fake provider 需求抽取问题）；走查新建的测试任务 `RFQ-20260816-8B557B`（PE 快递袋采购询价）留在演示库，可经 UI 删除。

### P1-1 状态文案与枚举修复（半天）

- `ProcurementWorkbench.tsx` L78–89：**"待审批"（analyzed）与"等待审批"（approval_pending）文案无法区分（实测同列表共存）**。重写为可行动文案，建议：analyzed→「待审批（比价完成）」、approval_pending→「审批处理中」、draft→「需求整理中」、no_award→「本轮未选定」、collecting→「待上传报价」。
- `WorkbenchHome.tsx` L163：最近任务表格**直接渲染英文枚举 `request.status`**，改为与 P1-1 同一映射（抽取共用 `statusLabel()` 工具函数，放 `viewModel.ts`，带单测）。
- 验收：全站无英文状态枚举直出（首页/侧边栏/详情页共用同一映射）；`viewModel.ts` 单测覆盖全部 10 个状态；实测确认"待审批/等待审批"不再同屏共存。

### P1-2 下一步引导条（半天）

- 在任务详情头部（`proc-request-head` 下方）加一行**「下一步」引导**：由状态驱动文案（如 `下一步：保存需求确认 → 上传至少 2 家报价 → 复核 N 个字段 → 开始比价`），当前卡点高亮并**可见**显示原因（现 `analyzeDisabledReason` 只放 `title`，hover 才可见；分析栏小字提示位置不显眼，且"保存需求确认"动作所在面板需用户自行发现）。
- **创建环节也要覆盖**：`ProcurementConversation.tsx` L218 的"至少 2 个附件"硬门槛（实测：无前置说明）→ 在创建面板加可见提示（如"至少上传 2 家报价才能开始分析"），并给出已选文件计数。
- 验收：任意状态下列表项文案与该状态可执行动作一一对应（单测覆盖）；创建面板对附件数量要求有可见说明。

### P1-3 闭环进度条扩展 + 业务衔接入口（1 天，面试主菜）

- 进度条 `STEPS`（L91）只有 5 步且止于审批（实测确认）。扩展为完整闭环：`创建需求 → 报价 → 复核 → 比价 → 审批 → 订单 → 收货 → 对账 → 付款`（9 步）。
- 审批通过后：任务详情页显示「订单已生成 →」按钮直达对应订单（`/orders` 视图）；订单详情提供「去对账」；对账单提供「去付款」。
- 数据来源：复用现有 API（orders/settlements），不新增后端接口（如缺"按 task_id 查订单"接口，在 Java `OrderService` 加只读查询并同步 contracts）。
- 验收：一个已批准任务点 3 下走完 审批→订单→对账→付款 的页面跳转；`WorkbenchNavigation` 无重复入口；实测：从任意已批准任务详情页可直达其订单。

### P1-4 信息密度治理（半天）

- 对话面板（`ProcurementConversation`）在任务详情**默认折叠**为一条抽屉，展开才显示（实测：常驻显示 Agent 会话 + SESSION/RUN ID + 工具进度）；
- `QuoteWorkspace` 字段复核默认开启 `onlyReview=true`（L299）（实测：默认全展开 20 字段行）；
- 比价表（`ComparisonView` L147–161）默认收起「税额/运费/成本指数」列，加"展开详情"（实测 11 列）；
- 证据信息（SHA-256、解析用时 ms、快照 v1、input_sha256）**收进"证据"折叠面板**，默认只留「证据已验证」徽标（实测：证据条默认展示）。
- 验收：默认状态下任务详情页首屏无需滚动即可看到进度条与下一步引导；走查脚本（`output/ui-walk/walk.py`）复跑确认。

### P1-5 上帝组件拆分（1 天）

- `ProcurementWorkbench.tsx`（1071 行）拆为：
  - `useWorkbenchState.ts`：URL 状态 + navigate/openView/openTask（从 L144–151、L239–266、L594–643 抽出）；
  - `useRequestQueries.ts`：全部 react-query + 轮询策略 + commit/invalidate（L169–216、L310–321 抽出）；
  - `useWorkbenchActions.ts`：13 个动作 handler + busy/error 状态（L323–557 抽出）；
  - `DeleteDialog.tsx`、`ConfigDrawer.tsx`：两个内联弹窗独立成组件（L923–1068 抽出）；
  - 组件本体只留布局壳 + 视图分发，**目标 < 300 行**。
- 验收：每个新文件职责一句话能说清；`npm test + lint + build` 通过；行为不变（Playwright 全旅程回归）。

### P1-6 仓库卫生（0.5 小时）

- 删除空目录：`web/src/app/`、`web/src/eval/`、`web/src/events/`、`web/src/runs/`、`web/src/store/`、`web/src/trace/`。

---

## 4. P2 AI 治理补强（4–5 天）

> 原则重申：状态/规则/校验/数据/执行归 Java；理解/解析/生成/建议/解释归 Python Agent；影响业务状态的 Agent 输出必须经后端校验 + 人工 allow-once 审批；结构化业务字段由后端从权威源注入。

### P2-1 LLM 网关：限流 / 熔断 / 降级（2–3 天）

- 现状：Python `providers/` 有 429 Retry-After + 有界退避 + run 级成本上限；**无 QPS 限流、无熔断、无降级**。
- 实现（放 Python 侧，Java 不动调用协议）：
  - 并发配额：`asyncio.Semaphore` 按 provider 全局限并发（如 4），超限排队；配置走 `.env`；
  - QPS 限流：令牌桶（provider 维度）；
  - 熔断：连续失败率/错误数阈值（如 30s 窗口失败率 > 50%）→ 熔断 60s；熔断期间**降级**：
    - 解析类任务 → 返回结构化错误，Java `AiTask` 进入 FAILED（前端恢复路径已有，`AiTaskRecovery`）；
    - 解释类任务（比价解释）→ **模板降级**（用快照数据生成确定性文本，注明"模型不可用，展示确定性摘要"）；
  - 熔断/限流事件写入 Python 心跳或审计事件，Java 侧 `/api/procurement/platform` 暴露熔断状态（脱敏）。
- 验收：构造 provider 故障（fake provider 注入延迟/错误），观察熔断→降级→恢复全链路；前端可看到降级标识；新增单测覆盖限流/熔断/降级三路径。

### P2-2 冲突裁决流程化 + 修正回灌评测集（1–2 天）

- 现状：字段冲突只显示候选值列表（`QuoteWorkspace` L274–283）+ 手输修正。升级：
  - 冲突字段的修正框提供**候选值单选**（来自 `field.conflicts`），选中即提交修正；仍可手输；
  - Java `QuoteCorrection` 落库时记录 `chosen_from_conflicts` 标记；
  - 新增只读接口/前端视图：「修正回灌」——把人工修正记录导出为评测集扩展候选（脚本 `scripts/export_corrections_to_eval.py`，输出到新文件 `frozen-evaluation-corrections.json`，**冻结资源不动**，用户审核后才启用）。
- 验收：冲突字段可点选候选值完成修正；导出脚本幂等可重跑；README 说明回灌流程。

### P2-3 语义缓存（1 天）

- 场景：相同/相似报价文件重复上传解析、相同参考区间查询重复调用——用 Redis 缓存 LLM 解析结果与参考区间。
- 实现：Java `cache/TaskContextCache` 之外新增 `SemanticResultCache`（或 Python 侧直连 Redis）：key = 输入内容 SHA-256（精确层）+ 结构相似度（语义层可选，本期只做精确层+TTL+版本失效）；`doc_version`（原件 SHA-256 或 schema 版本）参与 key；原件更新即失效。
- 纪律：**缓存命中=确定性返回，不产生审计事件；解析结果缓存只对"已通过校验"的结果生效**。
- 验收：同一文件二次上传不产生 LLM 调用（心跳/成本指标可证）；单测覆盖 TTL 与版本失效。

### P2-4 评测扩展（与 P3-1 配套，见 §5）

---

## 5. P3 业务广度（核心，10–14 天）

> 总原则：新模块 = Java 状态机 + 确定性规则 + Agent 参与点 + 前端视图 + 评测，五件套齐了才算完成。

### P3-1 发票三单匹配（旗舰，5–6 天，先做）

**业务规则（全部 Java 确定性）**：
- 发票实体：发票代码、发票号码、开票日期、不含税金额、税额、价税合计、税率（`DECIMAL`/`BigDecimal`）；Flyway V13；
- 三单匹配：订单（PO）vs 收货单（GRN，现有 `PurchaseOrder` 收货记录）vs 发票（Invoice）：数量、单价、总价、税率逐一比对，容差规则可配置（如单价 ±0.01、数量 0 容差）；
- 匹配结果：`MATCHED` / `DIFF`（差异挂起）；差异挂起后可操作：退回重开（发票作废）、手工改单（记录审计）、强制通过+人工备注（allow-once 审批）；
- 状态机：发票状态机 `REGISTERED → MATCHED → RECONCILED`（与对账联动，付款 `settle/pay` 前置要求发票已匹配）或 `DIFF_HOLD` / `VOIDED`，**注册进 `StateMachineRegistry`**；
- 付款联动：`SettlementService.pay` 增加校验——存在未匹配发票时拒绝（409）或要求确认；
- 审计：新业务事件挂 `business_type=invoice` / `business_id`。

**Agent 参与点（严格边界）**：
- 发票解析：复用 Python 解析管线（`procurement/parsing.py`）扩展发票字段抽取——**建议先读解析器扩展点再动手**；
- 差异解释：Java 匹配引擎产出结构化差异（`{field, expected, actual, diff}`），Python 生成自然语言原因与处理建议（模式 C：后端算、Agent 说），存为可审计的解释记录；
- 禁止：LLM 不参与匹配判断、不计算金额、不修改发票状态。

**前端**：发票中心页（列表/详情/三单对比视图/差异挂起队列/处理操作）；任务闭环进度条在「对账」前插入「发票」环节（P1-3 的 9 步扩展为 10 步：…审批→订单→收货→**发票**→对账→付款）。

**评测**：新增 `frozen-evaluation-invoice.json`（发票字段抽取，复用评测脚本框架）+ 差异解释**数值引用一致性硬校验**（解释中每个数字必须存在于注入的结构化差异中，自动化评测）。

**验收**：五服务跑通；演示路径「上传发票 → 解析 → 匹配 → 差异挂起 → 人工处理 → 核销 → 付款成功」完整可走；评测字段抽取 ≥ 99%（合成数据集，README 如实标注 synthetic）。

### P3-2 合同管理（5–7 天）

**业务规则（Java 确定性）**：
- 合同实体：合同编号、供应商、关联任务/订单、金额、交期、条款集、状态；Flyway V14；
- 合同状态机 `DRAFT → PENDING_APPROVAL → EFFECTIVE → EXECUTING → CLOSED` + `CHANGE_REQUEST`，注册进 `StateMachineRegistry`；
- **字段注入**：合同金额/交期/供应商从定标结果（approved decision + snapshot）注入，不来自 LLM；必填条款校验（金额条款、交期条款必须存在）；
- 合同变更单：变更需重新审批（allow-once），变更后旧条款留痕。

**Agent 参与点**：
- 合同草拟（模式 B：模板 + 条款库 RAG 软提示——参照 K5 的 `ReferencePriceService` 边界实现条款检索，只进草拟文本，不进正式合同）；
- 条款风险识别（模式 A+C：从草拟/上传文本提取条款 → 分级（高风险/提示）→ 自然语言解释，结构化结果经后端校验后展示）；
- 后端一致性校验：草拟文本中的金额/日期必须与注入字段一致，不一致拦截并要求人工确认。

**前端**：合同中心页（列表/详情/审批/变更）；任务详情审批通过后显示「生成合同」入口。

**评测**：`frozen-evaluation-contract.json`（条款提取 precision/recall + 草拟字段注入一致性校验）。

**验收**：演示路径「定标 → 草拟（AI）→ 风险提示（AI）→ 人工审批 → 生效 → 关联订单」完整可走。

### P3-3 供应商准入（3–4 天，可选，若时间不足降级为设计笔记）

- 准入流程：提交资质文件（营业执照/证书）→ Agent 解析资质 + 生成风险画像摘要与准入推荐理由（模式 A+C）→ Java 校验（有效期/经营范围规则、黑名单检查）+ 评分卡 → 人工审批 → 供应商状态 `ACTIVE`；
- 状态机：准入状态机 `SUBMITTED → UNDER_REVIEW → APPROVED/REJECTED`，注册进 `StateMachineRegistry`；
- 复用：现有 `supplier/` 档案与绩效模型（黑名单封顶 30 等口径不动）；
- 前端：准入中心（列表/详情/审批）；SupplierCenter 加入口。
- 验收：演示路径「提交资质 → AI 解析+画像 → 规则校验 → 人工审批 → 供应商生效」。

---

## 6. 纪律与禁止事项（违反 = 阶段不合格）

1. **冻结资产零改动**：`ApprovalService`、`ComparisonEngine`、`frozen-evaluation.json`、`frozen-evaluation-ext.json`、`contracts/golden/*`、31 份黄金契约——一个字节不动；
2. **新评测集独立文件**，且 README 如实标注 synthetic；评测口径只增不改；
3. 新 API 必须同步 `contracts/`（OpenAPI + schema）+ web bundle（`check_web_build_determinism.py` 验证）；
4. 演示数据 synthetic 纪律；README/架构文档随代码同步，禁止单侧更新；
5. 每个任务硬时间盒，超时降级为「预研笔记 + 面试话术」写进 docs，不拖延不烂尾；
6. 每阶段完成：测试全绿 → commit → 证据落 `docs/evidence/`；
7. **明确不做**：登录/RBAC（演示角色已有）、注册中心/网关/K8s、向量库选型（Track B 已定方案）、重写 Python 解析核心与 Harness 运行时、MCP（Track B P1-1）；
8. 禁止重复建设 §2 已有能力；动手前先读对应代码。

---

## 7. 与 Track B（recruitment-value-upgrade.md）的衔接

- 本文档执行完毕后，建议另开 goal 会话执行 Track B（压测/可观测/虚拟线程/LLM-as-Judge/MCP/向量 RAG）；
- 两计划的交集：P2-1 的熔断/降级事件与 Track B 的 P0-2 监控指标天然衔接——本计划只落事件与接口，不装 Prometheus/Grafana；
- 若用户要求合并执行，先完成本文档 P1–P3 再进 Track B，不可并行（工作区纪律）。

---

## 8. goal 会话汇报格式（结束/阶段完成时向用户报告）

```text
## 完成项
- P1-x：…（验收证据链接）
## 未完成项与原因
- …
## 证据清单
- docs/evidence/…（截图/JSON/报告）
## 数字变化
- 测试数：Java N / Python N / Web N；评测：617/620 → …
## 面试话术更新点
- 新增可讲的一句话故事（如三单匹配差异处理闭环）
## 建议的下一步
- Track B / 新阶段 goal 的切入点
```

---

## 9. 建议的 goal 拆法

- 推荐：**每个阶段开一个 goal**（P1 → P2 → P3-1 → P3-2 → P3-3），每阶段完成即汇报；
- 或：一个 goal 带 `max_goal_rounds` 逐阶段推进，但每阶段必须满足 §0 总验收才进入下一阶段；
- 开工前先与用户确认 §1 的 git 处置方式。
