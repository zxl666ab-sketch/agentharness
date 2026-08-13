# 简历项目描述（双语言架构版）· AI 智能采购平台

> 用法：把「项目描述」节直接替换简历中的旧项目段（旧版写的是"Python/FastAPI/SQLite 单体"，与仓库 master 架构脱节）；「面试问答」节用于准备面试。量化指标保留：617/620、31/31。

## 项目描述（中文）

**AI 智能采购平台（Java 业务主机 + Python Agent 微服务）｜后端开发 · 全栈**

采购询价 → 报价解析 → 确定性比价 → 人工审批 → 订单 → 收货 → 对账 → 付款的完整业务闭环；Java 21 / Spring Boot 4.1 承载全部业务状态，Python Agent 微服务负责文档解析与 LLM 结构化，Kafka 为唯一通道（HMAC 签名 + 双侧幂等 + DLQ）。

**核心工作与量化结果：**

1. **确定性比价与冻结评测**：Java 实现到货总成本（含税/运费/汇率归一化）稳定排序与硬约束检查；31 份冻结报价黄金契约双侧（Java/Python）回归，**字段抽取 617/620（99.52%）、物料匹配 31/31、成本计算 31/31、硬约束漏检 0、错误入选 0**。
2. **注册式通用状态机引擎**：自研 `StateMachine` 引擎（状态/事件枚举 + 流转表 + 动作钩子 + 注册表），订单（待发货→已发货→已收货→已关闭）与对账（未对账→已对账→已付款）注册式实现；非法流转 409，并发由乐观锁兜底——新业务只需注册流转表即可复用引擎与审计。
3. **并发与一致性三层防护**：幂等表防重放、乐观锁（version 条件更新）防版本覆盖、Redis 分布式锁（SETNX + 请求标识 + Lua 条件释放，10s TTL）防并发双写；锁加在 Controller 层且不侵入冻结的审批服务，Redis 不可用自动回退无锁路径。
4. **惰性派生与幂等**：已批准任务在查询时惰性派生订单（读接口带写副作用的反模式 tradeoff——绝不触碰审批证据链），`UNIQUE(task_id)` + `DuplicateKeyException` 吞掉保证并发双请求只落一条。
5. **业务域建模**：供应商档案（报价/中标按名称关联、删除保护、绩效分实时派生：中标率 0–60 且 <3 次报价折减 0.5、活跃度 min(20, 次数×2)、黑名单封顶 30）；订单/对账状态机 + 超收校验 + 收货自动派生对账单（缺成本拒绝）+ 发货/付款双超时调度（clock 注入可测，审计幂等）。
6. **统计与可观测**：报表聚合（状态漏斗/月度趋势/中标排行/品类分布 + 成本节约率 `(Σ预算−Σ到货)/Σ预算`，BigDecimal 4 位，无预算任务不计入）；全局审计（V11 迁移：`task_id` 可空 + `business_type/business_id` 通用定位，AuditEvent 工厂重载保持兼容）；看板缓存 TTL 60s + 写操作主动失效。
7. **历史报价 RAG（软提示）**：Java RPC `get_reference_prices` 聚合历史成交价（p25/p75，不足 3 条返回 null），Python 侧注入参考区间与异常价格风险 flag——只进解释文本与风险标记，不参与比价排序、不排除报价、不影响冻结评测。
8. **前端与演示**：React 工作台 9 视图（供应商/订单/对账/报表/审计/系统信息/管理驾驶舱待办中心），角色选择器（采购员/审批人/管理员）纯前端演示视角；Playwright headless 全流程实测 45+ 项全绿；演示数据全部标记 synthetic 且用 demo-seed actor 写审计，不混入冻结评测。

**技术栈**：Java 21 · Spring Boot 4.1 · Spring Data JPA · Flyway（V1–V11）· MySQL 8 · Redis（分布式锁/缓存）· Kafka（KRaft + SASL 可选）· Python 3.11（解析/评测）· React 18 + TypeScript · Docker Compose · Playwright

## Project Description (English)

**AI Procurement Platform — Java Business Control Plane + Python Agent Microservice**

End-to-end procurement loop: sourcing → quote parsing → deterministic comparison → human approval → purchase order → receiving → settlement → payment. Java 21 / Spring Boot 4.1 owns all business state; a Python Agent microservice handles document parsing and LLM structuring; Kafka is the single transport (HMAC-signed, dual-side idempotent, DLQ).

**Highlights:**

1. **Deterministic comparison with frozen evaluation**: landed-cost normalization (tax/freight/FX) and hard-constraint checks in Java; 31-case golden contract regression on both sides — **617/620 (99.52%) field extraction, 31/31 item matching, 31/31 cost calculation, 0 hard-constraint misses, 0 wrong eligible selections**.
2. **Registration-based generic state machine engine**: custom `StateMachine` (state/event enums + transition table + action hooks + registry); order and settlement machines are declarative registrations — new business domains reuse the engine and audit hooks.
3. **Three-layer concurrency defense**: idempotency table (replay), optimistic locking (version overwrite), Redis distributed lock (SETNX + request id + Lua conditional release, 10s TTL) for concurrent double-write; lock added at the Controller layer without touching the frozen approval service; automatic no-op fallback when Redis is down.
4. **Lazy derivation with idempotency**: orders derived from approved tasks on query (read-with-write-side-effect tradeoff — never touches the approval evidence chain); `UNIQUE(task_id)` + swallowed `DuplicateKeyException` guarantees exactly one row under concurrent requests.
5. **Domain modeling**: supplier profiles (name-based quote/win association, delete protection, real-time performance score: win-rate 0–60 halved under 3 samples, activity min(20, 2×count), blacklist capped at 30); order/settlement state machines with over-receipt rejection, automatic settlement derivation on receiving (rejected when landed cost missing), dual overdue schedulers (clock-injectable, idempotent audit).
6. **Insights & observability**: reports (status funnel / monthly trend / supplier ranking / category distribution + cost-saving rate `(Σbudget−Σlanded)/Σbudget`, BigDecimal 4 decimals, budget-less tasks excluded); global audit (V11 migration: nullable `task_id` + `business_type/business_id`, backward-compatible factory overloads); dashboard cache TTL 60s with active eviction.
7. **Historical-quote RAG (soft hint)**: Java RPC `get_reference_prices` aggregates approved transaction prices (p25/p75, null under 3 samples); Python injects the interval and abnormal-price risk flags — explanation text and risk flags only, never affecting ranking, exclusion, or frozen evaluation metrics.
8. **Frontend & demo**: React workbench with 9 views (suppliers/orders/settlements/reports/audit/system/cockpit todo center), demo role switcher (buyer/approver/admin); 45+ Playwright headless checks green; all demo data marked synthetic with demo-seed actor audit, never mixed into frozen evaluation.

**Stack**: Java 21 · Spring Boot 4.1 · Spring Data JPA · Flyway V1–V11 · MySQL 8 · Redis (lock/cache) · Kafka (KRaft, optional SASL) · Python 3.11 · React 18 + TypeScript · Docker Compose · Playwright

## 面试问答速查

| 面试问题 | 回答要点 |
|---|---|
| 为什么已有幂等表还要分布式锁？ | 三层防三种故障：幂等防**重放**、乐观锁防**版本覆盖**、分布式锁防**并发双写**（两个不同请求同时为同一任务发起审批）。锁只加在 Controller 层，不侵入审批服务。 |
| 订单派生为什么用惰性（读接口写数据）？ | 反模式，权衡是绝不触碰审批证据链（ApprovalService 冻结）；用 `UNIQUE(task_id)` + DuplicateKeyException 幂等保证并发安全；生产化可改为审批完成事件监听。 |
| 状态机引擎解决了什么？ | 手写 if-else 不可扩展；注册式引擎让新业务只声明"从哪+事件→到哪"即可复用校验与审计；任务状态机是历史实现不迁移（如实说明）。 |
| 绩效分为什么 <3 次报价要折减？ | 最小样本量：1/1 与 9/10 不应同分；黑名单封顶 30 防止"劣币驱逐良币"；实时派生不落表避免口径漂移。 |
| 成本节约率为什么用预算上限做分母？ | 保守口径（实际节约率不低于此值）；无预算任务不计入保持口径一致；BigDecimal 4 位防浮点误差。 |
| RAG 怎么保证不污染评测？ | 参考区间是软提示：只进解释文本与风险 flag；冻结评测资源一个字节不动，扩展用例放独立文件；硬规则化与向量库留作后续。 |
| 超时调度怎么做？ | @Scheduled 60s 扫描 + 7 天阈值 + clock 注入可测 + 审计事件幂等去重（一任务一订单，task_id+event_type 去重）。 |
