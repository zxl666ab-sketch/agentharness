
# Java 后端 + Agent 工程化改造方案（基于当前 0.5.0 实际代码）

> 依据：本仓库 procurement-service（Spring Boot 4.1 / Java 21 / JPA / Flyway V1–V11 / MySQL 8）、
> Python Agent 微服务（Kafka 命令/结果/RPC/事件）、React 工作台。以下方案按
> **保持现有契约与冻结评测不变** 的前提设计，逐步落地。

## 一、现状盘点（代码中已存在的能力）

| 能力 | 现状位置 | 缺口 |
|---|---|---|
| 状态机 | platform/statemachine（订单/对账已注册） | 采购任务仍为字段驱动（status 列），未接入状态机引擎 |
| 幂等 | idempotency_record + Idempotency-Key | 覆盖了 conversation/analyze/approve/agent retry，但 supplier/order 写接口未统一接入 |
| 乐观锁 | version 条件更新 | 仅任务/审核/订单/对账部分使用，未统一 AOP 化 |
| 分布式锁 | DecisionLockGuard（SETNX+Lua） | 仅审批决定一处 |
| Outbox | agent_command outbox | 无通用 Outbox 抽象，Java 侧缺少事件发布 outbox（Kafka events 由 Python 发布） |
| 审计 | audit_event + V11 业务定位 | 缺少统一的 @Audited 注解切面，手工埋点 |
| Agent 任务 | ai_task / ai_task_record | 无 Prompt 版本表、无 Tool Call 明细表、无 Token 成本账本、无上下文压缩事件模型 |
| 可观测性 | actuator 基础 | 无 OpenTelemetry trace/metrics，日志无结构化 trace_id 贯穿 |
| 权限 | 纯前端 demo 角色 | 无服务端 RBAC |

## 二、总体目标架构

    Browser → Gateway/Controller（参数校验+幂等+鉴权） → Application Service（事务+Outbox）
            → Domain（聚合+领域服务+状态机+规则引擎）  → Repository（JPA+乐观锁）
            → 事件总线（Outbox → Kafka events）
            → Agent 适配层（Command/Result/RPC，HMAC+幂等） → Python Agent

## 三、Spring Boot 3 / Java 21 落地清单（按优先级 P0→P2）

### P0-1 采购任务接入注册式状态机引擎
- 将任务状态机注册到现有 StateMachineRegistry：DRAFT→COLLECTING→REVIEW→READY→ANALYZING→ANALYZED→APPROVAL_PENDING→APPROVED/NO_AWARD/CANCELLED，非法流转 409（复用现有 409 语义与前端错误文案）。
- 所有状态迁移只经 TaskStateMachine.transition(当前状态, 事件, 版本)，在 Application Service 统一调用；乐观锁版本号作为并发兜底（现状已有 version 字段）。
- 收益：把散落在 ApproveService/QuoteService 里的 if/else 状态判断收敛；审计事件由状态机动作钩子统一发出。

### P0-2 通用幂等注解（扩展现状）
- 现状：Idempotency-Key 只覆盖部分接口。新增 @Idempotent(timeout=24h) 注解 + 拦截器：
  - 生成 idempotency_record（key_sha256=HMAC(body) 防止大 body 索引膨胀，payload_sha256 校验同键异载荷 409）；
  - 命中缓存返回原响应（结果 JSON 快照存储）；
  - 应用到 supplier create/update/delete、order transition、settlement transition、decision、reopen。
- 前端无需改动（api.ts 已对所有 POST 生成 idempotencyKey，需为 PUT/DELETE 补齐）。

### P0-3 审计切面统一
- 新增 @Audited(type="order_transitioned", businessType="order") 注解 + AOP 切面，在事务提交后写入 audit_event；
- 现有手写 AuditEvent.create 保留为底层 API，切面包装之；V11 的 business_type/business_id 直接复用；
- 全局请求 ID（trace_id）贯穿 Web→Service→Audit→日志，前端 SSE 事件与审计可按 trace 关联。

### P1-1 Outbox 事件总线（Java 侧补齐）
- 现状只有 agent_command outbox。新增通用 outbox_event 表 + TransactionOutboxPublisher：
  - 业务事务内写事件 → 后台轮询/调度发布到 Kafka caijiatai.events（HMAC 签名复用现有 envelope）；
  - 用于：订单派生事件、对账派生事件、审批完成事件（解决 README 承认的"惰性派生反模式"，改为审批完成事件驱动派生，保留幂等唯一键）；
  - 事件消费者幂等（事件 ID 唯一索引）。
- 兼容策略：默认仍保留惰性派生兜底，事件驱动先行，双写校验期日志告警。

### P1-2 Agent 任务持久化与治理（Java 侧补齐当前 Python 独有部分）
- 新增表（Flyway V12）：
  - agent_prompt_version（prompt_name, version, sha256, content, created_by, active）——Prompt 版本化，Agent 结果回填 prompt_version；
  - agent_tool_call（run_id, tool_name, arguments_sha256, result_sha256, started_at, finished_at, status, error_code）——Tool Call 证据链；
  - agent_cost_ledger（run_id, provider, model, input_tokens, output_tokens, cached_tokens, cost_usd, currency）——Token 与成本账本，前端 AI 任务中心/运行审计展示；
  - agent_context_compact（run_id, before_tokens, after_tokens, strategy, created_at）——上下文压缩事件模型。
- Java 侧只做只读投影与账本聚合（运行时真值仍在 Python runtime schema），保持"业务 Schema 只由 Java Flyway 创建、禁止交叉建表"边界不变。

### P1-3 失败恢复与人工接管（Java 控制面）
- 现状：operation failed → 任务恢复 ready + 前端可重试。补齐：
  - agent_task_recovery 表记录恢复动作（retry/cancel/supplement/human_takeover），审计留痕；
  - 人工接管：POST /api/procurement/ai-tasks/{id}/takeover → 任务标记 HUMAN_TAKEOVER，后续 Run 不再自动重试，等待人工输入（前端 AiTaskRecovery 增加"人工接管"按钮，复用现有二次确认模式）；
  - 失败恢复策略表（error_category → 动作：RETRY/SUPPLEMENT/TAKEOVER），由 Java 决策而非前端硬编码。

### P1-4 结构化输出与规则校验（Java 侧强化）
- 现状：Python 结构化结果 → Java 校验后入库。补齐：
  - comparison_result_schema 版本化 JSON Schema（Java 侧存 schema_sha256，校验失败拒绝入库并打审计）；
  - 新增规则引擎规则包版本表（ruleset_version 已有，补 ruleset 内容 sha256 与变更审计）；
  - 前端 QuoteWorkspace 的修正提交在 Java 侧二次校验（数字/日期/枚举），非法值 422（现状已有部分）。

### P2-1 权限模型（服务端 RBAC）
- 现状角色为纯前端 localStorage。新增 app_user / role_permission / user_role 表 + Spring Security 简单会话（本地单用户模式默认单管理员）：
  - 审计页/系统信息/模型配置 → ADMIN；审批决定 → APPROVER；订单/供应商 → BUYER；
  - 前端 roles.ts 只做菜单可见性，服务端做最终鉴权（403 → 前端统一错误提示，不破坏现有 UI）。

### P2-2 OpenTelemetry + 指标
- 引入 micrometer-tracing + OTLP（本地可无后端，先打点）：
  - procurement.operation.duration、agent.task.state、outbox.publish.count、decision.lock.contention；
  - SSE/审计/日志统一 trace_id；前端 console 错误与网络失败可关联 trace。
- 指标/日志/trace 三通道由 Web 请求 ID 关联，告警规则：operation failed 率、锁等待、outbox 积压。

## 四、Agent（Python）工程化方案

1. 任务持久化：ai_task 已持久化，补齐 prompt_version、tool_call 明细、cost_ledger 的 Python 侧写入与 Java 只读投影对齐（契约见 P1-2）。
2. 上下文压缩：超过阈值（如 60k tokens）触发 summarize 压缩，写 context_compact 事件；前端运行审计展示压缩历史。
3. 失败恢复：错误分类器（可重试/需补充/需接管）输出结构化 recovery_advice，Java 决策动作；人工接管后 Agent 暂停自动重试。
4. 结构化输出：响应 JSON Schema 校验（现已有 parser_version），增加 provider 无关的 schema 注册表；校验失败自动降级为解析器修复而非整 Run 失败。
5. 证据链：每个 tool call 记录 arguments/result sha256（现已有部分），与 Java audit_event 通过 run_id/task_id 关联，形成端到端证据链。
6. 可观测性：Python 侧同样打 OTel（provider 调用时延、token 消耗、解析成功率），与 Java trace 同 ID。

## 五、数据库与接口兼容性影响

- 不破坏：现有 V1–V11 表结构、Kafka topic、HTTP 契约、冻结评测资源一律不动；
- 新增：V12 迁移（prompt_version / tool_call / cost_ledger / context_compact / outbox_event / 权限表），全部可空或带默认值；
- 接口：全部新增端点；现有端点仅增加幂等注解（行为兼容）；
- 回滚：新表不影响旧代码运行（Java 侧按 feature flag 渐进启用）。

## 六、测试策略

| 层级 | 内容 |
|---|---|
| 单元 | 状态机流转表全路径（含非法流转 409）、幂等注解（同键/异载荷）、审计切面、规则校验、成本账本 BigDecimal 精度 |
| 集成 | @SpringBootTest + Testcontainers MySQL：Flyway 迁移、Outbox 发布+消费幂等、Agent RPC 契约（get_reference_prices 等） |
| 端到端 | 现有 Playwright 用户旅程（新建→复核→比价→审批→订单→对账→付款）扩展为 CI 回归，覆盖 API 失败注入（500/503/超时）与刷新/后退 |
| 评测 | 冻结评测资源（frozen-evaluation.json / -ext.json）在每次 Agent 改动后跑全量，保证 617/620、31/31 等指标不回退 |

## 七、落地顺序建议

1. P0-1/P0-2（状态机+幂等）→ 2. P0-3（审计切面）→ 3. P1-1（Outbox+事件驱动订单派生）
→ 4. P1-2/P1-3（Agent 持久化+恢复）→ 5. P1-4（结构化校验）→ 6. P2（RBAC+OTel）
每步独立可发布，回滚粒度为单功能。

