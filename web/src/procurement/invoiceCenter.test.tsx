import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvoiceCenter } from "./InvoiceCenter";
import type { InvoiceView } from "./types";

const diffInvoice: InvoiceView = {
  id: "d".repeat(32),
  order_id: "o".repeat(32),
  order_no: "PO-RFQ-20260814-O1",
  task_reference: "RFQ-20260814-A1",
  invoice_code: "INV-CODE-1",
  invoice_no: "INV-2026081601",
  issue_date: "2026-08-16",
  quantity: "900",
  unit: "个",
  unit_price: "5.7778",
  amount_excluding_tax: "4601.77",
  tax_amount: "598.23",
  total_amount: "5200.00",
  tax_rate: "0.13",
  supplier_name: "演示供应商",
  parser_version: "invoice-v1",
  status: "DIFF_HOLD",
  match_result: {
    matched: false,
    expected_unit_price: "5.2",
    actual_unit_price: "5.7778",
    diffs: [
      { field: "quantity", expected: "1000", actual: "900", diff: "-100" },
      { field: "unit_price", expected: "5.2", actual: "5.7778", diff: "0.5778" },
    ],
  },
  match_explanation: {
    reason: "三单匹配存在 2 项差异：数量不一致：订单/收货为 1000，发票为 900（差异 -100）；单价不一致：订单/收货为 5.2，发票为 5.7778（差异 0.5778）。",
    suggestions: ["请核对收货数量与发票数量；如为补开票请说明原因", "请核对单价口径（含税/不含税）与订单到货单价"],
    source: "deterministic_agent",
  },
  notes: null,
  version: 2,
  created_at: "2026-08-16T01:00:00Z",
  updated_at: "2026-08-16T01:01:00Z",
  matched_at: null,
  reconciled_at: null,
  order_quantity: "1000",
  order_received_quantity: "1000",
  order_landed_total: "5200.00",
  expected_tax_rate: "0.13",
  three_way: {
    po: { quantity: "1000", received_quantity: "1000", landed_total: "5200.00" },
    grn: { received_quantity: "1000", received_at: "2026-08-15" },
    invoice: { quantity: "900", unit_price: "5.7778", total_amount: "5200.00", tax_rate: "0.13" },
  },
};

const matchedInvoice: InvoiceView = {
  ...diffInvoice,
  id: "m".repeat(32),
  invoice_no: "INV-2026081602",
  status: "MATCHED",
  quantity: "1000",
  unit_price: "5.2",
  match_result: {
    matched: true,
    expected_unit_price: "5.2",
    actual_unit_price: "5.2",
    diffs: [],
  },
  match_explanation: null,
};

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

function mount(invoices: InvoiceView[]) {
  const queryClient = client();
  queryClient.setQueryData(["procurement-invoices", ""], { items: invoices, page: 0, size: 100, total: invoices.length });
  queryClient.setQueryData(["procurement-invoices-orders"], { items: [], page: 0, size: 100, total: 0 });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  return { queryClient, host, root };
}

describe("invoice center", () => {
  it("renders the list with status chips and diff badges", async () => {
    const { queryClient, host, root } = mount([diffInvoice, matchedInvoice]);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <InvoiceCenter />
        </QueryClientProvider>,
      );
    });
    expect(host.textContent).toContain("发票中心");
    expect(host.textContent).toContain("INV-2026081601");
    expect(host.textContent).toContain("差异挂起");
    expect(host.textContent).toContain("2 项差异");
    expect(host.textContent).toContain("已匹配");
    await act(async () => root.unmount());
  });

  it("shows the three-way comparison, structured diffs and agent explanation for a DIFF_HOLD invoice", async () => {
    const { queryClient, host, root } = mount([diffInvoice]);
    queryClient.setQueryData(["procurement-invoice", diffInvoice.id], diffInvoice);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <InvoiceCenter />
        </QueryClientProvider>,
      );
    });
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("INV-2026081601")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });

    expect(host.textContent).toContain("三单匹配对比");
    expect(host.textContent).toContain("数量不一致");
    expect(host.textContent).toContain("期望 1000");
    expect(host.textContent).toContain("手工改单");
    expect(host.textContent).toContain("强制通过");
    expect(host.textContent).toContain("作废（退回重开）");
    expect(host.querySelector(".proc-invoice-actions")?.textContent).not.toContain("核销");
    await act(async () => root.unmount());
  });

  it("offers reconcile only for MATCHED invoices", async () => {
    const { queryClient, host, root } = mount([matchedInvoice]);
    queryClient.setQueryData(["procurement-invoice", matchedInvoice.id], matchedInvoice);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <InvoiceCenter />
        </QueryClientProvider>,
      );
    });
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("INV-2026081602")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    expect(host.textContent).toContain("核销");
    expect(host.textContent).toContain("三单匹配通过");
    await act(async () => root.unmount());
  });

  it("manual-correction dialog includes unit_price and force dialog requires confirmation", async () => {
    const { queryClient, host, root } = mount([diffInvoice]);
    queryClient.setQueryData(["procurement-invoice", diffInvoice.id], diffInvoice);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <InvoiceCenter />
        </QueryClientProvider>,
      );
    });
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("INV-2026081601")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });

    const correct = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("手工改单")) as HTMLButtonElement;
    await act(async () => { correct.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("单价");
    expect(dialog?.textContent).toContain("价税合计");
    await act(async () => { root.unmount(); });

    // 强制通过：未勾选确认时点击 → 校验提示，不发请求
    const { queryClient: queryClient2, host: host2, root: root2 } = mount([diffInvoice]);
    queryClient2.setQueryData(["procurement-invoice", diffInvoice.id], diffInvoice);
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(diffInvoice), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => {
      root2.render(
        <QueryClientProvider client={queryClient2}>
          <InvoiceCenter />
        </QueryClientProvider>,
      );
    });
    const card2 = [...host2.querySelectorAll("button")].find((button) => button.textContent?.includes("INV-2026081601")) as HTMLButtonElement;
    await act(async () => { card2.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    const force = [...host2.querySelectorAll("button")].find((button) => button.textContent?.includes("强制通过")) as HTMLButtonElement;
    await act(async () => { force.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const confirmForce = [...host2.querySelectorAll("button")].find((button) => button.textContent?.includes("确认强制通过")) as HTMLButtonElement;
    await act(async () => { confirmForce.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    expect(host2.textContent).toContain("强制通过必须勾选确认并填写人工备注");
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => root2.unmount());
  });
});
