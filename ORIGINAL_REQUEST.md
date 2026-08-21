# Original User Request

## Initial Request — 2026-08-19T15:46:40Z

Refactor the frontend of AgentHarness (Procurement Workbench) into a modern, Cursor/Canvas-inspired AI collaborative workspace using Tailwind CSS, featuring a dual-pane split canvas layout and maintaining 100% test compatibility.

Working directory: D:\个人通用agentharness\web
Integrity mode: development

## Requirements

### R1. Design System & Styling Foundation
Integrate Tailwind CSS, PostCSS, and a modern CSS variable theme system supporting dark/light modes, subtle glassmorphism, glowing pulse accents (glow-pulse), and clean typography.

### R2. Dual-Pane AI & Canvas Workspace Layout
Restructure the task workspace into a split-pane layout with the AI Agent conversation/reasoning stream and human interaction panel on the left (collapsible/resizable), and the structured procurement canvas (quotes extraction, comparison matrix, approval reports, contracts) on the right.

### R3. Cockpit Dashboard & Business Centers Overhaul
Redesign the homepage cockpit with dynamic KPI stats and a sleek natural language launcher, and modernize the data grids and detail drawers across Orders, Contracts, Invoices (3-way matching), Suppliers, and Reviews centers.

### R4. Test & Semantic Compatibility Preservation
Retain all semantic element IDs, aria-* attributes, test attributes, and event handlers so that all existing Vitest test suites and TypeScript builds pass without regressions.

## Acceptance Criteria

### Automated Verification
- [ ] npm test -- --run passes all 13 test files and 80+ unit tests with 0 failures in web/.
- [ ] npm run build (TypeScript check and Vite build) completes successfully with 0 errors.

### Visual & Interactive Quality
- [ ] The app renders clean, modern Cursor/Canvas aesthetic across light and dark themes.
- [ ] Task detail view functions seamlessly with split-pane AI stream and structured canvas tabs.
- [ ] Navigation, role switcher, drawer configuration, and KPI cockpit cards operate smoothly.
