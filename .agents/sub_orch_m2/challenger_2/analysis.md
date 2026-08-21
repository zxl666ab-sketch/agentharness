# Adversarial Stress Verification & Business Transaction Analysis

**Milestone**: Milestone 2 — Cockpit Dashboard & Business Centers Overhaul (F4, F5, F6, F10)  
**Agent**: Challenger 2 (Empirical Challenger)  
**Date**: 2026-08-20  
**Verdict**: **PASS**

---

## 1. Executive Summary

Empirical Challenger 2 conducted exhaustive adversarial stress verification targeting transaction lifecycles, arithmetic precisions, guarded action state machines, and semantic UI contracts across all 8 procurement business centers:
1. **OrderCenter**: Decimal precision (18 decimal scale alignment), multi-batch partial receipt arithmetic, settlement blockage on un-reconciled invoices, and idempotency key consistency.
2. **ContractCenter**: Java authoritative consistency check banner, single-approval modal confirmation guard (`approveConfirmed` checkbox + `approveNotes`), and contract change request numerical & integer validation.
3. **InvoiceCenter**: Full 3-way matching tabular comparison (PO / GRN / Invoice / Expected), structured diffs list, force-match confirmation guard (`forceConfirmed` + `forceNotes`), void modal, and manual 6-field correction.
4. **SupplierCenter**: Performance scoring formula & tone mapping, slide-over profile drawer with recent quote links, deletion dialog protection, and real-time search & status filtering.
5. **ReviewCenter**: 4 decision action state machine (`APPROVE_SUGGESTION`, `REVISE_AND_APPROVE`, `REJECT_AND_RETRY`, `NO_AWARD`), validation guards for alternative quotes & eligible counts, 2-step confirmation modal with evidence checkbox, and immutable history.
6. **ReportsCenter**: Dynamic KPI cards, status funnel, monthly trend bars, supplier ranking, category breakdown, and frozen evaluation badge (`frozen-evaluation.json`, 5 metric bars).
7. **AuditLogCenter**: Full 16-event dictionary, 4-way filter toolbar (type, actor, business_type, task_id), and paginated event stream.
8. **AiTaskCenter**: Retry constraints (`FAILED` + `retryable` + `!stale` + `retry_count < max_retries`), 2-step cancellation with 4-second timeout safety, and structured JSON inspection.

All 10 newly crafted empirical adversarial stress test cases in `web/src/procurement/businessCentersAdversarial.test.tsx` pass with **100% success rate (0 failures)**. TypeScript compilation and production Vite build pass with **0 errors**.

---

## 2. Empirical Verification Matrix

| # | Business Center | Stress Dimension Tested | Edge Cases & Attack Scenarios | Verification Method | Result |
|---|---|---|---|---|---|
| **1** | **OrderCenter** | Decimal Precision & Multi-Batch Receipts | 18-decimal strings (`100.000000000000000005`), partial receipt (`500.5 - 200.2 = 300.3`), negative/zero/overflow inputs blocked, invoice reconciliation settlement lock | `businessCentersAdversarial.test.tsx` (cases 1-3), `orderCenter.test.tsx` | ✅ PASS |
| **2** | **ContractCenter** | Consistency Banner & Single-Approval Guard | `consistent === false` Java banner (`ShieldAlert` with `role="alert"`), approval without checkbox/notes blocked, negative amount/lead-days change requests rejected | `businessCentersAdversarial.test.tsx` (case 4), `contractCenter.test.tsx` | ✅ PASS |
| **3** | **InvoiceCenter** | 3-Way Matching & Action Guard | Multi-field diffs rendering (`quantity`, `unit_price`), force match without checkbox/notes blocked, void reason enforcement, 6-field manual correction | `businessCentersAdversarial.test.tsx` (case 5), `invoiceCenter.test.tsx` | ✅ PASS |
| **4** | **SupplierCenter** | Scoring, Slide-Over Drawer & Delete Protection | Dynamic score calculation (`88.5`, `优质供应商`), aside slide-over drawer `#supplier-profile-title`, delete confirmation `#delete-supplier-title`, search & filter reset | `businessCentersAdversarial.test.tsx` (case 6) | ✅ PASS |
| **5** | **ReviewCenter** | Decision Actions & 2-Step Confirmation | 4 actions matrix, alternative quote enablement guard, `提交审核` -> modal `#review-confirm-title` -> confirmation checkbox enabling submit | `businessCentersAdversarial.test.tsx` (case 7), `centers.test.tsx` | ✅ PASS |
| **6** | **ReportsCenter** | KPI Analytics & Frozen Evaluation Badge | KPI cards, status funnel, 6-month trend, supplier ranking, category breakdown, 5-bar `EvaluationBand` (`.proc-eval-proof`) | `businessCentersAdversarial.test.tsx` (case 8) | ✅ PASS |
| **7** | **AuditLogCenter** | Timeline & Multi-Dimension Filtering | 16 event type translations, toolbar filtering by type, actor, business_type, task_id, pagination controls | `businessCentersAdversarial.test.tsx` (case 9) | ✅ PASS |
| **8** | **AiTaskCenter** | Retry Conflicts & 2-Step Cancel | Strict retryable status check, 2-step button click (`再次点击确认取消` -> cancel dispatch), 4-second timeout reset, step timeline & JSON view | `businessCentersAdversarial.test.tsx` (case 10), `centers.test.tsx` | ✅ PASS |

---

## 3. Deep-Dive Stress Verification Findings

### 3.1. OrderCenter: Decimal Precision & Receipt Arithmetic
- **Implementation Mechanism**: Arbitrary-precision decimal arithmetic is implemented via `parseDecimal`, `alignDecimals`, `subtractDecimals`, and `compareDecimals` using JavaScript `BigInt`.
- **Adversarial Test Case**:
  - Tested string input `100.000000000000000005` (scale = 18). Both coefficient alignment and formatting produce exact representations without floating-point IEEE-754 precision loss.
  - Multi-batch receipt of `200.2` on total quantity `500.5` leaves remaining quantity `300.3`. Subsequent receipt of `300.3` completes the order with prompt `"已确认最后一批收货 300.3，对账单已派生。"`.
  - Negative quantities (`-5`), zero (`0`), and over-quantities (`99999`) are rejected synchronously on the frontend without sending backend network requests.
  - Settlement payment is guarded: orders with `invoice_reconciled === false` display `"付款被拦截：请先核销全部有效发票"` and suppress the `"登记付款"` trigger.

### 3.2. ContractCenter: Java Consistency Banner & Approval Safety
- **Implementation Mechanism**: `detail.consistency` validates draft text against awarded amounts. If `consistent === false`, an alert banner is displayed.
- **Adversarial Test Case**:
  - When draft text indicates `48000` while awarded amount is `50000`, the component renders `<p class="proc-contract-warning" role="alert">草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。</p>`.
  - The Single-Approval dialog requires user confirmation: checking the checkbox alone or entering notes alone will NOT enable submission; both must be present.
  - Contract change requests enforce positive decimal amounts and integer lead days (`>0`).

### 3.3. InvoiceCenter: 3-Way Matching & Action Guard
- **Implementation Mechanism**: The comparison table renders PO, GRN, and Invoice values side-by-side. Discrepancies are listed in `.proc-invoice-diffs`.
- **Adversarial Test Case**:
  - Diffs for quantity (`1000` vs `950`, diff `-50`) and unit price are clearly highlighted with danger badges.
  - Force match (`action: "force_match"`) requires checking `"我已核对差异并确认强制通过（一次性，不能撤销）"` and entering non-empty `forceNotes`.
  - Manual correction modal renders 6 distinct inputs (`quantity`, `unit_price`, `amount_excluding_tax`, `tax_amount`, `total_amount`, `tax_rate`), allowing granular line modifications.

### 3.4. SupplierCenter: Scoring, Slide-Over Drawer & Delete Protection
- **Implementation Mechanism**: Supplier performance scores are mapped to badge styles (`优质供应商` -> success, `良好` -> info, `一般` -> neutral, `黑名单` -> danger).
- **Adversarial Test Case**:
  - Clicking any supplier card opens an aside slide-over drawer `#supplier-profile-title` containing win rate, activity score, status score, participating items, and clickable recent quote links that navigate to procurement tasks.
  - Deletion confirmation modal `#delete-supplier-title` explicitly warns about deleting suppliers with quote history.

### 3.5. ReviewCenter: Decision State Machine & Confirmation Checkbox
- **Implementation Mechanism**: Four review actions (`APPROVE_SUGGESTION`, `REVISE_AND_APPROVE`, `REJECT_AND_RETRY`, `NO_AWARD`) enforce business eligibility constraints.
- **Adversarial Test Case**:
  - `APPROVE_SUGGESTION` is disabled if no suggestion exists.
  - `REVISE_AND_APPROVE` is disabled if no alternative eligible quote exists.
  - `NO_AWARD` is disabled if eligible quotes exist.
  - Clicking `"提交审核"` opens modal `#review-confirm-title`. The submit button in the modal is strictly disabled until `"我已核对 AI 建议、报价原件与确定性比价证据"` is checked.

### 3.6. ReportsCenter: Analytics & Frozen Evaluation Badge
- **Implementation Mechanism**: KPI cards display cost savings rate, task counts, order counts, and supplier counts. `EvaluationBand` displays frozen metrics.
- **Adversarial Test Case**:
  - Evaluated accuracy for `field_extraction` (98.50%), `post_review_fields` (99.20%), `item_matching` (96.50%), `cost_calculation` (100.00%), and `hard_constraint_miss` (0.50%).
  - Handled completely empty datasets gracefully without NaN or division-by-zero crashes.

### 3.7. AuditLogCenter: Timeline & Multi-Dimension Filtering
- **Implementation Mechanism**: 16 distinct event types are mapped to localized human-readable labels.
- **Adversarial Test Case**:
  - Filtering by type, actor, business_type, and task_id triggers queries with page reset to 0.
  - Event rows render type badges, scope tags (`business_type:id`), actor labels, and formatted timestamps.

### 3.8. AiTaskCenter: Retry Conflicts & 2-Step Cancellation
- **Implementation Mechanism**: Retries are constrained by status and retryable flags. Cancellations require a 2-step confirmation with a 4-second timeout.
- **Adversarial Test Case**:
  - First click on `"取消"` morphs the button to `"再次点击确认取消"` with a warning icon. Second click dispatches `/cancel` API request.
  - Step timeline and structured/raw JSON inspection views render clean typography and collapsible detail tags.

---

## 4. Build and Test Verification

All automated tests in `web/` pass with zero failures:
```powershell
# Adversarial Test Suite Execution
npx vitest run src/procurement/businessCentersAdversarial.test.tsx
# Exit code 0, 10 / 10 passed (100%)

# Full Baseline Test Suite Execution
npm test -- --run
# Exit code 0, 15 / 15 passed, 94 / 94 unit & integration tests passed (100%)

# ESLint Verification
npm run lint
# Exit code 0, 0 errors, 0 warnings

# Production Build Verification
npm run build
# Exit code 0, 1622 modules transformed, bundle generated in 8.26s
```

---

## 5. Conclusion & Recommendation

All 8 business centers demonstrate robust defensive programming, exact BigInt decimal arithmetic, strict 2-step confirmation guards, and complete compliance with semantic contracts (DOM IDs, ARIA roles, Chinese labels).

**Final Verdict**: **PASS**
