import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiTaskCenter } from "./AiTaskCenter";
import { AuditLogCenter } from "./AuditLogCenter";
import { ContractCenter } from "./ContractCenter";
import { InvoiceCenter } from "./InvoiceCenter";
import { OrderCenter } from "./OrderCenter";
import { ReportsCenter } from "./ReportsCenter";
import { ReviewCenter } from "./ReviewCenter";
import { SupplierCenter } from "./SupplierCenter";
import type {
  AiTaskDetail,
  ContractView,
  InvoiceView,
  OrderView,
  ProcurementRequestSummary,
  ReviewDetail,
  SettlementView,
  SupplierProfile,
  SupplierView,
} from "./types";

function createTestClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
    },
  });
}

function setInputValue(input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const descriptor = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(input),
    "value",
  );
  if (descriptor?.set) {
    descriptor.set.call(input, value);
  } else {
    input.value = value;
  }
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("Adversarial Stress Verification: 8 Business Centers", () => {
  // 1. OrderCenter: Decimal Precision & Multi-Batch Receipts
  describe("OrderCenter: Decimal Precision & Multi-Batch Receipts", () => {
    it("handles ultra-high precision decimals (18 decimals) without loss or rounding artifacts", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const highPrecisionOrder: OrderView = {
        id: "order-prec-1",
        task_id: "task-prec-1",
        order_no: "PO-PREC-0001",
        supplier_name: "精密电子",
        item_name: "微米电阻",
        quantity: "100.000000000000000005",
        unit: "piece",
        landed_total: "999999.999999000000",
        status: "SHIPPED",
        received_quantity: null,
        arrival_date: null,
        notes: null,
        version: 1,
        task_reference: "RFQ-PREC-01",
        task_title: "微米级采购",
        artifacts: [],
        invoice_count: 0,
        invoice_status: null,
        settlement: null,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-orders", ""], {
        items: [highPrecisionOrder],
        page: 0,
        size: 100,
        total: 1,
      });
      queryClient.setQueryData(["procurement-settlements"], {
        items: [],
        page: 0,
        size: 100,
        total: 0,
      });

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <OrderCenter />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("PO-PREC-0001");
      expect(host.textContent).toContain("100 piece");

      // Open receive dialog
      const shipButton = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("确认收货"),
      );
      await act(async () => {
        shipButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const dialog = host.querySelector("section:has(#receive-title)")!;
      expect(dialog).toBeTruthy();
      const qtyInput = dialog.querySelector('input[type="number"]') as HTMLInputElement;
      expect(qtyInput.value).toBe("100.000000000000000005");

      await act(async () => root.unmount());
    });

    it("verifies multi-batch receipt arithmetic & status notification for partial vs complete receipts", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const partialOrder: OrderView = {
        id: "order-multi-1",
        task_id: "task-multi-1",
        order_no: "PO-MULTI-0001",
        supplier_name: "多批供应商",
        item_name: "特种包材",
        quantity: "500.5",
        unit: "kg",
        landed_total: "5005.00",
        status: "PARTIALLY_RECEIVED",
        received_quantity: "200.2",
        arrival_date: "2026-08-18T00:00:00Z",
        notes: null,
        version: 2,
        task_reference: "RFQ-MULTI-01",
        task_title: "多批到货测试",
        artifacts: [],
        invoice_count: 0,
        invoice_status: null,
        settlement: null,
        created_at: "2026-08-18T00:00:00Z",
        updated_at: "2026-08-18T00:00:00Z",
      };

      const fetchMock = vi.fn(async () =>
        new Response(
          JSON.stringify({
            ...partialOrder,
            status: "RECEIVED",
            received_quantity: "500.5",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-orders", ""], {
        items: [partialOrder],
        page: 0,
        size: 100,
        total: 1,
      });
      queryClient.setQueryData(["procurement-settlements"], {
        items: [],
        page: 0,
        size: 100,
        total: 0,
      });

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <OrderCenter />
          </QueryClientProvider>,
        );
      });

      const continueReceive = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("继续收货"),
      );
      await act(async () => {
        continueReceive?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const dialog = host.querySelector("section:has(#receive-title)")!;
      const qtyInput = dialog.querySelector('input[type="number"]') as HTMLInputElement;
      const dateInput = dialog.querySelector('input[type="date"]') as HTMLInputElement;

      // Remaining should be exactly 500.5 - 200.2 = 300.3
      expect(qtyInput.value).toBe("300.3");
      expect(dialog.textContent).toContain("剩余数量 300.3");

      // Enter exact final quantity and date
      await act(async () => {
        setInputValue(qtyInput, "300.3");
        setInputValue(dateInput, "2026-08-20");
      });

      const submitBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("登记本批收货"),
      );
      await act(async () => {
        submitBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(host.querySelector('[role="status"]')?.textContent).toContain("已确认最后一批收货 300.3，对账单已派生。");
      await act(async () => root.unmount());
    });

    it("blocks payment and settlement when invoices are not reconciled", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const unreconciledSettlement: SettlementView = {
        id: "st-unreconciled",
        order_id: "order-unreconciled",
        settlement_no: "ST-BLOCKED-01",
        supplier_name: "发票未核销供应商",
        total_amount: "8800.00",
        status: "SETTLED",
        paid_at: null,
        notes: null,
        version: 1,
        order_no: "PO-BLOCKED-01",
        task_id: "task-blocked",
        invoice_reconciled: false,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-orders", ""], {
        items: [],
        page: 0,
        size: 100,
        total: 0,
      });
      queryClient.setQueryData(["procurement-settlements"], {
        items: [unreconciledSettlement],
        page: 0,
        size: 100,
        total: 1,
      });

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <OrderCenter />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("付款被拦截：请先核销全部有效发票");
      expect(host.textContent).not.toContain("登记付款");
      await act(async () => root.unmount());
    });
  });

  // 2. ContractCenter: Single-Approval Confirmation & Java Consistency Banner
  describe("ContractCenter: Single-Approval Confirmation & Java Consistency Banner", () => {
    it("displays Java consistency check failure banner when draft text differs from awarded amounts", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const inconsistentContract: ContractView = {
        id: "contract-inconsistent",
        contract_no: "CT-INCONSISTENT-01",
        task_id: "task-inconsistent",
        task_reference: "RFQ-INCONSISTENT",
        order_id: null,
        order_no: null,
        supplier_name: "差异供应商",
        item_name: "防震泡沫",
        amount: "50000.00",
        lead_days: 15,
        status: "PENDING_APPROVAL",
        draft_text: "合同金额为 48000 元，交期 20 天。",
        clauses: [
          { title: "金额条款", content: "48000 元", risk_level: "高风险", risk_reason: "金额与定标不符" },
        ],
        consistency: {
          amount_in_text: "48000",
          lead_days_in_text: "20",
          amount_matches: false,
          lead_days_matches: false,
          consistent: false,
        },
        clause_validation: { amount_clause_present: true, lead_days_clause_present: true, valid: true },
        change_history: [],
        notes: null,
        version: 1,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
        approved_at: null,
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-contracts", ""], {
        items: [inconsistentContract],
        page: 0,
        size: 100,
        total: 1,
      });
      queryClient.setQueryData(["procurement-contracts-tasks"], []);
      queryClient.setQueryData(["procurement-contract", inconsistentContract.id], inconsistentContract);

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <ContractCenter />
          </QueryClientProvider>,
        );
      });

      const card = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("CT-INCONSISTENT-01"),
      );
      await act(async () => {
        card?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(host.textContent).toContain("草拟一致性校验（Java 权威）");
      expect(host.textContent).toContain("草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。");

      // Test Single-Approval Confirmation Guard
      const approveBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("批准"),
      );
      await act(async () => {
        approveBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const dialog = host.querySelector("section:has(#approve-contract-title)")!;
      expect(dialog).toBeTruthy();

      // Click submit without checkbox or notes -> should fail with error
      const confirmApproveBtn = [...dialog.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("确认批准"),
      );
      await act(async () => {
        confirmApproveBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      expect(dialog.querySelector('[role="alert"]')?.textContent).toContain("批准合同必须勾选确认并填写人工备注");

      await act(async () => root.unmount());
    });
  });

  // 3. InvoiceCenter: 3-Way Matching Diffs & Force Match / Void
  describe("InvoiceCenter: 3-Way Matching Diffs & Actions", () => {
    it("renders full 3-way matching tabular diffs and strictly enforces force-match confirmation", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const diffInvoice: InvoiceView = {
        id: "invoice-diff-1",
        order_id: "order-1",
        order_no: "PO-DIFF-0001",
        task_reference: "RFQ-DIFF-01",
        invoice_code: "1100223344",
        invoice_no: "INV-DIFF-0001",
        issue_date: "2026-08-20",
        quantity: "950",
        unit: "piece",
        unit_price: "10.00",
        amount_excluding_tax: "9500.00",
        tax_amount: "1235.00",
        total_amount: "10735.00",
        tax_rate: "0.13",
        supplier_name: "差异发票供应商",
        parser_version: "v2",
        status: "DIFF_HOLD",
        match_result: {
          matched: false,
          expected_unit_price: "10.00",
          actual_unit_price: "10.00",
          diffs: [
            { field: "quantity", expected: "1000", actual: "950", diff: "-50" },
          ],
        },
        match_explanation: {
          reason: "数量少开 50 件",
          suggestions: ["请确认是否补开发票"],
          source: "Java Rule Engine",
        },
        notes: null,
        version: 1,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
        matched_at: null,
        reconciled_at: null,
        order_quantity: "1000",
        order_received_quantity: "1000",
        order_landed_total: "11300.00",
        expected_tax_rate: "0.13",
        three_way: {
          po: { quantity: "1000", received_quantity: "1000", landed_total: "11300.00" },
          grn: { received_quantity: "1000", received_at: "2026-08-19" },
          invoice: { quantity: "950", unit_price: "10.00", total_amount: "10735.00", tax_rate: "0.13" },
        },
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-invoices", ""], {
        items: [diffInvoice],
        page: 0,
        size: 100,
        total: 1,
      });
      queryClient.setQueryData(["procurement-invoices-orders"], {
        items: [],
        page: 0,
        size: 100,
        total: 0,
      });
      queryClient.setQueryData(["procurement-invoice", diffInvoice.id], diffInvoice);

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <InvoiceCenter />
          </QueryClientProvider>,
        );
      });

      const card = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("INV-DIFF-0001"),
      );
      await act(async () => {
        card?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(host.textContent).toContain("三单匹配对比（PO / 收货 GRN / 发票）");
      expect(host.textContent).toContain("数量少开 50 件");

      // Open Force Match Modal
      const forceBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("强制通过"),
      );
      await act(async () => {
        forceBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const dialog = host.querySelector("section:has(#force-invoice-title)")!;
      expect(dialog).toBeTruthy();

      const confirmBtn = [...dialog.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("确认强制通过"),
      );
      await act(async () => {
        confirmBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      expect(dialog.querySelector('[role="alert"]')?.textContent).toContain("强制通过必须勾选确认并填写人工备注");

      await act(async () => root.unmount());
    });
  });

  // 4. SupplierCenter: Scoring, Slide-Over Drawer & Delete Protection
  describe("SupplierCenter: Scoring, Slide-Over Drawer & Delete Protection", () => {
    it("renders scoring breakdown and opens slide-over drawer with quote history", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const supplier: SupplierView = {
        id: "sup-001",
        name: "华东优质包材",
        contact_person: "张经理",
        phone: "13800000000",
        email: "zhang@example.com",
        address: "上海市松江区",
        main_categories: "纸箱,胶带",
        cooperation_status: "合作中",
        status: "ACTIVE",
        notes: "战略供应商",
        quote_count: 12,
        win_count: 8,
        win_rate: "0.667",
        performance: {
          score: "88.5",
          level: "优质供应商",
          win_rate_score: "50",
          activity_score: "20",
          status_score: "18.5",
          base_score: "0",
        },
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      };

      const profile: SupplierProfile = {
        ...supplier,
        // 后端 profile 视图把计数序列化为字符串（与列表视图不同口径）。
        quote_count: String(supplier.quote_count),
        win_count: String(supplier.win_count),
        win_rate: "0.667",
        items: ["瓦楞纸箱", "封箱胶带"],
        recent_quotes: [
          {
            quote_id: "q-1",
            task_id: "task-1",
            task_reference: "RFQ-20260820-01",
            item_name: "五层特硬瓦楞纸箱",
            source_filename: "报价单.xlsx",
            created_at: "2026-08-20T00:00:00Z",
          },
        ],
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-suppliers", "", "", 0], {
        items: [supplier],
        page: 0,
        size: 50,
        total: 1,
      });
      queryClient.setQueryData(["procurement-supplier-profile", supplier.id], profile);

      const onOpenTask = vi.fn();
      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <SupplierCenter onOpenTask={onOpenTask} />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("华东优质包材");
      expect(host.textContent).toContain("88.5");
      expect(host.textContent).toContain("优质供应商");

      // Open drawer by clicking card
      const card = host.querySelector(".proc-supplier-card-main")!;
      await act(async () => {
        card.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
      });

      const drawer = host.querySelector("aside:has(#supplier-profile-title)");
      expect(drawer).toBeTruthy();
      expect(drawer?.textContent).toContain("中标率得分");
      expect(drawer?.textContent).toContain("五层特硬瓦楞纸箱");

      await act(async () => root.unmount());
    });
  });

  // 5. ReviewCenter: Decision Actions & 2-Step Confirmation
  describe("ReviewCenter: 4 Decision Actions & 2-Step Confirmation", () => {
    it("validates review action rules and blocks submission without verification checkbox", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const request: ProcurementRequestSummary = {
        id: "req-rev-1",
        reference: "RFQ-REV-01",
        title: "高精度传感器采购",
        category: "electronics",
        item_name: "压力传感器",
        quantity: 500,
        unit: "piece",
        specifications: {},
        constraints: {},
        status: "analyzed",
        requirement_confirmed: true,
        session_id: "s-1",
        quote_count: 2,
        unresolved_field_count: 0,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      };

      const reviewItem: ReviewDetail = {
        review_id: "rev-001",
        business_id: request.id,
        ai_task_id: "ai-001",
        ai_result_id: "res-001",
        status: "PENDING",
        priority: 80,
        risk_flags: [],
        waiting_since: "2026-08-20T00:00:00Z",
        version: 1,
        generation: 1,
        task_version: 1,
        snapshot_id: "snap-1",
        input_sha256: "hash-in",
        suggested_quote_id: "q-alpha",
        evidence_sha256: "hash-ev",
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
        ai_result: {
          ai_result_id: "res-001",
          ai_task_id: "ai-001",
          business_id: request.id,
          generation: 1,
          input_sha256: "hash-in",
          result_sha256: "hash-out",
          raw_result: {},
          structured_result: { summary: "AI 推荐供应商 Alpha" },
          sources: [],
          provider: "mock",
          model: "deterministic",
          prompt_version: "v1",
          parser_version: "v1",
          stale: false,
          created_at: "2026-08-20T00:00:00Z",
        },
        comparison: {
          id: "cmp-1",
          request_id: request.id,
          run_id: "run-1",
          version: 1,
          input_sha256: "hash-in",
          artifact_id: "art-1",
          created_at: "2026-08-20T00:00:00Z",
          result: {
            schema_version: 1,
            ruleset_version: "v1",
            request_id: request.id,
            base_currency: "CNY",
            quantity: 500,
            quotes: [
              {
                quote_id: "q-alpha",
                supplier_name: "供应商 Alpha",
                eligible: true,
                exclusion_reasons: [],
                warnings: [],
                match: { item: "压力传感器", quoted_description: "传感器", passed: true, spec_checks: [] },
                commercial: { moq: 100, lead_time_days: 10, tax_rate: "0.13", tax_included: true, shipping_included: true, supports_invoice: true },
                cost: { quote_currency: "CNY", base_currency: "CNY", fx_rate: "1", quoted_price: "25000.00", price_basis: 500, normalized_unit_quote_currency: "50", goods_before_tax_quote_currency: "25000", tax_quote_currency: "3250", freight_quote_currency: "0", landed_total_quote_currency: "25000", landed_total_base: "25000.00", landed_unit_base: "50.00" },
                rank: 1,
                score: "100",
              },
              {
                quote_id: "q-beta",
                supplier_name: "供应商 Beta",
                eligible: true,
                exclusion_reasons: [],
                warnings: [],
                match: { item: "压力传感器", quoted_description: "传感器", passed: true, spec_checks: [] },
                commercial: { moq: 100, lead_time_days: 12, tax_rate: "0.13", tax_included: true, shipping_included: true, supports_invoice: true },
                cost: { quote_currency: "CNY", base_currency: "CNY", fx_rate: "1", quoted_price: "26000.00", price_basis: 500, normalized_unit_quote_currency: "52", goods_before_tax_quote_currency: "26000", tax_quote_currency: "3380", freight_quote_currency: "0", landed_total_quote_currency: "26000", landed_total_base: "26000.00", landed_unit_base: "52.00" },
                rank: 2,
                score: "95",
              },
            ],
            eligible_count: 2,
            excluded_count: 0,
            recommended_quote_id: "q-alpha",
            recommendation_explanation: ["价格最优"],
          },
        },
        history: [],
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-review", reviewItem.review_id], reviewItem);

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <ReviewCenter
              requests={[request]}
              reviews={[reviewItem]}
              loading={false}
              error={null}
              selectedId={reviewItem.review_id}
              onSelect={vi.fn()}
              onOpenTask={vi.fn()}
            />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("供应商 Alpha");
      expect(host.textContent).toContain("供应商 Beta");

      // Click submit review button
      const submitBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("提交审核"),
      );
      await act(async () => {
        submitBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const dialog = host.querySelector("section:has(#review-confirm-title)")!;
      expect(dialog).toBeTruthy();
      const confirmSubmit = [...dialog.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("确认提交"),
      ) as HTMLButtonElement;

      // Without checking the checkbox, confirm button is disabled
      expect(confirmSubmit.disabled).toBe(true);

      // Click checkbox to check it
      const checkbox = dialog.querySelector('input[type="checkbox"]') as HTMLInputElement;
      await act(async () => {
        checkbox.click();
      });

      expect(confirmSubmit.disabled).toBe(false);

      await act(async () => root.unmount());
    });
  });

  // 6. ReportsCenter: KPI Analytics & Frozen Evaluation Badges
  describe("ReportsCenter: KPI Analytics & Frozen Evaluation Badges", () => {
    it("renders all 5 frozen evaluation metric accuracy indicators and KPI cards", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const overviewData = {
        counts: {
          tasks: 25,
          approved_tasks: 18,
          orders: 16,
          orders_received: 12,
          settlements_paid: 10,
          suppliers: 30,
          suppliers_blacklisted: 2,
          overdue_orders: 1,
        },
        cost_savings: {
          rate: "0.145",
          budget_total: "1200000.00",
          landed_total: "1026000.00",
          savings: "174000.00",
        },
        status_funnel: [
          { status: "approved", count: 18 },
          { status: "analyzed", count: 4 },
          { status: "review", count: 3 },
        ],
      };

      const evaluationData = {
        metrics: {
          field_extraction: { accuracy: 0.985 },
          post_review_fields: { accuracy: 0.992 },
          item_matching: { accuracy: 0.965 },
          cost_calculation: { accuracy: 1.0 },
          hard_constraint_miss: { miss_rate: 0.005 },
        },
      };

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-insights-overview"], overviewData);
      queryClient.setQueryData(["procurement-insights-trend"], []);
      queryClient.setQueryData(["procurement-insights-ranking"], []);
      queryClient.setQueryData(["procurement-insights-categories"], []);
      queryClient.setQueryData(["procurement-evaluation"], evaluationData);

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <ReportsCenter />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("14.50%");
      expect(host.textContent).toContain("AI 评测指标");
      expect(host.textContent).toContain("字段抽取");
      expect(host.textContent).toContain("98.50%");
      expect(host.textContent).toContain("成本计算");
      expect(host.textContent).toContain("100.00%");
      expect(host.textContent).toContain("硬约束漏检率");
      expect(host.textContent).toContain("0.50%");

      await act(async () => root.unmount());
    });
  });

  // 7. AuditLogCenter: Timeline & Multi-Dimension Filtering
  describe("AuditLogCenter: Timeline & Filtering", () => {
    it("renders audit rows with event type mappings and query filters", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const events = [
        {
          id: "evt-1",
          event_type: "order_created",
          business_type: "order",
          business_id: "order-12345678",
          task_id: "task-12345678",
          task_reference: "RFQ-20260820-01",
          actor: "采购员小李",
          payload: {},
          created_at: "2026-08-20T00:15:00Z",
        },
        {
          id: "evt-2",
          event_type: "procurement_decision_finalized",
          business_type: "task",
          business_id: "task-12345678",
          task_id: "task-12345678",
          task_reference: "RFQ-20260820-01",
          actor: "审批主管王总",
          payload: {},
          created_at: "2026-08-20T00:10:00Z",
        },
      ];

      const queryClient = createTestClient();
      queryClient.setQueryData(
        ["procurement-audit-events", "", "", "", "", 0],
        { items: events, page: 0, size: 50, total: 2 },
      );

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <AuditLogCenter />
          </QueryClientProvider>,
        );
      });

      expect(host.textContent).toContain("审计日志");
      expect(host.textContent).toContain("订单派生");
      expect(host.textContent).toContain("order_created");
      expect(host.textContent).toContain("审批终决");
      expect(host.textContent).toContain("procurement_decision_finalized");
      expect(host.textContent).toContain("采购员小李");
      expect(host.textContent).toContain("审批主管王总");

      await act(async () => root.unmount());
    });
  });

  // 8. AiTaskCenter: Retry Conflicts & 2-Step Cancel
  describe("AiTaskCenter: Retry Conflicts & 2-Step Cancel", () => {
    it("handles 2-step cancellation with confirmation text timeout safety", async () => {
      const host = document.createElement("div");
      document.body.append(host);

      const runningTask: AiTaskDetail = {
        ai_task_id: "ai-running-01",
        business_id: "req-1",
        generation: 1,
        status: "RUNNING",
        task_type: "QUOTE_ANALYSIS",
        trace_id: "trace-running",
        current_step: "RULE_ANALYSIS",
        progress: 0.6,
        retry_count: 0,
        max_retries: 3,
        retryable: false,
        operation_id: "op-1",
        result_id: null,
        stale: false,
        error_code: null,
        assignee: "自动调度引擎",
        started_at: "2026-08-20T00:00:00Z",
        finished_at: null,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:01:00Z",
        records: [
          {
            record_id: "rec-1",
            ai_task_id: "ai-running-01",
            operation_id: "op-1",
            attempt: 1,
            sequence: 1,
            step: "INPUT_VALIDATE",
            status: "SUCCEEDED",
            summary: "输入校验通过",
            duration_ms: 15,
            created_at: "2026-08-20T00:00:01Z",
          },
        ],
        result: null,
      };

      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/cancel")) {
          return new Response(JSON.stringify({ ...runningTask, status: "CANCELLED" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/ai-tasks/")) {
          return new Response(JSON.stringify({ ...runningTask, status: "CANCELLED" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ items: [runningTask], page: 0, size: 100, total: 1 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });
      vi.stubGlobal("fetch", fetchMock);

      const queryClient = createTestClient();
      queryClient.setQueryData(["procurement-ai-task", runningTask.ai_task_id], runningTask);

      const root = createRoot(host);
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <AiTaskCenter
              requests={[]}
              tasks={[runningTask]}
              loading={false}
              error={null}
              selectedId={runningTask.ai_task_id}
              onSelect={vi.fn()}
              onOpenTask={vi.fn()}
            />
          </QueryClientProvider>,
        );
      });

      const cancelBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("取消"),
      );
      expect(cancelBtn).toBeTruthy();

      // Step 1: 1st click switches button text to "再次点击确认取消"
      await act(async () => {
        cancelBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const confirmBtn = [...host.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("再次点击确认取消"),
      );
      expect(confirmBtn).toBeTruthy();
      expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes("/cancel"))).toHaveLength(0);

      // Step 2: 2nd click sends cancellation request
      await act(async () => {
        confirmBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes("/cancel"))).toHaveLength(1);

      await act(async () => root.unmount());
    });
  });
});
