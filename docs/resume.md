# 简历项目描述（双语言架构版）· AI 智能采购平台

## 投递定稿（最终版全文快照）

> 实际投递文本的最终快照（2026-09 定稿）。项目段与下方"投递版"的差异均为本人压缩选择，所有数字口径与仓库证据一致；面试口径见文末问答表。

**张鑫磊 ｜ AI应用开发**

**教育背景**：2023.09–2027.06 河南城建学院 数据科学与大数据技术（本科）；软件设计与开发竞赛校级一等奖、学院程序设计竞赛一等奖、校园实践大赛（软件开发类）一等奖。

**专业技能**：Agent 与大模型（LangGraph/LangChain、ReAct、Function Calling、HITL、Checkpoint）；RAG 检索链路（BM25 + BGE-M3 + RRF + Cross-Encoder、Milvus、Few-Shot 结构化提取）；后端（主 Python/FastAPI，熟 Java/Spring Boot，Kafka KRaft/Outbox、Redis 缓存与分布式锁、MySQL 事务与索引）；质量与可观测（Docker、Git、Linux、HitRate/MRR、LLM-as-a-Judge、Agent 全链路 Tracing）。

**实习经历**

杭州聚米云软件有限公司 ｜ 智能电商客服 Agent 平台 ｜ 2026.01–2026.06
技术栈：LangGraph、ReAct、RAG、BGE-M3、RRF、Rerank、Milvus、FastAPI
业务背景：为杭州优勤网络科技定制研发电商客服平台，自动应答售前售后咨询，置信度不足时转接人工。

- **Agent 编排与工具调用**：基于 LangGraph 构建条件路由状态机，实现意图识别与工具受控调用；工具入参经 Pydantic 严格校验，工具权限白名单隔离，工具调用成功率由 72% 提升至 93%。
- **RAG 检索流水线优化**：将召回链路由单路向量检索升级为「BM25 + 向量召回 → RRF 融合 → Cross-Encoder 重排」，结合 Milvus 增量索引与元数据过滤，召回率由 78.3% 提升至 93.5%，Top-1 命中率提升 10%。
- **分层记忆设计**：短期记忆基于 State 维护对话并摘要压缩；长期记忆维护用户画像，含去重、冲突消解与 TTL 过期。

**项目经历**

AI 智能采购平台（Java 业务主机 + Python Agent 微服务） ｜ 独立研发 ｜ 2026.06–2026.09
技术栈：Java 21、Spring Boot 4.1、Python 3.11、FastAPI、Kafka、MySQL、Redis、React 18
业务背景：询价→报价解析→比价→人工审批→订单→对账→付款；Spring Boot 承载业务状态，Kafka 唯一通道。

- **Agent runtime 治理**：自研步骤图执行引擎；LLM 只提案、确定性校验与 Java 事务执行，AI 不能绕过人工审批闸门；模型失败沿四级兜底链降至零 LLM 确定性路径。
- **模型路由与成本工程**：需求捕获沿四厂商模型阶梯自最便宜档起步逐级升档，成本中位 -39.7%；重复需求按内容哈希直连确定性工具图，远端 3→0 轮、延迟 -97.9%；成本面板按模型/任务计价，Prompt Cache 命中率 58.7%。
- **确定性比价与双层评测**：到货总成本（税/运费/汇率归一化）排序与硬约束检查；31 份黄金契约双侧（Java/Python）回归——字段抽取 99.52%、物料匹配 31/31、硬约束漏检 0、错误入选 0；LLM-as-a-Judge 判官只看事实、不看期望答案，四维 rubric 评审 31 例结论+31 例错误对照组防虚高——一致率 95.2%、Cohen's κ=0.9032。
- **一致性与质量保障**：幂等表+乐观锁+Redis 分布式锁；决定与订单同一事务；CI 四 job、Playwright 45+ 项全绿。
- **Java 业务主机**：注册式状态机引擎（订单/对账声明流转表复用）；绩效实时派生、报表审计、React 工作台 9 视图。

## 投递版（精简 ~45%，数字全保真；口头弹药留在详版）

**AI 智能采购平台（Java 业务主机 + Python Agent 微服务）** ｜2026.06 – 2026.09

询价 → 报价解析 → 确定性比价 → 人工审批 → 订单 → 对账 → 付款业务闭环；Spring Boot 4.1 承载全部业务状态，自研 Python Agent runtime 负责解析与 LLM 结构化，Kafka 唯一通道（HMAC 信封 + 幂等 + DLQ）。

1. **Agent runtime 治理**：自研步骤图执行引擎；LLM 只提案、确定性校验与 Java 事务执行，AI 不能绕过人工审批闸门；模型失败沿四级兜底链降至零 LLM 确定性路径。
2. **模型路由与成本工程**：capture 步骤沿四厂商模型阶梯自最便宜档起步、失败逐级升档，实测成本中位 **-39.7%**；重复需求按内容哈希**前置路由**到确定性工具图，远端调用 3→0 轮、端到端延迟 **-97.9%**；成本面板按模型/任务计价，线上 Prompt Cache 命中率 **58.7%**。
3. **LLM-as-a-Judge 双层评测**：判官只看事实不看期望答案，四维 rubric 评审 31 例结论，另注入 31 例构造错误对照组防一致率虚高——真值一致率 **95.2%**、Cohen's **κ=0.9032**。
4. **确定性比价与冻结评测**：到货总成本（税/运费/汇率归一化）排序与硬约束检查；31 份黄金契约双侧回归——字段抽取 **99.52%**、物料匹配 31/31、硬约束漏检 0、错误入选 0。
5. **一致性与质量保障**：幂等表 + 乐观锁 + Redis 分布式锁三层防护；正式决定与订单同一事务；CI 四 job 全绿、Playwright 全流程 45+ 项全绿。
6. **Java 业务主机**：注册式通用状态机引擎（订单/对账声明流转表即复用）；供应商绩效实时派生；报表聚合、全局审计、React 工作台 9 视图。

**技术栈**：Java 21 · Spring Boot 4.1 · MySQL 8 · Redis · Kafka · Python 3.11 · React 18 · Docker · Playwright

## 最终粘贴版（简历权重：Agent 4 条在前 · 架构居中 · Java 1 条收尾）

**AI 智能采购平台（Java 业务主机 + Python Agent 微服务）** ｜独立全栈｜2026.06 – 2026.09

询价 → 报价解析 → 确定性比价 → 人工审批 → 订单 → 对账 → 付款完整业务闭环；Java 21 / Spring Boot 4.1 承载全部业务状态与事务，自研 Python Agent runtime 负责文档解析与 LLM 结构化，Kafka 唯一通道（HMAC-SHA256 信封 + 双侧幂等 + DLQ），MySQL/Redis/React 工作台。

1. **Agent runtime 治理**：自研步骤图执行引擎 + 工具注册 + run 元数据全程留痕；坚持"LLM 只提案、确定性校验与 Java 事务执行"，AI 不能绕过人工审批闸门；模型失败沿四级兜底链降级（阶梯升档 → 主模型 → 确定性 planner），降级路径零远程调用可审计。
2. **模型路由与成本工程**：capture 步骤按四厂商阶梯（MiMo / LongCat / DeepSeek / 混元）自最便宜档起步，8×8 开关对照实测该步骤成本中位 **-39.7%**（+45% 延迟如实记录）；重复需求凭内容哈希**前置路由**到确定性工具图，8 对冷热对照远端调用 3→0 轮（8/8 命中）、端到端 **-97.9%**；Java 成本面板按模型/任务归集计价（与 Python 同口径缓存折扣，线上 Prompt Cache 命中率 **58.7%**），未计价模型如实单列。
3. **LLM-as-a-Judge 双层评测**：判官只看需求与事实、看不到期望答案，四维 rubric 评审 31 例比价结论，另注入 31 例构造错误对照组（成本漂移/全量漏检/匹配翻转）防一致率虚高——真值一致率 **59/62（95.2%）**、Cohen's **κ=0.9032**；标注记录与冻结集版本指纹漂移时自动拒用并在报告注明。
4. **确定性比价与冻结评测**：到货总成本（含税/运费/汇率归一化）稳定排序 + 硬约束检查；31 份报价黄金契约双侧（Java/Python）回归——字段抽取 **617/620（99.52%）**、物料匹配 31/31、成本计算 31/31、硬约束漏检 0、错误入选 0。
5. **并发与一致性三层防护**：幂等表防重放、乐观锁防版本覆盖、Redis 分布式锁（SETNX+Lua 条件释放）防并发双写；正式决定与订单同一事务、UNIQUE(task_id) 最终防重；GitHub Actions 四 job（Java 217 测试/Python/密钥扫描/Web 构建）全绿，Playwright 无头全流程 45+ 项全绿。
6. **Java 业务主机**：注册式通用状态机引擎（订单/对账声明流转表即复用校验与审计）；供应商绩效分实时派生（小样本折减、黑名单封顶）；报表聚合 + 全局审计迁移 + 看板缓存主动失效；React 工作台 9 视图角色演示视角。

**技术栈**：Java 21 · Spring Boot 4.1 · JPA · Flyway（V1–V21）· MySQL 8 · Redis · Kafka · Python 3.11（Agent/评测/Judge）· React 18 + TS · Docker Compose · Playwright · GitHub Actions

## 项目描述（中文·详版）

**AI 智能采购平台（Java 业务主机 + Python Agent 微服务）｜后端开发 · 全栈**

采购询价 → 报价解析 → 确定性比价 → 人工审批 → 订单 → 收货 → 对账 → 付款的完整业务闭环；Java 21 / Spring Boot 4.1 承载全部业务状态，Python Agent 微服务负责文档解析与 LLM 结构化，Kafka 为唯一通道（HMAC 签名 + 双侧幂等 + DLQ）。

**核心工作与量化结果：**

1. **确定性比价与冻结评测**：Java 实现到货总成本（含税/运费/汇率归一化）稳定排序与硬约束检查；31 份冻结报价黄金契约双侧（Java/Python）回归，**字段抽取 617/620（99.52%）、物料匹配 31/31、成本计算 31/31、硬约束漏检 0、错误入选 0**。
2. **注册式通用状态机引擎**：自研 `StateMachine` 引擎（状态/事件枚举 + 流转表 + 动作钩子 + 注册表），订单（待发货→已发货→已收货→已关闭）与对账（未对账→已对账→已付款）注册式实现；非法流转 409，并发由乐观锁兜底——新业务只需注册流转表即可复用引擎与审计。
3. **并发与一致性三层防护**：幂等表防重放、乐观锁（version 条件更新）防版本覆盖、Redis 分布式锁（SETNX + 请求标识 + Lua 条件释放，10s TTL）防并发双写；锁加在 Controller 层且不侵入冻结的审批服务，Redis 不可用自动回退无锁路径。
4. **正式决定与履约幂等**：Java 在正式决定事务内生成唯一订单，失败整体回滚；`UNIQUE(task_id)` 最终防重。分批收货、对账、付款均以规范化载荷绑定 `Idempotency-Key`，同键重放返回原结果、不同载荷 409。
5. **业务域建模**：供应商档案（报价/中标按名称关联、删除保护、绩效分实时派生：中标率 0–60 且 <3 次报价折减 0.5、活跃度 min(20, 次数×2)、黑名单封顶 30）；订单/对账状态机 + 分批累计收货与超收校验 + 累计收满自动派生对账单（缺成本拒绝）+ 发货/付款双超时调度（clock 注入可测，审计幂等）。
6. **统计与可观测**：报表聚合（状态漏斗/月度趋势/中标排行/品类分布 + 成本节约率 `(Σ预算−Σ到货)/Σ预算`，BigDecimal 4 位，无预算任务不计入）；全局审计（V11 迁移：`task_id` 可空 + `business_type/business_id` 通用定位，AuditEvent 工厂重载保持兼容）；看板缓存 TTL 60s + 写操作主动失效。
7. **前端与演示**：React 工作台 9 视图（供应商/订单/对账/报表/审计/系统信息/管理驾驶舱待办中心），角色选择器（采购员/审批人/管理员）纯前端演示视角；Playwright headless 全流程实测 45+ 项全绿；演示数据全部标记 synthetic 且用 demo-seed actor 写审计，不混入冻结评测。
8. **模型路由与成本工程**：解析类 run 按步骤复杂度降档到阶梯第一档（最便宜/最高容量小窗口模型，上下文压缩成为必经路径），失败沿阶梯逐级升档、耗尽回主模型、仍失败走确定性 planner（四级兜底链，run 元数据全程留痕）；重复需求消息凭"内容哈希+版本"缓存**前置路由**到确定性工具图——**8 对冷热对照实测**远端调用 3→0 轮（8/8 全命中）、端到端 33.4s→0.69s（-97.9%）；**8×8 开关对照** capture 步骤成本中位 -39.7%（mimo vs hy3，代价 +45% 延迟，如实记录）。Java 成本面板按模型/任务归集 token 并计价（与 Python `_cost_usd` 同口径缓存折扣，线上 Prompt Cache 实测命中率 58.7%），未计价如实单列。
9. **LLM-as-a-Judge 双层评测**：独立 LLM 按四维 rubric（结论正确性/证据一致性/约束遵循/幻觉）评审 31 例比价结论——**Judge 只看需求与事实、看不到期望答案**，另注入 31 例构造错误对照组（成本漂移/全量漏检/匹配翻转）防一致率虚高；真值一致率 **59/62（95.2%）、Cohen's κ=0.9032**；人工盲测记录与冻结集版本漂移时自动拒用并在报告注明（诚实纪律优先于好看数字）。

**技术栈**：Java 21 · Spring Boot 4.1 · Spring Data JPA · Flyway（V1–V21）· MySQL 8 · Redis（分布式锁/缓存/语义缓存）· Kafka（KRaft + SASL 可选）· Python 3.11（解析/评测/Judge）· React 18 + TypeScript · Docker Compose · Playwright

## Project Description (English)

**AI Procurement Platform — Java Business Control Plane + Python Agent Microservice**

End-to-end procurement loop: sourcing → quote parsing → deterministic comparison → human approval → purchase order → receiving → settlement → payment. Java 21 / Spring Boot 4.1 owns all business state; a Python Agent microservice handles document parsing and LLM structuring; Kafka is the single transport (HMAC-signed, dual-side idempotent, DLQ).

**Highlights:**

1. **Deterministic comparison with frozen evaluation**: landed-cost normalization (tax/freight/FX) and hard-constraint checks in Java; 31-case golden contract regression on both sides — **617/620 (99.52%) field extraction, 31/31 item matching, 31/31 cost calculation, 0 hard-constraint misses, 0 wrong eligible selections**.
2. **Registration-based generic state machine engine**: custom `StateMachine` (state/event enums + transition table + action hooks + registry); order and settlement machines are declarative registrations — new business domains reuse the engine and audit hooks.
3. **Three-layer concurrency defense**: idempotency table (replay), optimistic locking (version overwrite), Redis distributed lock (SETNX + request id + Lua conditional release, 10s TTL) for concurrent double-write; lock added at the Controller layer without touching the frozen approval service; automatic no-op fallback when Redis is down.
4. **Transactional decision and fulfillment idempotency**: Java creates the unique order in the formal-decision transaction and rolls back atomically on failure; `UNIQUE(task_id)` is the final guard. Receipt, settlement, and payment retries bind canonical payloads to `Idempotency-Key`.
5. **Domain modeling**: supplier profiles (name-based quote/win association, delete protection, real-time performance score: win-rate 0–60 halved under 3 samples, activity min(20, 2×count), blacklist capped at 30); order/settlement state machines with over-receipt rejection, automatic settlement derivation on receiving (rejected when landed cost missing), dual overdue schedulers (clock-injectable, idempotent audit).
6. **Insights & observability**: reports (status funnel / monthly trend / supplier ranking / category distribution + cost-saving rate `(Σbudget−Σlanded)/Σbudget`, BigDecimal 4 decimals, budget-less tasks excluded); global audit (V11 migration: nullable `task_id` + `business_type/business_id`, backward-compatible factory overloads); dashboard cache TTL 60s with active eviction.
7. **Frontend & demo**: React workbench with 11 views (tasks/AI reviews/suppliers/orders/invoices/contracts/reports/audit/system/cockpit), demo role switcher (buyer/approver/admin); isolated headless Playwright checks cover desktop/mobile navigation; all demo data is marked synthetic with demo-seed actor audit and never mixed into frozen evaluation.
8. **Model routing & cost engineering**: parse-heavy runs start at tier-0 of an ordered model ladder (cheapest / highest-throughput small-context model, making context compaction a required path); failures escalate tier by tier → primary → deterministic planner, fully audited in run metadata. Cache pre-routing sends repeated requirement messages to the deterministic graph with zero remote calls — over 8 cold/warm pairs: 3→0 remote turns (8/8 hits), end-to-end 33.4s→0.69s (−97.9%); over an 8×8 on/off A-B: median capture-step cost −39.7% (mimo tier vs hy3 primary, at the cost of +45% latency — honestly recorded). Java cost panel aggregates tokens per model/task with cache-discounted pricing; unpriced models are honestly surfaced, never zeroed.
9. **LLM-as-a-Judge second-layer evaluation**: an independent LLM scores all 31 comparison conclusions on a 4-dimension rubric while seeing only the requirement, extracted facts and the system conclusion (never the expected answer); 31 injected-error controls (cost drift / dropped exclusions / flipped match) guard against inflated agreement — 59/62 agreement with curated ground truth, Cohen's κ=0.9032. Human blind-trial records are auto-rejected when their dataset fingerprint drifts (skipped_dataset_drift), preferring an honest gap over a fake number.

**Stack**: Java 21 · Spring Boot 4.1 · Spring Data JPA · Flyway V1–V21 · MySQL 8 · Redis (lock/cache/semantic cache) · Kafka (KRaft, optional SASL) · Python 3.11 (parsing/evaluation/LLM-judge) · React 18 + TypeScript · Docker Compose · Playwright

## 面试问答速查

| 面试问题 | 回答要点 |
|---|---|
| 为什么已有幂等表还要分布式锁？ | 三层防三种故障：幂等防**重放**、乐观锁防**版本覆盖**、分布式锁防**并发双写**（两个不同请求同时为同一任务发起审批）。锁只加在 Controller 层，不侵入审批服务。 |
| 正式决定与订单如何保证一致？ | Java 在任务悲观锁下重新校验版本、快照、资格与金额，并在同一事务写决定和订单；任一步失败整体回滚，`UNIQUE(task_id)` 最终防重，GET 查询保持只读。 |
| 状态机引擎解决了什么？ | 手写 if-else 不可扩展；注册式引擎让新业务只声明"从哪+事件→到哪"即可复用校验与审计；任务状态机是历史实现不迁移（如实说明）。 |
| 绩效分为什么 <3 次报价要折减？ | 最小样本量：1/1 与 9/10 不应同分；黑名单封顶 30 防止"劣币驱逐良币"；实时派生不落表避免口径漂移。 |
| 成本节约率为什么用预算上限做分母？ | 保守口径（实际节约率不低于此值）；无预算任务不计入保持口径一致；BigDecimal 4 位防浮点误差。 |
| 为什么比价项目不用 LangGraph？ | 通用框架给不了审批快照与业务事务的同库强绑定、HITL generation 防串扰、checkpoint 与业务状态一起恢复——治理约束要求自研 runtime；实习项目用 LangGraph，两边场景不同正好说明选型判断。 |
| 语义缓存怎么做到"前置"？ | 缓存在 run 创建前查（消息 SHA+schema 版本做 key），命中直接把整轮路由到确定性工具图、由缓存供已校验需求——如果缓存在工具内才查，LLM 那轮已经花了，省不下成本。这个 bug 就是 A/B 实测揪出来的。 |
| Judge 怎么防止"自己给自己打分"和一致率虚高？ | 被评对象是确定性解析+Java 比价（无 LLM），评审模型天然异源；31 例干净结论外注入 31 例构造错误（成本漂移/全量漏检/匹配翻转）作对照组，Judge 必须两边都判对；一致率 59/62、κ=0.9032，3 例分歧如实保留在报告里可复查。 |
| 为什么人工盲测没参与 Judge 校准？ | 盲测记录是 v2 数据集，当前冻结集 v3，6 例里 4 例期望值已漂移——版本不一致的标注不能拿来校准，CLI 自动拒用并在报告注明 skipped_dataset_drift。宁可少一个卖点，不要一个假数字。 |
| redis 客户端升级把缓存打挂过？ | 是——redis-py 8 拒绝 float TTL，所有写入抛异常被容错 except 吞掉，只有 errors 计数露出。教训：静默降级路径必须有可观测出口。修复后 TTL 出口统一 int，回归测试用严格 store 复现类型拒绝。 |
| 超时调度怎么做？ | @Scheduled 60s 扫描 + 7 天阈值 + clock 注入可测 + 审计事件幂等去重（一任务一订单，task_id+event_type 去重）。 |
