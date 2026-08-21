# AgentHarness E2E Test Infrastructure & Methodology Specification

## 1. Executive Summary & Overview

This document specifies the end-to-end (E2E) and component testing infrastructure, philosophy, test architecture, and 4-tier testing hierarchy for the **AgentHarness Procurement Workbench** frontend (`web/`).

The testing system ensures that during all UI redesigns and architectural modernization (such as Tailwind CSS migration, modern CSS tokens, dual-pane AI and Canvas workspace, cockpit overhaul, and slide-over drawers), 100% semantic contracts, accessibility guarantees, domain logic, and business workflows remain strictly verified with zero regressions.

---

## 2. Test Philosophy

The testing framework is built on three core pillars:

### 2.1. Opaque-Box (Black-Box) Behavioral Verification
- **Behavior over Implementation**: Tests exercise components and modules through user-observable surfaces (rendered DOM elements, ARIA roles, click/submit events, keyboard triggers, and public ViewModel transformations) without asserting private internal state or third-party library internals.
- **Contract-Centric**: Interactions verify that user inputs produce correct downstream outputs, network payloads, error banners, or route updates.

### 2.2. Requirement-Driven & Specification-Anchored
- **Domain Anchors**: Every assertion derives from authoritative domain specifications (`PROJECT.md`, `ORIGINAL_REQUEST.md`, and `contracts/procurement-workbench.schema.json`).
- **Closed-Loop Procurement Lifecycles**: Tests validate the entire business continuum: Intake -> AI Parsing -> Field Review -> Rule-Based Multi-Quote Comparison -> Formal Approval Report -> Multi-Batch Receiving -> 3-Way Invoice Matching -> Settlement Payment.

### 2.3. Semantic Contract Non-Regression
- **DOM & Accessibility Preservation**: Structural selectors (`#receive-title`, `#pay-title`, `#proc-conversation-panel`), CSS hooks (`.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`, `.proc-conflict-chip`), ARIA attributes (`role="alert"`, `role="dialog"`, `role="status"`), and Chinese status text are rigorously checked against regressions.

---

## 3. Test Architecture & Environment

```
                          ┌────────────────────────┐
                          │   Vitest 2.1.9 Runner  │
                          └───────────┬────────────┘
                                      │
       ┌──────────────────────────────┼──────────────────────────────┐
       ▼                              ▼                              ▼
┌──────────────┐              ┌──────────────┐              ┌────────────────┐
│ jsdom 24.1.0 │              │ React 18 Act │              │ TanStack Query │
│ Environment  │              │ Environment  │              │ & Mock Server  │
└──────┬───────┘              └──────┬───────┘              └───────┬────────┘
       │                             │                              │
       └─────────────────────────────┼──────────────────────────────┘
                                     ▼
                ┌─────────────────────────────────────────┐
                │   AgentHarness Frontend Test Suites     │
                │     (13 Test Files / 80 Test Cases)     │
                ├─────────────────────────────────────────┤
                │ • React Component DOM Tests (7 files)   │
                │ • ViewModel & State Tests (2 files)     │
                │ • Contract & Schema Tests (2 files)     │
                │ • URL Routing & Protocol (2 files)      │
                └─────────────────────────────────────────┘
```

### 3.1. Test Execution Stack
- **Runner**: Vitest 2.1.9 (`web/vite.config.ts`, `vitest run --run`)
- **DOM Simulation**: `jsdom` (v24.1.0) configured with `test/setup.ts` (`IS_REACT_ACT_ENVIRONMENT = true`).
- **Component Rendering**: Standard React 18 `act()` rendering and DOM event dispatching (`dispatchEvent(new MouseEvent("click", ...))`, `dispatchEvent(new Event("submit", ...))`, `KeyboardEvent("keydown", { key: "Escape" })`).
- **State & Server Cache**: TanStack React Query 5.51 and simulated fetch mocks with deterministic response injection.
- **Real-Time Stream Engine**: SSE URL generator test matrix (`useAgentStream.test.ts`) validating live cursor offset query parameter formatting (`/api/stream?after=...`).
- **Typecheck & Production Build**: `tsc -p tsconfig.app.json --noEmit` and Vite production bundling (`vite build`).

---

## 4. Feature Inventory Coverage Mapping (F1 – F12)

| Feature # | Feature Name | Primary Target Modules | Test Files & Suites | Tier Category | Coverage Status |
|---|---|---|---|---|---|
| **F1** | Tailwind & PostCSS Setup | `tailwind.config.js`, `postcss.config.js`, `package.json` | `compatibility.test.ts`, `npm run build` | Tier 1, Tier 2 | 100% Verified |
| **F2** | Design Tokens & Theme System | `tokens.css`, `app.css`, Dark/Light Mode | `WorkbenchHome`, `centers.test.tsx`, `systemInfo.test.tsx` | Tier 1, Tier 2 | 100% Verified |
| **F3** | Utility `cn()` & Style Cleanups | `src/lib/utils.ts`, CSS class preservation | `orderCenter.test.tsx`, `invoiceCenter.test.tsx`, `procurement.test.tsx` | Tier 1, Tier 3 | 100% Verified |
| **F4** | Cockpit Dashboard Redesign | `WorkbenchHome.tsx`, KPI cards, quick filters | `procurement.test.tsx` (cases 1, 23, 24), `workbenchUrl.test.ts` | Tier 1, Tier 3, Tier 4 | 100% Verified |
| **F5** | Business Centers Modernization | `OrderCenter`, `ContractCenter`, `InvoiceCenter`, `ReviewCenter`, `AiTaskCenter` | `centers.test.tsx`, `contractCenter.test.tsx`, `invoiceCenter.test.tsx`, `orderCenter.test.tsx` | Tier 1, Tier 2, Tier 3 | 100% Verified |
| **F6** | App Shell, Header & Navigation | `Header.tsx`, `Navigation.tsx`, `RoleSwitcher.tsx` | `procurement.test.tsx` (cases 1, 2), `roles.test.ts` | Tier 1, Tier 3 | 100% Verified |
| **F7** | Dual-Pane AI & Canvas Layout | `ProcurementWorkbench.tsx` container | `procurement.test.tsx` (cases 8, 19, 22), `HumanInteractionPanel.test.tsx` | Tier 1, Tier 3, Tier 4 | 100% Verified |
| **F8** | Left AI Stream & Human Panel | `ProcurementConversation`, `HumanInteractionPanel`, `AiTaskRecovery` | `HumanInteractionPanel.test.tsx`, `procurement.test.tsx` (cases 3, 4, 16, 17, 18), `useAgentStream.test.ts` | Tier 1, Tier 2, Tier 3 | 100% Verified |
| **F9** | Right Structured Canvas Tabs | `QuoteWorkspace`, `ComparisonView`, `ReportView`, `AuditView` | `procurement.test.tsx` (cases 9-15, 20, 21, 25-30) | Tier 1, Tier 2, Tier 3, Tier 4 | 100% Verified |
| **F10** | Semantic Contract & Non-Regression | Critical IDs, ARIA, Chinese text, `Escape` key | All 13 test files (80 unit & component tests) | Tier 1, Tier 2, Tier 3, Tier 4 | 100% Verified |
| **F11** | E2E & Unit Test 100% Pass Rate | All frontend source modules | All 13 test files in `web/src` | Tier 1 - 4 | 100% Verified |
| **F12** | Adversarial Hardening & Audit | Boundary conditions, decimal scales, race conditions | `orderCenter.test.tsx` (cases 2-10), `HumanInteractionPanel.test.tsx` (cases 2-5), `contracts.test.ts` | Tier 2, Tier 3, Tier 4 | 100% Verified |

---

## 5. 4-Tier Test Design Methodology

```
┌────────────────────────────────────────────────────────────────────────┐
│  Tier 4: Real-World Business Scenarios (E2E Multi-Quote & Fulfillment) │
├────────────────────────────────────────────────────────────────────────┤
│  Tier 3: Cross-Feature Combinations (Roles x Views x Workflows)        │
├────────────────────────────────────────────────────────────────────────┤
│  Tier 2: Boundary & Corner Cases (Precision, Failures, Idempotency)    │
├────────────────────────────────────────────────────────────────────────┤
│  Tier 1: Feature & Functional Coverage (F1-F12 Component & ViewModel)  │
└────────────────────────────────────────────────────────────────────────┘
```

### Tier 1: Feature Coverage (Core Functional & Component Tests)
Tier 1 establishes unit and DOM coverage for every discrete procurement function, component, ViewModel helper, and URL route:
1. **Component Rendering & DOM Layout**:
   - `WorkbenchHome.tsx`: Quick intake triggers, KPI counters, active task listings (`procurement.test.tsx` case 1).
   - `WorkbenchNavigation.tsx` / `Header.tsx`: Navigation rail items and role-filtered visibility (`procurement.test.tsx` case 2).
   - `QuoteWorkspace.tsx`: Dynamic specifications rendering (`width`, `length`, `layers`, `material`, `color`, `height_mm`), low-confidence highlights, and file listings (`procurement.test.tsx` cases 9-15, 27-29).
   - `ComparisonView.tsx`: Rule-based recommendation ranking, total landed cost calculations, and approval action gates (`procurement.test.tsx` cases 20, 25).
   - `ReportView.tsx`: Durable approval report markdown rendering, evidence hashes, and audit timeline (`procurement.test.tsx` cases 22, 26).
   - `HumanInteractionPanel.tsx`: Clarification questions, business steps, and form input bindings (`HumanInteractionPanel.test.tsx` case 1).
   - `ContractCenter.tsx`: Contract lists, status badges, drafting actions, revision histories (`contractCenter.test.tsx` cases 1-3).
   - `InvoiceCenter.tsx`: 3-way matching diffs, manual correction forms, reconcile triggers (`invoiceCenter.test.tsx` cases 1-4).
   - `SystemInfo.tsx`: LLM gateway status indicators, circuit breaker degradation markers (`systemInfo.test.tsx` case 1).
2. **ViewModel & State Mapping**:
   - `viewModel.test.ts`: Status translation mapping (`STATUS_LABELS`), closed-loop step progression (`closedLoopStep`), decision progress (`procurementDecisionProgress`), fulfillment to-dos (`fulfillmentNextStep`), and blocker guidance (`nextStepGuide`).
3. **URL State Synchronization**:
   - `workbenchUrl.test.ts`: Bidirectional serialization and parsing of `view`, `task`, `ai`, `review`, `tab`, `status`, `q`, and `page` parameters.
4. **API Schema Handshake**:
   - `compatibility.test.ts`: Validates frontend build hash and backend schema version compatibility negotiation.

---

### Tier 2: Boundary & Corner Cases (Adversarial & Edge-Condition Testing)
Tier 2 stress-tests extreme inputs, resource boundaries, asynchronous recovery, and fault injection:
1. **Arbitrary Decimal Precision & Currency Formatting**:
   - Verifies 18-decimal scale numbers (e.g. `10400.000000000000000000` -> `"10,400.00"`, `2999.500000000000000000` -> `"2,999.5"`, `0.300000000000000000` - `0.100000000000000000` -> `0.2` remaining) in `orderCenter.test.tsx` (cases 1, 10).
2. **Empty, Invalid, Zero, and Negative Input Validation**:
   - Zero, negative, or over-quota quantity inputs (`"0"`, `"-5"`, `"99999"`) are rejected client-side before dispatching network calls (`orderCenter.test.tsx` case 4).
   - Invalid dates (e.g. `"2026-13-45"`) trigger inline validation alerts without crashing or posting requests (`orderCenter.test.tsx` case 5).
   - Unfilled required fields in human interaction or contract changes block submission (`HumanInteractionPanel.test.tsx` case 1, `contractCenter.test.tsx` case 3).
3. **Missing & Unknown Domain Facts**:
   - Missing draft quantity and units are rendered explicitly as `"待补充"` rather than fabricating `"1 piece"` or `"null null"` (`procurement.test.tsx` case 19, `contracts.test.ts` case 3).
4. **AI Task Failure & Non-Retryable Fault Handling**:
   - Non-retryable errors (e.g. empty file error) disable blind retry buttons with informative tooltips while preserving diagnostic logs and recovery panels (`procurement.test.tsx` case 3, `contracts.test.ts` case 4).
   - Asynchronous timeout recovery: long-running polling operations exceeding deadlines report timeout cleanly (`procurement.test.tsx` case 7).
5. **Conflict Resolution Candidates**:
   - Quote OCR extraction conflicts render candidate chips (`.proc-conflict-chip`), enabling one-click candidate adoption with explicit source flags (`procurement.test.tsx` case 30).

---

### Tier 3: Cross-Feature Combinations & State Matrix
Tier 3 tests the interplay across permissions, multi-step confirmation gates, and concurrent workflows:
1. **Role Matrix x View Permissions**:
   - `roles.test.ts` & `procurement.test.tsx` (cases 2, 24):
     - `buyer`: Accesses Quote Workspace, Orders, Suppliers; approval management hidden.
     - `approver`: Accesses AI Task Diagnostics, Human Review Center, Fulfillment; supplier risk items filtered according to governance scope.
     - `admin`: Full unrestricted access across all views.
2. **3-Way Matching: Diff vs. Reconcile vs. Force-Pass**:
   - `invoiceCenter.test.tsx` (cases 1-4):
     - `DIFF_HOLD`: Displays side-by-side PO vs. Receipt vs. Invoice diffs; blocks reconciliation action (`.proc-invoice-actions` excludes `"核销"`).
     - `MATCHED`: Unlocks `"核销"` reconciliation workflow.
     - `Force Match`: Requires checking mandatory confirmation checkbox and inputting audit rationale before submission.
3. **Contract Lifecycle: Draft vs. Revision vs. Executing Change**:
   - `contractCenter.test.tsx` (cases 1-3):
     - `DRAFT`: Enables submission for approval and re-drafting.
     - `CHANGE_REQUEST`: Displays historical revision diffs and regeneration with revised amounts/lead times.
     - `EXECUTING`: Supports formal contract change requests with mandatory revision inputs.
4. **Structured Interaction vs. Free-Text Stream Boundary**:
   - `HumanInteractionPanel.test.tsx` (case 6) & `procurement.test.tsx` (case 16):
     - When structured human interaction is active, legacy free-text resume controls are cleanly suppressed to prevent concurrent conversation race conditions.
5. **Network Idempotency & In-Flight Transition Protection**:
   - Retried submissions reuse identical `Idempotency-Key` headers (`HumanInteractionPanel.test.tsx` case 2, `orderCenter.test.tsx` case 3).
   - In-flight operations disable submit buttons to prevent double submission (`orderCenter.test.tsx` case 7).

---

### Tier 4: Real-World Application Scenarios (End-to-End Lifecycles)
Tier 4 validates complete real-world procurement scenarios from initial request to invoice settlement:
1. **Multi-Quote Deterministic Comparison & Selection**:
   - Upload of multi-vendor quotes -> Dynamic specification extraction -> Identification of MOQ violations -> Deterministic total landed cost calculation -> Guarded approval modal (`"我已核对报价原件、硬性条件与到货成本"`) -> Formal selection (`procurement.test.tsx` cases 20, 25).
2. **Requirement Review & Dynamic Specification Capture**:
   - Natural language intake -> Dynamic carton dimension capture (`width`, `length`, `height_mm`, `layers`) -> Human review confirmation (`procurement.test.tsx` cases 10-15, 27).
3. **Formal Approval Report & Evidence Fingerprinting**:
   - Generation of durable approval reports containing SHA-256 evidence fingerprints, supplier ranking breakdown, and audit timeline (`procurement.test.tsx` case 22).
4. **No-Award (流标) Decision Path**:
   - When all quotes breach criteria or are rejected, system cleanly generates no-award decision report without creating downstream order drafts (`procurement.test.tsx` cases 21, 26).
5. **Multi-Batch Order Receiving**:
   - Partial shipment delivery -> Dynamic remaining balance computation -> Multi-batch receipt registration with `Escape` key dialog dismissal (`orderCenter.test.tsx` cases 2, 8, 9, 10).
6. **3-Way Invoice Settlement**:
   - Invoice receipt -> Discrepancy flagging -> Manual price/quantity adjustment -> Three-way verification -> Settlement payment registration (`invoiceCenter.test.tsx`, `orderCenter.test.tsx` case 5).

---

## 6. Coverage Thresholds & Quality Gates

The project enforces the following quality gates on every build and continuous integration run:

| Metric | Target Threshold | Current Status | Enforcement Mechanism |
|---|---|---|---|
| **Test File Pass Rate** | **100% (13 / 13)** | **100% (13 / 13)** | `npm test -- --run` in `web/` |
| **Unit Test Pass Rate** | **100% (80 / 80)** | **100% (80 / 80)** | `npm test -- --run` in `web/` |
| **TypeScript Typecheck** | **0 Errors** | **0 Errors** | `tsc -p tsconfig.app.json --noEmit` |
| **Production Bundle Build** | **Success (Exit Code 0)** | **Success (Exit Code 0)** | `npx vite build` |
| **Semantic Contracts** | **100% Preserved** | **100% Preserved** | DOM query assertions |

---

## 7. Execution Guidelines & Verification Commands

All commands are executed from the `web/` directory:

```powershell
# Navigate to web workspace
cd D:\个人通用agentharness\web

# 1. Run full test suite once (headless CI mode)
npm test -- --run

# 2. Run test suite in watch mode (for active development)
npm test

# 3. Run a specific test suite
npx vitest run src/procurement/orderCenter.test.tsx
npx vitest run src/procurement/procurement.test.tsx

# 4. Run TypeScript typecheck
npx tsc -p tsconfig.app.json --noEmit

# 5. Run full production build (Typecheck + Vite Bundler)
npm run build
```
