# Scope: E2E Testing Track

## Architecture
- **Test Runner & Environment**: Vitest 2.1.9 + jsdom 24.1.0 in `web/` (`npm test -- --run`)
- **Build & Typecheck**: `tsc -p tsconfig.app.json --noEmit && vite build` (`npm run build`)
- **Test Matrix**: 13 test files, 80 unit & component test cases covering React components, API compatibility, JSON Schema contracts, URL state routing, view models, and real-time SSE stream cursors.

## Feature Inventory Coverage (F1-F12)
| # | Feature | Scope / Module | Test Files | Coverage Tier | Status |
|---|---------|----------------|------------|---------------|--------|
| F1 | Tailwind & PostCSS Setup | Build configuration & styling pipeline | `npm run build`, `compatibility.test.ts` | Tier 1, Tier 2 | VERIFIED |
| F2 | Design Tokens & Theme System | `tokens.css`, Dark/Light mode, Glassmorphism | `WorkbenchHome`, `centers.test.tsx`, `systemInfo.test.tsx` | Tier 1, Tier 2 | VERIFIED |
| F3 | Utility cn() & Style Cleanups | Class merging & selector stability | `orderCenter.test.tsx`, `invoiceCenter.test.tsx`, `procurement.test.tsx` | Tier 1, Tier 3 | VERIFIED |
| F4 | Cockpit Dashboard Redesign | KPI cards, task launchers, quick filters, deep links | `procurement.test.tsx` (cases 1, 23, 24), `workbenchUrl.test.ts` | Tier 1, Tier 3, Tier 4 | VERIFIED |
| F5 | Business Centers Modernization | Orders, Contracts, Invoices, Suppliers, Reviews, Audit | `centers.test.tsx`, `contractCenter.test.tsx`, `invoiceCenter.test.tsx`, `orderCenter.test.tsx` | Tier 1, Tier 2, Tier 3 | VERIFIED |
| F6 | App Shell, Header & Navigation | Header, RoleSwitcher, Navigation rail | `procurement.test.tsx` (cases 1, 2), `roles.test.ts` | Tier 1, Tier 3 | VERIFIED |
| F7 | Dual-Pane AI & Canvas Layout | Split-pane workspace container | `procurement.test.tsx` (cases 8, 19, 22), `HumanInteractionPanel.test.tsx` | Tier 1, Tier 3, Tier 4 | VERIFIED |
| F8 | Left AI Stream & Human Panel | Chat timeline, human interaction, tool calls, recovery | `HumanInteractionPanel.test.tsx`, `procurement.test.tsx` (cases 3, 4, 16, 17, 18), `useAgentStream.test.ts` | Tier 1, Tier 2, Tier 3 | VERIFIED |
| F9 | Right Structured Canvas Tabs | Quotes, Comparison, Reports, Audit tabs | `procurement.test.tsx` (cases 9-15, 20, 21, 25-30) | Tier 1, Tier 2, Tier 3, Tier 4 | VERIFIED |
| F10 | Semantic Contract & Non-Regression | IDs, ARIA roles, class hooks, Chinese text, Escape | All 13 test files (80 tests) | Tier 1, Tier 2, Tier 3, Tier 4 | VERIFIED |
| F11 | E2E & Unit Test 100% Pass Rate | 13 test files / 80 unit tests | All 13 test files in `web/` | Tier 1-4 | VERIFIED |
| F12 | Adversarial Hardening & Audit | Edge-cases, decimal scales, race conditions, idempotency | `orderCenter.test.tsx` (cases 2-10), `HumanInteractionPanel.test.tsx` (cases 2-5), `contracts.test.ts` | Tier 2, Tier 3, Tier 5 | VERIFIED |

## Testing Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| T1 | Test Infrastructure & Philosophy Definition | Author `TEST_INFRA.md` at project root | none | DONE |
| T2 | Test Suite Verification & Execution | Run all 13 test files & build check via Test Writer | none | DONE |
| T3 | Publication of TEST_READY.md | Author `TEST_READY.md` summarizing runner commands & tier checklist | T1, T2 | DONE |
| T4 | Review & Audit Gate | Review by Reviewer, Gate sign-off | T3 | DONE |

## Interface Contracts & Pass Criteria
1. Test command: `npm test -- --run` in `web/` MUST output 13 passed test files, 80 passed unit tests, 0 failures. (PASSED)
2. Build command: `npm run build` in `web/` MUST complete with exit code 0 and 0 TypeScript errors. (PASSED)
3. No semantic regression: All element IDs (`#receive-title`, `#pay-title`), CSS classes (`.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`, `.proc-conflict-chip`), ARIA attributes, and Chinese strings MUST be verified. (VERIFIED)
