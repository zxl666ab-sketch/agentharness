# Scope: Milestone 2 — Cockpit Dashboard & Business Centers Overhaul

## Overview
Milestone 2 modernizes the entire main application shell, cockpit workbench, and all 8 business centers with modern Tailwind CSS, dark/light theme support, glassmorphism, responsive cards/grids,Lucide icons, glowing status badges, and slide-over drawers while strictly preserving all semantic invariants required by tests and user interactions.

## Target Components
1. **F4 Cockpit Dashboard**:
   - `src/components/WorkbenchHome.tsx`
2. **F5 Business Centers**:
   - `src/components/OrderCenter.tsx`
   - `src/components/ContractCenter.tsx`
   - `src/components/InvoiceCenter.tsx`
   - `src/components/SupplierCenter.tsx`
   - `src/components/ReviewCenter.tsx`
   - `src/components/ReportsCenter.tsx`
   - `src/components/AuditLogCenter.tsx`
   - `src/components/AiTaskCenter.tsx`
3. **F6 App Shell & Navigation**:
   - `src/components/Header.tsx`
   - `src/components/Navigation.tsx`
   - `src/components/RoleSwitcher.tsx`
4. **F10 Semantic Compatibility Preservation**:
   - Preserve all DOM IDs (`#receive-title`, `#pay-title`, `#workbench-title`, etc.)
   - Preserve all CSS classes (`.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`, `.proc-home-section`, etc.)
   - Preserve all ARIA attributes (`aria-current`, `aria-label`, `role="tab"`, etc.)
   - Preserve exact Chinese strings for buttons, headings, status values, error messages, and table columns
   - Preserve keyboard event listeners (Escape key to close modals/drawers).

## Acceptance Criteria
- `npm test -- --run` passes (14 test files, 84+ tests passing 100%).
- `npm run lint` passes with 0 errors and 0 warnings.
- `npm run build` passes with clean bundle output.
- 2 Reviewers independently APPROVE.
- 2 Challengers PASS.
- 1 Forensic Auditor confirms CLEAN.
