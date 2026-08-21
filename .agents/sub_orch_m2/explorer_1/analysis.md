# Milestone 2 Explorer 1: Cockpit Dashboard & App Shell Architectural Analysis

**Explorer**: Explorer 1 (Cockpit Dashboard & App Shell Explorer)  
**Milestone**: Milestone 2 (Cockpit Dashboard & Business Centers Overhaul)  
**Scope**: `WorkbenchHome.tsx` (F4), `Header.tsx` (F6), `Navigation.tsx` / `WorkbenchNavigation.tsx` (F6), `RoleSwitcher.tsx` (F6)  
**Test Baseline**: 14 test files, 84 unit tests (100% PASS)  
**Target Styling**: Tailwind CSS 3.4 + CSS Variables (`tokens.css`), Glassmorphism, Glowing Badges, Responsive Layout  

---

## 1. Executive Summary

Milestone 2 modernizes the core application shell, topbar, navigation rail, role switcher, and cockpit dashboard of AgentHarness into a modern, Cursor/Canvas-inspired AI procurement workspace.

This analysis provides the complete architectural audit, semantic contract inventory, and implementation blueprint for:
1. **`WorkbenchHome.tsx` (F4 Cockpit Dashboard)**: High-density dynamic KPI metric cards, natural language intake launcher with multi-file quote dropzone, todo quick-filter action strip, dual-column queue & exception triage grid, and recent tasks pro-table.
2. **`Header.tsx` / Topbar (F6 App Shell Header)**: Modern glassmorphic top navigation bar featuring branding, embedded `RoleSwitcher`, live backend health & version indicator, API/model config trigger, and theme toggle.
3. **`Navigation.tsx` / `WorkbenchNavigation.tsx` (F6 Navigation Rail)**: Responsive multi-group navigation rail with primary workflow items, nested fulfillment sub-routes, collapsible business archives, and management sections with real-time attention badges.
4. **`RoleSwitcher.tsx` (F6 Role Switcher)**: Pure frontend demo role selector (`buyer`, `approver`, `admin`) controlling view visibility, navigation item filtering, and exception metric isolation without server authentication dependencies.

All changes strictly preserve 100% of existing semantic DOM IDs, CSS class selectors, ARIA attributes, exact Chinese strings, and keyboard shortcuts required by the Vitest suite (`npm test -- --run`).

---

## 2. Component Deep-Dive Analysis

### 2.1. `WorkbenchHome.tsx` (F4 Cockpit Dashboard)

#### File Location & Responsibilities
- **Current Path**: `web/src/procurement/WorkbenchHome.tsx` (re-exportable / co-locatable with `src/components/WorkbenchHome.tsx`)
- **Key Responsibilities**:
  - Serves as the primary operational cockpit when `view === "workbench"`.
  - Aggregates high-level metrics across procurement tasks, orders, invoices, and AI runs.
  - Houses the natural language AI intake launcher (`NewProcurementConversation` embedded).
  - Provides quick 1-click filter navigation into active queues (`attention`, `reviews`, `orders`, `invoices`, `ai`).
  - Displays the high-density Recent Tasks Pro-Table with status chips and quote counts.

#### Props & State Contract
```typescript
export type WorkbenchHomeProps = {
  role: DemoRole;
  requests: ProcurementRequestSummary[];
  aiTasks: AiTaskView[];
  reviews: ReviewView[];
  loading: boolean;
  createBusy: boolean;
  createError?: string | null;
  maxFileBytes: number;
  maxTotalBytes: number;
  maxQuotes: number;
  onStart: (message: string, files: File[]) => Promise<void>;
  onOpenTask: (id: string) => void;
  onOpenTasks: (filter: TaskFilter) => void;
  onOpenView: (view: WorkbenchView) => void;
  onOpenOrders: () => void;
};
```

#### Queries & Data Aggregation
1. **`insightsOverview`**: Query key `["procurement-insights-overview"]`, polling every 15s via `procurementApi.insightsOverview`. Provides `counts` (`overdue_orders`, `overdue_payments`, `orders_shipped`, `orders_partially_received`, `suppliers_blacklisted`, etc.).
2. **`invoiceHolds`**: Query key `["procurement-home-invoice-holds"]`, polling every 15s via `procurementApi.invoices("DIFF_HOLD", undefined, 0, 1)`. Provides `invoiceHoldsQuery.data?.total`.
3. **Computed Metrics**:
   - `attention = requests.filter(r => ["waiting_human", "review", "ready", "analyzed", "approval_pending"].includes(r.status)).length`
   - `aiIssues = aiTasks.filter(t => t.status === "FAILED" || t.stale).length`
   - `pendingReviews = reviews.filter(rev => rev.status === "PENDING").length`
   - `overdueTotal = (counts?.overdue_orders ?? 0) + (counts?.overdue_payments ?? 0)`
   - `agentWaiting = requests.filter(r => r.status === "waiting_human").length`
   - `fieldReviews = requests.filter(r => r.status === "review").length`
   - `planConfirmations = requests.filter(r => r.status === "analyzed" || r.status === "approval_pending").length`
   - `pendingReceipts = (counts?.orders_shipped ?? 0) + (counts?.orders_partially_received ?? 0)`

#### Role Visibility Invariants
- `canOpenTasks = isViewVisible(role, "tasks")` (Buyer: ✅, Approver: ❌, Admin: ✅)
- `canOpenOrders = isViewVisible(role, "orders")` (Buyer: ✅, Approver: ✅, Admin: ✅)
- `canOpenInvoices = isViewVisible(role, "invoices")` (Buyer: ✅, Approver: ❌, Admin: ✅)
- `canOpenReviews = isViewVisible(role, "reviews")` (Buyer: ✅, Approver: ✅, Admin: ✅)

**Critical Edge Case (`procurement.test.tsx` Case 24)**:
When `role === "approver"`:
- `canOpenTasks` is `false`, so the "待办任务" section is completely omitted.
- The first `.proc-home-section` in DOM is the "需要处理" section.
- Its `<header>` must contain `"0 项"` when there are no exceptions (`<span className="proc-pill-count danger">{count} 项异常</span>`).
- It must **never leak** supplier blacklist alerts or supplier management stats.

---

### 2.2. `Header.tsx` (F6 App Shell Header)

#### Current Implementation & Extraction
- Currently in `ProcurementWorkbench.tsx:144-176`.
- Can be cleanly extracted to `src/procurement/Header.tsx` (or `src/components/Header.tsx` with re-export).

#### Props Contract
```typescript
export type HeaderProps = {
  theme: "light" | "dark";
  backendVersion: string;
  role: DemoRole;
  configData?: ConfigPayload;
  onRoleChange: (role: DemoRole) => void;
  onToggleTheme: () => void;
  onOpenConfig: (config?: ConfigPayload) => void;
};
```

#### Visual & Functional Elements
1. **Brand**: Icon `Scale` (size 20), Title `<strong>采价台</strong>`, Subtitle `<small>采购询价与供应商比价</small>`.
2. **Role Switcher**: Container `<label className="proc-role-selector" title="演示角色切换（K9，纯前端视角控制）">`, label text `<span>角色</span>`, dropdown `<select aria-label="演示角色">`.
3. **Runtime State**: Icon `Wifi` (size 14), text `"采购服务 {backendVersion}"`.
4. **API / Model Config**: Icon `Settings` (size 16), tooltip & aria-label `"API / 模型配置"`.
5. **Theme Toggle**: Icon `Moon` / `Sun` (size 16), tooltip & aria-label `"切换主题"`.

---

### 2.3. `Navigation.tsx` / `WorkbenchNavigation.tsx` (F6 Navigation Rail)

#### Current Implementation
- Located at `web/src/procurement/WorkbenchNavigation.tsx`.

#### Props Contract
```typescript
export type NavigationProps = {
  active: WorkbenchView;
  role: DemoRole;
  aiAttention: number;
  reviewAttention: number;
  onChange: (view: WorkbenchView) => void;
};
```

#### Navigation Structure & Route Definitions
1. **Primary Route Group ("采购主线")**:
   - `workbench`: "工作台" (`LayoutDashboard`)
   - `tasks`: "采购任务" (`ListTodo`)
   - `orders`: "履约中心" (`ShoppingCart`)
   - *Fulfillment Sub-items*: `invoices`: "发票匹配" (`Receipt`) inside `<div className="proc-nav-children" aria-label="履约中心二级入口">`
2. **Business Archive Group ("业务资料")** (`FolderOpen`, collapsible):
   - `suppliers`: "供应商档案" (`Users`)
   - `contracts`: "合同管理" (`FileSignature`)
   - `reports`: "统计报表" (`BarChart3`)
3. **Management Group ("管理与技术")** (`Wrench`, collapsible):
   - `ai`: "AI 任务诊断" (`Bot`, attention badge `aiAttention`)
   - `reviews`: "人工审核" (`ClipboardCheck`, attention badge `reviewAttention`)
   - `audit`: "全局审计" (`ScrollText`)
   - `system`: "系统信息" (`Server`)

#### Active Item Auto-Expansion
If an active view is inside a collapsed group (e.g. user navigates to `contracts` or `ai`), the group automatically expands via `useEffect`.

---

### 2.4. `RoleSwitcher.tsx` (F6 Role Switcher)

#### Implementation Specification
- **Supported Roles**: `buyer` ("采购员"), `approver` ("审批人"), `admin` ("管理员").
- **Persistence**: `localStorage.getItem("procurement.demo-role")` with fallback to `"buyer"`.
- **View Fallback**: When role changes, if current active view is not allowed for the new role, auto-redirect to `"workbench"` via `visibleViewOrDefault(role, view)`.

```typescript
export type RoleSwitcherProps = {
  role: DemoRole;
  onChange: (role: DemoRole) => void;
};
```

---

## 3. Test Suite & Semantic Contract Preservation Matrix (F10)

The refactored components must preserve the following exact selectors, attributes, and text strings:

### 3.1. DOM IDs & Data Attributes
| Selector / ID / Attribute | Target Component | Purpose & Verification |
|---|---|---|
| `#proc-conversation-panel` | `ProcurementConversation.tsx` | Target panel for `aria-controls` |
| `data-testid="conversation-upload"` | `ProcurementConversation.tsx` | Quote file input for New Conversation |
| `[role="table"]` | `WorkbenchHome.tsx` | Pro-Table container |
| `[role="row"]` | `WorkbenchHome.tsx` | Table rows (header & body) |

### 3.2. CSS Class Name Preservation Matrix
| CSS Class Name | Component | Required For |
|---|---|---|
| `.proc-app` | `ProcurementWorkbench` | Root application container |
| `.proc-topbar` | `Header` / `ProcurementWorkbench` | Top header bar |
| `.proc-brand` | `Header` / `ProcurementWorkbench` | App branding container |
| `.proc-role-selector` | `Header` / `RoleSwitcher` | Role switcher container |
| `.proc-runtime-state` | `Header` / `ProcurementWorkbench` | Backend status pill |
| `.proc-icon-button` | `Header` / `ProcurementWorkbench` | Action buttons |
| `.proc-primary-nav` | `Navigation` / `WorkbenchNavigation` | Navigation rail container (`aria-label="采购工作台主导航"`) |
| `.proc-nav-section` | `Navigation` / `WorkbenchNavigation` | Navigation section |
| `.proc-nav-primary-items` | `Navigation` / `WorkbenchNavigation` | Primary navigation items |
| `.proc-nav-item` | `Navigation` / `WorkbenchNavigation` | Navigation item button (`active`, `nested`) |
| `.proc-nav-icon` | `Navigation` / `WorkbenchNavigation` | Icon container |
| `.proc-nav-label` | `Navigation` / `WorkbenchNavigation` | Label text |
| `.proc-nav-badge` | `Navigation` / `WorkbenchNavigation` | Attention count badge (`danger`, `warning`) |
| `.proc-nav-children` | `Navigation` / `WorkbenchNavigation` | Sub-item container (`aria-label="履约中心二级入口"`) |
| `.proc-nav-group-toggle` | `Navigation` / `WorkbenchNavigation` | Group accordion header |
| `.proc-nav-group-items` | `Navigation` / `WorkbenchNavigation` | Group children container |
| `.proc-home` | `WorkbenchHome` | Cockpit dashboard wrapper |
| `.proc-cockpit-stats` | `WorkbenchHome` | KPI stats container (`aria-label="核心指标看板"`) |
| `.proc-stat-card` | `WorkbenchHome` | Individual KPI stat button (`highlight-attention`, `highlight-danger`) |
| `.proc-stat-header` | `WorkbenchHome` | KPI header |
| `.proc-stat-title` | `WorkbenchHome` | KPI title |
| `.proc-stat-icon-wrap` | `WorkbenchHome` | Icon container (`primary`, `warning`, `info`, `danger`) |
| `.proc-stat-body` | `WorkbenchHome` | Number & subtitle |
| `.proc-stat-number` | `WorkbenchHome` | Large numeric display |
| `.proc-stat-sub` | `WorkbenchHome` | Subtitle text |
| `.proc-home-intake` | `WorkbenchHome` | AI intake launcher container |
| `.proc-intake-banner` | `WorkbenchHome` | Header banner |
| `.proc-intake-badge` | `WorkbenchHome` | Accent badge |
| `.proc-demo-entry-pill` | `WorkbenchHome` | Demo entry pill |
| `.proc-demo-tag` | `WorkbenchHome` | Demo tag |
| `.proc-todo-quick-strip` | `WorkbenchHome` | Actionable todo strip (`aria-label="待办中心"`) |
| `.proc-todo-chip` | `WorkbenchHome` | Actionable todo chip (`attention`, `danger`) |
| `.proc-todo-chip-icon` | `WorkbenchHome` | Chip icon |
| `.proc-todo-chip-label` | `WorkbenchHome` | Chip label |
| `.proc-todo-chip-count` | `WorkbenchHome` | Chip count badge |
| `.proc-home-grid` | `WorkbenchHome` | 2-column task & exception grid |
| `.proc-home-section` | `WorkbenchHome` | Section container (queried in Case 24: `host.querySelector(".proc-home-section")`) |
| `.proc-pill-count` | `WorkbenchHome` | Pill count badge in section header |
| `.proc-home-list` | `WorkbenchHome` | Todo task list |
| `.proc-task-row-btn` | `WorkbenchHome` | Todo task button row |
| `.proc-home-alerts` | `WorkbenchHome` | Exception alerts container |
| `.proc-alert-item` | `WorkbenchHome` | Alert button item (`danger`, `warning`, `quiet`) |
| `.proc-pro-table-wrap` | `WorkbenchHome` | Table wrapper |
| `.proc-pro-table` | `WorkbenchHome` | Recent tasks table |
| `.proc-pro-tr` | `WorkbenchHome` | Table row |
| `.proc-status-chip` | `WorkbenchHome` | Status chip with dot indicator |
| `.proc-quote-count-badge` | `WorkbenchHome` | Quote count badge |

### 3.3. Exact ARIA Attributes & Labels
- `aria-label="演示角色"` on `<select>`
- `aria-label="采购工作台主导航"` on `<nav>`
- `aria-label="履约中心二级入口"` on `<div>`
- `aria-label="核心指标看板"` on `<section>`
- `aria-label="待办中心"` on `<section>`
- `aria-label="最近采购任务"` on `<table>`
- `aria-label="新建采购任务"` on `<section>`
- `aria-label="快速模板提示词"` on `<div>`
- `aria-label="采购目标"` on `<textarea>`
- `aria-current="page"` on active navigation item buttons

### 3.4. Exact Chinese Text Strings
- **Intake & Home**: `"开始采购比价"`, `"开始解析报价"`, `"演示数据"`, `"查看 31 份真实物料比价闭环"`, `"支持自然语言描述采购需求并批量拖拽多份供应商报价（XLSX / PDF），由 Agent 自动结构化解析并比价。"`
- **Todo Chips**: `"Agent 等待回答"`, `"等待字段复核"`, `"等待确认采购方案"`, `"待收货订单"`, `"发票差异待处理"`, `"付款被拦截"`
- **Sections & Exceptions**: `"采购总任务"`, `"待办决策"`, `"履约中订单"`, `"风险与异常预警"`, `"待办任务"`, `"需要处理"`, `"最近任务"`, `"个 AI 任务需处理"`, `"项履约逾期"`, `"项等待高风险确认"`, `"查看全部"`, `"采购任务全部列表"`
- **Navigation**: `"工作台"`, `"采购任务"`, `"履约中心"`, `"发票匹配"`, `"业务资料"`, `"供应商档案"`, `"合同管理"`, `"统计报表"`, `"管理与技术"`, `"AI 任务诊断"`, `"人工审核"`, `"全局审计"`, `"系统信息"`
- **Roles**: `"角色"`, `"采购员"`, `"审批人"`, `"管理员"`
- **Forbidden Strings**: Must **NOT** contain `"管理驾驶舱"` or `"成本节约率"` on `WorkbenchHome`.

---

## 4. Modernization & UI Engineering Blueprint

### 4.1. Design System Tokens & Classes Application

| Component | Target Look & Feel | Modern Tailwind CSS Classes |
|---|---|---|
| **Header (Topbar)** | Sleek glassmorphism header with border divider and subtle blur | `h-14 px-5 flex items-center justify-between backdrop-blur-md bg-surface/85 border-b border-border/80 sticky top-0 z-30 shadow-xs` |
| **Role Selector Pill** | Compact select wrapper with hover glow and custom focus ring | `flex items-center gap-2 px-2.5 py-1 rounded-lg bg-surface-subtle/80 border border-border hover:border-border-strong text-xs font-medium transition-all` |
| **Navigation Rail** | Clean sidebar with dark mode support, active left indicator, smooth badge accents | `w-56 flex-shrink-0 flex flex-col bg-surface border-r border-border h-full p-3 gap-4 overflow-y-auto` |
| **Nav Item Button** | Cursor-styled clickable button with soft hover, active emerald tint, and animated badge | `flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium text-text-secondary hover:text-text hover:bg-surface-subtle transition-all duration-150 relative group` |
| **KPI Stat Card** | Elevating card with border gradient, glowing icon backdrop, and bold mono count | `p-4 rounded-xl bg-surface border border-border hover:border-border-strong shadow-xs hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 flex flex-col justify-between min-h-[110px]` |
| **AI Intake Launcher** | Emerald-tinted gradient card with glowing badge and clean prompt box | `p-5 rounded-2xl bg-gradient-to-br from-emerald-500/10 via-surface to-surface border border-emerald-500/20 shadow-sm` |
| **Todo Quick Strip** | Smooth horizontal pill strip with counter pill and subtle status border | `flex items-center gap-2 overflow-x-auto py-1 scrollbar-none` |
| **Dual Grid Sections** | Clean card panels with distinct headers, pill badges, and hover list rows | `p-4 rounded-xl bg-surface border border-border shadow-xs flex flex-col gap-3` |
| **Recent Pro-Table** | Dense tabular layout with sticky header, monospaced refs, and animated pulse status dot | `w-full text-left text-xs border-collapse rounded-lg overflow-hidden` |

---

## 5. Proposed Implementation Strategy & Architecture

### Recommended File Structure
```
src/
├── components/
│   ├── Header.tsx              [Extract & Modernize Header Topbar]
│   ├── Navigation.tsx          [Extract & Modernize Navigation Rail]
│   ├── RoleSwitcher.tsx        [Extract & Modernize Role Switcher]
│   ├── WorkbenchHome.tsx       [Modernize Cockpit Dashboard]
│   ├── EffectBadge.tsx         [Preserved]
│   └── RunReport.tsx           [Preserved]
├── procurement/
│   ├── Header.tsx              [Re-export from components/Header]
│   ├── Navigation.tsx          [Re-export from components/Navigation]
│   ├── RoleSwitcher.tsx        [Re-export from components/RoleSwitcher]
│   ├── WorkbenchHome.tsx       [Re-export / Main implementation]
│   ├── WorkbenchNavigation.tsx [Backward compatibility re-export]
│   └── ProcurementWorkbench.tsx[Assembles Header, Navigation, and WorkbenchHome]
```

### Safety & Compatibility Principles
1. **Dual Class Strategy**: Always retain the legacy class (e.g. `className="proc-stat-card group relative flex flex-col justify-between ..."`). This guarantees 100% test compatibility while applying modern utility styling.
2. **Strict Attribute Preservation**: Preserve all `aria-label`, `role`, `aria-current`, and `aria-expanded` attributes verbatim.
3. **Exact String Match**: Do not alter or translate any Chinese strings tested in `procurement.test.tsx`.
4. **Role Gating Logic**: Ensure `canOpenTasks` condition strictly hides the "待办任务" section for `approver` role.

---

*Report prepared by Explorer 1 (Cockpit Dashboard & App Shell)*
