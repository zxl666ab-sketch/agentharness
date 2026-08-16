import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContractCenter } from "./ContractCenter";
import type { ContractView } from "./types";

const draft: ContractView = {
  id: "c".repeat(32),
  contract_no: "CT-RFQ-20260814-B1",
  task_id: "t".repeat(32),
  task_reference: "RFQ-20260814-B1",
  order_id: null,
  order_no: null,
  supplier_name: "演示供应商",
  item_name: "热敏标签",
  amount: "10400",
  lead_days: 12,
  status: "DRAFT",
  draft_text: "一、合同金额为人民币 10400 元。\n二、交期 12 天。",
  clauses: [
    { title: "金额条款", content: "合同金额为人民币 10400 元。", risk_level: "提示", risk_reason: "金额较大" },
    { title: "交期条款", content: "交期 12 天。", risk_level: "低", risk_reason: "与定标结果一致" },
  ],
  consistency: { amount_in_text: "10400", lead_days_in_text: "12", amount_matches: true, lead_days_matches: true, consistent: true },
  clause_validation: { amount_clause_present: true, lead_days_clause_present: true, valid: true },
  change_history: [],
  notes: null,
  version: 1,
  created_at: "2026-08-16T01:00:00Z",
  updated_at: "2026-08-16T01:01:00Z",
  approved_at: null,
};

const changeRequest: ContractView = {
  ...draft,
  id: "x".repeat(32),
  contract_no: "CT-RFQ-20260814-X1",
  status: "CHANGE_REQUEST",
  change_history: [
    {
      captured_at: "2026-08-16T02:00:00Z",
      reason: "供应商调价",
      clauses: draft.clauses,
      from_status: "EXECUTING",
      new_amount: "12000",
      new_lead_days: 18,
    },
  ],
};

const executing: ContractView = {
  ...draft,
  id: "e".repeat(32),
  contract_no: "CT-RFQ-20260814-E1",
  order_id: "o".repeat(32),
  order_no: "PO-RFQ-20260814-E1",
  status: "EXECUTING",
};

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("contract center", () => {
  it("renders list, draft actions and regen button for DRAFT contracts", async () => {
    const queryClient = client();
    queryClient.setQueryData(["procurement-contracts", ""], { items: [draft], page: 0, size: 100, total: 1 });
    queryClient.setQueryData(["procurement-contracts-tasks"], []);
    queryClient.setQueryData(["procurement-contract", draft.id], draft);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ContractCenter />
        </QueryClientProvider>,
      );
    });
    expect(host.textContent).toContain("合同中心");
    expect(host.textContent).toContain("CT-RFQ-20260814-B1");
    // 打开草拟详情：提交审批 + 重新草拟两个动作都在
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("CT-RFQ-20260814-B1")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    expect(host.textContent).toContain("提交审批");
    expect(host.textContent).toContain("重新草拟");
    await act(async () => root.unmount());
  });

  it("shows pending revision values in change history and regen for CHANGE_REQUEST", async () => {
    const queryClient = client();
    queryClient.setQueryData(["procurement-contracts", ""], { items: [changeRequest], page: 0, size: 100, total: 1 });
    queryClient.setQueryData(["procurement-contracts-tasks"], []);
    queryClient.setQueryData(["procurement-contract", changeRequest.id], changeRequest);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ContractCenter />
        </QueryClientProvider>,
      );
    });
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("CT-RFQ-20260814-X1")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    expect(host.textContent).toContain("修订：金额 12,000.00 · 交期 18 天（待审批）");
    expect(host.textContent).toContain("按修订值重新草拟");
    await act(async () => root.unmount());
  });

  it("change dialog requires revised amount and lead days before submitting", async () => {
    const queryClient = client();
    queryClient.setQueryData(["procurement-contracts", ""], { items: [executing], page: 0, size: 100, total: 1 });
    queryClient.setQueryData(["procurement-contracts-tasks"], []);
    queryClient.setQueryData(["procurement-contract", executing.id], executing);
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(executing), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ContractCenter />
        </QueryClientProvider>,
      );
    });
    const card = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("CT-RFQ-20260814-E1")) as HTMLButtonElement;
    await act(async () => { card.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    const change = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("发起变更")) as HTMLButtonElement;
    await act(async () => { change.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("修订后金额（元）");
    expect(dialog?.textContent).toContain("修订后交期（天）");
    // 原因与数值留空提交 → 前端校验拦截，不发请求
    const confirm = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("确认发起变更")) as HTMLButtonElement;
    await act(async () => { confirm.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { await Promise.resolve(); });
    expect(host.textContent).toContain("合同变更必须填写变更原因");
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });
});
