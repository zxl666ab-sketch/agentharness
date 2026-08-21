# Governance & Analytics Centers Architectural Analysis & Modernization Blueprint

**Explorer**: Explorer 3 (Milestone 2 — Governance & Analytics Centers)  
**Date**: 2026-08-20  
**Target Working Directory**: `D:\个人通用agentharness\web`  
**Target Components**:
1. `src/procurement/ReviewCenter.tsx` (`view=reviews` / `review={id}`)
2. `src/procurement/ReportsCenter.tsx` (`view=reports`)
3. `src/procurement/AuditLogCenter.tsx` (`view=audit`)
4. `src/procurement/AiTaskCenter.tsx` (`view=ai` / `ai={id}`)

---

## 1. Executive Summary & Component Scope

The **Governance & Analytics Centers** represent the core compliance, diagnostic, decision review, and intelligence reporting layer of AgentHarness. They provide deterministic auditability, human-in-the-loop oversight over AI proposals, analytics into procurement cost savings, and operational health monitoring of autonomous AI tasks.

```
+---------------------------------------------------------------------------------------------------+
|                            AgentHarness Governance & Analytics Centers                           |
+---------------------------------+---------------------------------+-------------------------------+
| 1. ReviewCenter (人工审核)       | 2. ReportsCenter (统计报表)      | 3. AuditLogCenter (审计日志)  |
| - High-Risk Triage Queue        | - 4 Core KPI Summary Cards      | - 16 Business Event Types     |
| - Immutable AI Advice           | - Dynamic Status Funnel         | - Actor, Type, Task Filters   |
| - Comparison Snapshot & Hashes  | - 6-Month Volume/Amount Trend   | - Paginated Audit Trail       |
| - 4 Decision Actions & Gates    | - Supplier Win Leaderboard      | - Monospace Scopes & Times    |
| - Checkbox Confirmation Modal   | - Category Distribution         | - Error & Empty State Guards  |
| - Finalized Decision Read-Only  | - Frozen AI Evaluation Proof    |                               |
+---------------------------------+---------------------------------+-------------------------------+
| 4. AiTaskCenter (AI 任务中心 / 诊断工作台)                                                         |
| - Execution State Diagnostic Panel & Progress Bar                                                 |
| - Gated Retry & Two-Step Cancellation Workflow ("再次点击确认取消")                               |
| - Chronological Execution Steps Timeline (6 Canonical AI Steps)                                   |
| - Dual Structured & Raw JSON Inspectable Payloads                                                 |
| - Source Artifact Fingerprints with Confidence Ratings & Deep Links                               |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Detailed Component Breakdown & Functional Workflows

### 2.1 `ReviewCenter.tsx` (Human Review & Governance Center)

#### 1. Purpose & Business Workflow
Handles human-in-the-loop triage for high-risk procurement decisions (e.g. low confidence extractions, conflicting fields, no eligible quotes, or policy exceptions).

#### 2. State Machine & Review Lifecycle
- **Statuses (`ReviewStatus`)**:
  - `PENDING` ("待审核"): Active in review queue; awaiting human decision.
  - `APPROVED` ("已批准"): Human approved (either AI suggestion or revised quote).
  - `REJECTED` ("已驳回"): Rejected by human and sent back to analysis.
  - `NO_AWARD` ("已流标"): Formal no-award decision recorded; closed without order generation.
  - `STALE` ("已过期"): Underlying RFQ inputs or quote files changed; submission blocked.
- **Review Actions (`ReviewAction`)**:
  1. `APPROVE_SUGGESTION` ("确认 AI 建议"): Accepts AI recommended supplier directly.
  2. `REVISE_AND_APPROVE` ("修改后通过"): Selects an alternative eligible supplier quote.
  3. `REJECT_AND_RETRY` ("驳回重跑"): Rejects current analysis and triggers agent re-evaluation.
  4. `NO_AWARD` ("本轮流标"): Marks RFQ as no-award (only permitted when `hasEligibleQuotes === false`).
- **Risk Flags (`RISK_LABELS`)**:
  - `NO_ELIGIBLE_QUOTES` ("无合格报价")
  - `UNRESOLVED_FIELDS` ("字段待复核")
  - `INSUFFICIENT_QUOTES` ("报价不足")
  - `LOW_CONFIDENCE` ("低置信度")
  - `CONFLICTING_FIELDS` ("字段冲突")

#### 3. Critical Interactive & Gated Behaviors
- **Form Validation Gating**:
  - `actor.trim()` must not be empty (default `"采购员"`).
  - When `action !== "APPROVE_SUGGESTION"`, `reason.trim()` is mandatory.
  - When `action === "REVISE_AND_APPROVE"`, a valid alternative eligible quote must be selected.
  - When `action === "NO_AWARD"`, `hasEligibleQuotes` must be `false` (disabled otherwise).
- **Two-Step Checkbox Confirmation Modal**:
  - Clicking `"提交审核"` opens `[role="dialog"]` with title `确认提交：{ACTION_LABELS[action]}`.
  - The confirm button `"确认提交"` remains `disabled` until the checkbox `"我已核对 AI 建议、报价原件与确定性比价证据"` is checked.
  - Modal binds `useEscape`, allowing dismissal via `Escape` key on `window`.
- **Read-Only Finalized State**:
  - When `detail.decision_id` is present or status is terminal (`APPROVED`, `REJECTED`, `NO_AWARD`), the action panel is hidden and replaced by `.proc-review-outcome` with "正式决定已形成" and "人工审核记录".

---

### 2.2 `ReportsCenter.tsx` (Analytics & Evaluation Center)

#### 1. Purpose & Business Workflow
Provides real-time procurement insights, cost savings metrics, supplier performance rankings, status pipeline funnels, and AI evaluation benchmarks.

#### 2. Query Architecture & Data Flow
- `procurement-insights-overview` (refetched every 15s):
  - `counts`: `tasks`, `approved_tasks`, `orders`, `orders_received`, `settlements_paid`, `suppliers`, `suppliers_blacklisted`, `overdue_orders`.
  - `cost_savings`: `rate`, `budget_total`, `landed_total`, `savings`.
  - `status_funnel`: Array of `{ status, count }`.
- `procurement-insights-trend`: 6-month historical trend of task counts and approved amount.
- `procurement-insights-ranking`: Top 10 suppliers by win count, total quotes, and performance score.
- `procurement-insights-categories`: Breakdown by procurement category.
- `procurement-evaluation`: Frozen AI evaluation benchmarks (`frozen-evaluation.json`).

#### 3. Core Visual Sections
1. **4 KPI Metric Cards (`.proc-report-kpis`)**:
   - `成本节约率`: Formatted rate (`(rate * 100).toFixed(2)%`), budget, landed cost, savings.
   - `已批准任务`: Count of approved vs total.
   - `采购订单`: Total orders, received count, paid settlements count.
   - `供应商`: Total suppliers, blacklisted count, overdue orders count.
2. **Reports Grid (`.proc-reports-grid`)**:
   - `状态漏斗`: Horizontal progress bars mapped to status labels and status tones.
   - `月度趋势`: 6-month vertical bar chart with hover tooltips and BigDecimal calculation note.
   - `供应商中标排行`: Leaderboard with rank index (1..10), supplier name, win ratio, performance score, and blacklist warning badge (`danger`).
   - `品类分布`: Category count bars.
3. **AI 评测指标 (`.proc-eval-proof`)**:
   - Evaluation progress bars: `field_extraction` ("字段抽取"), `post_review_fields` ("复核后字段"), `item_matching` ("物料匹配"), `cost_calculation` ("成本计算"), `hard_constraint_miss` ("硬约束漏检率").
   - Error state: `<p className="proc-muted" role="alert">评测数据加载失败</p>`.

---

### 2.3 `AuditLogCenter.tsx` (Audit Trail & Event Log Center)

#### 1. Purpose & Business Workflow
Comprehensive, immutable audit trail logger capturing all business mutations, system seeds, approvals, orders, settlements, and AI snapshots.

#### 2. Event Types & Filter Architecture
- **Supported Event Types (16 Canonical Events)**:
  - `demo_seed_created` ("演示数据预置")
  - `demo_seed_approved` ("演示数据审批")
  - `order_created` ("订单派生")
  - `order_transitioned` ("订单流转")
  - `order_shipment_overdue` ("发货逾期")
  - `settlement_created` ("对账派生")
  - `settlement_settled` ("对账确认")
  - `settlement_paid` ("付款登记")
  - `settlement_payment_overdue` ("付款逾期")
  - `supplier_created` ("供应商建档")
  - `supplier_updated` ("供应商更新")
  - `supplier_status_changed` ("供应商状态变更")
  - `supplier_deleted` ("供应商删除")
  - `supplier_approval_requested` ("审批请求")
  - `procurement_decision_finalized` ("审批终决")
  - `comparison_snapshot_created` ("比价快照")
- **Filter Toolbar (`.proc-toolbar` / `role="toolbar"`)**:
  - `aria-label="事件类型"`: Search by raw event type string.
  - `aria-label="操作人"`: Filter by actor username/role.
  - `aria-label="业务对象类型"`: Select dropdown (`""` (全部业务对象), `"task"`, `"supplier"`, `"order"`, `"settlement"`).
  - `aria-label="任务ID"`: Filter by specific `task_id`.
- **Paginated Event Stream**:
  - Pagination controls (`.proc-task-pagination`): Previous (`aria-label="上一页"`), Page index `{page + 1} / {totalPages}`, Next (`aria-label="下一页"`).
  - Row elements: `.proc-audit-type`, `<code>{event.event_type}</code>`, `.proc-audit-scope`, `.proc-audit-actor`, `<time>`.

---

### 2.4 `AiTaskCenter.tsx` (AI Task Diagnostic & Monitoring Center)

#### 1. Purpose & Business Workflow
Diagnostic mission control for asynchronous AI agent pipelines (parsing, specification extraction, multi-quote ruleset analysis, and explanation generation).

#### 2. Status Lifecycle & Step Definitions
- **Statuses (`AiTaskStatus`)**:
  - `PENDING` ("等待调度")
  - `DISPATCHING` ("正在投递")
  - `RUNNING` ("正在分析")
  - `SUCCEEDED` ("已成功")
  - `FAILED` ("失败")
  - `RETRYING` ("重试中")
  - `CANCELLED` ("已取消")
- **Canonical Execution Steps (`STEP_LABELS`)**:
  - `INPUT_VALIDATE` ("输入校验")
  - `ARTIFACT_FETCH` ("读取资料")
  - `QUOTE_PARSE` ("核对报价")
  - `RULE_ANALYSIS` ("规则分析")
  - `EXPLANATION` ("生成解释")
  - `RESULT_PUBLISH` ("发布结果")

#### 3. Interactive & Recovery Actions
- **Gated Retry Action**:
  - Button text: `"重试"`.
  - Gated condition: `detail.status === "FAILED" && detail.retryable && !detail.stale && detail.retry_count < (detail.max_retries ?? 3)`.
  - Tooltip: `title="仅可重试未过期的可重试失败任务"` (disabled) or `title="重试任务"` (enabled).
  - Error recovery: If retry returns 409 conflict, `.proc-inline-error` displays the error message and re-enables the retry button.
- **Two-Step Cancellation**:
  - 1st click: Sets `confirmCancel = true`, button label becomes `"再次点击确认取消"` (with 4-second auto-reset timer).
  - 2nd click: Dispatches cancel request to `/api/procurement/ai-tasks/{id}/cancel`.
- **Diagnostic Execution Timeline & JSON Inspection**:
  - `.proc-step-timeline`: Chronological list of recorded attempts, step labels, summaries, duration in ms, and timestamps.
  - `.proc-result-summary`: Structured AI summary callout with Bot icon.
  - `.proc-result-meta`: Displays `prompt_version`, `parser_version`, `input_sha256`, `result_sha256`.
  - Collapsible JSON inspector: `<details open><summary>结构化结果</summary><pre>...</pre></details>` and `<details><summary>原始结果</summary><pre>...</pre></details>`.
  - `.proc-source-list`: Source artifact cards with confidence percentage badges and raw artifact viewer links.

---

## 3. Test Invariant & Compatibility Contract (R4 / F10)

The following matrix documents **100% of the DOM selectors, CSS classes, ARIA roles/labels, exact Chinese strings, and keyboard shortcuts** asserted across the test suite for these 4 components.

### 3.1 Test Files & Direct Assertions Matrix

| Component | Test File | Test Case # / Name | Asserted Selectors / Content |
|---|---|---|---|
| `AiTaskCenter` | `centers.test.tsx` | Case 1: `shows persisted steps, result versions and source artifacts` | `"AI 任务中心"`, `"已核对两份报价来源"`, `"两份报价均已核对"`, `"quote-analysis-v1"`, `"packaging-quote-v3"`, `"报价明细!B4"`, `"采购详情"` |
| `AiTaskCenter` | `centers.test.tsx` | Case 2: `recovers busy state and shows the error when retry/cancel fails` | Button `"重试"`, `.proc-inline-error` contains `"任务状态已变化"`, button `"取消"` -> `"再次点击确认取消"` -> sends `/cancel` |
| `ReviewCenter` | `centers.test.tsx` | Case 3: `shows immutable AI advice, quote evidence and all four actions` | `"AI 建议"`, `"甲方标签"`, `"乙方标签"`, `"交期超过上限"`, `"确认建议"`, `"修改后通过"`, `"驳回重跑"`, `"流标"`, `"提交审核"` |
| `ReviewCenter` | `centers.test.tsx` | Case 4: `requires a second confirmation before submitting an action` | Button `"提交审核"`, `[role="dialog"]` contains `"确认提交：确认 AI 建议"` and `"我已核对 AI 建议、报价原件与确定性比价证据"` |
| `ReviewCenter` | `centers.test.tsx` | Case 5: `keeps a finalized decision read-only` | `"正式决定已形成"`, `"人工审核记录"`, NOT contains `"提交审核"` |
| `WorkbenchHome` / `ReviewCenter` | `procurement.test.tsx` | Case 23: `home task entries carry the correct destination` | Clicks `"等待确认采购方案"` -> invokes `onOpenView("reviews")` |
| `WorkbenchHome` / `AiTaskCenter` | `procurement.test.tsx` | Case 23: `home task entries carry the correct destination` | Clicks `"AI 任务需处理"` -> invokes `onOpenView("ai")` |
| `WorkbenchHome` / `ReportsCenter` | `procurement.test.tsx` | Case 24: `does not count supplier risks hidden from the approver view` | `.proc-home-section header` contains `"0 项"` |
| `WorkbenchUrl` | `workbenchUrl.test.ts` | Case 1 & 4: `round-trips view, task, tab, filters...` | `?ai=...` -> `view: "ai"`, `?review=...` -> `view: "reviews"` |

---

### 3.2 Preserved DOM IDs & CSS Class Names

#### 1. DOM IDs
- `#review-confirm-title`: Modal dialog title in `ReviewCenter.tsx`.

#### 2. Semantic CSS Class Names (Must be preserved alongside Tailwind classes)
```
ReviewCenter:
.proc-center-page, .proc-page-head, .proc-page-summary, .proc-center-filters, .reviews,
.proc-filter-search, .proc-center-layout, .proc-center-list, .review-list, .proc-queue-status,
.proc-priority, .proc-risk-row, .proc-center-list-meta, .proc-center-empty, .proc-center-detail,
.proc-review-detail, .proc-detail-head, .proc-button, .secondary, .proc-review-banner, .stale,
.pending, .success, .rejected, .proc-detail-section, .ai-advice, .proc-advice-grid,
.proc-review-quotes-wrap, .proc-review-quotes, .eligible, .excluded, .suggested, .proc-eligibility,
.pass, .fail, .proc-quote-reason, .proc-evidence-strip, .proc-review-action-panel,
.proc-review-actions, .proc-review-form, .proc-field, .wide, .proc-inline-error,
.proc-review-outcome, .compact-history, .proc-modal-backdrop, .proc-confirm-dialog,
.proc-confirm-warning, .proc-confirm-supplier, .proc-check, .approval-confirm, .proc-form-error,
.proc-icon-button

ReportsCenter:
.proc-main, .proc-page-head, .proc-reports-body, .proc-report-kpis, .proc-kpi-card,
.proc-reports-grid, .proc-report-section, .proc-loading-line, .proc-funnel, .proc-trend-bars,
.proc-eval-note, .proc-ranking, .proc-rank-no, .proc-categories, .proc-eval-proof, .proc-muted,
.danger, .success, .warning, .info

AuditLogCenter:
.proc-main, .proc-page-head, .proc-page-count, .proc-toolbar, .proc-toolbar-search, .proc-search,
.proc-select, .proc-filter-input, .proc-audit-list, .proc-loading-state, .proc-empty-state,
.compact, .proc-audit-row, .proc-audit-type, .proc-audit-scope, .proc-audit-actor,
.proc-task-pagination, .proc-button, .secondary

AiTaskCenter:
.proc-center-page, .proc-page-head, .proc-page-summary, .proc-center-filters, .proc-filter-search,
.proc-center-layout, .proc-center-list, .proc-queue-status, .proc-center-list-meta,
.proc-center-empty, .danger, .proc-center-detail, .proc-ai-detail, .proc-detail-head, .proc-button,
.secondary, .danger-text, .proc-detail-facts, .proc-ai-state-panel, .failed, .running, .pending,
.dispatching, .retrying, .proc-ai-progress, .proc-inline-error, .proc-detail-section,
.proc-step-timeline, .proc-detail-empty, .proc-result-summary, .proc-result-meta, .proc-result-json,
.proc-source-list, .proc-trace-strip
```

---

### 3.3 ARIA Roles & Accessibility Attributes

| Attribute | Component | Target Element | Purpose / Assertion |
|---|---|---|---|
| `role="alert"` | `ReviewCenter`, `ReportsCenter`, `AuditLogCenter`, `AiTaskCenter` | Error boxes & banners | Displays error messages (`.proc-inline-error`, `proc-review-banner stale`, evaluation error, audit log error) |
| `role="status"` | `ReviewCenter` | Success notification notice | Notice banner after review submission |
| `role="dialog"` | `ReviewCenter` | Confirmation modal section | `aria-modal="true" aria-labelledby="review-confirm-title"` |
| `role="toolbar"` | `AuditLogCenter` | Filter toolbar | Contains filter inputs |
| `role="group"` | `ReviewCenter` | Action button group | `aria-label="审核动作"` |
| `aria-label="人工审核筛选"` | `ReviewCenter` | Filters container | Filter container selector |
| `aria-label="搜索人工审核"` | `ReviewCenter` | Search input | Input selector |
| `aria-label="人工审核列表"` | `ReviewCenter` | List section | List container |
| `aria-label="人工审核详情"` | `ReviewCenter` | Detail section | Detail container |
| `aria-label="关闭"` | `ReviewCenter` | Modal close button | Modal icon button |
| `aria-label="事件类型"` | `AuditLogCenter` | Text input | Type filter input |
| `aria-label="操作人"` | `AuditLogCenter` | Text input | Actor filter input |
| `aria-label="业务对象类型"` | `AuditLogCenter` | Select dropdown | Object type dropdown |
| `aria-label="任务ID"` | `AuditLogCenter` | Text input | Task ID filter input |
| `aria-label="上一页"` | `AuditLogCenter` | Pagination button | Prev page button |
| `aria-label="下一页"` | `AuditLogCenter` | Pagination button | Next page button |
| `aria-label="AI 任务筛选"` | `AiTaskCenter` | Filters container | AI Filter container |
| `aria-label="搜索 AI 任务"` | `AiTaskCenter` | Search input | AI Search input |
| `aria-label="AI 任务类型"` | `AiTaskCenter` | Select dropdown | AI Task type dropdown |
| `aria-label="AI 任务列表"` | `AiTaskCenter` | List section | AI Task list |
| `aria-label="AI 任务详情"` | `AiTaskCenter` | Detail section | AI Task detail |

---

### 3.4 Exact Chinese Strings Inventory

#### ReviewCenter:
- Headings & Counters: `"决策队列"`, `"人工审核"`, `"等待审核"`, `"全部状态"`, `"全部风险"`, `"项审核"`, `"按优先级与等待时间排序"`, `"采购人工审核"`, `"采购详情"`
- Empty & Loading States: `"正在读取审核队列"`, `"当前筛选没有审核事项"`, `"正在读取审核详情"`, `"选择一项审核查看证据和操作"`
- Banners: `"审核证据已过期"`, `"审核动作已提交"`, `"正式决定已形成"`, `"已驳回并返回分析"`
- AI Advice: `"AI 建议"`, `"不可人工覆盖"`, `"建议供应商"`, `"AI 摘要"`, `"模型 / Prompt"`, `"风险"`
- Evidence Table: `"报价与规则证据"`, `"快照 v"`, `"供应商"`, `"资格"`, `"总到货成本"`, `"到货单价"`, `"起订量（MOQ）"`, `"交期"`, `"依据"`, `"通过"`, `"淘汰"`, `"AI 建议"`, `"审核证据"`, `"比价输入"`, `"AI 结果"`
- Actions: `"人工决定"`, `"提交后不可修改"`, `"确认建议"`, `"修改后通过"`, `"驳回重跑"`, `"流标"`, `"审核人"`, `"最终供应商"`, `"审核说明（选填）"`, `"流标原因"`, `"驳回原因"`, `"修改理由"`, `"提交审核"`
- Outcomes & History: `"人工审核记录"`, `"人工动作"`, `"审核历史"`
- Modal: `"确认提交："` + action, `"关闭"`, `"本次操作绑定当前采购版本、AI 结果和比价快照。提交后会写入不可变审核历史；证据变化时服务端会拒绝旧页面提交。"`, `"我已核对 AI 建议、报价原件与确定性比价证据"`, `"确认使用当前证据指纹提交人工决定"`, `"取消"`, `"确认提交"`

#### ReportsCenter:
- Headings: `"统计报表"`, `"状态漏斗 / 月度趋势 / 供应商中标排行 / 品类分布 / 成本节约率 / AI 评测"`
- KPI Cards: `"成本节约率"`, `"已批准任务"`, `"采购订单"`, `"供应商"`
- Sections: `"状态漏斗"`, `"按任务状态分组"`, `"月度趋势"`, `"近 6 个月任务数与批准金额"`, `"供应商中标排行"`, `"按中标次数 / 中标率 + 绩效分"`, `"品类分布"`, `"按任务品类分组"`, `"AI 评测指标"`, `"冻结评测资源（frozen-evaluation.json，不变）"`
- Notes & Alerts: `"批准金额 = 比价快照中批准报价的到货总价（基准币种，BigDecimal 口径）。"`, `"评测数据加载失败"`, `"暂无任务数据"`, `"暂无趋势数据"`, `"暂无中标记录（供应商档案按名称关联报价）"`, `"暂无品类数据"`
- Evaluation Keys: `"字段抽取"`, `"复核后字段"`, `"物料匹配"`, `"成本计算"`, `"硬约束漏检率"`

#### AuditLogCenter:
- Headings: `"审计日志"`, `"全量事件留痕：类型 / 操作人 / 业务对象 / 任务筛选（V11 通用业务定位）"`, `"共 "` + total + `" 条"`
- Filter Options: `"全部业务对象"`
- States: `"正在加载审计日志…"`, `"审计日志加载失败"`, `"未知错误"`, `"重新加载"`, `"没有匹配的审计事件"`, `"调整筛选条件后重试。"`
- 16 Event Labels: `"演示数据预置"`, `"演示数据审批"`, `"订单派生"`, `"订单流转"`, `"发货逾期"`, `"对账派生"`, `"对账确认"`, `"付款登记"`, `"付款逾期"`, `"供应商建档"`, `"供应商更新"`, `"供应商状态变更"`, `"供应商删除"`, `"审批请求"`, `"审批终决"`, `"比价快照"`

#### AiTaskCenter:
- Headings: `"执行队列"`, `"AI 任务中心"`, `"异常任务"`, `"个任务"`, `"采购状态与 AI 状态独立"`, `"采购 AI 任务"`, `"采购详情"`
- Filters: `"全部状态"`, `"全部负责人"`, `"全部时间"`, `"最近 24 小时"`, `"最近 7 天"`, `"最近 30 天"`, `"报价分析"`
- Facts: `"任务类型"`, `"负责人"`, `"总耗时"`, `"尝试次数"`, `"未分配"`
- Status Panel: `"结果已过期"`, `"重试"`, `"取消"`, `"再次点击确认取消"`, `"重试任务"`, `"仅可重试未过期的可重试失败任务"`
- Timeline: `"执行步骤"`, `"条记录"`, `"任务仍在等待调度，尚无步骤记录。"`
- Result: `"分析结果"`, `"结构化 AI 结果已持久化"`, `"Prompt"`, `"Parser"`, `"输入指纹"`, `"结果指纹"`, `"结构化结果"`, `"原始结果"`, `"来源 Artifact"`, `"原始报价"`, `"查看原件"`, `"本次结果没有可展示的来源。"`, `"任务完成后将在此显示结构化结果、原始结果、模型版本和来源。"`, `"Trace"`, `"Operation"`
- 7 Status Labels: `"等待调度"`, `"正在投递"`, `"正在分析"`, `"已成功"`, `"失败"`, `"重试中"`, `"已取消"`
- 6 Step Labels: `"输入校验"`, `"读取资料"`, `"核对报价"`, `"规则分析"`, `"生成解释"`, `"发布结果"`

---

## 4. Modernization Strategy & Visual Architecture Blueprint

### 4.1 Design System Integration (Tailwind CSS + Tokens)
All components will leverage the M1 Design System:
- **Glassmorphism Containers**: `backdrop-blur-md bg-surface/80 border border-border/70 shadow-sm rounded-xl`
- **Typography & Weights**: Inter / Segoe UI font stack, monospace numbers with `font-mono`, clear hierarchy (`text-xl font-semibold`, `text-xs text-text-muted`)
- **Status Indicators**: Pulsing status indicators (`animate-glow-pulse`, `bg-accent/15 text-accent border-accent/30`), warning indicators (`bg-warning-soft text-warning`), danger badges (`bg-danger-soft text-danger`)
- **Interactive Micro-animations**: Subtle button hover (`hover:translate-y-[-1px] active:translate-y-0 transition-transform`), smooth drawer and backdrop transitions.

---

### 4.2 Component-by-Component Modernization Blueprint

#### 1. `ReviewCenter.tsx`
- **Queue List (Left Column)**:
  - Card-styled queue items with glowing priority badge (P70+ in amber/rose glow).
  - Risk chips with icon tags and rounded pill styling.
  - Hover state with subtle left accent border glow.
- **Review Canvas (Right Column)**:
  - Header: Breadcrumb-style title with status badge and direct navigation button ("采购详情" with arrow).
  - AI Advice Panel: Glass card with glowing ShieldCheck icon, high-density 4-metric grid with subtle background fills (`bg-surface-subtle/60 rounded-lg p-3`).
  - Pro-Table Evidence Grid: Modernized table layout with sticky headers, alternate row tints, highlighted AI recommended row with subtle accent border, and monospace SHA-256 fingerprint badges.
  - Decision Panel: 4-action segmented button group with active state glow, clean form fields with focus rings, and primary/danger CTA.
  - Confirmation Modal: Centered glassmorphism modal with subtle backdrop blur, bold warnings, prominent verification checkbox, and dual action buttons.

#### 2. `ReportsCenter.tsx`
- **4 Top KPI Cards**:
  - Elevated metric cards (`bg-surface/90 border border-border/80 rounded-xl p-5 shadow-xs hover:shadow-md transition-shadow`).
  - Large bold numbers (`text-2xl font-bold font-mono tracking-tight`), colored icon containers with soft circular background.
- **Analytics Visual Grid**:
  - `状态漏斗`: Smooth rounded progress bars with gradient fills and percentage width animation.
  - `月度趋势`: Sleek column bars with rounded top corners, subtle hover brightness, and amount tooltips.
  - `供应商中标排行`: Leaderboard with gold/silver/bronze badge accents for top 3, performance score meter, and blacklist pill.
  - `AI 评测指标`: Benchmarking band with colored accuracy bars (`bg-accent`, `bg-info`, `bg-warning`) and precise percentage figures.

#### 3. `AuditLogCenter.tsx`
- **Filter Toolbar**:
  - Integrated toolbar pill with search input, actor text field, business object select, and task ID input, all sharing unified height and focus ring styling.
- **Audit Timeline / Stream**:
  - Streamlined event rows with categorized event type badges (blue for creation, amber for transition/overdue, green for approval/payment, rose for deletion/failure).
  - Monospace scope tags (`business_type:id`, `task_reference`) with copy-friendly styling.
  - Clean pagination footer with disabled states and active page indicators.

#### 4. `AiTaskCenter.tsx`
- **Diagnostic State Panel**:
  - Glowing status hero panel with animated spinner for active states (`RUNNING`, `RETRYING`), danger border for `FAILED`, and emerald check for `SUCCEEDED`.
  - Gradient progress bar (`.proc-ai-progress`) with smooth transition.
  - Inline retry & cancel action buttons with loading spinners.
- **Step Execution Timeline**:
  - Modern vertical step tree with connected line guides, attempt pills, duration badges, and step-specific Lucide icons.
- **Analysis Result & JSON Viewer**:
  - Structured bot summary callout with glowing AI avatar.
  - Collapsible syntax-styled JSON inspector with clean borders and monospace formatting.
  - Source artifact cards with confidence score pill (e.g. `97%` in emerald badge) and external link trigger.

---

## 5. Non-Regression Checklist & Implementation Guidelines

When implementing Milestone 2 modernization in these 4 files:
1. **Never delete `.proc-*` class names**: Combine them with Tailwind classes (e.g. `className="proc-center-page min-h-screen bg-bg p-6 space-y-6"`).
2. **Never change ARIA attributes or Element IDs**: Retain `#review-confirm-title`, `role="alert"`, `role="dialog"`, `aria-label="人工审核筛选"`, `aria-label="AI 任务筛选"`, etc.
3. **Never alter exact Chinese strings**: Every button label, empty state text, heading, error message, and status label must match verbatim.
4. **Preserve two-step confirmation state machines**: Both `AiTaskCenter` cancel ("再次点击确认取消") and `ReviewCenter` approval ("我已核对 AI 建议...") must retain their exact gating logic.
5. **Preserve `useEscape` hooks**: Ensure `Escape` key closes dialogs and modals.
6. **Continuous Verification**: Execute `npm test -- --run` and `npm run build` after every component modernization.

---
*Analysis prepared by Explorer 3 (Milestone 2)*
