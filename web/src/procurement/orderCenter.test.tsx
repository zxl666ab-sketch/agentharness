import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OrderCenter } from "./OrderCenter";
import type { OrderView, SettlementView } from "./types";

function order(overrides: Partial<OrderView>): OrderView {
  return {
    id: "order-1",
    task_id: "task-1",
    order_no: "PO-TEST-0001",
    supplier_name: "测试供应商",
    item_name: "快递袋",
    quantity: "300",
    unit: "piece",
    landed_total: "1560.00",
    status: "SHIPPED",
    received_quantity: null,
    arrival_date: null,
    notes: null,
    version: 1,
    task_reference: "RFQ-TEST-0001",
    task_title: "测试任务",
    artifacts: [],
    settlement: null,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
    ...overrides,
  };
}

function settlement(overrides: Partial<SettlementView>): SettlementView {
  return {
    id: "settlement-1",
    order_id: "order-1",
    settlement_no: "ST-TEST-0001",
    supplier_name: "测试供应商",
    total_amount: "1560.00",
    status: "SETTLED",
    paid_at: null,
    notes: null,
    version: 1,
    order_no: "PO-TEST-0001",
    task_id: "task-1",
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
    ...overrides,
  };
}

const shippedOrder = order({ id: "order-shipped", order_no: "PO-TEST-SHIP", status: "SHIPPED", quantity: "300" });
const receivedOrder = order({
  id: "order-received",
  order_no: "PO-TEST-RECV",
  status: "RECEIVED",
  received_quantity: "300",
  settlement: {
    id: "settlement-1",
    settlement_no: "ST-TEST-0001",
    total_amount: "1560.00",
    status: "SETTLED",
    paid_at: null,
    notes: null,
    version: 1,
    created_at: "2026-08-13T00:00:00Z",
    updated_at: "2026-08-13T00:00:00Z",
  },
});
const settledSettlement = settlement({});
const unsettledSettlement = settlement({ id: "settlement-2", settlement_no: "ST-TEST-0002", status: "UNSETTLED" });

type FetchCall = { method: string; url: string; body?: unknown };
const calls: FetchCall[] = [];

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(transition: (url: string, body: unknown) => Promise<Response> | Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method || "GET";
    let body: unknown;
    if (init?.body) {
      try { body = JSON.parse(String(init.body)); } catch { body = String(init.body); }
    }
    calls.push({ method, url, body });
    if (method === "POST") return transition(url, body);
    if (url.includes("/orders?")) {
      return jsonResponse({ items: [shippedOrder, receivedOrder], page: 0, size: 100, total: 2 });
    }
    if (url.includes("/settlements?")) {
      return jsonResponse({ items: [settledSettlement, unsettledSettlement], page: 0, size: 100, total: 2 });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function client() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queryClient.setQueryData(["procurement-orders", ""], { items: [shippedOrder, receivedOrder], page: 0, size: 100, total: 2 });
  queryClient.setQueryData(["procurement-settlements"], { items: [settledSettlement, unsettledSettlement], page: 0, size: 100, total: 2 });
  return queryClient;
}

function mount(host: HTMLElement) {
  const queryClient = client();
  const root = createRoot(host);
  root.render(
    <QueryClientProvider client={queryClient}>
      <OrderCenter />
    </QueryClientProvider>,
  );
  return root;
}

function clickButton(host: HTMLElement, text: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.includes(text));
  if (!button) throw new Error(`button not found: ${text}`);
  button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  return button;
}

function setInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function pressEscape() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  calls.length = 0;
});

describe("OrderCenter operations", () => {
  it("keeps the receive dialog open with input intact when the API fails", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    mockFetch(() => jsonResponse({ message: "模拟服务器故障" }, 500));
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const card = [...host.querySelectorAll(".proc-order-card")].find((item) => item.textContent?.includes("PO-TEST-SHIP"))!;
    const openReceive = [...card.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货"))!;
    await act(async () => { openReceive.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(host.querySelector("#receive-title")).toBeTruthy();

    const dialog = host.querySelector("section:has(#receive-title)")!;
    await act(async () => {
      setInput(dialog.querySelector('input[type="number"]') as HTMLInputElement, "100");
      setInput(dialog.querySelector('input[type="date"]') as HTMLInputElement, "2026-08-20");
    });
    await act(async () => { clickButton(host, "确认收货并派生对账单"); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelector("#receive-title")).toBeTruthy();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("模拟服务器故障");
    const posts = calls.filter((item) => item.method === "POST" && item.url.includes("/transition"));
    expect(posts.length).toBe(1);
    await act(async () => root.unmount());
  });

  it("does not send a request for zero, negative or over-quantity receive input", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    mockFetch(() => jsonResponse({ message: "should not be called" }, 500));
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const card = [...host.querySelectorAll(".proc-order-card")].find((item) => item.textContent?.includes("PO-TEST-SHIP"))!;
    const openReceive = [...card.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货"))!;
    await act(async () => { openReceive.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    for (const bad of ["0", "-5", "99999"]) {
      const dialog = host.querySelector("section:has(#receive-title)")!;
      await act(async () => {
        setInput(dialog.querySelector('input[type="number"]') as HTMLInputElement, bad);
        setInput(dialog.querySelector('input[type="date"]') as HTMLInputElement, "2026-08-20");
      });
      await act(async () => { clickButton(host, "确认收货并派生对账单"); });
      expect(host.querySelector("#receive-title")).toBeTruthy();
      expect(host.querySelector('[role="alert"]')?.textContent).toContain("数量");
    }
    expect(calls.filter((item) => item.method === "POST")).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("does not throw or send a request for an invalid payment date", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    mockFetch(() => jsonResponse({ message: "should not be called" }, 500));
    const promptSpy = vi.spyOn(window, "prompt");
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const row = [...host.querySelectorAll(".proc-settlement-row")].find((item) => item.textContent?.includes("ST-TEST-0001"))!;
    const payButton = [...row.querySelectorAll("button")].find((item) => item.textContent?.includes("登记付款"))!;
    await act(async () => { payButton.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    expect(host.querySelector("#pay-title")).toBeTruthy();
    expect(promptSpy).not.toHaveBeenCalled();

    const dialog = host.querySelector("section:has(#pay-title)")!;
    await act(async () => {
      setInput(dialog.querySelector('input[type="date"]') as HTMLInputElement, "2026-13-45");
    });
    await act(async () => { clickButton(host, "确认付款"); });
    await act(async () => { await Promise.resolve(); });

    expect(host.querySelector("#pay-title")).toBeTruthy();
    const message = host.querySelector('[role="alert"]')?.textContent || "";
    expect(message).toMatch(/无效|必须填写/);
    expect(calls.filter((item) => item.method === "POST")).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("closes dialogs on Escape", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    mockFetch(() => jsonResponse({ message: "no" }, 500));
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const card = [...host.querySelectorAll(".proc-order-card")].find((item) => item.textContent?.includes("PO-TEST-SHIP"))!;
    const openReceive = [...card.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货"))!;
    await act(async () => { openReceive.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(host.querySelector("#receive-title")).toBeTruthy();
    await act(async () => { pressEscape(); });
    expect(host.querySelector("#receive-title")).toBeFalsy();
    await act(async () => root.unmount());
  });

  it("ignores a second click while a receive transition is in flight", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    let release: (value: Response) => void = () => undefined;
    const gate = new Promise<Response>((resolve) => { release = resolve; });
    mockFetch(() => gate);
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const card = [...host.querySelectorAll(".proc-order-card")].find((item) => item.textContent?.includes("PO-TEST-SHIP"))!;
    const openReceive = [...card.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货"))!;
    await act(async () => { openReceive.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const dialog = host.querySelector("section:has(#receive-title)")!;
    await act(async () => {
      setInput(dialog.querySelector('input[type="number"]') as HTMLInputElement, "100");
      setInput(dialog.querySelector('input[type="date"]') as HTMLInputElement, "2026-08-20");
    });
    const confirm = [...host.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货并派生对账单")) as HTMLButtonElement;
    await act(async () => { confirm.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    // While the first transition is still in flight the confirm button is disabled,
    // so a second click cannot fire a duplicate request.
    await act(async () => { confirm.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const confirmDisabled = confirm.disabled;
    await act(async () => { release(jsonResponse({ ...shippedOrder, status: "RECEIVED" })); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    expect(confirmDisabled).toBe(true);
    expect(calls.filter((item) => item.method === "POST" && item.url.includes("/transition"))).toHaveLength(1);
    await act(async () => root.unmount());
  });

  it("shows a success notice after a successful receive", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    mockFetch(() => jsonResponse({ ...shippedOrder, status: "RECEIVED", received_quantity: "100", arrival_date: "2026-08-20T00:00:00Z" }));
    const root = mount(host);
    await act(async () => { await Promise.resolve(); });

    const card = [...host.querySelectorAll(".proc-order-card")].find((item) => item.textContent?.includes("PO-TEST-SHIP"))!;
    const openReceive = [...card.querySelectorAll("button")].find((item) => item.textContent?.includes("确认收货"))!;
    await act(async () => { openReceive.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const dialog = host.querySelector("section:has(#receive-title)")!;
    await act(async () => {
      setInput(dialog.querySelector('input[type="number"]') as HTMLInputElement, "100");
      setInput(dialog.querySelector('input[type="date"]') as HTMLInputElement, "2026-08-20");
    });
    await act(async () => { clickButton(host, "确认收货并派生对账单"); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelector("#receive-title")).toBeFalsy();
    expect(host.querySelector('[role="status"]')?.textContent).toContain("已确认收货");
    await act(async () => root.unmount());
  });
});
