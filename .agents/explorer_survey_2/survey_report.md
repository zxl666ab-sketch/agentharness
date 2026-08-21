# Frontend Component & Layout Architectural Survey Report

**Explorer**: Explorer 2 (Component & Layout Explorer)  
**Date**: 2026-08-19  
**Target Repository**: `D:\个人通用agentharness\web`  
**Test Baseline**: 13 test files, 80 unit tests (100% passing)  

---

## 1. Executive Summary

The AgentHarness web frontend (`web/`) is a React 18 + TypeScript single-page application built on Vite, with server state orchestrated via TanStack React Query v5 and UI styling defined primarily through vanilla CSS (`procurement.css`, `tokens.css`, `app.css`).

The platform functions as an AI-native procurement and supplier benchmarking workbench ("采价台"), managing end-to-end procurement workflows:
1. **Intake & NLP Specification Extraction**: Buyers input natural language procurement requests with quote attachments (XLSX/PDF).
2. **AI Parsing & Human Interaction Clarification**: Agent analyzes files and proactively asks questions when data is ambiguous or low-confidence.
3. **Deterministic Comparison & Matrix Evaluation**: Ruleset-based multi-supplier cost calculation (Decimal accuracy), hard constraint checking, and recommendation ranking.
4. **Human Approval & Decision Finalization**: Formal selection or no-award ("本轮流标") with cryptographically verified audit records and frozen evaluation metrics.
5. **Downstream Business Fulfillment**: Automated downstream transition to Purchase Orders (`OrderCenter`), AI contract drafting & clause risk analysis (`ContractCenter`), and 3-way invoice matching (`InvoiceCenter`).

This survey establishes the complete blueprint for **R2 (Dual-Pane AI & Canvas Layout)** and **R3 (Cockpit Dashboard & Business Centers Overhaul)** while preserving 100% test compatibility.

---

## 2. Comprehensive Directory & File Inventory

### Root & Configuration
| File Path | Description |
|---|---|
| `web/package.json` | Dependencies (`@tanstack/react-query`, `lucide-react`, `react`, `react-dom`), devDeps (`typescript`, `vite`, `vitest`, `jsdom`, `eslint`) |
| `web/vite.config.ts` | Custom build metadata plugin (`agentharness-build-meta`), API proxy to `http://127.0.0.1:8741`, JSDOM test runner |
| `web/tsconfig.app.json` | TypeScript configuration (ES2020 target, React JSX, strict mode) |
| `web/src/main.tsx` | App entry point, React Query Provider initialization (`staleTime: 2000, retry: 1`) |
| `web/src/App.tsx` | Root component: dark/light theme switcher, backend health check & version compatibility gate |

### State Management, URL & Routing
| File Path | Description | Key Exports / Hooks |
|---|---|---|
| `web/src/procurement/workbenchUrl.ts` | URL query state serialization/deserialization | `readWorkbenchUrl()`, `writeWorkbenchUrl()`, `WORKBENCH_VIEWS`, `TASK_TABS` |
| `web/src/procurement/useWorkbenchState.ts` | Core workbench navigation & active view state hook | `useWorkbenchState()` (view, selectedId, activeTab, filters, navigate) |
| `web/src/procurement/useRequestQueries.ts` | React Query server state coordinator | `useRequestQueries()` (detail, list, meta, configs, reviews, orders) |
| `web/src/procurement/useWorkbenchActions.ts` | Mutation action dispatcher & error handling | `useWorkbenchActions()` (startConversation, upload, correct, analyze, approve) |
| `web/src/procurement/roles.ts` | Role-based view filtering (Demo role switcher) | `readRole()`, `writeRole()`, `visibleViews()`, `ROLES` (`buyer`, `approver`, `admin`) |
| `web/src/useAgentStream.ts` | SSE real-time event streaming subscriber | `useAgentStream()` (EventSource `/api/stream?after={seq}`) |
| `web/src/procurement/viewModel.ts` | Procurement business view model & formatters | `statusLabel()`, `statusTone()`, `fulfillmentNextStep()`, `PROCUREMENT_DECISION_STEPS` |

### Views & Center Modules (`web/src/procurement/`)
| Component | Route / View | Key Responsibilities |
|---|---|---|
| `ProcurementWorkbench.tsx` | Main App Shell | Topbar, Rail navigation, task list sidebar, view dispatcher, drawers/dialogs |
| `WorkbenchNavigation.tsx` | Navigation Rail | Primary items (workbench, tasks, orders), business group, management group, attention counters |
| `WorkbenchHome.tsx` | `workbench` (Cockpit) | 4 KPI cards, AI intake conversation launcher, Todo quick strip, task queue, recent tasks table |
| `ProcurementConversation.tsx` | `tasks` (AI Stream) | Agent chat messages, tool execution progress, human reply form, recovery actions |
| `HumanInteractionPanel.tsx` | `tasks` (Interaction) | Structured question/answer form (fields, single/multi choice, file upload), submit/retry/cancel |
| `RequirementReview.tsx` | `tasks` -> `quotes` | Requirement specifications review/edit form (dimensions, material, lead time, budget) |
| `QuoteWorkspace.tsx` | `tasks` -> `quotes` | Supplier quote files tabs, extracted field editor, conflict resolution chips, analyze trigger |
| `ComparisonView.tsx` | `tasks` -> `compare` | Comparison matrix, qualification checks, landed cost breakdown, approve modal, no-award modal |
| `ReportView.tsx` | `tasks` -> `report` | Markdown procurement approval report, print, download, reopen/clone task |
| `AuditView.tsx` | `tasks` -> `audit` | Frozen evaluation metrics table, RunReport, JSON checkpoint inspector |
| `OrderCenter.tsx` | `orders` | Order cards, shipment toggle, multi-batch GRN reception modal, order cancellation, settlement table |
| `ContractCenter.tsx` | `contracts` | Contract draft generator, clause risk breakdown, consistency verification, approve/change modals |
| `InvoiceCenter.tsx` | `invoices` | Invoice upload, 3-way matching table (PO vs GRN vs Invoice), discrepancy explanation, force match |
| `SupplierCenter.tsx` | `suppliers` | Supplier cards, performance score breakdown, create/edit drawer, delete dialog, profile drawer |
| `ReviewCenter.tsx` | `reviews` | High-risk review workspace, AI recommendation justification, revise & approve, reject & retry |
| `ReportsCenter.tsx` | `reports` | KPI stats, status funnel, monthly trend bars, supplier win ranking, category distribution |
| `AuditLogCenter.tsx` | `audit` | Comprehensive audit trail table, filters by event type, actor, business type, task ID |
| `SystemInfo.tsx` | `system` | Service version, components health, parsers/rulesets, model config, LLM gateway breaker |
| `AiTaskCenter.tsx` | `ai` | AI task diagnostic workbench, trace viewer, execution step timeline, retry/cancel |
| `AiTaskRecovery.tsx` | `tasks` (Recovery) | Embedded AI task diagnostic card when active task encounters error/retry |
| `ConfigDrawer.tsx` | Global Drawer | API provider & model parameters config, cost limit per run |
| `DeleteDialog.tsx` | Global Modal | Task deletion confirmation modal |
| `NextStepBar.tsx` | `tasks` | Contextual next-step guidance bar with direct action button |

---

## 3. Page Structure & User Flow Analysis

### 3.1 Cockpit Dashboard (`WorkbenchHome.tsx`)
```
[ Topbar: Brand | Role Switcher | Runtime Status | Settings | Dark/Light Mode ]
---------------------------------------------------------------------------------
[ 4 KPI Metric Cards: Total Tasks | Pending Decision | In-Flight Orders | Risk Alerts ]
---------------------------------------------------------------------------------
[ AI Intake Command Center: NLP Prompt Input + Drag-and-Drop XLSX/PDF Quotes (>=2) ]
---------------------------------------------------------------------------------
[ Todo Quick Filter Strip: Agent Waiting | Field Reviews | Plan Confirmation | Receipts ]
---------------------------------------------------------------------------------
[ Dual Column Grid: Left: Active Todo Tasks Queue | Right: Actionable Exceptions & Alerts ]
---------------------------------------------------------------------------------
[ Recent Tasks High-Density Pro-Table: Reference | Title & Specs | Quotes | Status | Action ]
```
**User Flow**:
1. User enters dashboard, immediately views overall health and pending actions.
2. User can either launch a new procurement task by typing NLP requirement + uploading quotes, or click any KPI / Todo chip to filter down to tasks needing attention.
3. Clicking any task row navigates directly to the task detail view (`?view=tasks&task={id}`).

---

### 3.2 Task Workspace: Current vs. Target Dual-Pane Layout

#### Current Layout Architecture:
Currently in `ProcurementWorkbench.tsx`:
- Wide top banner: `proc-request-head` with item facts and multi-step progress bar (`procurementDecisionProgress` or `fulfillmentProgress`).
- Full-width banners above the main content: `HumanInteractionPanel`, `NextStepBar`, `proc-contract-entry`, `AiTaskRecovery`.
- Below the banners: `.proc-task-body` with CSS Grid:
  - Left pane: `.proc-conversation-shell` (narrow accordion 320–360px with `.proc-conversation`).
  - Right pane: `.proc-structured-workspace` with tab bar (`quotes`, `compare`, `report`, `audit`).

#### Target Cursor/Canvas Dual-Pane Architecture (R2):
```
+---------------------------------------------------------------------------------------------------+
|  Topbar: [采价台 Logo] [Role Switcher] [Service Status] [Settings] [Theme Toggle]                 |
+---------------------------------------------------------------------------------------------------+
| [Nav Rail] | [Task List] | Task Header Bar: Ref # | Title | Facts Pills | Progress Step Indicator |
|            | (Collapsible) -----------------------------------------------------------------------|
|            |             | Left Pane: AI Stream & Interaction  | Right Pane: Structured Canvas    |
|            |             | (Resizable / Collapsible)           | (Multi-Tab Interactive Canvas)   |
|            |             |-------------------------------------|----------------------------------|
|            |             | • Agent Chat Messages (User/Bot)    | [Tab 1: 报价与复核 (Quotes)]     |
|            |             | • Live Tool Execution Cards         |   - Requirement Specs Card       |
|            |             | • Inline HumanInteractionPanel      |   - Quote Files Tabs & Editor    |
|            |             | • Inline AiTaskRecovery Banner      |   - Conflict Resolution Chips    |
|            |             | • Clarification Reply Form          | [Tab 2: 供应商比价 (Compare)]    |
|            |             | • Streaming Reasoning Glow Indicator|   - Comparison Matrix Table      |
|            |             |                                     |   - Landed Cost Calculation      |
|            |             |                                     |   - Approve / No-Award Modals    |
|            |             |                                     | [Tab 3: 审批报告 (Report)]       |
|            |             |                                     |   - Full Markdown Report         |
|            |             |                                     |   - Print / Download / Reopen    |
|            |             |                                     | [Tab 4: 运行审计 (Audit)]        |
|            |             |                                     |   - Benchmark Accuracy Metrics   |
|            |             |                                     |   - Checkpoints & RunReport      |
+---------------------------------------------------------------------------------------------------+
```

---

### 3.3 Business Centers Deep Dive

#### 1. Orders Center (`OrderCenter.tsx`)
- **Status Pipeline**: `PENDING_SHIPMENT` -> `SHIPPED` -> `PARTIALLY_RECEIVED` / `RECEIVED` -> `CLOSED`.
- **Fulfillment Next Step Engine**: Evaluates order status, invoice matching status, and settlement status to generate exact contextual guidance.
- **Key Interactivity**:
  - "标记发货" (Ship) transition.
  - "确认收货" (Receive) modal with decimal arithmetic preventing over-receipt.
  - "取消订单" / "完成关闭" modals with reason input.
  - Settlement Table with "确认对账" (Settle) and "登记付款" (Pay) modal.
  - Interlocking rule: Payment is blocked until all active invoices are reconciled (`invoice_reconciled`).

#### 2. Contracts Center (`ContractCenter.tsx`)
- **Status Pipeline**: `DRAFT` -> `PENDING_APPROVAL` -> `EFFECTIVE` -> `EXECUTING` -> `CHANGE_REQUEST` -> `CLOSED`.
- **Key Interactivity**:
  - Draft generation directly from approved procurement task (auto-injecting supplier, landed amount, lead days).
  - Left contract list + Right contract document canvas.
  - Consistency checker: compares draft text against quote/award truth. If inconsistent, approval is blocked.
  - AI clause extraction with risk tagging (高风险/提示/低).
  - Change request workflow: input revised amount & lead days -> regenerates draft -> re-triggers consistency validation -> allow-once approval.

#### 3. Invoices Center (`InvoiceCenter.tsx`)
- **Status Pipeline**: `REGISTERED` -> `MATCHED` / `DIFF_HOLD` -> `RECONCILED` / `VOIDED`.
- **3-Way Matching Engine**:
  - PO (Purchase Order) vs GRN (Goods Receipt Note) vs Invoice.
  - Compares Quantity, Unit Price, Total Amount, Tax Rate using deterministic tolerance rules.
- **Key Interactivity**:
  - Upload invoice file associated with specific order.
  - Discrepancy explanation card by AI with concrete suggestions.
  - Action buttons: "手工改单" (Manual edit re-match), "强制通过" (Allow-once approval modal), "作废" (Void modal), "核销" (Reconcile).

#### 4. Supplier Center (`SupplierCenter.tsx`)
- **Key Interactivity**:
  - Supplier cards with performance rating (优质供应商 / 良好 / 一般 / 黑名单) and score bar breakdown (win rate, activity, status score).
  - Create / Edit drawer with field validation.
  - Delete protection dialog (rejects deletion if supplier has quote history).
  - Supplier Profile Drawer aggregating cooperation metrics, quoted item tags, and historical quotes timeline.

#### 5. Review Center (`ReviewCenter.tsx`)
- **Key Interactivity**:
  - Triage queue for high-risk / exceptional procurement decisions (no eligible quotes, conflicting fields, low confidence).
  - Multi-supplier comparison snapshot preview with AI suggested quote highlighted.
  - Human review decision modal: Approve AI Suggestion, Revise & Approve, Reject & Retry, or No Award.

#### 6. Reports & Analytics (`ReportsCenter.tsx`)
- Visual dashboard with KPI summary cards, status funnel bar chart, monthly volume/amount trend bars, supplier win leaderboard, category distribution bars, and AI frozen evaluation benchmarks.

#### 7. Audit Log Center (`AuditLogCenter.tsx`)
- Filter toolbar (Event type, Actor, Business type, Task ID) + paginated audit event timeline with JSON payload inspection.

#### 8. AI Task Diagnostics (`AiTaskCenter.tsx`)
- Diagnostic control center with status filter, time range, trace ID search, record step timeline, error category classification, and retry/cancel actions.

---

## 4. State Management, Event Dispatching & Data Flow

```
                                  +-------------------+
                                  |    App (Root)     |
                                  | Theme + Health    |
                                  +---------+---------+
                                            |
                                            v
                               +----------------------------+
                               |    ProcurementWorkbench    |
                               +-----+-------+--------+-----+
                                     |       |        |
         +---------------------------+       |        +---------------------------+
         |                                   |                                    |
         v                                   v                                    v
+------------------+             +----------------------+            +------------------------+
| useWorkbenchState|             |  useRequestQueries   |            |   useWorkbenchActions  |
| - URL sync       |             | - requestsQuery      |            | - startConversation    |
| - view/tab/q     |             | - detailQuery        |            | - uploadQuotes         |
| - selectedId     |             | - aiTasksQuery       |            | - correctField         |
| - role filtering |             | - reviewsQuery       |            | - correctRequirement   |
| - push/popstate  |             | - metaQuery          |            | - analyze/approve      |
+------------------+             +----------------------+            +------------------------+
         |                                   |                                    |
         +-----------------------------------+------------------------------------+
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
                   v                                                   v
      +-------------------------+                         +-------------------------+
      |  Left AI Stream Pane    |                         |  Right Canvas Tabs      |
      | - ProcurementConv       |                         | - QuoteWorkspace        |
      | - HumanInteractionPanel |                         | - ComparisonView        |
      | - AiTaskRecovery        |                         | - ReportView            |
      | - SSE EventStream       |                         | - AuditView             |
      +-------------------------+                         +-------------------------+
```

### Key State Characteristics:
1. **Zero External Routing Library**: State is synchronized directly to browser URL via `URLSearchParams` in `useWorkbenchState.ts`. All views, tasks, tabs, search queries, and filters are URL-shareable.
2. **TanStack React Query Cache Invalidation**: Every mutation in `useWorkbenchActions.ts` systematically invalidates affected query keys (`["procurement-requests"]`, `["procurement-request", id]`, `["procurement-interactions", id]`, `["procurement-contracts"]`, `["procurement-invoices"]`, `["procurement-settlements"]`).
3. **Idempotency Safeguard**: Actions touching backend transitions (`transitionOrder`, `transitionSettlement`, `answerInteraction`) utilize cryptographic idempotency keys (`newIdempotencyKey()`) to prevent accidental double-dispatch during network retries.
4. **SSE Liveness Driver**: Real-time event streaming (`useAgentStream.ts`) subscribes to `/api/stream?after={seq}`. Background queries refresh every 750ms to 5,000ms while runs/tasks are active, and turn off polling once in a terminal state.

---

## 5. Test & Semantic Compatibility Contract

All existing 13 test files and 80 unit tests rely on specific DOM structures, ARIA attributes, semantic tags, and `data-testid` attributes. The following contract **must be preserved with 100% fidelity** during any layout and styling overhaul:

### 5.1 Critical `data-testid` Elements
- `data-testid="conversation-upload"`: Quote file input inside `NewProcurementConversation` (`ProcurementConversation.tsx:225`).
- `data-testid="quote-upload"`: Quote file input inside `QuoteWorkspace` (`QuoteWorkspace.tsx:382`).
- `data-testid="invoice-upload"`: Invoice file input inside `InvoiceCenter` (`InvoiceCenter.tsx:200`).

### 5.2 Critical `aria-label` & `role` Selectors
| Role / Aria Label | Component | Used In Unit Tests |
|---|---|---|
| `role="dialog"` | Modals / Dialogs (`ComparisonView`, `DeleteDialog`, `OrderCenter`, `ContractCenter`, `InvoiceCenter`) | Esc-key close tests, backdrop click tests, confirm selection tests |
| `aria-label="采购任务视图"` | Tab Navigation in `ProcurementWorkbench.tsx` | Tab switching tests (`quotes`, `compare`, `report`, `audit`) |
| `aria-label="采购任务状态筛选"` | Filter Bar in `ProcurementWorkbench.tsx` | Task filtering tests (`all`, `attention`, `active`, `completed`) |
| `aria-label="搜索采购任务"` | Task Search Input | Search filter tests |
| `aria-label="供应商报价列表"` | `QuoteWorkspace.tsx` | Quote list container tests |
| `aria-label="报价字段复核"` | `QuoteWorkspace.tsx` | Extracted fields review tests |
| `aria-label="报价证据详情"` | `QuoteWorkspace.tsx` | Source SHA-256 evidence details tests |
| `aria-label="选择已定标任务"` | `ContractCenter.tsx` | Contract generation task selection tests |
| `aria-label="选择采购订单"` | `InvoiceCenter.tsx` | Invoice upload order selection tests |
| `aria-label="下一步引导"` | `NextStepBar.tsx` | Next step action guide tests |
| `aria-label="核心指标看板"` | `WorkbenchHome.tsx` | Cockpit KPI card click tests |
| `aria-label="待办中心"` | `WorkbenchHome.tsx` | Todo quick filter chip tests |
| `aria-label="Agent 等待回答"` | `HumanInteractionPanel.tsx` | Human interaction form & question card tests |
| `aria-label="AI 任务状态"` | `AiTaskRecovery.tsx` | AI task diagnostic recovery card tests |
| `aria-label="演示角色"` | `ProcurementWorkbench.tsx` | Role switcher select tests |

### 5.3 Test Suite Inventory & Coverage
1. `web/src/procurement/procurement.test.tsx` (30 tests) — Core workbench, intake, quote workspace, field editing, conflict resolution, comparison matrix, approval dialog, report markdown generator.
2. `web/src/procurement/orderCenter.test.tsx` (10 tests) — Orders listing, shipment toggle, multi-batch reception, decimal calculations, closing, settlements, and payment.
3. `web/src/procurement/HumanInteractionPanel.test.tsx` (6 tests) — Field review schema, single/multiple choice, date/number inputs, file upload schema, cancel/retry transitions.
4. `web/src/procurement/centers.test.tsx` (5 tests) — Supplier CRUD, delete protection, supplier profile score, and audit log filters.
5. `web/src/procurement/contractCenter.test.tsx` (3 tests) — Contract generation, consistency check, allow-once approval.
6. `web/src/procurement/invoiceCenter.test.tsx` (4 tests) — Invoice upload, 3-way matching table, diff display, force match.
7. `web/src/procurement/workbenchUrl.test.ts` (6 tests) — URL state roundtripping and query string parsing.
8. `web/src/procurement/viewModel.test.ts` (5 tests) — Next step rules, fulfillment progression, tone mappings.
9. `web/src/procurement/contracts.test.ts` (4 tests) — Contract state machine, change history snapshots.
10. `web/src/api/compatibility.test.ts` (3 tests) — Backend version and schema compatibility validator.
11. `web/src/useAgentStream.test.ts` (2 tests) — SSE event parsing and sequence filtering.
12. `web/src/procurement/systemInfo.test.tsx` (1 test) — System platform status and LLM gateway status.
13. `web/src/procurement/roles.test.ts` (1 test) — Role-based view visibility rules.

---

## 6. Concrete Architectural Recommendations for R2 & R3

### 6.1 Recommendation for R2: Dual-Pane AI & Canvas Workspace Layout

#### 1. Pane Architecture & Spatial Allocation
- **Container**: Refactor `.proc-task-body` from a standard horizontal grid into a modern **Dual-Pane Split Workspace**.
- **Left Pane (AI Collaboration Stream)**:
  - Default width: ~380px – 440px (resizable / collapsible).
  - Embeds the live AI conversation (`ProcurementConversation`), tool execution stream, and inline `HumanInteractionPanel`.
  - When the Agent pauses in `waiting_human` state, instead of showing a disconnected top-level banner, the `HumanInteractionPanel` renders directly as an active, highlighted card within the AI Stream, maintaining the natural conversational chronology.
  - When an AI task is retrying or failing, `AiTaskRecovery` renders inline within the stream with quick-action buttons.
  - Sleek collapsible toggle (`ChevronLeft` / `ChevronRight` or floating collapse trigger) to maximize the Canvas when needed.
- **Right Pane (Structured Procurement Canvas)**:
  - Takes `flex: 1` (fluid width).
  - Modern, responsive Tab Bar (`Quotes & Review`, `Supplier Comparison`, `Approval Report`, `Run Audit`).
  - Contains full interactive workspaces (`QuoteWorkspace` + `RequirementReview`, `ComparisonView`, `ReportView`, `AuditView`).
- **Header Bar Integration**:
  - Streamline `.proc-request-head` with modern glassmorphism, breadcrumbs (`采购任务 / RFQ-xxx`), status tone pill, compact facts pills (Item, Quantity, Specs, Max Lead Days), and a sleek progress step bar.
  - Embed `NextStepBar` cleanly below the facts row as an actionable status pill.

---

### 6.2 Recommendation for R3: Cockpit Dashboard & Business Centers Overhaul

#### 1. Cockpit Dashboard (`WorkbenchHome.tsx`)
- **KPI Stats Cards**:
  - Modern card design with subtle border gradients, icon containers with soft color fills (`primary`, `warning`, `info`, `danger`), big bold numbers, and subtitle descriptions.
  - Hover micro-interactions (subtle `translate-y-[-2px]`, soft shadow).
- **AI Command Center Launcher (`NewProcurementConversation embedded`)**:
  - Modernized NLP textarea with smooth focus glow.
  - Visual suggestion chips ("华东仓热敏不干胶标签采购...", "电商打包定制五层瓦楞纸箱...").
  - Clean drag-and-drop dropzone with distinct active states (`border-dashed`, subtle accent background).
  - File chips list with file type icons, byte formatters, and remove buttons.
- **Todo Action Strip & Alert Grid**:
  - Todo chips with count badges (`Agent 等待回答`, `等待字段复核`, `等待确认采购方案`, `待收货订单`, `发票差异待处理`, `付款被拦截`).
  - Two-column card grid: Left: Todo tasks queue; Right: High-risk exceptions.
- **Recent Tasks Pro-Table**:
  - Clean table typography, sticky table header, hover highlight, status chips with glowing indicator dots, quote count pill with icon, and clear action link.

#### 2. Business Centers Overhaul
- **Orders Center (`OrderCenter.tsx`)**:
  - Modernized status filter pills with active state indicators.
  - Order cards with status badges, facts grid, next-step guidance callouts, and clean action buttons.
  - Improved settlement table styling with invoice reconciliation warning tags.
- **Contracts Center (`ContractCenter.tsx`)**:
  - Two-column layout (Left: Contract items list; Right: Contract document canvas).
  - Visual clause risk cards with risk level badges (`高风险`, `提示`, `低`).
  - Draft text container with monospace typography, line clamping, and consistency match badges (`BadgeCheck` / `X`).
  - Contract change history timeline.
- **Invoices Center (`InvoiceCenter.tsx`)**:
  - Two-column layout (Left: Invoice list with diff indicators; Right: 3-way matching workspace).
  - 3-way matching table (PO vs GRN vs Invoice) with highlighted difference badges.
  - AI discrepancy reason box with structured bullet suggestions.
  - Action bar with primary reconcile CTA, manual edit modal, force-match modal, and void modal.
- **Supplier Center (`SupplierCenter.tsx`)**:
  - Grid card view with performance score badges, visual score bars (Win rate score, Activity score, Status score), main categories tags, and quote count statistics.
  - Smooth slide-out Supplier Profile Drawer.
- **Review Center (`ReviewCenter.tsx`)**:
  - Filter bar (status, risk flag, search).
  - Split review list + comparison evaluation workspace.
  - Decision action bar with approve, revise, retry, or no-award options.
- **Reports & Audit Centers (`ReportsCenter.tsx`, `AuditLogCenter.tsx`, `SystemInfo.tsx`)**:
  - Unified grid layouts, clean metric cards, responsive CSS bar charts, structured event timelines, and LLM gateway circuit breaker status visualizer.

---

## 7. Migration Safety & Implementation Plan

To ensure zero regressions across all 13 test files and 80 unit tests:
1. **Preserve Semantic HTML & Selectors**: Retain all existing HTML tag types, class names (supplement with Tailwind utility classes or maintain dual class names), `data-testid` attributes, `aria-label` attributes, form field `name` / `placeholder` attributes, and button text contents.
2. **Preserve State Hooks & Handlers**: Keep `useWorkbenchState`, `useRequestQueries`, `useWorkbenchActions`, `useEscape`, and `useAgentStream` APIs intact.
3. **Preserve Component Props & Contract Interfaces**: Maintain exact props signatures across all components in `src/procurement/`.
4. **Step-by-Step Verification**: Run `npm test -- --run` and `npm run build` after each component refactoring phase to immediately catch any attribute or selector mismatch.

---
*Report prepared by Explorer 2 (Component & Layout Explorer)*
