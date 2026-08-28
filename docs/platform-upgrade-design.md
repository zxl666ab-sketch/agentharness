# 采价台 → AI 智能采购平台：含金量升级设计文档

> 目标读者：求职者本人（审核人）+ goal 模式执行 Agent（施工方）
> 状态：待审核（v1.1，对抗式审查修订版）
> 关联简历：候选人 · AI 应用后端开发 · 27 届
> 修订记录：v1.1 修复审查发现的 P0 三项（基线编译失败/路径冲突/演示数据缺失）、P1 两项（审计表约束矛盾/订单派生并发）、补契约与确定性检查纪律

---

## 0. 一句话目标

在**不破坏现有 AI 采购闭环**的前提下，把「采价台」升级为 **AI 智能采购平台**：补传统 Java 项目的含金量四件套（Redis 深度、经典并发难题、业务闭环、量化结果），同时保住并放大独有差异化（Java 业务主机 + Python Agent 微服务 + Kafka 消息可靠性 + 确定性比价评测），最终让简历、README、GitHub 三者叙事一致，面试官 3 分钟看懂含金量。

## 1. 现状盘点（已核实，2026-08）

| 维度 | 现状 | 面试价值 |
|---|---|---|
| Java 后端 | Spring Boot 4.1 / Java 21，Flyway V1–V7，领域建模完整 | ✅ |
| 微服务 | Java 业务主机 + Python Agent，Kafka 唯一通道（HMAC 签名 + 双侧幂等 + DLQ + SASL） | ✅ 超标 |
| 审批闭环 | 一次性 Approval（pending_decision → Agent approval → decision），乐观锁 version，幂等表 | ✅ 但只是"单级审批" |
| 审计 | procurement_audit_event 全量事件，task_id NOT NULL | ✅ 但只能挂在任务下 |
| Redis | 仅任务上下文缓存（RedisTaskContextCache） | ❌ 深度不足 |
| 经典难题 | 幂等✅ 乐观锁✅；分布式锁❌ 缓存三兄弟❌ 超时任务❌ 并发状态流转❌ | ❌ 缺 |
| 业务闭环 | 需求→报价→比价→审批→订单 Artifact；缺供应商档案/订单状态流转/收货/对账/付款/绩效/多角色视角 | ⚠️ 前半段完整，后半段缺失 |
| AI 差异化 | 报价解析（Python）、确定性比价、冻结评测（617/620、31/31） | ✅✅ 独有 |
| 前端 | React 工作台 4 个视图（工作台/采购任务/AI 任务/人工审核） | ⚠️ 入口少、单一角色视角 |
| 简历/README | 简历写的是旧版"Python/FastAPI/SQLite 单体"，与 master 架构严重脱节 | ❌ 最大浪费 |

**结论**：架构底子远超普通学生项目，但"含金量没有变现"——简历描述过时、缺传统高频考点（Redis 深度/并发难题/超时任务）、业务闭环不完整（无收货/对账/付款/绩效/多角色）、平台感不足。

## 2. 目标形态

```
┌─────────────────────────────────────────────────────┐
│ Web 工作台（React，分组导航 + 角色视角 + 管理驾驶舱）  │
│  采购管理：工作台/采购任务/供应商管理/采购订单/         │
│           统计报表/AI 任务/人工审核                    │
│  系统管理：审计日志/系统信息                           │
│  业务全链路：需求→报价→比价→审批→订单→收货→对账→付款   │
│            →供应商绩效（K1/K2/K8 补全闭环）            │
├─────────────────────────────────────────────────────┤
│ Java 业务主机（Spring Boot 4.1）                     │
│  ├─ 业务域：任务 / 报价 / 供应商档案 / 订单状态机 /    │
│  │          对账付款 / 统计聚合 / 审计 / 系统信息       │
│  ├─ 平台能力：幂等、乐观锁、Redis 缓存与分布式锁、      │
│  │          超时调度、审计留痕、角色视图（K9）          │
│  └─ 契约：contracts/ 双语言 OpenAPI + 黄金测试        │
├─────────────────────────────────────────────────────┤
│ Kafka 唯一通道（commands/results/rpc/events + DLQ）  │
├─────────────────────────────────────────────────────┤
│ Python Agent 微服务（解析/结构化/评测）               │
└─────────────────────────────────────────────────────┘
```

## 3. 含金量加码清单（全部落地的验收标准）

| # | 加码项 | 对应面试考点 | 工作量 |
|---|---|---|---|
| K1 | 供应商档案 CRUD + 与报价/中标记录自动关联 + **绩效评分**（中标率/活跃度/合作状态派生） | 领域建模、CRUD 分层、外键与删除保护 | 1 周 |
| K2 | 采购订单状态机（待发货→已发货→已收货→已关闭）+ 乐观锁防并发 + 超时调度（未发货逾期提醒） | 状态机设计、并发控制、定时任务 | 1 周 |
| K3 | 统计报表：状态漏斗/月度趋势/供应商中标排行/品类分布/AI 评测指标 + **成本节约率** | 聚合查询、指标口径、BigDecimal 精度 | 3 天 |
| K4 | Redis 分布式锁（审批防并发重复提交）+ 比价快照/看板缓存 | 分布式锁、缓存一致性、缓存三兄弟 | 2 天 |
| K6 | 审计日志全局页 + 系统信息页（版本/组件/解析器/规则集/模型脱敏状态） | 可观测性、运维 | 2 天 |
| K7 | 简历 + README + GitHub 叙事重构（面试官视角架构图） | 表达 | 1 天 |
| K8 | **对账与付款**：收货后生成对账单（未对账→已对账→已付款），付款记录与审计 | 业务全链路、状态机复用、金额精度 | 3 天 |
| K9 | **多角色视角 + 待办中心**：采购员/审批人/管理员三种演示角色切换，待办中心按角色聚合（待我审批/待收货/逾期订单/AI 异常）；工作台升级为**管理驾驶舱**（成本节约率/待办/风险一屏概览） | 角色与视图设计、需求分析 | 3 天 |

**明确不做**（防止范围蔓延）：不做登录/权限体系（本地单机工具，角色为演示视角，README 如实说明）；不改 Python 解析核心算法；不引入向量数据库；不做多业务 OA 平台（报销/合同等业务线本期不做，仅保留"平台可扩展"叙事）。

## 4. 关键设计冻结（Agent 施工时不得偏离）

### 4.1 表结构新增（Flyway V8–V11，遵守现有命名与类型惯例）

```sql
-- V8__supplier_domain.sql
CREATE TABLE supplier (
    id varchar(32) PRIMARY KEY,
    name varchar(300) NOT NULL UNIQUE,
    contact_person varchar(100),
    phone varchar(50),
    email varchar(150),
    address varchar(500),
    main_categories varchar(500),          -- 主营品类，逗号分隔
    status varchar(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','PAUSED','BLACKLISTED')),  -- 合作中/暂停/黑名单
    notes varchar(1000),
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- V9__purchase_order_domain.sql
CREATE TABLE purchase_order (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    order_no varchar(64) NOT NULL UNIQUE,     -- PO-YYYYMMDD-XXXX
    supplier_name varchar(300) NOT NULL,
    item_name varchar(200) NOT NULL,
    quantity decimal(60,18) NOT NULL CHECK (quantity > 0),
    unit varchar(50) NOT NULL,
    landed_total decimal(60,18),              -- 到货总价（含税运费汇率）
    status varchar(30) NOT NULL DEFAULT 'PENDING_SHIPMENT'
        CHECK (status IN ('PENDING_SHIPMENT','SHIPPED','PARTIALLY_RECEIVED','RECEIVED','CLOSED')),
    received_quantity decimal(60,18),
    arrival_date datetime(6),
    notes varchar(1000),
    version bigint NOT NULL DEFAULT 0,        -- 乐观锁
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX idx_purchase_order_status ON purchase_order(status, updated_at);
CREATE INDEX idx_purchase_order_task ON purchase_order(task_id, created_at);
CREATE UNIQUE INDEX uq_purchase_order_task ON purchase_order(task_id);   -- 正式决定订单最终防重

-- V10__settlement_domain.sql（K8 对账付款；对抗审查修订 D4：财务数据禁止级联删除）
CREATE TABLE purchase_settlement (
    id varchar(32) PRIMARY KEY,
    order_id varchar(32) NOT NULL REFERENCES purchase_order(id) ON DELETE RESTRICT,  -- RESTRICT：财务记录不可随订单级联消失
    settlement_no varchar(64) NOT NULL UNIQUE,   -- ST-YYYYMMDD-XXXX
    supplier_name varchar(300) NOT NULL,
    total_amount decimal(60,18) NOT NULL,        -- 与订单 landed_total 一致（NULL 时禁止派生，见 4.3）
    status varchar(30) NOT NULL DEFAULT 'UNSETTLED'
        CHECK (status IN ('UNSETTLED','SETTLED','PAID')),
    paid_at datetime(6),
    notes varchar(1000),
    version bigint NOT NULL DEFAULT 0,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX idx_purchase_settlement_status ON purchase_settlement(status, updated_at);
CREATE INDEX idx_purchase_settlement_order ON purchase_settlement(order_id);

-- V11__audit_event_generic_business.sql（对抗审查新增，P1 修复）
-- 现有 procurement_audit_event.task_id 是 NOT NULL，而供应商/对账等事件没有任务上下文，
-- 直接插入会违反约束。迁移：task_id 改可空 + 增加通用业务对象定位列（旧行 task_id 保留）。
ALTER TABLE procurement_audit_event
    MODIFY task_id varchar(32) NULL;
ALTER TABLE procurement_audit_event
    ADD COLUMN business_type varchar(30) NULL,   -- supplier / order / settlement / task
    ADD COLUMN business_id varchar(32) NULL;
CREATE INDEX idx_procurement_audit_business ON procurement_audit_event(business_type, business_id, created_at);
```

> **审计写入纪律**：新事件（supplier_created、order_created、settlement_paid 等）必须填 `business_type`/`business_id`；task 事件保持 task_id + business_type='task'。`AuditEvent.create` 工厂方法签名保持向后兼容（新增重载，不动现有调用点）。

### 4.2 通用状态机引擎（对抗审查新增 D1 修复——平台叙事落地的关键）

**问题**：原设计各状态机手写枚举+if-else，"平台可扩展"叙事没有代码支撑。冻结为**注册式通用状态机引擎**：

```java
// 新增 platform/statemachine 包（新代码，不动现有任务状态机）
public final class StateMachine<S, E> {
    // 注册：状态、事件、合法流转、动作钩子
    public static <S, E> Builder<S, E> define(Class<S> states, Class<E> events);
    public void transition(String businessId, S from, E event, Map<String, Object> args);
    // 非法流转抛 IllegalStateTransition(409)；并发由调用方乐观锁兜底
}
```

- **注册表**：`OrderStateMachine`（PENDING_SHIPMENT/SHIPPED/PARTIALLY_RECEIVED/RECEIVED/CLOSED）、`SettlementStateMachine`（UNSETTLED/SETTLED/PAID）通过 `StateMachineRegistry` 注册；业务代码只声明"从哪个状态+什么事件→到哪个状态"，引擎统一校验与审计
- **面试叙事**：新增业务（如报销）只需定义自己的状态枚举+注册流转表，引擎与审计复用——"平台"有代码证据
- 现有 `TaskStatus` 状态机**不迁移**（冻结，避免动审批证据链），README 如实说明"任务状态机为历史实现，新业务走引擎"

### 4.3 订单状态机（K2，冻结定义）

```
PENDING_SHIPMENT --ship--> SHIPPED --receive(batch)--> PARTIALLY_RECEIVED
       |                                              | receive(batch, 累计收满)
       |                                              v
       |_________________________________________ RECEIVED --close--> CLOSED
```

- 合法流转：`ship`（待发货→已发货）、`receive`（已发货/部分收货→登记一批到货，必填本批 received_quantity 与 arrival_date；累计小于订单量保持部分收货、累计等于订单量才转已收货、累计超量返回 409 `quantity_exceeded`）、`close`（待发货→已关闭=**取消**，可填 notes；已收货→已关闭=**完成**，可填 notes）
- `close` 语义冻结：PENDING_SHIPMENT 关闭=取消（不派生对账单）；RECEIVED 关闭=完成（对账单已派生，正常流转）
- 非法流转一律 409 Conflict，错误码 `invalid_order_transition`
- **并发控制**：`UPDATE purchase_order SET status=?, version=version+1 WHERE id=? AND version=?`，影响行数为 0 时返回 409 `order_concurrent_modification`，前端提示刷新
- **超时调度**：`@Scheduled(fixedDelay=60s)` 扫描 `PENDING_SHIPMENT` 且 `updated_at < now()-7d` 的订单，写审计事件 `order_shipment_overdue`（幂等：同一订单只写一次，用 order_id 去重）
- **分批收货与对账派生（K8/V16）**：每批数量累计到订单 `received_quantity`；只有累计收满、订单流转到 `RECEIVED` 时才生成对账单 `purchase_settlement`（金额=订单 landed_total）。**landed_total 为 NULL 时禁止完成收货及派生**，返回 409 `settlement_requires_cost`（先补录成本）；状态机：`UNSETTLED --settle--> SETTLED --pay--> PAID`，`pay` 必填 paid_at；每次流转写审计事件；同一订单只允许一张对账单（order_id UNIQUE）
- **付款逾期调度（对抗审查新增）**：`@Scheduled(fixedDelay=60s)` 扫描 `SETTLED` 且 `updated_at < now()-7d` 的对账单，写审计事件 `settlement_payment_overdue`（幂等去重）——补上原设计承诺的"付款逾期预警"

### 4.4 订单生成（K2，正式决定事务）

订单由 Java 在**正式采购决定完成的同一事务**中生成，审批证据链与订单事实原子提交。派生方式：

- `OrderService.ensureOrderForApprovedTask(task)`：若任务 status=approved 且无对应订单，则用已批准报价 + 比价快照的 landed cost 生成订单（`order_no` 生成规则 `PO-{reference}` 保证唯一）
- 触发点：`ApprovalService.finalizeFromAgent()` 通过版本、generation、快照、资格和重新计算校验后调用订单服务；订单失败时正式决定一并回滚
- **并发安全**：任务悲观锁串行化同任务正式决定，`purchase_order.UNIQUE(task_id)` 作为最终防重；重复 Agent 结果先返回原决定与原订单
- **查询纯度**：`GET /api/procurement/orders` 只读取正式订单和履约投影，不创建订单或审计事件
- 已批准任务的订单**不可删除**，只可流转或关闭（证据链完整性）

### 4.4 供应商档案（K1，冻结设计）

- 供应商 `name` 与 `procurement_quote.supplier_name` **按名称自动关联**（不做外键，报价是历史证据）
- 档案详情聚合：报价次数、中标次数、中标率、合作状态（派生：有中标→合作中；只有报价→待合作；手工置为暂停/黑名单优先）、参与物料清单、最近报价列表（可跳任务）
- **删除保护**：有关联报价的供应商禁止 DELETE（409 `supplier_has_quotes`，提示改状态为暂停/黑名单）；无关联的可物理删除
- 状态变更（暂停/黑名单）写审计事件 `supplier_status_changed`

### 4.6 供应商绩效评分（K1，冻结规则——对抗审查修订 D2）

绩效分 = 派生计算（不落表，实时算），范围 0–100，展示在供应商列表/档案：

```
if status == BLACKLISTED: 绩效分 = min(30, 基础分)   # 黑名单强制封顶，即使中标率高
else:
  基础分 = 中标率得分(0–60) + 活跃度得分(0–20) + 合作状态得分(0–20)
  中标率得分 = win_count / quote_count × 60，但仅当 quote_count ≥ 3 时计入；< 3 次按活跃度折减（×0.5）
  活跃度得分   = min(20, quote_count × 2)（1 次报价=2 分，10 次封顶）
  合作状态得分 = ACTIVE=20 / PAUSED=10 / BLACKLISTED=0（档案手工状态优先）
```

- **最小样本量（D2 修复）**：中标率在报价次数 < 3 时不可信（1/1 与 9/10 不应同分），不足 3 次按 0.5 折减，避免新供应商虚高
- 等级映射：≥80 优质供应商 / 60–79 良好 / 40–59 一般 / <40 待观察；**黑名单等级显示固定为"黑名单"**（不参与等级映射）
- 计算用 BigDecimal 精确到 2 位，前端展示等级徽章；评分口径写入 README（面试可讲"绩效模型设计"，含最小样本与防刷分）

### 4.7 多角色视角与待办中心（K9，冻结设计）

- **不做登录鉴权**（本地单机工具），做"演示角色切换"：右上角角色选择器（采购员/审批人/管理员），存 localStorage，纯前端视角控制
- 角色决定侧边栏可见项与待办中心聚合范围：
  - 采购员：工作台/采购任务/AI 任务/人工审核/供应商/订单/报表
  - 审批人：待审批优先（人工审核/AI 任务/订单收货确认），工作台突出"待我审批"
  - 管理员：全部 + 系统管理（审计日志/系统信息）
- **待办中心（工作台主区升级）**按角色聚合卡片：待我审批（review 状态）、待收货订单（RECEIVED 未关闭）、逾期订单（overdue 标记）、AI 异常、待处理供应商（BLACKLISTED 风险提示）
- 前端 `WorkbenchHome.tsx` 扩展为管理驾驶舱：角色名 + 四张待办卡片 + 成本节约率/订单数/供应商数指标条 + 最近任务表（现有保留）

### 4.8 统计报表口径（K3，冻结定义）

- 状态漏斗：按 task.status 分组计数（全部任务）
- 月度趋势：按 `created_at` 月度分组，输出任务数 + 批准金额
- 供应商中标排行：按中标次数/中标率排序
- 品类分布：按 task.category 分组计数
- **成本节约率**：`(Σ预算单价×数量 − Σ批准到货总价) / Σ预算单价×数量`；预算取任务 constraints 的 `max_landed_unit_cost`（已核实存在），无预算的任务不计入；所有金额 BigDecimal，比例保留 4 位
- **口径假设（对抗审查新增 D7）**：`max_landed_unit_cost` 语义为"到货单价上限"，与比价快照 `landed_total_base` 同为基准币种口径；节约率口径写入 README（面试被问"为什么用上限做分母"可答：保守口径，实际节约率不低于此值）
- AI 评测指标：直接复用 `frozen-evaluation.json` 资源（已有，冻结不变）

### 4.9 Redis 分布式锁（K4，冻结设计——对抗审查修订 D6）

- **加锁位置（D6 修复）**：新建 `DecisionLockGuard` 组件，包装在 **Controller 层** `POST /api/procurement/requests/{id}/decision` 调用前——**不修改冻结的 ApprovalService**
- 加锁：key `lock:decision:{taskId}`，SETNX + value=请求标识（UUID）+ 过期 10s；**释放**：`finally` 中 Lua 脚本 `if get(key)==myValue then del(key)`（只释放自己持有的锁，防止误删他人锁）；业务执行超过 10s 锁过期由乐观锁（version）兜底
- 看板/统计接口加缓存：key `cache:insights:{name}`，TTL 60s，失效策略：写操作（新增报价/审批/订单流转/供应商变更）时 `evictInsights()` 主动失效
- 不新增依赖，用现有 Spring Data Redis 客户端 + `StringRedisTemplate`；**禁用**则回退无锁路径（与 NoopTaskContextCache 同思路）
- **面试叙事（三层防护各司其职）**：幂等表防**重放**（同一请求重复提交返回原结果）；乐观锁防**版本覆盖**（任务被修正后旧审批失效）；分布式锁防**并发双写**（两个不同请求同时为同一任务发起 pending_decision）。面试被问"已有幂等为什么还要锁"时按此三层回答

### 4.10 历史报价 RAG（K5，已下线）

> 历史报价 RAG 已随 814a90e 清理 Python 死代码而整体下线：`get_reference_prices` RPC、`ReferencePriceService`、`frozen-evaluation-ext.json` 与相关文档均已移除；当前项目不再提供历史成交参考区间软提示。

### 4.10 前端导航与页面（K1/K2/K3/K6/K8/K9，冻结设计）

- `workbenchUrl.ts`：WORKBENCH_VIEWS 扩展为 `workbench | tasks | ai | reviews | suppliers | orders | reports | audit | system`
- `WorkbenchNavigation.tsx`：分组菜单——采购管理（工作台/采购任务/供应商管理/采购订单/统计报表/AI 任务/人工审核）+ 系统管理（审计日志/系统信息）；**角色选择器**（右上角，localStorage 持久化）按 K9 规则控制可见项
- 新页面组件（沿用现有 React Query + CSS 体系，无新依赖）：
  - `SupplierCenter.tsx`：列表（搜索/状态筛选/绩效等级徽章）+ 新建/编辑弹窗 + 删除确认 + 档案聚合（报价/中标/绩效，点击跳任务）
  - `OrderCenter.tsx`：订单列表（状态筛选）+ 状态操作按钮 + 收货弹窗（数量/日期，超收校验）+ 对账付款操作（对账单列表/settle/pay）+ 关闭确认 + **供应商确认邮件附件下载**（复用 Artifact 接口）
  - `ReportsCenter.tsx`：统计报表（漏斗/趋势/排行/品类/成本节约率/AI 评测）
  - `AuditLogCenter.tsx`：全局审计日志（类型/操作人/任务筛选，分页）
  - `SystemInfo.tsx`：系统信息（版本/组件状态/解析器/规则集/模型脱敏状态）
- 现有 4 个核心页面逻辑**不改**，只改导航容器与工作台（驾驶舱化）

### 4.11 新接口清单（全部挂 /api/procurement 下，遵循现有错误码/幂等惯例）

```text
GET  /api/procurement/suppliers?q=&status=&page=&size=      # K1
POST /api/procurement/suppliers                             # K1
PUT  /api/procurement/suppliers/{id}                        # K1（含 status 变更）
DELETE /api/procurement/suppliers/{id}                      # K1（删除保护）
GET  /api/procurement/suppliers/{id}/profile                # K1 档案聚合（含关联报价/中标）
GET  /api/procurement/orders?status=&page=&size=            # K2（只读列表）
GET  /api/procurement/orders/{id}                           # K2 详情
POST /api/procurement/orders/{id}/transition                # K2 状态流转（Idempotency-Key + body）
GET  /api/procurement/settlements?status=&page=&size=       # K8 对账单列表
POST /api/procurement/settlements/{id}/transition           # K8 对账流转（Idempotency-Key + body）
GET  /api/procurement/insights/overview                     # K3 漏斗+成本节约率
GET  /api/procurement/insights/trend?months=6               # K3 月度趋势
GET  /api/procurement/insights/supplier-ranking             # K3 中标排行（含绩效分）
GET  /api/procurement/insights/categories                   # K3 品类分布
GET  /api/procurement/audit-events?type=&actor=&business_type=&task_id=&page=&size=  # K6（已有雏形，扩展筛选）
GET  /api/procurement/platform                              # K6 系统信息
```

### 4.12 审计事件扩展（K6/K8）

- 新增事件类型：`supplier_created/supplier_updated/supplier_status_changed/supplier_deleted`、`order_created/order_transitioned/order_shipment_overdue`、`settlement_created/settlement_settled/settlement_paid/settlement_payment_overdue`
- 审计写入按 V11 迁移后的 `business_type/business_id` 定位（见 4.1 审计写入纪律），task 事件沿用 task_id；审计查询接口按 `business_type` 过滤（K6 审计页）

## 5. 阶段划分与验收标准（goal 模式按阶段执行，每阶段独立验收）

> 施工纪律：每个阶段结束跑全套测试 + 提交 commit；任何阶段未验收不得进入下一阶段。
> 全局纪律（每个阶段都适用）：① 新接口必须同步 `contracts/procurement-workbench-openapi.yaml` + `procurement-workbench.schema.json` + 黄金契约，跑 `npm test`（contracts.test.ts）与 `FrozenComparisonContractTest`；② 前端改动后必须 `npm run build` 并同步 Java static bundle，跑 `scripts/check_web_build_determinism.py`；③ Java 集成测试依赖 Testcontainers（Docker Desktop 需可用）；④ 演示数据一律标记 synthetic，不得混入冻结评测。

**阶段总览（估算总工作量 4~5 周，按每天 3~4 小时折算约 6~8 周；K1/K2/K8/K9 先行的理由：业务闭环完整度是"管理系统感"的核心，传统 Java 项目含金量考点优先）**

| 阶段 | 内容 | 工作量 |
|---|---|---|
| 0 | **基线修复（对抗审查新增）**：清 BOM 修复编译、处置遗留 management 包、演示数据扩展 | 1 天 |
| 1 | 供应商 CRUD + 绩效 + 导航分组 + 角色选择器（K1/K9 前半） | 1 周 |
| 2 | 订单状态机 + 对账付款 + 超时调度（K2+K8） | 1 周 |
| 3 | 报表 + 成本节约率 + 审计页 + 系统信息 + 驾驶舱待办中心（K3+K6+K9 后半） | 3 天 |
| 4 | Redis 分布式锁 + 缓存治理（K4） | 2 天 |
| 5 | 简历 + README + GitHub 叙事重构（K7） | 1 天 |

### 阶段 0：基线修复（对抗式审查新增，必须先做）

**问题背景**：审查发现当前 master 存在三项阻断性隐患，任何后续阶段都会踩到：
1. **Java 编译失败**：遗留 `management/ProcurementInsightsController.java` 与 `ProcurementInsightsService.java` 带 UTF-8 BOM（`\ufeff`），`mvnw test-compile` 直接报错
2. **路由冲突**：遗留 `ProcurementInsightsController` 已映射 `GET /api/procurement/suppliers`、`/orders`、`/audit-events`，与 K1/K2 新控制器路径冲突（Spring 启动 ambiguous mapping）
3. **演示数据缺失**：`DemoSeedRunner` 只把任务 seed 到 READY 状态，**不生成 approved 决策**——导致 K2 订单派生、K3 成本节约率在 demo 模式下全部无数据，验收截图会是空的

**交付**：
- 处置遗留 management 包：删除 `ProcurementInsightsController/Service`（其能力由 K1/K2/K3 新控制器按冻结接口重建；`audit-events` 查询能力并入 K6 审计页，接口保持 `GET /api/procurement/audit-events` 兼容）
- 演示数据扩展：`DemoSeedRunner` 新增"历史业务种子"——在现有 3 套场景基础上，额外生成 2~3 套**已走完审批闭环的 synthetic 任务**（含 approved 决策、已生成订单、部分已收货/已对账/已付款），全部标记 synthetic，用 demo-seed actor 写审计，复用现有 evidence 机制；目标：打开订单页/报表页/供应商档案时数据不为空
- 修复 BOM 问题（两个遗留文件删除后自然解决；若保留任何文件需转 UTF-8 无 BOM）
- 验收：`.\mvnw.cmd test` 全绿（基线回归）+ demo seed 启动后 `GET /api/procurement/orders` 返回非空 synthetic 订单

### 阶段 1：供应商档案 CRUD + 绩效评分（K1）
- 交付：V8 迁移、Supplier 实体/仓储/服务/控制器、档案聚合（报价/中标/绩效分）、删除保护、Web 列表+弹窗+导航分组+角色选择器
- 验收：`.\mvnw.cmd test` 全绿（新增 Supplier 单测，含绩效分规则与删除保护）；`npm test`、`npm run lint`、`npm run build` 全绿；Playwright 无头验收截图（列表/新建/编辑/删除保护/绩效徽章）

### 阶段 2：采购订单状态机 + 对账付款 + 超时调度（K2 + K8，含 D1 状态机引擎）
- 交付：V9/V10 迁移、StateMachine 引擎 + Order/Settlement 状态机注册、Order/Settlement 实体/仓储/服务、乐观锁、正式决定事务内订单生成（UNIQUE 防重）、幂等流转、发票核销门禁、发货/付款双超时调度、Web 履约中心
- 验收：状态机引擎单测（注册/非法流转/钩子）、订单合法/非法流转测试、并发流转测试（version 冲突）、超收校验测试（received_quantity > quantity 拒绝）、派生幂等测试（并发双请求仅一条）、对账派生/流转测试（含无成本拒绝）、双超时调度测试（clock 注入）；Playwright 截图

### 阶段 3：统计报表 + 审计全局页 + 系统信息 + 导航收尾（K3 + K6）
- 交付：insights 聚合接口（含成本节约率）、审计筛选扩展、platform 接口、Reports/AuditLog/System 三页面、工作台驾驶舱化 + 待办中心（K9 角色聚合）
- 验收：聚合单测（含 BigDecimal 精度、无预算任务跳过）；Web 测试（角色切换/待办聚合）；Playwright 截图

### 阶段 4：Redis 分布式锁 + 缓存治理（K4）
- 交付：审批分布式锁、insights 缓存与失效、Noop 回退
- 验收：锁竞争测试（并发审批请求只有一个成功）、缓存失效测试；无 Redis 时回退测试

### 阶段 5：简历 + README + GitHub 叙事重构（K7）
- 交付：README 新增"面试官视角架构图"小节、简历项目描述替换（双语言架构版）、GitHub 截图更新
- 验收：README 通读 3 分钟可理解架构与差异化；简历与 README 技术栈一致

## 6. 风险与回退

| 风险 | 等级 | 对策 |
|---|---|---|
| 审批证据链被破坏 | 高 | 冻结 ApprovalService/ComparisonEngine 核心逻辑；订单派生走独立 reconcile，不注入审批流程；分布式锁加在 Controller 层（DecisionLockGuard），不碰 Service |
| 平台叙事与设计脱节 | 高（已处置） | 新增通用状态机引擎（4.2），订单/对账注册式实现；任务状态机不迁移并在 README 如实说明 |
| 绩效公式小样本失真 | 中（已处置） | 最小样本量 ≥3 + 黑名单封顶 30（4.6） |
| Flyway 迁移破坏现有库 | 中 | V8–V11：V8/V9/V10 只新增表；**V11 是唯一的 ALTER（audit_event 改可空+加列），先在 demo 库验证后在生产库执行**；失败回退=删迁移文件+重建数据卷 |
| 遗留 management 包与 BOM | 高（已处置） | 阶段 0 删除遗留控制器/服务（BOM 文件 + 路径冲突一并解决），能力由新控制器按冻结接口重建 |
| 演示数据为空（订单/报表无数据） | 高（已处置） | 阶段 0 扩展 DemoSeedRunner 生成 synthetic 已审批历史业务（决策+订单+对账+付款），全部标记 synthetic |
| 审计表 task_id 约束与业务事件矛盾 | 高（已处置） | V11 迁移改可空 + business_type/business_id 列，AuditEvent.create 新增重载保持兼容 |
| 正式决定并发双写 | 高（已处置） | 任务悲观锁 + 决定/订单同事务 + purchase_order UNIQUE(task_id) |
| 财务数据级联删除 | 中（已处置） | purchase_settlement 外键 RESTRICT，禁止随订单级联消失 |
| 前端路由破坏 | 低 | 现有 URL 兼容（view 缺省回退 workbench）；只加不改 |
| 锁/缓存引入不稳定 | 低 | Noop 回退路径保留；锁释放 Lua 校验持有者；缓存 TTL 短（60s） |
| Web 静态 bundle 不同步 | 中 | 每个前端阶段跑 check_web_build_determinism.py，build 后同步 Java static |

## 7. goal 模式执行说明（写给执行 Agent）

- 每个阶段一个 goal 回合目标，objective 形如：`按 docs/platform-upgrade-design.md 第 5 节"阶段 N"完成交付并跑通该阶段验收标准（mvnw test / npm test / lint / build / 契约回归），完成后报告完成项与测试结果`
- 阶段内允许自决的实现细节：包名（遵循现有分层）、DTO 形状（遵循现有 Dtos 惯例）、前端组件命名（遵循现有 .tsx 惯例）
- 阶段内**不允许**自决：表结构字段、状态机定义、接口路径、口径公式、冻结的现有文件
- 每阶段结束必须输出：完成项清单 / 测试命令与结果 / 新增文件清单 / 未完成项与原因 / 风险提示
