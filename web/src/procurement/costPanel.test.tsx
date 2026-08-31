import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsCenter } from "./ReportsCenter";
import type { ModelCostSummary } from "./types";

const summary: ModelCostSummary = {
  cost_status: "partial",
  pricing_configured: true,
  pricing_snapshot: { hy3: { input_per_million_usd: 1, output_per_million_usd: 2, cached_input_per_million_usd: 0.5 } },
  total_cost_usd: 0.0057,
  unpriced_tokens: 1000,
  totals: { input_tokens: 3700, cached_input_tokens: 600, output_tokens: 1800, model_turns: 3, cache_hit_rate: 0.1622 },
  by_model: [
    { model: "hy3", input_tokens: 3000, cached_input_tokens: 600, output_tokens: 1500, model_turns: 2, cost_usd: 0.0057, priced: true },
    { model: "mystery", input_tokens: 700, cached_input_tokens: 0, output_tokens: 300, model_turns: 1, cost_usd: null, priced: false },
  ],
  by_task: [
    { task_id: "taskbbbb00000000000000000000000001", run_id: null, model_turns: 1, total_tokens: 3000, cost_usd: 0.004, priced: true },
    { task_id: null, run_id: "runaaaa00000000000000000000000001", model_turns: 1, total_tokens: 1000, cost_usd: null, priced: false },
  ],
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

async function mount(): Promise<HTMLElement> {
  const host = document.createElement("div");
  document.body.append(host);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ReportsCenter />
      </QueryClientProvider>,
    );
  });
  return host;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("ReportsCenter 模型成本面板", () => {
  it("已计价/未计价分层展示，未计价 token 不折算为免费", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/procurement/costs")) return jsonResponse(summary);
      if (url.includes("/insights/overview")) {
        return jsonResponse({ status_funnel: [], cost_savings: { budget_total: "0", landed_total: "0", savings: "0", rate: null, included_tasks: 0 }, counts: { tasks: 0, approved_tasks: 0, orders: 0, orders_pending_shipment: 0, orders_shipped: 0, orders_received: 0, orders_closed: 0, settlements_unsettled: 0, settlements_settled: 0, settlements_paid: 0, suppliers: 0, suppliers_blacklisted: 0, reviews_pending: 0, ai_tasks_failed: 0, overdue_orders: 0, overdue_payments: 0 } });
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const host = await mount();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });

    const html = host.innerHTML;
    expect(html).toContain("模型成本");
    expect(html).toContain("部分计价");           // 状态中文化，不直出 partial 枚举
    expect(html).toContain("$0.0057");            // 小额成本保留 4 位有效展示
    expect(html).toContain("1,000 tokens 未计价"); // 未计价 token 如实单列
    expect(html).toContain("16.2%");              // Prompt Cache 命中率
    expect(html).toContain("hy3");
    expect(html).toContain("mystery");
    expect(html).toContain("未计价");
    expect(html).toContain("按任务查看（2）");
    expect(html).not.toContain(">partial<");      // 英文枚举不得作为文案直出
  });

  it("未配置定价时展示未计价口径与配置提示", async () => {
    const unpriced: ModelCostSummary = {
      ...summary,
      cost_status: "unpriced",
      pricing_configured: false,
      pricing_snapshot: {},
      total_cost_usd: 0,
      unpriced_tokens: 5500,
      by_model: summary.by_model.map((row) => ({ ...row, priced: false, cost_usd: null })),
      by_task: summary.by_task.map((row) => ({ ...row, priced: false, cost_usd: null })),
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/procurement/costs")) return jsonResponse(unpriced);
      if (url.includes("/insights/overview")) {
        return jsonResponse({ status_funnel: [], cost_savings: { budget_total: "0", landed_total: "0", savings: "0", rate: null, included_tasks: 0 }, counts: { tasks: 0, approved_tasks: 0, orders: 0, orders_pending_shipment: 0, orders_shipped: 0, orders_received: 0, orders_closed: 0, settlements_unsettled: 0, settlements_settled: 0, settlements_paid: 0, suppliers: 0, suppliers_blacklisted: 0, reviews_pending: 0, ai_tasks_failed: 0, overdue_orders: 0, overdue_payments: 0 } });
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const host = await mount();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });

    const html = host.innerHTML;
    expect(html).toContain("未计价");
    expect(html).toContain("未配置");               // 定价配置卡片
    expect(html).toContain("来源 PROCUREMENT_MODEL_PRICING");
  });
});
