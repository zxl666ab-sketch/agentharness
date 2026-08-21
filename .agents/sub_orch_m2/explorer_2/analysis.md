# Architectural Analysis & Modernization Blueprint: Transaction Business Centers (F5)

**Explorer**: Explorer 2 (Milestone 2 - Transaction Business Centers)  
**Date**: 2026-08-19  
**Target Modules**:
- `src/procurement/OrderCenter.tsx` (Purchase Orders & Settlement Management)
- `src/procurement/ContractCenter.tsx` (AI Contract Drafting, Risk Analysis & Change Control)
- `src/procurement/InvoiceCenter.tsx` (3-Way Matching: PO vs GRN vs Invoice & Reconcile)
- `src/procurement/SupplierCenter.tsx` (Supplier Directory, Performance Rating & Profile Drawer)
**Baseline Verification**: 14 Test Files, 84 Unit & Component Tests Passing (100%), TypeScript Typecheck 0 Errors, Vite Production Build Clean.

---

## 1. Executive Summary & Domain Architecture

The **Transaction Business Centers** form the downstream fulfillment and governance pillar of the AgentHarness platform. Once a procurement request completes multi-quote comparison and formal award selection, the downstream business lifecycle transitions across four dedicated transaction hubs:

```
+---------------------------------------------------------------------------------------------------+
|                                  DOWNSTREAM TRANSACTION LIFECYCLE                                 |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|  [Procurement Task Approved]                                                                      |
|               |                                                                                   |
|               +----------------------------+-----------------------------------+                  |
|               | (Auto-Generates PO)        | (Auto-Generates Draft Trigger)    |                  |
|               v                            v                                   |                  |
|     +--------------------+       +--------------------+                        |                  |
|     |  1. OrderCenter    |       |  2. ContractCenter |                        |                  |
|     |  • PENDING_SHIPMENT|       |  • DRAFT (AI)      |                        |                  |
|     |  • SHIPPED         |       |  • PENDING_APPROVAL|                        |                  |
|     |  • PARTIAL_RECEIVED|       |  • EFFECTIVE       |                        |                  |
|     |  • RECEIVED        |       |  • EXECUTING       |                        |                  |
|     |  • CLOSED          |       |  • CHANGE_REQUEST  |                        |                  |
|     +---------+----------+       +--------------------+                        |                  |
|               | (Delivers Goods)                                               |                  |
|               +----------------------------+                                   |                  |
|                                            v                                   |                  |
|                                  +--------------------+                        |                  |
|                                  |  3. InvoiceCenter  |                        |                  |
|                                  |  • 3-Way Matching  |                        |                  |
|                                  |  • PO vs GRN vs INV|                        |                  |
|                                  |  • DIFF_HOLD / FIX |                        |                  |
|                                  |  • RECONCILED      |                        |                  |
|                                  +---------+----------+                        |                  |
|                                            | (Reconciled Unlocks Payment)      |                  |
|               +----------------------------+                                   |                  |
|               v                                                                |                  |
|     +--------------------+                                                     |                  |
|     | Settlement Table   | <---------------------------------------------------+                  |
|     | • UNSETTLED        |                                                     |                  |
|     | • SETTLED          |                                                     |                  |
|     | • PAID             |                                                     |                  |
|     +--------------------+                                                     |                  |
|               ^                                                                |                  |
|               | (Supplier Historical Facts Aggregation)                        |                  |
|     +---------+----------+                                                     |                  |
|     |  4. SupplierCenter | <---------------------------------------------------+                  |
|     |  • Performance Lvl |                                                                        |
|     |  • Win Rate Score  |                                                                        |
|     |  • Profile Drawer  |                                                                        |
|     +--------------------+                                                                        |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Deep Dive: The 4 Transaction Centers

### 2.1 Order Center (`OrderCenter.tsx`)

#### Responsibilities & State Machine
- Manages the full purchase order lifecycle: `PENDING_SHIPMENT` -> `SHIPPED` -> `PARTIALLY_RECEIVED` / `RECEIVED` -> `CLOSED`.
- Evaluates contextual fulfillment progression via `fulfillmentNextStep()` from `viewModel.ts`.
- Automatically generates derived settlement records (`SettlementView`) upon full receipt (`RECEIVED`).

#### Critical Business Rules & Invariants
1. **Arbitrary Precision Decimal Arithmetic**:
   - Persisted amounts and quantities may contain up to 18 decimal places (e.g., `"10400.000000000000000000"`, `"3000.000000000000000000"`, `"0.300000000000000000"`).
   - Functions `parseDecimal`, `alignDecimals`, `formatDecimal`, `subtractDecimals`, and `compareDecimals` use `BigInt` for exact integer/fraction operations.
   - Formatting formats `10400.000000000000000000` to `"10,400.00"`, `3000.000000000000000000` to `"3,000 piece"`, and partial remainder `0.3 - 0.1` to `"0.2"`.
2. **Multi-Batch Receipt Dialog**:
   - Triggered by `"确认收货"` (or `"继续收货"` for `PARTIALLY_RECEIVED`).
   - Modal identified by heading `#receive-title` inside `section[role="dialog"]`.
   - Requires quantity `> 0` and `<= remainingQuantity`. Inputs `0`, `-5`, or `99999` display validation error in `[role="alert"]` without firing network requests.
   - Input date must match `YYYY-MM-DD`.
   - On submission, submit button shows `<LoaderCircle className="spin" />` and disables double submission.
   - Uses `Idempotency-Key` header which is preserved across failure retries.
   - On full receipt completion, displays `[role="status"]` with text `"最后一批收货"`.
3. **Settlement & Payment Interlocking Rule**:
   - Settlement status transitions: `UNSETTLED` -> `SETTLED` -> `PAID`.
   - Interlocking condition: Payment (`"登记付款"`) is **BLOCKED** if `!settlement.invoice_reconciled`, rendering `<small className="proc-muted">付款被拦截：请先核销全部有效发票</small>`.
   - Payment modal identified by heading `#pay-title` inside `section[role="dialog"]`. Validates payment date `YYYY-MM-DD`.
4. **Order Cancellation & Completion**:
   - Cancel button for `PENDING_SHIPMENT` with optional note. Modal heading `#close-title`.
   - Complete button for `RECEIVED` orders.
5. **Escape Key Handling**:
   - Global `keydown` listener on `window` listening for `event.key === "Escape"` closes the active dialog (`receiveTarget`, `closeTarget`, `payTarget`) unless an async operation is currently in-flight.

---

### 2.2 Contract Center (`ContractCenter.tsx`)

#### Responsibilities & State Machine
- Manages legal contract drafting and change lifecycle: `DRAFT` -> `PENDING_APPROVAL` -> `EFFECTIVE` -> `EXECUTING` -> `CHANGE_REQUEST` -> `CLOSED`.
- Automatically injects supplier name, landed amount, and lead days from the approved procurement award.
- Evaluates contract clause risks (AI-driven) and draft text consistency against Java source truth.

#### Critical Business Rules & Invariants
1. **Draft Generation & Regeneration**:
   - Dropdown with `aria-label="选择已定标任务"` lists approved tasks.
   - Button `"生成合同（AI 草拟）"` triggers `procurementApi.createContractDraft(taskId)`.
   - For `DRAFT` contracts, actions display `"提交审批"` and `"重新草拟"`.
   - For `CHANGE_REQUEST` contracts, actions display `"按修订值重新草拟"`.
2. **Authoritative Consistency Verification (Java Ruleset)**:
   - Compares text against injected numbers: `amount_in_text` vs `amount`, `lead_days_in_text` vs `lead_days`.
   - If inconsistent (`consistent === false`), renders `<p className="proc-contract-warning" role="alert">草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。</p>`.
3. **Structured AI Clause Risk Tags**:
   - Clauses contain `title`, `content`, `risk_level` (`高风险`, `提示`, `低`), and `risk_reason`.
   - High risk clauses trigger badge in contract card: `<small className="proc-invoice-diff">{riskCount} 项高风险条款</small>`.
4. **Change Request Workflow**:
   - For `EFFECTIVE` or `EXECUTING` contracts, clicking `"发起变更"` opens modal `#change-contract-title`.
   - Requires non-empty change reason, revised amount (`new_amount > 0`), and revised lead days (`new_lead_days` positive integer).
   - If submitted empty, displays error `"合同变更必须填写变更原因"`.
   - Change history displays revisions: `"修订：金额 12,000.00 · 交期 18 天（待审批）"`.
5. **Allow-Once Approval Modal**:
   - Modal identified by `#approve-contract-title`.
   - Requires text note and confirmation checkbox: `"我已核对草拟文本与条款风险，确认批准（一次性）"`.
   - If submitted unchecked, displays error `"批准合同必须勾选确认并填写人工备注"`.

---

### 2.3 Invoice Center (`InvoiceCenter.tsx`)

#### Responsibilities & State Machine
- Manages invoice ingestion and 3-way matching: `REGISTERED` -> `MATCHED` / `DIFF_HOLD` -> `RECONCILED` / `VOIDED`.
- 3-way reconciliation engine comparing Purchase Order (PO) vs Goods Receipt Note (GRN) vs Vendor Invoice.

#### Critical Business Rules & Invariants
1. **Invoice File Upload**:
   - Dropdown with `aria-label="选择采购订单"` lists orders.
   - File input has `data-testid="invoice-upload"`, accepts `.xlsx,.pdf`.
2. **3-Way Matching Table & Difference Analysis**:
   - Compares Quantity, Landed Total, and Tax Rate across PO, GRN, and Invoice.
   - For `DIFF_HOLD` status:
     - Displays diff count chip: `<small className="proc-invoice-diff">{diffCount} 项差异</small>`.
     - Displays structured diff list: `DIFF_LABELS` (`quantity` -> `"数量"`, `unit_price` -> `"单价"`, `total_amount` -> `"价税合计"`, `tax_rate` -> `"税率"`).
     - Displays AI explanation box: `Agent 差异解释` with reasons and bulleted suggestions.
     - Actions container `.proc-invoice-actions` contains `"手工改单"`, `"强制通过"`, `"作废（退回重开）"`, and **MUST NOT** contain `"核销"`.
   - For `MATCHED` status:
     - Displays badge `<p className="proc-invoice-matched"><BadgeCheck size={15} />三单匹配通过：数量、单价、总价、税率全部在容差内（Java 确定性规则）。</p>`.
     - Actions container contains `"核销"`.
3. **Manual Correction Modal (`#correct-invoice-title`)**:
   - Allows manual override of `quantity`, `unit_price`, `amount_excluding_tax`, `tax_amount`, `total_amount`, and `tax_rate`.
4. **Force Match Modal (`#force-invoice-title`)**:
   - Guarded approval: Requires non-empty notes and checking `"我已核对差异并确认强制通过（一次性，不能撤销）"`.
   - Submitting without check displays `"强制通过必须勾选确认并填写人工备注"`.
5. **Void Modal (`#void-invoice-title`)**:
   - Requires non-empty void reason.

---

### 2.4 Supplier Center (`SupplierCenter.tsx`)

#### Responsibilities & State Machine
- Manages vendor directory, cooperation status (`ACTIVE` / `PAUSED` / `BLACKLISTED`), and performance scoring.
- Derives performance score (0-100) and rating (`优质供应商`, `良好`, `一般`, `黑名单`, `暂停合作`) from win rate, quoting activity, and status.

#### Critical Business Rules & Invariants
1. **Search & Filter Toolbar**:
   - Search input with `aria-label="搜索供应商"`.
   - Status dropdown with `aria-label="供应商状态筛选"`.
2. **Supplier Cards & Performance Rating**:
   - Score badge container `.proc-score-badge` displaying formatted score (`Number(score).toFixed(1)`) and rating level.
   - Buttons:
     - Main card button has `aria-label="查看供应商档案 ${supplier.name}"`.
     - Edit button has `aria-label="编辑供应商 ${supplier.name}"`.
     - Delete button has `aria-label="删除供应商 ${supplier.name}"`.
3. **Create / Edit Drawer (`#supplier-form-title`)**:
   - Form fields: name (disabled when editing), contact person, phone, email, main categories, address, status, notes.
   - Validation: name is required (`"供应商名称不能为空"`).
4. **Delete Protection Dialog (`#delete-supplier-title`)**:
   - Deletion is rejected by backend if supplier has associated quote history. Warning: `"有关联报价历史的供应商会被拒绝删除（删除保护），可将状态改为暂停或黑名单。"`.
5. **Supplier Profile Drawer (`#supplier-profile-title`)**:
   - Triggered by clicking supplier card; displays performance score breakdown bars:
     - Win rate score (`performance.win_rate_score`)
     - Activity score (`performance.activity_score`)
     - Cooperation status score (`performance.status_score`)
   - Lists facts grid, quote metrics, participated item tags (`.proc-tag`), and recent quotes list (`.proc-quote-list`) with buttons linking to procurement tasks (`onOpenTask`).

---

## 3. Strict Preservation Matrix (F10 Semantic Compatibility)

To guarantee that 100% of unit tests and E2E assertions pass, the following items **MUST be strictly preserved**:

### 3.1 Critical DOM Element IDs

| DOM ID | Component | Selector / Used In | Mandatory Purpose |
|---|---|---|---|
| `#receive-title` | `OrderCenter.tsx` | `orderCenter.test.tsx` (`section:has(#receive-title)`) | Modal heading for Order Receipt. Test queries dialog inputs via this container. |
| `#pay-title` | `OrderCenter.tsx` | `orderCenter.test.tsx` (`section:has(#pay-title)`) | Modal heading for Settlement Payment. Test queries dialog inputs via this container. |
| `#close-title` | `OrderCenter.tsx` | `OrderCenter.tsx` (`aria-labelledby="close-title"`) | Modal heading for Order Cancellation / Completion. |
| `#approve-contract-title` | `ContractCenter.tsx` | `ContractCenter.tsx` (`aria-labelledby="approve-contract-title"`) | Modal heading for Contract Approval. |
| `#change-contract-title` | `ContractCenter.tsx` | `ContractCenter.tsx` (`aria-labelledby="change-contract-title"`) | Modal heading for Contract Change Request. |
| `#void-invoice-title` | `InvoiceCenter.tsx` | `InvoiceCenter.tsx` (`aria-labelledby="void-invoice-title"`) | Modal heading for Invoice Void. |
| `#force-invoice-title` | `InvoiceCenter.tsx` | `InvoiceCenter.tsx` (`aria-labelledby="force-invoice-title"`) | Modal heading for Invoice Force Match. |
| `#correct-invoice-title` | `InvoiceCenter.tsx` | `InvoiceCenter.tsx` (`aria-labelledby="correct-invoice-title"`) | Modal heading for Invoice Manual Correction. |
| `#supplier-form-title` | `SupplierCenter.tsx` | `SupplierCenter.tsx` (`aria-labelledby="supplier-form-title"`) | Modal heading for Supplier Create / Edit. |
| `#delete-supplier-title` | `SupplierCenter.tsx` | `SupplierCenter.tsx` (`aria-labelledby="delete-supplier-title"`) | Modal heading for Supplier Delete Confirmation. |
| `#supplier-profile-title` | `SupplierCenter.tsx` | `SupplierCenter.tsx` (`aria-labelledby="supplier-profile-title"`) | Drawer heading for Supplier Profile. |

---

### 3.2 Critical CSS Class Names

| CSS Class Name | Component | Tested In | Verification Hook |
|---|---|---|---|
| `.proc-order-card` | `OrderCenter.tsx` | `orderCenter.test.tsx` (cases 2, 3, 4, 6, 7, 8) | Finds order cards by PO number (`PO-TEST-SHIP`). |
| `.proc-settlement-row` | `OrderCenter.tsx` | `orderCenter.test.tsx` (case 5) | Finds settlement row by settlement number (`ST-TEST-0001`). |
| `.proc-invoice-actions` | `InvoiceCenter.tsx` | `invoiceCenter.test.tsx` (case 2) | Verifies `"核销"` button is omitted during `DIFF_HOLD`. |
| `.proc-inline-error` | `AiTaskCenter.tsx`, `OrderCenter.tsx` | `centers.test.tsx` (case 2) | Error display container for busy state/conflicts. |
| `.proc-order-list` | `OrderCenter.tsx` | `OrderCenter.tsx` | Container for order cards. |
| `.proc-invoice-list` | `InvoiceCenter.tsx`, `ContractCenter.tsx` | `invoiceCenter.test.tsx`, `contractCenter.test.tsx` | List container for invoice/contract cards. |
| `.proc-invoice-card` | `InvoiceCenter.tsx`, `ContractCenter.tsx` | `invoiceCenter.test.tsx`, `contractCenter.test.tsx` | Clickable card button in left pane. |
| `.proc-invoice-diff` | `InvoiceCenter.tsx`, `ContractCenter.tsx` | `invoiceCenter.test.tsx` | Diff indicator chip on invoice card. |
| `.proc-invoice-matched` | `InvoiceCenter.tsx` | `invoiceCenter.test.tsx` | 3-way match success callout. |
| `.proc-invoice-explanation` | `InvoiceCenter.tsx` | `invoiceCenter.test.tsx` | AI explanation block. |
| `.proc-score-badge` | `SupplierCenter.tsx` | `SupplierCenter.tsx` | Performance score container. |
| `.proc-filter-chip` | All Centers | `orderCenter.test.tsx`, `procurement.test.tsx` | Toolbar filter buttons. |
| `.proc-form-error` | All Centers | Modals & Dialogs | Inline error message paragraph. |
| `.proc-toolbar-error` | All Centers | Toolbars | Toolbar error alert span. |
| `.proc-toolbar-success` | All Centers | Toolbars | Toolbar success status span. |
| `.proc-empty-state` | All Centers | Empty state displays | Empty state section. |

---

### 3.3 Critical ARIA Roles, Attributes & Test IDs

| Attribute & Value | Element / Component | Tested In | Behavior |
|---|---|---|---|
| `role="alert"` | Form/toolbar errors | `orderCenter.test.tsx`, `invoiceCenter.test.tsx`, `contractCenter.test.tsx` | Asserted for error messages (`"模拟服务器故障"`, `"数量"`, `"合同变更必须填写变更原因"`, `"强制通过必须勾选确认并填写人工备注"`). |
| `role="dialog"` | All modal dialogs | `orderCenter.test.tsx`, `contractCenter.test.tsx`, `invoiceCenter.test.tsx` | Queried by `host.querySelector('[role="dialog"]')`. |
| `role="status"` | Success notices | `orderCenter.test.tsx` (case 8) | Asserted for `"最后一批收货"`. |
| `data-testid="invoice-upload"` | File input in `InvoiceCenter` | `InvoiceCenter.tsx` | Invoice file input hook. |
| `aria-label="选择已定标任务"` | `<select>` in `ContractCenter` | `contractCenter.test.tsx` | Task selection dropdown. |
| `aria-label="选择采购订单"` | `<select>` in `InvoiceCenter` | `invoiceCenter.test.tsx` | Order selection dropdown. |
| `aria-label="搜索供应商"` | `<input>` in `SupplierCenter` | `SupplierCenter.tsx` | Search input. |
| `aria-label="供应商状态筛选"` | `<select>` in `SupplierCenter` | `SupplierCenter.tsx` | Status filter dropdown. |
| `aria-label="查看供应商档案 ..."` | `<button>` in `SupplierCenter` | `SupplierCenter.tsx` | Card profile open button. |
| `aria-label="编辑供应商 ..."` | `<button>` in `SupplierCenter` | `SupplierCenter.tsx` | Edit button. |
| `aria-label="删除供应商 ..."` | `<button>` in `SupplierCenter` | `SupplierCenter.tsx` | Delete button. |

---

### 3.4 Exact Chinese Strings Asserted by Test Suites

```
Order Center:
- "10,400.00", "3,000 piece", "2,999.5"
- "待发货", "已发货", "部分收货", "已收货", "已关闭"
- "标记发货", "取消订单", "确认收货", "继续收货", "登记本批收货", "最后一批收货", "完成关闭"
- "剩余数量 200", "剩余数量 0.2"
- "对账单", "确认对账", "登记付款", "确认付款", "付款被拦截：请先核销全部有效发票"

Contract Center:
- "合同中心", "CT-RFQ-20260814-B1", "CT-RFQ-20260814-X1", "CT-RFQ-20260814-E1"
- "提交审批", "重新草拟", "按修订值重新草拟", "开始执行", "发起变更", "确认发起变更", "完成关闭"
- "修订：金额 12,000.00 · 交期 18 天（待审批）"
- "修订后金额（元）", "修订后交期（天）", "合同变更必须填写变更原因"
- "草拟一致性校验（Java 权威）", "草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。"
- "批准合同（allow-once）", "批准合同变更（重新审批）", "批准合同必须勾选确认并填写人工备注"
- "我已核对草拟文本与条款风险，确认批准（一次性）"

Invoice Center:
- "发票中心", "INV-2026081601", "INV-2026081602"
- "差异挂起", "2 项差异", "已匹配", "已核销", "已作废"
- "三单匹配对比", "数量不一致", "期望 1000", "手工改单", "强制通过", "作废（退回重开）", "核销"
- "三单匹配通过", "单价", "价税合计"
- "确认强制通过", "强制通过必须勾选确认并填写人工备注"
- "我已核对差异并确认强制通过（一次性，不能撤销）"

Supplier Center:
- "供应商管理", "新建供应商", "编辑供应商档案", "删除供应商档案", "供应商档案"
- "优质供应商", "良好", "一般", "黑名单", "合作中", "已暂停"
- "中标率得分", "活跃度得分", "合作状态得分"
- "有关联报价历史的供应商会被拒绝删除（删除保护），可将状态改为暂停或黑名单。"
```

---

## 4. Modernization Blueprint & Implementation Strategy

To elevate the UI to a modern **Cursor/Canvas-inspired AI collaborative workspace**, we adopt a hybrid styling architecture: **retain all existing semantic class names and DOM hooks while decorating them with modern Tailwind CSS utility classes and design tokens**.

### 4.1 Design System Integration

1. **Tokens & Theme Consistency**:
   - Backgrounds: `bg-bg`, `bg-surface`, `bg-surface-subtle`, `bg-surface-elevated`
   - Borders: `border-border`, `border-border-strong`, `border-accent/40`
   - Typography: `text-text`, `text-text-secondary`, `text-text-muted`, `font-sans`, `font-mono`
   - Accents: `bg-accent`, `text-accent`, `bg-accent-soft`, `bg-accent-softer`
   - Glassmorphism: `glass-panel backdrop-blur-md bg-surface/85 border border-border/70 shadow-glass dark:shadow-glass-dark`
   - Glowing Pulse: `animate-glow-pulse shadow-glow`

2. **Slide-Over Detail Drawers**:
   - Modernize backdrop overlays with `fixed inset-0 z-50 bg-black/40 backdrop-blur-sm transition-opacity`.
   - Position drawer modals with sleek entry transitions (`transform transition-all ease-out duration-200`).

3. **Status Badges & Glow Indicators**:
   - Status pills decorated with inline dot indicators (`w-1.5 h-1.5 rounded-full mr-1.5`):
     - `success` -> `bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]`
     - `warning` -> `bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]`
     - `info` -> `bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]`
     - `danger` -> `bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]`

---

### 4.2 Component Modernization Strategy

#### 1. `OrderCenter.tsx`
```tsx
// Structure Upgrade Example:
<section className="proc-main flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
  <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
    <div>
      <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
        <PackageCheck className="w-5 h-5 text-accent" />
        {highlightTaskId ? "任务订单" : "采购订单"}
      </h1>
      <p className="text-sm text-text-muted mt-1">{/* Context description */}</p>
    </div>
    {/* Page count pill */}
  </header>

  {/* Filter Toolbar */}
  <div className="proc-toolbar flex flex-wrap items-center gap-2" role="toolbar">
    {STATUS_FILTERS.map((option) => (
      <button
        key={option.value}
        type="button"
        className={cn(
          "proc-filter-chip px-3 py-1.5 rounded-lg text-xs font-medium border transition-all",
          status === option.value
            ? "active bg-accent text-white border-accent shadow-sm"
            : "bg-surface text-text-secondary border-border hover:border-border-strong hover:bg-surface-subtle"
        )}
        onClick={() => setStatus(option.value)}
      >
        {option.label}
      </button>
    ))}
  </div>

  {/* Order Cards Grid */}
  <div className="proc-order-list grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" aria-busy={ordersQuery.isPending}>
    {orders.map((order) => (
      <article
        key={order.id}
        className="proc-order-card glass-panel rounded-xl p-5 flex flex-col justify-between border border-border/80 hover:border-accent/50 hover:shadow-md transition-all duration-150"
      >
        {/* Card Header with code badge, item title, and glowing status badge */}
        {/* Facts Grid in 2 columns */}
        {/* Next Step Callout Pill */}
        {/* Action Toolbar */}
        {/* Artifact Links Footer */}
      </article>
    ))}
  </div>

  {/* Settlement Section with Pro-Table Grid */}
  <div className="proc-settlement-section glass-panel rounded-xl p-5 border border-border mt-4">
    {/* SettlementTable with striped rows and status pills */}
  </div>
</section>
```

#### 2. `ContractCenter.tsx`
```tsx
// Split Layout Upgrade:
<div className="proc-invoice-layout grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
  {/* Left Contracts List: 4 cols */}
  <div className="proc-invoice-list lg:col-span-4 flex flex-col gap-3">
    {contracts.map((contract) => (
      <button
        key={contract.id}
        type="button"
        className={cn(
          "proc-invoice-card glass-panel text-left p-4 rounded-xl border transition-all duration-150 flex flex-col gap-2",
          selectedId === contract.id
            ? "selected border-accent bg-accent-soft/30 shadow-sm"
            : "border-border hover:border-border-strong hover:bg-surface-subtle"
        )}
        onClick={() => setSelectedId(contract.id)}
      >
        {/* Head with contract_no & status */}
        {/* Supplier & item */}
        {/* Amount, lead days, and risk count tag */}
      </button>
    ))}
  </div>

  {/* Right Contract Document Canvas: 8 cols */}
  <div className="proc-invoice-detail lg:col-span-8 glass-panel rounded-xl p-6 border border-border flex flex-col gap-6">
    {/* Panel Header */}
    {/* Injected Facts Grid */}
    {/* Java Consistency Verification Callout */}
    {/* Structured AI Clauses Grid with Risk Pills */}
    {/* Draft Monospace Text Canvas */}
    {/* Change History Timeline */}
    {/* Actions Bar */}
  </div>
</div>
```

#### 3. `InvoiceCenter.tsx`
- Left 4-column invoice cards list with diff count tags.
- Right 8-column 3-way matching workspace:
  - Responsive comparison table (`PO` vs `GRN` vs `Invoice` vs `Expected`) with checkmark and diff badges.
  - Highlighted discrepancy explanation box (`Agent 差异解释`) with suggestions list.
  - Action buttons (`手工改单`, `强制通过`, `作废`, `核销`).
- Wide modal for manual correction with 6 auto-calculated financial fields.

#### 4. `SupplierCenter.tsx`
- Grid of supplier cards featuring avatar icon, facts, cooperation tone, and score badge with rating.
- Slide-over Supplier Profile Drawer (`proc-supplier-drawer`) displaying score breakdown progress bars (`中标率得分`, `活跃度得分`, `合作状态得分`), business facts grid, participation statistics, and clickable historical quote links.

---

## 5. Verification Checkpoints for Implementers

After completing any component refactor:

```powershell
# 1. Run full unit and component test suite
npm test -- --run

# 2. Run TypeScript compiler and production Vite build
npm run build
```

**Zero Regression Guarantee**:
1. All 14 test files and 84 tests must pass 100%.
2. All DOM IDs (`#receive-title`, `#pay-title`, etc.) and CSS classes (`.proc-order-card`, `.proc-settlement-row`, `.proc-invoice-actions`, `.proc-inline-error`, etc.) must remain present on elements.
3. All button text and status labels must match exact Chinese strings.
4. Modals and drawers must close when pressing `Escape`.
