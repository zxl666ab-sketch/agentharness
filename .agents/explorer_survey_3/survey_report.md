# Frontend Test Suite & Semantic Contract Survey Report

**Explorer**: Explorer 3 (Test & Semantic Explorer)  
**Date**: 2026-08-19  
**Scope**: `web/` Frontend Codebase (`D:\个人通用agentharness\web`)  
**Target Milestone**: UI Refactor (Tailwind CSS, Dual-Pane Split Canvas, Cockpit Overhaul) with 100% Test Compatibility (R4)

---

## 1. Executive Summary & Test Suite Status

A comprehensive inspection of the `web/` directory was performed. The test suite comprises **13 test files** containing **80 unit and component test cases**.

### Test Execution Baseline
- **Command**: `npm test -- --run` (in `web/`)
- **Status**: 13 passed / 13 test files (100%), 80 passed / 80 unit tests (100%)
- **Environment**: jsdom (configured via `vite.config.ts` and `test/setup.ts`)
- **Build Verification**: `npm run build` (`tsc -p tsconfig.app.json --noEmit && vite build`)

### Summary Table of Test Files

| # | Test File Path | Test Count | Primary Component / Module Tested | Test Type |
|---|---|---|---|---|
| 1 | `src/api/compatibility.test.ts` | 3 | Backend API schema & Web build ID handshake | Unit / Integration logic |
| 2 | `src/procurement/HumanInteractionPanel.test.tsx` | 6 | Human Interaction blocking dialog & recovery | React Component DOM |
| 3 | `src/procurement/centers.test.tsx` | 5 | `AiTaskCenter` & `ReviewCenter` | React Component DOM |
| 4 | `src/procurement/contractCenter.test.tsx` | 3 | `ContractCenter` (Drafting, Revision, Change Dialog) | React Component DOM |
| 5 | `src/procurement/contracts.test.ts` | 4 | JSON Schema contract definitions & status enums | Contract Validation |
| 6 | `src/procurement/invoiceCenter.test.tsx` | 4 | `InvoiceCenter` (3-Way Matching, Diff, Reconcile) | React Component DOM |
| 7 | `src/procurement/orderCenter.test.tsx` | 10 | `OrderCenter` (Shipment, Receiving, Payments, Esc) | React Component DOM |
| 8 | `src/procurement/procurement.test.tsx` | 30 | `ProcurementWorkbench`, `ComparisonView`, `QuoteWorkspace`, `RequirementReview`, `ReportView`, `WorkbenchHome`, `WorkbenchNavigation` | React Component DOM |
| 9 | `src/procurement/roles.test.ts` | 1 | Demo Role routing (`visibleViewOrDefault`) | Unit Logic |
| 10 | `src/procurement/systemInfo.test.tsx` | 1 | `SystemInfo` LLM Gateway & Circuit Breakers | React Component DOM |
| 11 | `src/procurement/viewModel.test.ts` | 5 | Status labels, tones, closed-loop steps, next actions | Unit / ViewModel Logic |
| 12 | `src/procurement/workbenchUrl.test.ts` | 6 | URL state parsing & serialization round-trip | Unit Logic |
| 13 | `src/useAgentStream.test.ts` | 2 | SSE stream URL cursor formatting | Unit Logic |

---

## 2. Test Setup and Infrastructure

### Configuration Files
- **`web/package.json`**:
  - Script `"test": "vitest run"`
  - Script `"build": "tsc -p tsconfig.app.json --noEmit && vite build"`
  - Dependencies: `@tanstack/react-query@^5.51.0`, `lucide-react@^0.414.0`, `react@^18.3.1`, `react-dom@^18.3.1`
  - DevDependencies: `vitest@^2.0.3`, `jsdom@^24.1.0`, `@vitejs/plugin-react@^4.3.1`
- **`web/vite.config.ts`**:
  - `test.environment`: `"jsdom"`
  - `test.include`: `["src/**/*.test.ts", "src/**/*.test.tsx"]`
  - `test.setupFiles`: `["./test/setup.ts"]`
  - Defines global build variables: `__AGENTHARNESS_WEB_BUILD_ID__`, `__AGENTHARNESS_API_SCHEMA_VERSION__`, `__AGENTHARNESS_ENFORCE_WEB_BUILD_ID__`
- **`web/test/setup.ts`**:
  - `Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });`
  - Note: React 18 `act()` is enabled globally.

---

## 3. Detailed Enumeration of All 13 Test Files and 80 Test Cases

### File 1: `src/api/compatibility.test.ts` (3 Tests)
1. **`rejects old servers that do not expose an API schema`**
   - Asserts: `checkBackendCompatibility(health({ api_schema_version: undefined }), null)` returns `{ compatible: false, reason: "api_schema", actual: "未提供" }`.
2. **`rejects a frontend that was rebuilt after the backend started`**
   - Asserts: `checkBackendCompatibility(health(), "web-new")` returns `{ compatible: false, reason: "web_build", expected: "web-new", actual: "web-current" }`.
3. **`accepts matching API and Web build identities`**
   - Asserts: `checkBackendCompatibility(health(), "web-current")` returns `{ compatible: true }`.

---

### File 2: `src/procurement/HumanInteractionPanel.test.tsx` (6 Tests)
1. **`renders the blocking question context and validates required review fields`**
   - Component: `<HumanInteractionPanel interaction={interaction()} />`
   - Queries/Text Assertions:
     - `host.textContent` contains `"请补充采购数量和最长交期"` (question)
     - `host.textContent` contains `"这些字段会影响供应商资格和排序。"` (reason)
     - `host.textContent` contains `"字段复核"` (business step)
   - Interaction: `host.querySelector("form")!.dispatchEvent(new Event("submit", ...))`
   - Alert Assertion: `host.querySelector('[role="alert"]')?.textContent === "请填写采购数量"`
   - Assert `fetchMock` was not called.
2. **`submits JSON numbers and reuses the idempotency key for the same network retry`**
   - Interaction: Queries `input[type="number"]`, fills `"5000"` and `"15"`, submits form twice.
   - Assertions:
     - Header `Idempotency-Key` is present and identical across retry.
     - Payload `answer` contains numeric values `{ quantity: 5000, max_lead_days: 15 }`.
     - `host.textContent` contains `"回答已保存，Agent 将从当前步骤继续"`.
3. **`uploads supplemental artifacts to Java before submitting their authorized IDs`**
   - Interaction: Sets `input[type="file"].files = [file]` with `change` event, submits form.
   - Assertions:
     - First fetch: POST to `/api/procurement/interactions/interaction-1/artifacts` with `FormData`.
     - Second fetch: JSON body contains `answer: ["jb..."]` and `artifact_ids: ["jb..."]`.
4. **`requires a second click to cancel a waiting task`**
   - Interaction: Clicks button containing `"取消"`.
   - Assertions:
     - Button text updates to contain `"再次点击确认取消"`.
     - No fetch on 1st click.
     - 2nd click sends POST to `/api/procurement/interactions/interaction-1/cancel`.
5. **`distinguishes saved recovery failures from applied answers without claiming completion`**
   - Component state: `status: "ANSWERED"` with failed operation.
   - Assertions:
     - `host.textContent` contains `"回答已保存，Agent 暂未恢复"` and `"重新派发"`.
     - When `status: "APPLIED"`: contains `"回答已应用"`, `"继续处理后续步骤"`, does NOT contain `"已完成"`.
6. **`structured interaction conversation boundary -> hides the legacy free-text resume when a structured interaction owns the wait`**
   - Component: `<ProcurementConversation structuredInteractionActive ... />`
   - Assertions:
     - `host.querySelector('[aria-label="补充澄清信息"]')` is `null`.
     - `host.textContent` does NOT contain `"恢复采购 Agent"`.

---

### File 3: `src/procurement/centers.test.tsx` (5 Tests)
1. **`AI task center -> shows persisted steps, result versions and source artifacts`**
   - Render: `renderToString(<AiTaskCenter ... />)`
   - Text Assertions: `"AI 任务中心"`, `"已核对两份报价来源"`, `"两份报价均已核对"`, `"quote-analysis-v1"`, `"packaging-quote-v3"`, `"报价明细!B4"`, `"采购详情"`.
2. **`AI task center -> recovers busy state and shows the error when retry/cancel fails`**
   - Component: `<AiTaskCenter ... />` with failed task.
   - Interaction: Clicks button `"重试"`.
   - Assertions:
     - Catches 409 conflict.
     - `.proc-inline-error` contains `"任务状态已变化"`.
     - Retry button `disabled === false` after error.
     - Clicks button `"取消"`, confirms button `"再次点击确认取消"` appears, clicks it, POST sent to `/cancel`.
3. **`human review center -> shows immutable AI advice, quote evidence and all four actions`**
   - Render: `renderToString(<ReviewCenter ... />)`
   - Text Assertions: `"AI 建议"`, `"甲方标签"`, `"乙方标签"`, `"交期超过上限"`, `"确认建议"`, `"修改后通过"`, `"驳回重跑"`, `"流标"`, `"提交审核"`.
4. **`human review center -> requires a second confirmation before submitting an action`**
   - Interaction: Clicks button `"提交审核"`.
   - Assertions:
     - `host.querySelector('[role="dialog"]')` contains `"确认提交：确认 AI 建议"` and `"我已核对 AI 建议、报价原件与确定性比价证据"`.
5. **`human review center -> keeps a finalized decision read-only`**
   - Render: `renderToString(<ReviewCenter ... />)` with `status: "APPROVED"`.
   - Assertions:
     - Contains `"正式决定已形成"`, `"人工审核记录"`.
     - Does NOT contain `"提交审核"`.

---

### File 4: `src/procurement/contractCenter.test.tsx` (3 Tests)
1. **`renders list, draft actions and regen button for DRAFT contracts`**
   - Component: `<ContractCenter />` with `DRAFT` contract.
   - Assertions:
     - Text contains `"合同中心"`, `"CT-RFQ-20260814-B1"`.
     - Clicks contract button card.
     - Detail panel displays `"提交审批"` and `"重新草拟"`.
2. **`shows pending revision values in change history and regen for CHANGE_REQUEST`**
   - Component: `<ContractCenter />` with `CHANGE_REQUEST` contract.
   - Assertions:
     - Clicks card `"CT-RFQ-20260814-X1"`.
     - Text contains `"修订：金额 12,000.00 · 交期 18 天（待审批）"` and `"按修订值重新草拟"`.
3. **`change dialog requires revised amount and lead days before submitting`**
   - Component: `<ContractCenter />` with `EXECUTING` contract.
   - Interaction: Clicks `"CT-RFQ-20260814-E1"`, clicks button `"发起变更"`.
   - Assertions:
     - `[role="dialog"]` contains `"修订后金额（元）"` and `"修订后交期（天）"`.
     - Clicks button `"确认发起变更"` without filling required fields.
     - Form displays error `"合同变更必须填写变更原因"`.
     - `fetchMock` was NOT called.

---

### File 5: `src/procurement/contracts.test.ts` (4 Tests)
1. **`keeps Web status values aligned with the shared contract`**
   - Asserts exact equality between frontend status arrays (`PROCUREMENT_STATUSES`, `AI_TASK_STATUSES`, `AI_TASK_TYPES`, `AI_TASK_STEPS`, `INVOICE_STATUSES`, `CONTRACT_STATUSES`, `HUMAN_INTERACTION_STATUSES`, `AI_STATUS_TRANSITIONS`) and `contracts/procurement-workbench.schema.json`.
2. **`publishes the complete human interaction and operation boundary`**
   - Asserts schema definition properties and required fields (`answer_schema`, `operation_id`, `sha256`, `payload_sha256`).
3. **`keeps missing draft quantity and unit as explicit unknown facts`**
   - Asserts `ProcurementRequirementView` allows nullable `quantity` and `unit`.
4. **`represents failure and stale results independently`**
   - Asserts `ai-task-failed.json` and `ai-task-stale.json` properties (`status: "FAILED"`, `retryable: true`, `stale: true`, `stale_reason: "INPUT_GENERATION_CHANGED"`).

---

### File 6: `src/procurement/invoiceCenter.test.tsx` (4 Tests)
1. **`renders the list with status chips and diff badges`**
   - Component: `<InvoiceCenter />` with `diffInvoice` and `matchedInvoice`.
   - Text Assertions: `"发票中心"`, `"INV-2026081601"`, `"差异挂起"`, `"2 项差异"`, `"已匹配"`.
2. **`shows the three-way comparison, structured diffs and agent explanation for a DIFF_HOLD invoice`**
   - Interaction: Clicks button `"INV-2026081601"`.
   - Text Assertions: `"三单匹配对比"`, `"数量不一致"`, `"期望 1000"`, `"手工改单"`, `"强制通过"`, `"作废（退回重开）"`.
   - Assert: `.proc-invoice-actions` does NOT contain `"核销"`.
3. **`offers reconcile only for MATCHED invoices`**
   - Interaction: Clicks button `"INV-2026081602"`.
   - Text Assertions: `"核销"`, `"三单匹配通过"`.
4. **`manual-correction dialog includes unit_price and force dialog requires confirmation`**
   - Interaction: Clicks `"手工改单"` -> `[role="dialog"]` contains `"单价"` and `"价税合计"`.
   - Interaction: Clicks `"强制通过"` -> clicks `"确认强制通过"` without checking confirmation checkbox.
   - Assertions: `host.textContent` contains `"强制通过必须勾选确认并填写人工备注"`; `fetchMock` was NOT called.

---

### File 7: `src/procurement/orderCenter.test.tsx` (10 Tests)
1. **`formats persisted decimal scales as business amounts and quantities`**
   - Assertions: Decimal scales like `10400.000000000000000000` format to `"10,400.00"`, `3000.000000000000000000` to `"3,000 piece"`, `2999.500000000000000000` to `"2,999.5"`.
2. **`keeps the receive dialog open with input intact when the API fails`**
   - Queries: Card `.proc-order-card` with `"PO-TEST-SHIP"`, button `"确认收货"`.
   - Selectors: `#receive-title`, `section:has(#receive-title)`.
   - Form inputs: `input[type="number"]` ("100"), `input[type="date"]` ("2026-08-20").
   - Click: button `"登记本批收货"`.
   - Assertions: `#receive-title` remains visible; `[role="alert"]` contains error message; single POST call.
3. **`reuses the same idempotency key when the same receive payload is retried after failure`**
   - Assertions: `Idempotency-Key` header is present and unchanged between failure retry.
4. **`does not send a request for zero, negative or over-quantity receive input`**
   - Tests: `"0"`, `"-5"`, `"99999"`.
   - Assertions: `#receive-title` exists; `[role="alert"]` contains `"数量"`; 0 POST calls made.
5. **`does not throw or send a request for an invalid payment date`**
   - Queries: Row `.proc-settlement-row` with `"ST-TEST-0001"`, button `"登记付款"`.
   - Selectors: `#pay-title`, `section:has(#pay-title)`.
   - Input: date `"2026-13-45"`, clicks `"确认付款"`.
   - Assertions: `#pay-title` exists; `[role="alert"]` matches `/无效|必须填写/`; 0 POST calls made.
6. **`closes dialogs on Escape`**
   - Interaction: Clicks `"确认收货"`, verifies `#receive-title`, dispatches `KeyboardEvent("keydown", { key: "Escape" })` on `window`.
   - Assertion: `#receive-title` is removed (falsy).
7. **`ignores a second click while a receive transition is in flight`**
   - Interaction: While in-flight, confirm button has `disabled === true`; second click ignored; exactly 1 POST call made.
8. **`shows a success notice after a successful receive`**
   - Interaction: Completes full receipt (`300` of `300`).
   - Assertions: `#receive-title` removed; `[role="status"]` contains `"最后一批收货"`.
9. **`offers only the remaining quantity for a partially received order`**
   - Interaction: For order with quantity 300 and received 100, clicks `"继续收货"`.
   - Assertions: `input[type="number"]` value is `"200"`; dialog text contains `"剩余数量 200"`.
10. **`keeps decimal precision when calculating the remaining receipt quantity`**
    - Interaction: Quantity `0.300000000000000000`, received `0.100000000000000000`.
    - Assertions: `input[type="number"]` value is `"0.2"`; dialog text contains `"剩余数量 0.2"`.

---

### File 8: `src/procurement/procurement.test.tsx` (30 Tests)
1. **`renders a task-first workbench and only real navigation`**
   - Components: `<WorkbenchHome />`, `<WorkbenchNavigation />` (admin role).
   - Home text: `"开始采购比价"`, `"开始解析报价"`, `"演示数据"`, `"等待字段复核"`, `"等待确认采购方案"`, `"待收货订单"`, `"发票差异待处理"`, `"付款被拦截"`.
   - Negative assertions: Does NOT contain `"管理驾驶舱"`, does NOT contain `"成本节约率"`.
   - Nav text: `"工作台"`, `"履约中心"`, `"业务资料"`, `"管理与技术"`, `"AI 任务诊断"`, `"供应商档案"`.
2. **`filters navigation by demo role (buyer hides approval views, admin sees all)`**
   - Buyer role: contains `"供应商档案"`, hides approval views.
   - Approver role: contains `"AI 任务诊断"`, `"人工审核"`, `"履约中心"`; hides `"采购任务"`, `"供应商档案"`.
3. **`disables blind retry for non-retryable AI failures and keeps recovery actions visible`**
   - Component: `<AiTaskRecovery />` with non-retryable failed AI task.
   - Text: `"分析失败"`, `"报价文件为空"`, `"补充资料"`, `"查看日志"`.
   - Regex check: `/<button[^>]*disabled=""[^>]*title="该错误不可直接重试/`.
4. **`offers cancellation while an AI task is still active`**
   - Component: `<AiTaskRecovery />` with `status: "RUNNING"`.
   - Text: `"正在分析"`, `"取消任务"`.
5. **`surfaces a failed durable conversation operation`**
   - Asserts `procurementApi.startConversation` rejects with `"采购数量无法从采购描述中识别"`.
6. **`surfaces a failed durable analysis operation`**
   - Asserts `procurementApi.analyze` rejects with `"缺少 USD 汇率不是有效数值"`.
7. **`does not report a still-running operation as successful after the polling deadline`**
   - Asserts `procurementApi.analyze` rejects with `/仍在后台处理中/` after timeout.
8. **`uses the server quote limit for a 30-file blind-test batch`**
   - Component: `<NewProcurementConversation />`.
   - Assert: HTML contains `"0<!-- --> / <!-- -->50<!-- --> 份"` (or `"0 / 50 份"`).
9. **`shows low-confidence fields with source evidence and blocks comparison`**
   - Component: `<QuoteWorkspace />`.
   - Text: `"55%"`, `"文件名"`, `"Alpha Packaging.xlsx"`, `"1 项待复核"`, `"确认当前值并完成复核"`, `"disabled"`.
10. **`does not duplicate V2 dynamic specs in the quote review`**
    - Component: `<QuoteWorkspace />`.
    - Assert: `data-field="material"` and `data-field="color"` count is exactly 1 each.
    - Text: `"报价字段与来源证据"`.
11. **`maps separate V2 width, length, and layers specs to standard parsed fields`**
    - Assert: `data-field="width"` with `value="48"`, `data-field="length"` with `value="100000"`, `data-field="layers"` with `value="5"`.
    - Negative assert: does NOT contain `"原文未找到"`.
12. **`shows V2 specs from standard quote fields and hides duplicated MOQ`**
    - Assert: `data-field="尺寸"` contains `"100×150"`, `"80"`, `"铜版纸"`, `"白色"`.
    - Negative assert: `data-field="MOQ"` count is 0; does NOT contain `"原文未找到"`.
13. **`keeps the quote workspace visible for completed requests`**
    - Component: `<RequirementReview />` with `status: "approved"`.
    - Text: `"采购需求已确认"`, `"展开"`.
    - Negative assert: does NOT contain `"保存人工确认"`.
14. **`derives the required delivery date from the request date and lead time`**
    - Assert: contains `value="2026-08-11"`.
15. **`renders dynamic specification rows for a V2 requirement`**
    - Assert: contains `"新增规格"`, `"长度"`; does NOT contain `"宽度（mm）"`.
16. **`keeps a reply composer when requirement capture asks for confirmation`**
    - Component: `<ProcurementConversation />` in `require_human` state.
    - Text: `"补充 Agent 请求的信息"`, `"恢复采购 Agent"`, `"报价字段尚未全部确认，请在右侧复核后继续。"`.
    - Negative asserts: does NOT contain `"verification requires human review"`, does NOT contain `"【采购决策已验证】"`.
17. **`counts backend completed tool invocations as finished`**
    - Assert: regex match `/3(?:<!-- -->)?\/(?:<!-- -->)?3/`, text `"已完成"`, does NOT contain `"执行中"`.
18. **`renders an accepted task before the Agent binds its session`**
    - Assert: contains `"SESSION"`, `"准备中"`.
19. **`shows unknown draft quantity and unit as pending instead of invented facts`**
    - Component: `<ProcurementWorkbench />`.
    - Assert: `match(/待补充/g).length >= 2`; does NOT contain `"1 piece"`, does NOT contain `"null null"`.
20. **`explains deterministic cost ranking after excluding hard violations`**
    - Component: `<ComparisonView />`.
    - Text: `"规则推荐"`, `"起订量（MOQ）20000 高于采购量 10000"`, `"总到货成本"`, `"精确金额核算"`, `"提交供应商审批"`.
21. **`keeps recovery actions available when every quote is excluded`**
    - Component: `<ComparisonView />` when all quotes excluded.
    - Text: `"调整需求"`, `"补充报价"`, `"重新比价"`, `"本轮流标"`.
    - Negative assert: does NOT contain `"提交供应商审批"`.
22. **`renders a durable approved report with source and comparison hashes`**
    - Component: `<ReportView />`, function `procurementReportMarkdown`.
    - Text: `"已选定"`, `"Alpha Packaging"`, `"证据指纹"`, `"报价原件与字段来源"`, `"采购审计时间线"`.
    - Markdown: `"# 采购审批报告"`, `"选定供应商：Alpha Packaging"`, `"供应商已人工批准"`, `"分析运行 ID：run"`.
23. **`home task entries carry the correct destination`**
    - Component: `<WorkbenchHome />`.
    - Clicks:
      - `"等待字段复核"` -> calls `onOpenTasks("attention")`.
      - `"待收货订单"` -> calls `onOpenOrders()`.
      - `"等待确认采购方案"` -> calls `onOpenView("reviews")`.
      - `"AI 任务需处理"` -> calls `onOpenView("ai")`.
24. **`does not count supplier risks hidden from the approver view`**
    - Component: `<WorkbenchHome role="approver" />`.
    - Selectors: `.proc-home-section header` contains `"0 项"`; does NOT contain `"供应商进入黑名单"`.
25. **`requires an explicit checkbox before the approval can be submitted`**
    - Component: `<ComparisonView />`.
    - Interaction:
      - Clicks button `"提交供应商审批"`.
      - Dialog `[role="dialog"]` appears with text `"正式选定供应商"`.
      - Button `"确认选定"` has `disabled === true`.
      - Clicks checkbox `input[type="checkbox"]` -> button `"确认选定"` has `disabled === false`.
      - Clicks `"确认选定"` -> `onApprove` called.
      - Escape key closes `[role="dialog"]`.
26. **`renders a durable no-award report without a supplier or execution drafts`**
    - Component: `<ReportView />` with `status: "no_award"`.
    - Text: `"本轮流标"`, `"流标不会生成订单或供应商邮件草稿"`.
27. **`renders and preserves the carton height field in requirement review`**
    - Assert: contains `"高度（mm）"` and `value="250"`.
28. **`renders the carton height quote field for correction`**
    - Component: `<QuoteWorkspace />`.
    - Assert: `data-field="height_mm"` with `value="250"`.
29. **`collapses accepted fields and evidence details by default (P1-4 density)`**
    - Component: `<QuoteWorkspace />`.
    - Assertions:
      - `querySelector('[data-field="supplier_name"]')` is truthy.
      - `querySelector('[data-field="unit_price"]')` is `null` (collapsed in `onlyReview` mode).
      - `querySelector('details.proc-evidence-panel')` is truthy, `open === false`, text contains `"证据已验证"`.
30. **`lets conflict candidates be chosen with a single click and submits the chosen flag (P2-2)`**
    - Component: `<QuoteWorkspace />`.
    - Selectors: `.proc-conflict-chip` length is 2; first candidate contains `"Beta Packaging"`.
    - Click: clicks first chip.
    - Assertion: `onCorrect` called with `("quote-alpha", "supplier_name", "Beta Packaging", true)`.

---

### File 9: `src/procurement/roles.test.ts` (1 Test)
1. **`keeps allowed views and redirects hidden views to the workbench`**
   - Tests `visibleViewOrDefault(role, view)`:
     - `visibleViewOrDefault("approver", "reviews")` => `"reviews"`
     - `visibleViewOrDefault("approver", "orders")` => `"orders"`
     - `visibleViewOrDefault("buyer", "system")` => `"workbench"`
     - `visibleViewOrDefault("admin", "system")` => `"system"`

---

### File 10: `src/procurement/systemInfo.test.tsx` (1 Test)
1. **`renders sanitized LLM gateway states with degradation markers`**
   - Component: `<SystemInfo />`.
   - Text Assertions: `"LLM 网关（限流 / 熔断 / 降级）"`, `"熔断中"`, `"剩余 42s"`, `"失败 7 / 限流 2 / 降级 1"`, `"正常"`, `"llm_gateway_v1"`.
   - Negative assert: does NOT contain `"api_key"`.

---

### File 11: `src/procurement/viewModel.test.ts` (5 Tests)
1. **`labels every status with actionable Chinese labels and never leaks raw enums`**
   - Asserts `statusLabel` returns localized Chinese strings for all statuses.
   - `statusLabel("analyzed")` contains `"待审批"`.
   - `statusLabel("approval_pending")` contains `"审批处理中"`.
   - `statusLabel("waiting_human") === "等待你补充信息"`.
   - `STATUS_LABELS.analyzed !== STATUS_LABELS.approval_pending`.
   - `STATUS_LABELS.analyzed` contains `"比价完成"`.
2. **`maps every status to a tone and a closed-loop step`**
   - Asserts `CLOSED_LOOP_STEPS`: `["上传与解析", "字段复核", "供应商比价", "确认采购方案", "采购订单", "发货", "分批收货", "发票匹配", "对账", "付款"]`.
   - Asserts `closedLoopStep("draft") === 1`, `closedLoopStep("collecting") === 1`, `closedLoopStep("review") === 2`, `closedLoopStep("ready") === 3`, `closedLoopStep("analyzed") === 4`, `closedLoopStep("approved") === 4`.
   - Asserts `PROCUREMENT_DECISION_STEPS.length === 4`, `FULFILLMENT_STEPS.length === 6`, `procurementDecisionProgress("review") === 2`.
3. **`extends approved tasks through order / invoice / settlement lifecycle`**
   - Asserts `closedLoopProgress` and `fulfillmentProgress` step indices.
4. **`projects every fulfillment todo from authoritative order, invoice and settlement facts`**
   - Asserts `fulfillmentNextStep` returns labels: `"待发货"`, `"待确认收货"`, `"部分收货"`, `"待上传发票"`, `"发票匹配处理中"`, `"发票差异待处理"`, `"待核销"`, `"待对账"`, `"可付款"`, `"付款被拦截"`, `"已完成"`, and flags `canSettle`, `canPay`.
5. **`guides the next action per status with a visible blocker reason`**
   - Asserts `nextStepGuide`:
     - `ready` / `analyzed`: `action: { kind: "compare" }`, label `"开始比价"`.
     - `approved`: `action: { kind: "orders" }`, label containing `"订单已生成"`.
     - `approval_pending`: `action: { kind: "reviews" }`.
     - `cancelled`: `action: { kind: "none" }`.
     - Blocker reasons:
       - `collecting` with 1 quote: contains `"至少需要 2 家报价"`.
       - `review` unconfirmed: contains `"需求待人工确认"`.
       - `review` unresolved: contains `"3 项报价字段待复核"`.

---

### File 12: `src/procurement/workbenchUrl.test.ts` (6 Tests)
1. **`round-trips view, task, tab, filters, search and pagination`**
   - Asserts `workbenchSearch` and `readWorkbenchUrl` round-trip for view, task, ai, review, tab, status, q, page.
2. **`round-trips the order-task focus used by the closed-loop entry`**
   - Asserts `order_task=` serialization and retrieval.
3. **`falls back from invalid URL values without losing a valid task`**
   - Asserts fallback to default `tab: "quotes"`, `view: "tasks"` when query params are invalid.
4. **`opens AI and review details directly when the view is omitted`**
   - Asserts `?ai=...` maps to `view: "ai"`, `?review=...` maps to `view: "reviews"`.
5. **`restores view, task, tab, filter and page after a refresh (URL round-trip)`**
   - Asserts round-trip resilience with or without leading `?`.
6. **`keeps task filter and page in the URL for home entry deep links`**
   - Asserts query generation `?view=tasks&status=completed&page=3`.

---

### File 13: `src/useAgentStream.test.ts` (2 Tests)
1. **`starts live without replaying the complete event history`**
   - Asserts `agentStreamUrl(0) === "/api/stream"`.
2. **`keeps an explicit positive cursor for replay and reconnect tests`**
   - Asserts `agentStreamUrl(42) === "/api/stream?after=42"`.

---

## 4. Comprehensive Semantic Contracts & Compatibility Rules (R4)

To guarantee that 100% of tests pass without any regression during the UI overhaul, the following semantic elements, selectors, attributes, text strings, and behaviors **MUST be strictly preserved**:

### 4.1. Critical Element IDs

| Element ID | Component | Tested In | Purpose / Verification |
|---|---|---|---|
| `#receive-title` | `OrderCenter.tsx` | `orderCenter.test.tsx` (cases 2, 3, 4, 6, 8, 9, 10) | Modal dialog heading for Order Receipt. Used in `section:has(#receive-title)` selector to query inputs inside receipt dialog. |
| `#pay-title` | `OrderCenter.tsx` | `orderCenter.test.tsx` (case 5) | Modal dialog heading for Settlement Payment. Used in `section:has(#pay-title)` selector. |
| `#proc-conversation-panel` | `ProcurementWorkbench.tsx` | `ProcurementWorkbench.tsx` | Agent conversation container controlled by `aria-controls="proc-conversation-panel"`. |
| `#proc-requirement-review-${id}` | `RequirementReview.tsx` | `RequirementReview.tsx` | Collapsible form controlled by `aria-controls`. |

> **⚠️ Rule**: Do not rename or remove `#receive-title` and `#pay-title`. The test suite queries `section:has(#receive-title)` and `section:has(#pay-title)`.

---

### 4.2. Critical CSS Class Selectors

The following classes are directly queried via `querySelector` / `querySelectorAll` in unit tests:

| CSS Class | Component | Tested In | Test Query / Assertion |
|---|---|---|---|
| `.proc-inline-error` | `AiTaskCenter.tsx`, `QuoteWorkspace.tsx`, etc. | `centers.test.tsx` (case 2), `procurement.test.tsx` | Checks error message display after retry/transition conflict. |
| `.proc-invoice-actions` | `InvoiceCenter.tsx` | `invoiceCenter.test.tsx` (case 2) | Asserts `"核销"` button is absent during DIFF_HOLD. |
| `.proc-order-card` | `OrderCenter.tsx` | `orderCenter.test.tsx` (cases 2, 3, 4, 6, 7, 8) | Finds order card by PO number (`PO-TEST-SHIP`). |
| `.proc-settlement-row` | `OrderCenter.tsx` | `orderCenter.test.tsx` (case 5) | Finds settlement row by settlement number (`ST-TEST-0001`). |
| `.proc-home-section` | `WorkbenchHome.tsx` | `procurement.test.tsx` (case 24) | Finds risk section and checks `header` for `"0 项"`. |
| `.proc-conflict-chip` | `QuoteWorkspace.tsx` | `procurement.test.tsx` (case 30) | Finds candidate chips for conflict resolution; clicks to select. |
| `details.proc-evidence-panel` | `QuoteWorkspace.tsx` | `procurement.test.tsx` (case 29) | Checks `open === false` and text `"证据已验证"`. |

> **⚠️ Rule**: Even when migrating to Tailwind CSS utility classes, keep these specific CSS class names on their respective container elements (or alongside Tailwind classes) so test DOM queries continue finding them.

---

### 4.3. Critical HTML Attributes & Data Attributes

| Attribute | Value / Format | Component | Tested In | Purpose |
|---|---|---|---|---|
| `data-field` | `"material"`, `"color"`, `"width"`, `"length"`, `"layers"`, `"尺寸"`, `"height_mm"`, `"supplier_name"`, `"unit_price"` | `QuoteWorkspace.tsx` | `procurement.test.tsx` (cases 10, 11, 12, 28, 29) | Dynamic and standard quote field editor rows. Tests count `[data-field="..."]` instances and verify `value`. |
| `data-testid` | `"conversation-upload"` | `ProcurementConversation.tsx` | Component test hook | File input for new conversation. |
| `data-testid` | `"quote-upload"` | `QuoteWorkspace.tsx` | Component test hook | File input for quote uploads. |
| `data-testid` | `"invoice-upload"` | `InvoiceCenter.tsx` | Component test hook | File input for invoice upload. |
| `input[type="number"]` | Standard numeric input | `HumanInteractionPanel`, `OrderCenter`, `RequirementReview` | `HumanInteractionPanel.test.tsx`, `orderCenter.test.tsx` | Number inputs filled via `setInput` helper. |
| `input[type="file"]` | File input | `HumanInteractionPanel`, `ProcurementConversation`, `QuoteWorkspace`, `InvoiceCenter` | `HumanInteractionPanel.test.tsx` | File uploads attached via `Object.defineProperty(input, 'files', ...)` and `change` event. |
| `input[type="date"]` | Date input | `OrderCenter`, `RequirementReview` | `orderCenter.test.tsx` | Date inputs filled via `setInput`. |
| `input[type="checkbox"]` | Checkbox | `ComparisonView`, `InvoiceCenter`, `ReviewCenter`, `ContractCenter` | `procurement.test.tsx`, `invoiceCenter.test.tsx` | Confirmation gates (e.g. approval confirm). |
| `button[title]` | Tooltip string | `AiTaskRecovery` | `procurement.test.tsx` (case 3) | Asserts regex `/<button[^>]*disabled=""[^>]*title="该错误不可直接重试/`. |

---

### 4.4. Critical ARIA Roles and Labels

| ARIA Attribute | Target / Element | Tested In | Assertion / Behavior |
|---|---|---|---|
| `[role="alert"]` | Error and validation containers | `HumanInteractionPanel.test.tsx` (case 1), `orderCenter.test.tsx` (cases 2, 4, 5), `centers.test.tsx` | Asserts exact error messages (e.g. `"请填写采购数量"`, `"模拟服务器故障"`). |
| `[role="dialog"]` | Modals / Dialog sections | `centers.test.tsx` (case 4), `contractCenter.test.tsx` (case 3), `invoiceCenter.test.tsx` (case 4), `procurement.test.tsx` (case 25) | Confirmation dialogs for review approval, contract change, invoice force-match, supplier approval. |
| `[role="status"]` | Success notices | `orderCenter.test.tsx` (case 8), `RequirementReview.tsx` | Asserts `"最后一批收货"` notice. |
| `aria-label="补充澄清信息"` | Textarea in `ProcurementConversation` | `HumanInteractionPanel.test.tsx` (case 6) | Must be `null` when `structuredInteractionActive` is true. |
| `aria-label="演示角色"` | Select element | `ProcurementWorkbench.tsx` | Demo role switcher dropdown. |
| `aria-label="采购任务状态筛选"` | Filter bar | `ProcurementWorkbench.tsx` | Status tab buttons. |
| `aria-label="搜索采购任务"` | Search input | `ProcurementWorkbench.tsx` | Task search input. |
| `aria-label="采购任务视图"` | Tab navigation bar | `ProcurementWorkbench.tsx` | Tab navigation. |
| `aria-label="履约进度"` / `aria-label="采购决策进度"` | Step indicator `<ol>` | `ProcurementWorkbench.tsx` | Progress bar. |
| `aria-label="采购 Agent 对话"` | Aside container | `ProcurementConversation.tsx` | Chat pane. |
| `aria-label="新建采购任务"` | Section container | `NewProcurementConversation.tsx` | Intake form. |
| `aria-label="快速模板提示词"` | Prompt chips | `NewProcurementConversation.tsx` | Prompt suggestion chips. |
| `aria-label="采购目标"` | Textarea | `NewProcurementConversation.tsx` | Prompt textarea. |
| `aria-label="供应商报价列表"` | Section container | `QuoteWorkspace.tsx` | Quote file list. |
| `aria-label="报价字段复核"` | Section container | `QuoteWorkspace.tsx` | Field review editor. |
| `aria-label="报价证据详情"` | Details element | `QuoteWorkspace.tsx` | Evidence panel. |
| `aria-label="采购需求人工复核"` | Section container | `RequirementReview.tsx` | Requirement form. |
| `aria-label="比价证据详情"` | Details element | `ComparisonView.tsx` | Snapshot evidence. |
| `aria-label="核心指标看板"` | Section container | `WorkbenchHome.tsx` | KPI stats cards. |
| `aria-label="待办中心"` | Section container | `WorkbenchHome.tsx` | Quick action chips. |
| `aria-label="采购工作台主导航"` | Nav element | `WorkbenchNavigation.tsx` | Navigation rail. |
| `aria-label="AI 任务筛选"` / `aria-label="人工审核筛选"` | Filter bars | `AiTaskCenter.tsx`, `ReviewCenter.tsx` | Center filter bars. |

---

### 4.5. Exact Text Strings & Matchers Asserted in Tests

The UI refactor MUST maintain exact Chinese wording for all key labels and prompts checked by tests:

#### 1. Home & Navigation
- `"开始采购比价"`, `"开始解析报价"`, `"演示数据"`, `"等待字段复核"`, `"等待确认采购方案"`, `"待收货订单"`, `"发票差异待处理"`, `"付款被拦截"`, `"AI 任务需处理"`
- `"工作台"`, `"履约中心"`, `"业务资料"`, `"管理与技术"`, `"AI 任务诊断"`, `"供应商档案"`, `"人工审核"`
- Negative assertions: Must NOT contain `"管理驾驶舱"` or `"成本节约率"`

#### 2. AI Task & Recovery
- `"AI 任务中心"`, `"分析失败"`, `"正在分析"`, `"报价文件为空"`, `"补充资料"`, `"查看日志"`, `"取消任务"`, `"再次点击确认取消"`, `"重试"`
- Result details: `"已核对两份报价来源"`, `"两份报价均已核对"`, `"采购详情"`

#### 3. Human Interaction & Conversation
- `"请补充采购数量和最长交期"`, `"这些字段会影响供应商资格和排序。"`, `"字段复核"`, `"请填写采购数量"`
- `"回答已保存，Agent 将从当前步骤继续"`, `"回答已保存，Agent 暂未恢复"`, `"重新派发"`, `"回答已应用"`, `"继续处理后续步骤"`
- `"补充 Agent 请求的信息"`, `"恢复采购 Agent"`, `"报价字段尚未全部确认，请在右侧复核后继续。"`
- `"SESSION"`, `"准备中"`, `"待补充"` (for unknown draft quantity/unit)

#### 4. Quote Workspace & Requirement Review
- `"55%"`, `"文件名"`, `"1 项待复核"`, `"确认当前值并完成复核"`, `"报价字段与来源证据"`, `"证据已验证"`, `"原文未找到"` (absence asserted for mapped specs)
- `"采购需求已确认"`, `"展开"`, `"新增规格"`, `"长度"`, `"高度（mm）"`

#### 5. Comparison View & Approval
- `"规则推荐"`, `"起订量（MOQ）20000 高于采购量 10000"`, `"总到货成本"`, `"精确金额核算"`, `"提交供应商审批"`
- `"调整需求"`, `"补充报价"`, `"重新比价"`, `"本轮流标"`, `"正式选定供应商"`, `"确认选定"`
- `"已选定"`, `"证据指纹"`, `"报价原件与字段来源"`, `"采购审计时间线"`, `"# 采购审批报告"`, `"选定供应商：Alpha Packaging"`, `"供应商已人工批准"`

#### 6. Order Center & Fulfillment
- `"10,400.00"`, `"3,000 piece"`, `"2,999.5"`
- `"确认收货"`, `"登记本批收货"`, `"最后一批收货"`, `"部分收货"`, `"继续收货"`, `"剩余数量 200"`, `"剩余数量 0.2"`
- `"登记付款"`, `"确认付款"`

#### 7. Invoice Center
- `"发票中心"`, `"差异挂起"`, `"2 项差异"`, `"已匹配"`, `"三单匹配对比"`, `"数量不一致"`, `"期望 1000"`, `"手工改单"`, `"强制通过"`, `"作废（退回重开）"`, `"核销"`, `"三单匹配通过"`, `"单价"`, `"价税合计"`, `"确认强制通过"`, `"强制通过必须勾选确认并填写人工备注"`

#### 8. Contract Center
- `"合同中心"`, `"提交审批"`, `"重新草拟"`, `"按修订值重新草拟"`, `"发起变更"`, `"确认发起变更"`, `"修订后金额（元）"`, `"修订后交期（天）"`, `"合同变更必须填写变更原因"`
- Revision text format: `"修订：金额 12,000.00 · 交期 18 天（待审批）"`

#### 9. Review Center
- `"AI 建议"`, `"确认建议"`, `"修改后通过"`, `"驳回重跑"`, `"流标"`, `"提交审核"`, `"确认提交：确认 AI 建议"`, `"我已核对 AI 建议、报价原件与确定性比价证据"`, `"正式决定已形成"`, `"人工审核记录"`

#### 10. System Info
- `"LLM 网关（限流 / 熔断 / 降级）"`, `"熔断中"`, `"剩余 42s"`, `"失败 7 / 限流 2 / 降级 1"`, `"正常"`, `"llm_gateway_v1"`
- Negative assertion: Must NOT leak `"api_key"`

---

### 4.6. User Interaction Behaviors & Event Handlers

1. **Two-Step Confirmations**:
   - Cancel Action (in `HumanInteractionPanel`, `AiTaskRecovery`, `AiTaskCenter`): 1st click alters button text to `"再次点击确认取消"`; 2nd click executes cancel network request.
   - Force Match (in `InvoiceCenter`): Requires checking the checkbox before submitting; otherwise shows alert `"强制通过必须勾选确认并填写人工备注"` without sending fetch.
   - Contract Change (in `ContractCenter`): Requires both change reason and positive revised numbers; otherwise shows alert `"合同变更必须填写变更原因"`.
   - Review Submit (in `ReviewCenter`): Opens confirmation dialog with text `"确认提交：..."`; requires checking `"我已核对 AI 建议..."` before `"确认提交"` button becomes enabled.
   - Comparison Approval (in `ComparisonView`): Opens dialog with text `"正式选定供应商"`; requires checking confirmation checkbox before `"确认选定"` button is enabled.
2. **Keyboard Handlers (`Escape` key)**:
   - `OrderCenter.tsx`: Pressing `Escape` key on `window` closes the receive dialog and pay dialog.
   - `ComparisonView.tsx`: Pressing `Escape` key closes the supplier approval dialog.
   - `useEscape` hook is used throughout modals and dialogs.
3. **Form Submission & Idempotency**:
   - `HumanInteractionPanel`: Form `submit` event triggers answer payload submission. Number inputs are converted to `number` in JSON. Retries send identical `Idempotency-Key` header.
   - `OrderCenter`: Receiving transition uses `Idempotency-Key` header, which is preserved across network retries. In-flight requests disable submit button to prevent double submission.
4. **Conflict Candidate Quick Selection**:
   - `QuoteWorkspace`: `.proc-conflict-chip` buttons handle single-click selection of alternative values and invoke `onCorrect(quoteId, fieldName, value, true)`.

---

## 5. Verification Commands & Acceptance Checkpoints

Before and after any UI code modifications, run the following commands from `web/`:

```powershell
# 1. Run the entire test suite (all 13 files, 80 tests)
npm test -- --run

# 2. TypeScript compilation & Vite production bundle check
npm run build
```

---

## 6. Safe Refactoring Guidelines for UI Developers

1. **Dual-Pane Split Canvas (R2)**:
   - When placing `ProcurementConversation` on the left pane and `QuoteWorkspace` / `ComparisonView` on the right pane, preserve all prop bindings, event handlers (`onResume`, `onRecover`, `onOpenComparison`), and container tags.
   - Ensure `structuredInteractionActive` continues to suppress legacy reply textarea (`[aria-label="补充澄清信息"]`).
2. **Tailwind CSS Styling (R1)**:
   - Add Tailwind classes to `className` strings, but **DO NOT strip** existing `.proc-*` class names queried by tests (e.g. `.proc-order-card`, `.proc-settlement-row`, `.proc-conflict-chip`, `details.proc-evidence-panel`, `.proc-inline-error`, `.proc-invoice-actions`).
3. **DOM Selectors & IDs**:
   - Retain IDs: `#receive-title`, `#pay-title`.
   - Retain attributes: `data-field="..."`, `data-testid="..."`, `aria-label="..."`, `role="..."`.
4. **Text Content**:
   - Do not alter Chinese status labels (`statusLabel`, `contractStatusLabel`, `STATUS_LABELS`, `ORDER_STATUS_LABELS`), button text (`"开始比价"`, `"确认收货"`, `"提交审批"`, `"确认选定"`), or dialog titles.
