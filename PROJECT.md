# Project: AgentHarness Frontend Refactor (Cursor/Canvas Modernization)

## Architecture
- **Framework & Tooling**: React 18.3, Vite 5.3, TypeScript 5.5, TanStack Query 5.51, Lucide React 0.414, Tailwind CSS 3.4, PostCSS 8.4, Autoprefixer 10.4, clsx 2.1, tailwind-merge 2.6.
- **State Management & Navigation**: Pure query-param driven routing via `useWorkbenchState` and `workbenchUrl.ts`. Server state cached via React Query. Real-time reasoning stream via `useAgentStream` (SSE).
- **Design System & Theme Tokens**:
  - CSS variables in `tokens.css` with dark mode support (`:root[data-theme="dark"]`).
  - Tailwind CSS configured to extend tokens (`bg-surface`, `text-text`, `border-border`, `accent-accent`, etc.).
  - Glassmorphism backdrop blur (`backdrop-blur-md bg-surface/80 border border-border/60 shadow-glass`), Glowing pulse animations (`glow-pulse`), sleek typography and rounded corners.
- **Workspace Architecture**:
  - **Cockpit View**: High-density KPI cards, natural language intake launcher, quick filter strip, dual-column tasks & exceptions, recent tasks pro-table.
  - **Task Dual-Pane Workspace**:
    - **Left Pane (AI Stream & Interaction)**: Collapsible/resizable (320px-480px default), housing conversational timeline, human interaction form (`HumanInteractionPanel`), tool call executions, live agent status badge, and recovery actions (`AiTaskRecovery`).
    - **Right Pane (Structured Procurement Canvas)**: Multi-tab workspace housing Quotes Extraction (`QuoteWorkspace`), Supplier Comparison Matrix (`ComparisonView`), Decision & Approval Report (`ReportView`), and Audit/Trace Logs (`AuditView`).
  - **Business Centers**: Standardized pro-table data grids, filter bars, status badges, action toolbars, and slide-over detail drawers across Orders, Contracts, Invoices (3-way matching), Suppliers, Reviews, Reports, and Audit Logs.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Tailwind & PostCSS Setup | Install tailwindcss, postcss, autoprefixer, clsx, tailwind-merge; create configs | M1 | R1 |
| F2 | Design Tokens & Theme System | Map CSS variables in tokens.css to Tailwind, dark/light theme switching, glassmorphism, glow-pulse | M1 | R1 |
| F3 | Utility cn() & Style Cleanups | Implement `src/lib/utils.ts` with `cn()` helper, cleanup unused icon imports | M1 | R1 |
| F4 | Cockpit Dashboard Redesign | Dynamic KPI cards, natural language task launcher with drag-drop upload, filter chips, task tables | M2 | R3 |
| F5 | Business Centers Modernization | Modern data grids, detail drawers, status badges for Orders, Contracts, Invoices, Suppliers, Reviews, Reports, Audit | M2 | R3 |
| F6 | App Shell, Header & Navigation | Modern header with theme toggle, role switcher, system info modal, modern responsive navigation | M2 | R3 |
| F7 | Dual-Pane AI & Canvas Layout | Split-pane task workspace with resizable left AI pane and right structured canvas | M3 | R2 |
| F8 | Left AI Stream & Human Panel | Integrated chat stream, inline human interaction panel, tool cards, recovery panel in left pane | M3 | R2 |
| F9 | Right Structured Canvas Tabs | Modernized QuoteWorkspace, ComparisonView, ReportView, AuditView in right canvas | M3 | R2 |
| F10 | Semantic Contract & Test Non-Regression | Retain 100% element IDs, ARIA roles, class hooks, test attributes, Chinese strings, Escape key listeners | M1-M4 | R4 |
| F11 | E2E & Unit Test 100% Pass Rate | All 13 test files and 80+ unit tests passing with 0 failures | M4 | AC |
| F12 | Adversarial Hardening & Audit | Code coverage audit, adversarial edge-case stress testing, forensic integrity audit | M4 | AC |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Design System & Styling Foundation | Tailwind CSS + PostCSS configuration, tokens.css mapping, dark/light theme toggle, glassmorphism, glow-pulse, `cn()` utility, lint cleanup | none | DONE |
| M2 | Cockpit Dashboard & Business Centers Overhaul | Redesign `WorkbenchHome.tsx`, `Navigation.tsx`, `Header.tsx`, `RoleSwitcher.tsx`, `OrderCenter.tsx`, `ContractCenter.tsx`, `InvoiceCenter.tsx`, `SupplierCenter.tsx`, `ReviewCenter.tsx`, `ReportsCenter.tsx`, `AuditLogCenter.tsx`, `AiTaskCenter.tsx` | M1 | DONE |
| M3 | Dual-Pane AI & Canvas Workspace Layout | Redesign `ProcurementWorkbench.tsx`, `ProcurementConversation.tsx`, `HumanInteractionPanel.tsx`, `AiTaskRecovery.tsx`, `QuoteWorkspace.tsx`, `ComparisonView.tsx`, `ReportView.tsx`, `AuditView.tsx`, `NextStepBar.tsx` | M1, M2 | DONE |
| M4 | Final Integration, Test Pass & Hardening | Full test run (16 test files, 111+ tests), Phase 2 Adversarial coverage hardening, Forensic integrity audit | M3 | DONE |

---

## Interface Contracts & Semantic Compatibility (R4)

### 1. Critical DOM IDs
- `#receive-title` — Used in multi-batch receipt dialogs in `OrderCenter.tsx`.
- `#pay-title` — Used in settlement payment dialogs in `OrderCenter.tsx`.
- `#proc-conversation-panel` — Used in `ProcurementConversation.tsx`.
- `#proc-requirement-review-${id}` — Used in requirement review list.
- `#close-title`, `#approve-contract-title`, `#change-contract-title`, `#void-invoice-title`, `#force-invoice-title`, `#correct-invoice-title`, `#supplier-form-title`, `#delete-supplier-title`, `#supplier-profile-title`, `#review-confirm-title`.

### 2. Semantic CSS Class Names
Must be preserved alongside new Tailwind utility classes:
- `.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`
- `.proc-home-section`, `.proc-conflict-chip`, `details.proc-evidence-panel`
- `.effect-badge`, `.run-report`, `.proc-app`

### 3. ARIA Roles & Labels
- `role="alert"` — Error and warning notification boxes
- `role="dialog"` — All modals (receipt, payment, approval, force-match, delete confirmation)
- `role="status"` — Loading, status indicators
- `aria-label="补充澄清信息"` — Human interaction form header
- `aria-label="采购任务视图"`, `aria-label="采购任务状态筛选"`, `aria-label="演示角色"`, `aria-label="履约进度"`, `aria-label="采购决策进度"`

### 4. Interactive Behaviors & Two-Step Actions
- `Escape` key listener on `window` to close modals and drawers.
- Two-step cancellation: 1st click changes text to `"再次点击确认取消"`, 2nd click dispatches cancel request.
- Guarded approval checkbox: `"我已核对报价原件、硬性条件与到货成本"` required before enabling `"确认选定"`.
- Guarded force-match: `"强制通过必须勾选确认并填写人工备注"`.

---

## Code Layout & Write Ownership
```
web/
├── package.json                   [M1: DONE]
├── postcss.config.js              [M1: DONE]
├── tailwind.config.js             [M1: DONE]
├── src/
│   ├── lib/
│   │   └── utils.ts               [M1: DONE]
│   ├── styles/
│   │   ├── tokens.css             [M1: DONE]
│   │   └── app.css                [M1: DONE]
│   ├── components/                [M2: DONE]
│   ├── procurement/
│   │   ├── WorkbenchHome.tsx      [M2: DONE]
│   │   ├── OrderCenter.tsx        [M2: DONE]
│   │   ├── ContractCenter.tsx     [M2: DONE]
│   │   ├── InvoiceCenter.tsx      [M2: DONE]
│   │   ├── SupplierCenter.tsx     [M2: DONE]
│   │   ├── ReviewCenter.tsx       [M2: DONE]
│   │   ├── ReportsCenter.tsx      [M2: DONE]
│   │   ├── AuditLogCenter.tsx     [M2: DONE]
│   │   ├── AiTaskCenter.tsx       [M2: DONE]
│   │   ├── Navigation.tsx         [M2: DONE]
│   │   ├── Header.tsx             [M2: DONE]
│   │   ├── RoleSwitcher.tsx       [M2: DONE]
│   │   ├── ProcurementWorkbench.tsx [M3: DONE]
│   │   ├── ProcurementConversation.tsx [M3: DONE]
│   │   ├── HumanInteractionPanel.tsx [M3: DONE]
│   │   ├── AiTaskRecovery.tsx     [M3: DONE]
│   │   ├── QuoteWorkspace.tsx     [M3: DONE]
│   │   ├── ComparisonView.tsx     [M3: DONE]
│   │   ├── ReportView.tsx         [M3: DONE]
│   │   ├── AuditView.tsx          [M3: DONE]
│   │   └── NextStepBar.tsx        [M3: DONE]
```
