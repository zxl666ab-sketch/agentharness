# Handoff Report: Utility `cn()`, ESLint Icon Cleanup & Semantic Contract Preservation

## 1. Observation

### 1.1 `utils.ts` and `web/src/lib/` Status
- Directory inspection: `web/src/lib/` currently does not exist in `D:\个人通用agentharness\web\src`.
- Current dependencies in `web/package.json`:
  ```json
  "dependencies": {
    "@tanstack/react-query": "^5.51.0",
    "lucide-react": "^0.414.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }
  ```
  `clsx` and `tailwind-merge` will be added in Milestone 1.

### 1.2 ESLint Linting Results & 5 Unused Lucide Icon Imports
Running `npm run lint` (`eslint . --ext ts,tsx --max-warnings 0`) in `D:\个人通用agentharness\web` produced verbatim:
```text
D:\个人通用agentharness\web\src\procurement\ProcurementWorkbench.tsx
  21:3  warning  'ClipboardCheck' is defined but never used  @typescript-eslint/no-unused-vars
  22:3  warning  'ShoppingCart' is defined but never used    @typescript-eslint/no-unused-vars
  23:3  warning  'Users' is defined but never used           @typescript-eslint/no-unused-vars
  24:3  warning  'ScrollText' is defined but never used      @typescript-eslint/no-unused-vars

D:\个人通用agentharness\web\src\procurement\WorkbenchHome.tsx
  14:3  warning  'TrendingUp' is defined but never used  @typescript-eslint/no-unused-vars

✖ 5 problems (0 errors, 5 warnings)
```

Inspection of `ProcurementWorkbench.tsx` (lines 1-26):
```tsx
import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  FileCheck2,
  Files,
  Moon,
  Plus,
  Scale,
  Search,
  Settings,
  Sun,
  Trash2,
  Wifi,
  ChevronUp,
  ClipboardCheck,  // Line 21: unused
  ShoppingCart,    // Line 22: unused
  Users,           // Line 23: unused
  ScrollText,      // Line 24: unused
} from "lucide-react";
```
Grep verification confirms `ClipboardCheck`, `ShoppingCart`, `Users`, and `ScrollText` appear nowhere else in `ProcurementWorkbench.tsx`.

Inspection of `WorkbenchHome.tsx` (lines 1-16):
```tsx
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  FileSpreadsheet,
  FileWarning,
  ListTodo,
  PackageCheck,
  ShieldAlert,
  Sparkles,
  TrendingUp,      // Line 14: unused
} from "lucide-react";
```
Grep verification confirms `TrendingUp` appears only on line 14 and is never used in the component body.

### 1.3 Baseline Test Status
Running `npm test` (`vitest run`) in `D:\个人通用agentharness\web` produced:
- Test Files: 13 passed (13)
- Tests: 80 passed (80)
- Suites tested:
  - `src/procurement/orderCenter.test.tsx` (10 tests)
  - `src/procurement/centers.test.tsx` (5 tests)
  - `src/procurement/viewModel.test.ts` (5 tests)
  - `src/procurement/contracts.test.ts` (4 tests)
  - `src/procurement/roles.test.ts` (1 test)
  - `src/useAgentStream.test.ts` (2 tests)
  - `src/procurement/workbenchUrl.test.ts` (6 tests)
  - `src/api/compatibility.test.ts` (3 tests)
  - `src/procurement/HumanInteractionPanel.test.tsx` (6 tests)
  - `src/procurement/systemInfo.test.tsx` (1 test)
  - `src/procurement/contractCenter.test.tsx` (3 tests)
  - `src/procurement/invoiceCenter.test.tsx` (4 tests)
  - `src/procurement/procurement.test.tsx` (30 tests)

### 1.4 Complete Inventory of Critical Semantic Contracts (F10)

#### A. Critical DOM IDs
| ID | Component File | Used in Tests / Assertions |
|---|---|---|
| `#receive-title` | `OrderCenter.tsx:475` | `orderCenter.test.tsx:208, 210, 218, 241, 270, 276, 340, 368, 376, 407, 438` |
| `#pay-title` | `OrderCenter.tsx:542` | Settlement payment dialog title |
| `#close-title` | `OrderCenter.tsx:510` | Order close/cancel dialog title |
| `#proc-conversation-panel` | `ProcurementWorkbench.tsx:433` | Dual-pane conversation container |
| `#proc-requirement-review-${request.id}` | `RequirementReview.tsx:308` | Form submission targeting |
| `#confirm-title` | `ComparisonView.tsx:287` | `procurement.test.tsx:971` (supplier award dialog) |
| `#no-award-title` | `ComparisonView.tsx:321` | No-award dialog title |
| `#proc-config-title` | `ConfigDrawer.tsx:36` | Model / API config drawer title |
| `#approve-contract-title` | `ContractCenter.tsx:416` | Contract approve dialog title |
| `#change-contract-title` | `ContractCenter.tsx:437` | Contract revision dialog title |
| `#void-invoice-title` | `InvoiceCenter.tsx:369` | Invoice void dialog title |
| `#force-invoice-title` | `InvoiceCenter.tsx:386` | Invoice force match dialog title |
| `#correct-invoice-title` | `InvoiceCenter.tsx:404` | Invoice manual edit dialog title |
| `#delete-request-title` | `DeleteDialog.tsx:25` | Task delete confirmation dialog |
| `#supplier-form-title` | `SupplierCenter.tsx:323` | Supplier create/edit dialog title |
| `#delete-supplier-title` | `SupplierCenter.tsx:387` | Supplier delete dialog title |
| `#supplier-profile-title` | `SupplierCenter.tsx:419` | Supplier drawer title |
| `#review-confirm-title` | `ReviewCenter.tsx:338` | Review decision dialog title |

#### B. Data Attributes (`data-*` / `data-testid`)
| Attribute | File | Context |
|---|---|---|
| `data-field="${name}"` | `QuoteWorkspace.tsx:237` | Field row identification asserted in `procurement.test.tsx:489, 490, 543, 545, 547, 646, 651, 1090, 1111, 1112` |
| `data-testid="conversation-upload"` | `ProcurementConversation.tsx:225` | Chat file upload input |
| `data-testid="quote-upload"` | `QuoteWorkspace.tsx:382` | Quote file upload input |
| `data-testid="invoice-upload"` | `InvoiceCenter.tsx:200` | Invoice file upload input |
| `data-theme="dark"` | `tokens.css:56`, `procurement.css:90` | Root theme attribute `:root[data-theme="dark"]` |

#### C. Semantic CSS Class Hooks
| Class Name | Component | Asserted in Test |
|---|---|---|
| `.proc-order-card` | `OrderCenter.tsx` | `orderCenter.test.tsx:205, 238, 265, 365` |
| `.proc-settlement-row` | `OrderCenter.tsx` | Settlement row identification |
| `.proc-inline-error` | `centers.test.tsx`, `ComparisonView.tsx`, `AiTaskCenter.tsx` | `centers.test.tsx:279` |
| `.proc-invoice-actions` | `InvoiceCenter.tsx` | `invoiceCenter.test.tsx:131` |
| `.proc-home-section` | `WorkbenchHome.tsx` | `procurement.test.tsx:952` |
| `.proc-conflict-chip` | `procurement.test.tsx` | `procurement.test.tsx:1161` |
| `details.proc-evidence-panel` | `ComparisonView.tsx` | `procurement.test.tsx:1114` |
| `.proc-confirm-dialog` | Modal dialogs | Modal layout and styling |
| `.proc-modal-backdrop`, `.proc-drawer-backdrop` | Modals & Drawers | Backdrop overlay (`role="presentation"`) |
| `.effect-badge` | `EffectBadge.tsx` | Governance effect badges (`read`, `write`, `network`, `danger`, `external`) |
| `.run-report` | `RunReport.tsx` | Evidence report verdicts (`passed`, `failed`, `needs_review`, `unverified`) |
| `.proc-app`, `.proc-workbench` | Layout root | Shell styling |

#### D. ARIA Roles & Accessibility Labels
- `role="dialog"` + `aria-modal="true"` + `aria-labelledby="..."` on all interactive modal windows.
- `role="presentation"` on dialog background overlays.
- `role="alert"` on error banners (`.proc-inline-error`, `.proc-form-error`, `.proc-toolbar-error`, `.proc-config-error`, `.proc-interaction-error`, `.proc-empty-state.compact`, `.proc-contract-warning`, `.report-failures`).
- `role="status"` on success notices (`.proc-form-success`, `.proc-toolbar-success`, `.proc-interaction-notice`).
- `role="toolbar"` on action/filter toolbars in centers.
- `role="region"` + `aria-label="AI 任务步骤日志"` in `AiTaskRecovery.tsx`.
- Key tested `aria-label` values:
  - `aria-label="补充澄清信息"` (`ProcurementConversation.tsx`, asserted in `HumanInteractionPanel.test.tsx:261`)
  - `aria-label="Agent 等待回答"` (`HumanInteractionPanel.tsx`)
  - `aria-label="AI 任务状态"`, `aria-label="AI 分析进度 ${percent}%"` (`AiTaskRecovery.tsx`)
  - `aria-label="比价证据详情"`, `aria-label="选择供应商"`, `aria-label="选择${supplier}"`, `aria-label="没有合格报价的恢复操作"` (`ComparisonView.tsx`)
  - `aria-label="选择已定标任务"` (`ContractCenter.tsx`)
  - `aria-label="选择采购订单"` (`InvoiceCenter.tsx`)
  - `aria-label="下一步引导"` (`NextStepBar.tsx`)
  - `aria-label="结果证据报告"` (`RunReport.tsx`)
  - `aria-label="搜索 AI 任务"`, `aria-label="AI 任务类型"`, `aria-label="AI 任务列表"`, `aria-label="AI 任务详情"` (`AiTaskCenter.tsx`)
  - `aria-label="事件类型"`, `aria-label="操作人"`, `aria-label="业务对象类型"`, `aria-label="任务ID"`, `aria-label="上一页"`, `aria-label="下一页"` (`AuditLogCenter.tsx`)

#### E. Guarded Actions & Interactive Flows
1. **Two-Step Cancellation**: 1st click sets text to `"再次点击确认取消"`, 2nd click executes cancel action (`HumanInteractionPanel.tsx`, `centers.test.tsx`).
2. **Guarded Supplier Selection Approval**: Checkbox `"我已核对报价原件、硬性条件与到货成本"` must be checked to enable `"确认选定"` button (`ComparisonView.tsx`).
3. **Guarded Review Submission**: Checkbox `"我已核对 AI 建议、报价原件与确定性比价证据"` must be checked to enable `"确认提交"` button (`ReviewCenter.tsx`).
4. **Guarded Force-Match Approval**: Must check confirmation box and provide manual notes, otherwise shows `"强制通过必须勾选确认并填写人工备注"` without dispatching API call (`InvoiceCenter.tsx`).
5. **Contract Change Validation**: Must supply revised amount, lead days, and reason before submission, failing with `"合同变更必须填写变更原因"` (`ContractCenter.tsx`).
6. **Keyboard Escape Listener**: Global window keydown listener (`useEscape.ts`) triggers modal/drawer close when active and not disabled.
7. **Idempotency-Key Preservation**: Retrying an action with identical input retains the exact same `Idempotency-Key` HTTP header.

---

## 2. Logic Chain

1. **`cn()` Utility Implementation Specification**:
   - Per `PROJECT.md` (F3) and `SCOPE.md` (F3), `web/src/lib/utils.ts` must export `cn(...inputs: ClassValue[]): string`.
   - `clsx` accepts variable numbers of arguments (strings, arrays, objects, booleans, undefined) and formats them into a clean string.
   - `twMerge` resolves conflicting Tailwind CSS classes according to Tailwind's specificity rules (e.g. `p-4` overriding `p-2`).
   - Combining them via `export function cn(...inputs: ClassValue[]): string { return twMerge(clsx(inputs)); }` provides standard, robust utility-first class handling.

2. **ESLint Unused Lucide Icons**:
   - Running `npm run lint` flagged exactly 5 unused variables in two files:
     1. `ProcurementWorkbench.tsx`: `ClipboardCheck` (line 21)
     2. `ProcurementWorkbench.tsx`: `ShoppingCart` (line 22)
     3. `ProcurementWorkbench.tsx`: `Users` (line 23)
     4. `ProcurementWorkbench.tsx`: `ScrollText` (line 24)
     5. `WorkbenchHome.tsx`: `TrendingUp` (line 14)
   - Removing these 5 unused imports from the import declarations will bring the ESLint warning count to 0 (`--max-warnings 0` passes).

3. **Semantic Compatibility & Non-Regression (F10)**:
   - Vitest tests across all 13 test files rely on explicit DOM selectors (`#receive-title`, `[role="dialog"]`, `[role="alert"]`, `[data-field]`, `[aria-label]`, button text matches, `.proc-order-card`, `.proc-inline-error`, etc.).
   - During CSS refactoring in M1 (and component template redesigns in M2/M3), any modification that strips these IDs, class hooks, ARIA attributes, or changes exact Chinese strings will cause Vitest suite regressions.
   - Co-locating new Tailwind utility classes alongside existing semantic class names (e.g. `className={cn("proc-order-card", "rounded-xl border border-border bg-surface p-4")}`) ensures 100% preservation of all test contracts while applying modern aesthetics.

---

## 3. Caveats

- **No Caveats**: All 13 test files and 80 unit tests were executed and passed. ESLint was executed and the exact 5 warnings were pinned down to file and line number. All test assertions were cross-referenced with component DOM structures.

---

## 4. Conclusion

1. **`web/src/lib/utils.ts` Specification**:
   Create `web/src/lib/utils.ts` with the following implementation:
   ```typescript
   import { clsx, type ClassValue } from "clsx";
   import { twMerge } from "tailwind-merge";

   export function cn(...inputs: ClassValue[]): string {
     return twMerge(clsx(inputs));
   }
   ```

2. **5 Unused Lucide Icon Cleanups**:
   - In `web/src/procurement/ProcurementWorkbench.tsx`: Remove `ClipboardCheck`, `ShoppingCart`, `Users`, and `ScrollText` from the `lucide-react` import statement (lines 21-24).
   - In `web/src/procurement/WorkbenchHome.tsx`: Remove `TrendingUp` from the `lucide-react` import statement (line 14).

3. **Milestone 1 Semantic Preservation Rule**:
   - All critical DOM IDs (`#receive-title`, `#pay-title`, `#close-title`, `#confirm-title`, `#no-award-title`, etc.) must be kept unchanged.
   - All `data-field`, `data-testid`, and `data-theme` attributes must be preserved.
   - All `role="..."` (`dialog`, `alert`, `status`, `toolbar`, `presentation`, `region`) and `aria-label="..."` attributes must remain intact.
   - Semantic CSS classes (`.proc-order-card`, `.proc-settlement-row`, `.proc-inline-error`, `.proc-invoice-actions`, `.proc-home-section`, `.proc-conflict-chip`, `.proc-evidence-panel`, `.effect-badge`, `.run-report`) must be retained alongside new Tailwind utility classes.

---

## 5. Verification Method

To independently verify the findings:
1. **ESLint Verification**:
   ```powershell
   cd D:\个人通用agentharness\web
   npm run lint
   ```
   Confirms the 5 warnings in `ProcurementWorkbench.tsx` and `WorkbenchHome.tsx`. Once the 5 unused imports are removed, the command exits with code 0.

2. **Vitest Test Suite**:
   ```powershell
   cd D:\个人通用agentharness\web
   npm test
   ```
   Confirms all 13 test files and 80 unit tests pass.

3. **File Inspection**:
   - `web/src/procurement/ProcurementWorkbench.tsx` lines 1-26
   - `web/src/procurement/WorkbenchHome.tsx` lines 1-16
   - `web/src/procurement/orderCenter.test.tsx`
   - `web/src/procurement/centers.test.tsx`
   - `web/src/procurement/invoiceCenter.test.tsx`
   - `web/src/procurement/contractCenter.test.tsx`
   - `web/src/procurement/HumanInteractionPanel.test.tsx`
   - `web/src/procurement/procurement.test.tsx`
