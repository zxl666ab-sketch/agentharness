# Orchestration Plan: AgentHarness Frontend Refactor

## 1. Objectives & Scope
Refactor the frontend of AgentHarness (Procurement Workbench) in `web/` into a modern Cursor/Canvas-inspired AI collaborative workspace using Tailwind CSS, featuring dual-pane split canvas layout and maintaining 100% test compatibility.

## 2. Phase Breakdown
### Phase 0: Codebase Survey (3 Explorers)
- **Explorer 1 (Infra & Styling)**: Analyze package.json, vite.config.ts, index.html, CSS architecture, Tailwind setup requirements, theme tokens (light/dark, glassmorphism, glow-pulse).
- **Explorer 2 (Components & Canvas)**: Map component hierarchy in `web/src/` (Task workspace, AI reasoning stream, canvas tabs, Cockpit dashboard, Business Centers: Orders, Contracts, Invoices, Suppliers, Reviews).
- **Explorer 3 (Tests & Semantic Contracts)**: Map all 13 test files in `web/tests/` or `web/src/`, all DOM selectors, IDs, aria attributes, test attributes, and event handlers to ensure 0 regressions.

### Phase 1: Synthesize Survey & Generate PROJECT.md
- Architecture and Component Map
- Feature Inventory (R1-R4) mapped to milestones
- Milestone Decomposition with interface contracts and write ownership
- Code Layout and styling standards

### Phase 2: Dual Track Execution
- **E2E Testing Track**: Build & verify test suite and create `TEST_READY.md`.
- **Implementation Track**: Sub-orchestrators for milestones:
  - Milestone 1: Tailwind CSS / PostCSS / Design System Foundation & Theme Switcher
  - Milestone 2: Cockpit Dashboard & Business Centers UI Overhaul
  - Milestone 3: Dual-Pane AI & Canvas Workspace Layout Overhaul
  - Milestone 4: Final Integration, 100% Test Passing, Adversarial Hardening, Forensic Audit

### Phase 3: Verification & Victory Audit
- Automated check: `npm test -- --run` passes all test suites.
- Build check: `npm run build` succeeds with 0 errors.
- Visual & layout inspection.
- Forensic Auditor clean verdict.
