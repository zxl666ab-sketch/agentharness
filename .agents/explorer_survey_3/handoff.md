# Handoff Report: Test Suites, Verification Commands & Semantic Contracts Survey

**Agent**: Explorer 3 (Test & Semantic Explorer)  
**Date**: 2026-08-19  
**Directory**: `D:\个人通用agentharness\.agents\explorer_survey_3`  
**Target Project**: `web/` (`D:\个人通用agentharness\web`)

---

## 1. Observation

1. **Test Runner & Baseline**:
   - `web/package.json` specifies `"test": "vitest run"` and `"build": "tsc -p tsconfig.app.json --noEmit && vite build"`.
   - Running `npm test -- --run` in `D:\个人通用agentharness\web` completed in 15.67s with **13 passed test files** and **80 passed unit tests** (0 failures).
   - Test files detected and verified:
     1. `src/api/compatibility.test.ts` (3 tests)
     2. `src/procurement/HumanInteractionPanel.test.tsx` (6 tests)
     3. `src/procurement/centers.test.tsx` (5 tests)
     4. `src/procurement/contractCenter.test.tsx` (3 tests)
     5. `src/procurement/contracts.test.ts` (4 tests)
     6. `src/procurement/invoiceCenter.test.tsx` (4 tests)
     7. `src/procurement/orderCenter.test.tsx` (10 tests)
     8. `src/procurement/procurement.test.tsx` (30 tests)
     9. `src/procurement/roles.test.ts` (1 test)
     10. `src/procurement/systemInfo.test.tsx` (1 test)
     11. `src/procurement/viewModel.test.ts` (5 tests)
     12. `src/procurement/workbenchUrl.test.ts` (6 tests)
     13. `src/useAgentStream.test.ts` (2 tests)

2. **DOM Selectors Directly Queried in Tests**:
   - Element IDs:
     - `#receive-title` (queried in `orderCenter.test.tsx:208,210,218,242,270,276,322,324,340,368,376,407,438` via `host.querySelector("#receive-title")` and `section:has(#receive-title)`)
     - `#pay-title` (queried in `orderCenter.test.tsx:295,298,305` via `host.querySelector("#pay-title")` and `section:has(#pay-title)`)
   - CSS Classes:
     - `.proc-order-card` (queried in `orderCenter.test.tsx:205,238,265,319,337,365`)
     - `.proc-settlement-row` (queried in `orderCenter.test.tsx:291`)
     - `.proc-inline-error` (queried in `centers.test.tsx:279`)
     - `.proc-invoice-actions` (queried in `invoiceCenter.test.tsx:131`)
     - `.proc-home-section` (queried in `procurement.test.tsx:952`)
     - `.proc-conflict-chip` (queried in `procurement.test.tsx:1161`)
     - `details.proc-evidence-panel` (queried in `procurement.test.tsx:1114`)
   - HTML & Data Attributes:
     - `data-field` attributes queried: `[data-field="material"]`, `[data-field="color"]`, `[data-field="width"]`, `[data-field="length"]`, `[data-field="layers"]`, `[data-field="尺寸"]`, `[data-field="height_mm"]`, `[data-field="supplier_name"]`, `[data-field="unit_price"]` (in `procurement.test.tsx`)
     - `data-testid` attributes: `conversation-upload`, `quote-upload`, `invoice-upload`
     - Form input types: `input[type="number"]`, `input[type="file"]`, `input[type="date"]`, `input[type="checkbox"]`, `input[type="radio"]`
   - ARIA Roles & Labels:
     - `[role="alert"]` (queried in `HumanInteractionPanel.test.tsx:107`, `orderCenter.test.tsx:219,277,307`)
     - `[role="dialog"]` (queried in `centers.test.tsx:337`, `contractCenter.test.tsx:143`, `invoiceCenter.test.tsx:169`, `procurement.test.tsx:970,986,990`)
     - `[role="status"]` (queried in `orderCenter.test.tsx:377`)
     - `[aria-label="补充澄清信息"]` (queried in `HumanInteractionPanel.test.tsx:261`; asserted to be `null` when `structuredInteractionActive` is true)
     - `[aria-label="演示角色"]` (in `ProcurementWorkbench.tsx`)
     - `[aria-label="采购任务状态筛选"]` (in `ProcurementWorkbench.tsx`)
     - `[aria-label="采购任务视图"]` (in `ProcurementWorkbench.tsx`)
     - `[aria-label="履约进度"]` / `[aria-label="采购决策进度"]` (in `ProcurementWorkbench.tsx`)

3. **User Interaction & Keyboard Event Patterns**:
   - `Escape` key closes receipt dialogs, payment dialogs, and supplier approval dialogs via `window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))`.
   - Two-step cancel confirmation across `HumanInteractionPanel`, `AiTaskRecovery`, `AiTaskCenter`: first click changes text to `"再次点击确认取消"`, second click sends POST.
   - Guarded submit checkboxes:
     - Supplier Approval: `"我已核对报价原件、硬性条件与到货成本"` checkbox required before `"确认选定"` button is enabled.
     - Invoice Force Match: Checkbox required before submission, otherwise shows `"强制通过必须勾选确认并填写人工备注"`.
     - Review Submit: Opens confirmation dialog `"确认提交：确认 AI 建议"`.
   - Decimal scale formatting: Exact business string outputs (`"10,400.00"`, `"3,000 piece"`, `"2,999.5"`, `"0.2"`).

---

## 2. Logic Chain

1. From **Observation 1**, `npm test -- --run` runs Vitest across all 13 test files and 80 unit tests in `web/` with 0 failures, establishing our non-regression baseline.
2. From **Observation 2**, tests query the DOM using specific element IDs (`#receive-title`, `#pay-title`), CSS class selectors (`.proc-order-card`, `.proc-settlement-row`, `.proc-conflict-chip`, `.proc-inline-error`, etc.), `data-field` attributes, and ARIA roles (`[role="alert"]`, `[role="dialog"]`, `[role="status"]`, `[aria-label="补充澄清信息"]`). Therefore, any UI refactor that removes these IDs, classes, or attributes will immediately break unit tests.
3. From **Observation 2 and Observation 3**, tests assert exact Chinese text content (e.g. status labels, button text, confirmation dialog titles, error notices) and negative text constraints (e.g. absence of `"管理驾驶舱"` or `"成本节约率"` on Home). Modifying or replacing these strings with different wording will cause string assertion failures.
4. From **Observation 3**, interactions depend on standard React event flows (`submit` on form, `input` on inputs, `change` on file upload, `click` on buttons, and `Escape` key listener on `window`). Preserving these event handlers and state transitions guarantees 100% interactive test compatibility.

---

## 3. Caveats

- **No Backend/Java Changes**: Investigation was strictly scoped to frontend `web/` test suites, semantic selectors, and contracts.
- **Tailwind Co-existence**: Tests query class names like `.proc-order-card`. When adding Tailwind utility classes, developers must keep existing semantic class names intact alongside Tailwind classes.
- **No caveats**: All 13 test files and 80 unit tests were completely inspected and documented.

---

## 4. Conclusion

The `web/` frontend test suite is well-structured and highly deterministic, covering full user interaction flows, accessibility attributes, error states, and schema compliance across 13 test files and 80 test cases.

To fulfill Requirement R4 (100% test compatibility and 0 regressions):
1. **Preserve all semantic element IDs**: `#receive-title`, `#pay-title`, `#proc-conversation-panel`, `#proc-requirement-review-${id}`.
2. **Retain critical CSS class hooks**: `.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`, `.proc-conflict-chip`, `details.proc-evidence-panel`, `.proc-home-section`.
3. **Maintain data and ARIA attributes**: `data-field="..."`, `data-testid="..."`, `[role="alert"]`, `[role="dialog"]`, `[role="status"]`, `[aria-label="补充澄清信息"]`.
4. **Preserve exact Chinese labels & prompt texts**: Retain `statusLabel`, `contractStatusLabel`, `STATUS_LABELS`, `ORDER_STATUS_LABELS`, and button labels.
5. **Preserve two-step confirmations & Escape key listeners**: Keep existing `useEscape` and confirmation state workflows.

A detailed analysis report has been written to:
`D:\个人通用agentharness\.agents\explorer_survey_3\survey_report.md`

---

## 5. Verification Method

To independently verify all findings and validate future code changes:

1. **Execute Vitest Test Suite**:
   ```powershell
   cd D:\个人通用agentharness\web
   npm test -- --run
   ```
   *Expected Result*: `Test Files 13 passed (13)`, `Tests 80 passed (80)`.

2. **Execute TypeScript & Vite Build Check**:
   ```powershell
   cd D:\个人通用agentharness\web
   npm run build
   ```
   *Expected Result*: Exit code 0, 0 TypeScript errors, bundle emitted into `dist/`.

3. **Inspect Survey Report**:
   - Open and review `D:\个人通用agentharness\.agents\explorer_survey_3\survey_report.md`.
