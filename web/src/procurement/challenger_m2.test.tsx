import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkbenchHome } from "./WorkbenchHome";
import { WorkbenchNavigation } from "./WorkbenchNavigation";
import { ProcurementWorkbench } from "./ProcurementWorkbench";
import { OrderCenter } from "./OrderCenter";
import { SupplierCenter } from "./SupplierCenter";
import { ReportsCenter } from "./ReportsCenter";
import { AuditLogCenter } from "./AuditLogCenter";
import { ROLES, type DemoRole, isViewVisible, visibleViewOrDefault, visibleViews } from "./roles";
import type { OrderView, ProcurementRequestSummary, SupplierView } from "./types";

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
    },
  });
}

/** jsdom 无 EventSource：工作台挂载必需的最小假实现。 */
class FakeEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(_url: string) {}
  addEventListener() {}
  close() {}
}

async function mountWorkbench(props: Partial<{ theme: "light" | "dark"; onToggleTheme: () => void }> = {}) {
  vi.stubGlobal("EventSource", FakeEventSource);
  const client = createTestQueryClient();
  client.setQueryData(["procurement-requests"], []);
  client.setQueryData(["procurement-ai-tasks"], { items: [], page: 0, size: 100, total: 0 });
  client.setQueryData(["procurement-reviews"], { items: [], page: 0, size: 100, total: 0 });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ProcurementWorkbench
          theme={props.theme ?? "light"}
          backendVersion="0.5.0-test"
          onToggleTheme={props.onToggleTheme ?? (() => undefined)}
        />
      </QueryClientProvider>,
    );
  });
  return { host, unmount: async () => { await act(async () => root.unmount()); host.remove(); } };
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("CHALLENGER 1: Visual Layout & Empty / Extreme Data Resilience", () => {
  it("renders WorkbenchHome cleanly with zero requests, zero AI tasks, and zero reviews", () => {
    const queryClient = createTestQueryClient();
    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <WorkbenchHome
          role="buyer"
          requests={[]}
          aiTasks={[]}
          reviews={[]}
          loading={false}
          onOpenCreate={vi.fn()}
          onOpenTask={vi.fn()}
          onOpenTasks={vi.fn()}
          onOpenView={vi.fn()}
          onOpenOrders={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(html).toContain("proc-home");
    expect(html).toContain("proc-cockpit-stats");
    expect(html).toContain("proc-todo-quick-strip");
    // 零待办不再渲染空态卡（三处零值文案收敛为待办条一处）；加载态除外
    expect(html).not.toContain("当前没有待办任务，所有流程均已推进");
    expect(html).toContain("当前没有需要你立即处理的待办入口");
    expect(html).toContain("尚无采购任务");
  });

  it("renders the todo section while loading and with pending attention items", () => {
    const withAttention = renderToString(
      <QueryClientProvider client={createTestQueryClient()}>
        <WorkbenchHome
          role="buyer"
          requests={[{
            id: "req-1",
            reference: "RFQ-20260830-01",
            title: "等待补充信息的任务",
            category: "ecommerce_packaging",
            item_name: "热敏标签",
            quantity: 100,
            unit: "piece",
            quote_count: 2,
            status: "waiting_human",
            updated_at: "2026-08-30T00:00:00Z",
          } as ProcurementRequestSummary]}
          aiTasks={[]}
          reviews={[]}
          loading={false}
          onOpenCreate={vi.fn()}
          onOpenTask={vi.fn()}
          onOpenTasks={vi.fn()}
          onOpenView={vi.fn()}
          onOpenOrders={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(withAttention).toContain("待办任务");
    // React 会在插值处插入注释节点，断言语义而非全字面
    expect(withAttention).toContain("项待处理");
    expect(withAttention).toContain("等待你补充信息");
  });

  it("renders WorkbenchHome with extreme data (long strings, high numbers, special chars)", () => {
    const extremeRequests: ProcurementRequestSummary[] = Array.from({ length: 50 }, (_, i) => ({
      id: `req-${i}`,
      reference: `RFQ-EXTREME-2026-${"9".repeat(10)}-${i}`,
      title: `超长物料需求标题 ${"测试物料".repeat(20)} #${i}`,
      category: "special-category-with-special-chars-@#$%",
      item_name: `物料规格 ${"A".repeat(50)}`,
      quantity: 999999999999,
      unit: "piece-extra-long-unit-name",
      specifications: {
        detail: { label: "规格", value: "超长规格说明".repeat(10), unit: "mm", type: "text", match: "exact", priority: "hard" },
      },
      constraints: { max_lead_days: 9999 },
      status: i % 2 === 0 ? "waiting_human" : "approved",
      requirement_confirmed: true,
      session_id: `sess-${i}`,
      quote_count: 99,
      unresolved_field_count: 5,
      created_at: new Date(Date.now() - i * 86400000).toISOString(),
      updated_at: new Date(Date.now() - i * 3600000).toISOString(),
    }));

    const queryClient = createTestQueryClient();
    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <WorkbenchHome
          role="buyer"
          requests={extremeRequests}
          aiTasks={[]}
          reviews={[]}
          loading={false}
          onOpenCreate={vi.fn()}
          onOpenTask={vi.fn()}
          onOpenTasks={vi.fn()}
          onOpenView={vi.fn()}
          onOpenOrders={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(html).toContain("50");
    expect(html).toContain("proc-pro-table");
    expect(html).toContain("proc-task-row-btn");
  });

  it("renders ReportsCenter with complete empty dataset without runtime errors", () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["procurement-insights-overview"], {
      status_funnel: [],
      cost_savings: { budget_total: "0", landed_total: "0", savings: "0", rate: 0 },
      counts: { tasks: 0, approved_tasks: 0, orders: 0, suppliers: 0 },
    });
    queryClient.setQueryData(["procurement-insights-trend"], []);
    queryClient.setQueryData(["procurement-insights-ranking"], []);
    queryClient.setQueryData(["procurement-insights-categories"], []);
    queryClient.setQueryData(["procurement-evaluation"], {
      metrics: {
        field_extraction: { accuracy: 0.98 },
        post_review_fields: { accuracy: 0.99 },
        item_matching: { accuracy: 0.95 },
        cost_calculation: { accuracy: 1.0 },
        hard_constraint_miss: { miss_rate: 0.01 },
      },
    });

    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <ReportsCenter />
      </QueryClientProvider>,
    );

    expect(html).toContain("统计报表");
    expect(html).toContain("成本节约率");
    expect(html).toContain("0.00%");
    expect(html).toContain("AI 评测指标");
  });

  it("renders AuditLogCenter with zero events without runtime errors", () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["procurement-audit-events", "", "", "", "", 0], {
      items: [],
      total: 0,
      page: 0,
      size: 50,
    });

    const html = renderToString(
      <QueryClientProvider client={queryClient}>
        <AuditLogCenter />
      </QueryClientProvider>,
    );

    expect(html).toContain("审计日志");
    expect(html).toContain("没有匹配的审计事件");
  });
});

describe("CHALLENGER 2: Theme Switching & Header Interaction", () => {
  it("toggles theme correctly from the workbench top bar", async () => {
    const toggleMock = vi.fn();
    const { host, unmount } = await mountWorkbench({ onToggleTheme: toggleMock });

    const themeButton = host.querySelector('button[aria-label="切换主题"]') as HTMLButtonElement;
    expect(themeButton).toBeTruthy();
    expect(themeButton.title).toBe("切换主题");

    await act(async () => {
      themeButton.click();
    });
    expect(toggleMock).toHaveBeenCalledTimes(1);

    await unmount();
  });
});

describe("CHALLENGER 3: Role Filtering & State Transition Matrix", () => {
  const allRoles: DemoRole[] = [...ROLES];
  const allViews = ["workbench", "tasks", "orders", "invoices", "suppliers", "contracts", "reports", "ai", "reviews", "audit", "system"] as const;

  it("verifies view visibility consistency for all roles", () => {
    for (const role of allRoles) {
      const views = visibleViews(role);
      expect(views.length).toBeGreaterThan(0);
      expect(views).toContain("workbench"); // All roles can see workbench

      for (const view of allViews) {
        const isVisible = isViewVisible(role, view);
        const fallback = visibleViewOrDefault(role, view);
        if (isVisible) {
          expect(fallback).toBe(view);
        } else {
          expect(views).toContain(fallback);
        }
      }
    }
  });

  it("WorkbenchNavigation renders only role-authorized navigation entries", () => {
    for (const role of allRoles) {
      const queryClient = createTestQueryClient();
      const views = new Set(visibleViews(role));
      const html = renderToString(
        <QueryClientProvider client={queryClient}>
          <WorkbenchNavigation
            active="workbench"
            role={role}
            aiAttention={1}
            reviewAttention={2}
            onChange={vi.fn()}
          />
        </QueryClientProvider>,
      );

      expect(html).toContain("工作台");
      if (!views.has("orders")) {
        expect(html).not.toContain("采购订单");
      }
      if (!views.has("invoices")) {
        expect(html).not.toContain("发票中心");
      }
      if (!views.has("contracts")) {
        expect(html).not.toContain("合同中心");
      }
      if (!views.has("audit")) {
        expect(html).not.toContain("审计日志");
      }
    }
  });

  it("role switcher in the workbench top bar persists the demo role", async () => {
    localStorage.removeItem("procurement.demo-role");
    const { host, unmount } = await mountWorkbench();

    const select = host.querySelector('select[aria-label="演示角色"]') as HTMLSelectElement;
    expect(select).toBeTruthy();
    expect(select.value).toBe("buyer");

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")!.set!;
      setter.call(select, "approver");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(localStorage.getItem("procurement.demo-role")).toBe("approver");
    await unmount();
    localStorage.removeItem("procurement.demo-role");
  });
});

describe("CHALLENGER 4: DOM Selector Stability & Semantic Interactive Invariants", () => {
  it("verifies SupplierCenter slide-over drawer ID, form modal ID, and delete modal ID", async () => {
    const sampleSupplier: SupplierView = {
      id: "sup-1",
      name: "常州标签实业有限公司",
      contact_person: "张经理",
      phone: "13800000000",
      email: "zhang@example.com",
      address: "江苏省常州市",
      main_categories: "热敏标签, 铜版纸标签",
      status: "ACTIVE",
      cooperation_status: "合作中",
      quote_count: 5,
      win_count: 3,
      win_rate: "0.6",
      performance: {
        score: "88.5",
        level: "良好",
        win_rate_score: "60",
        activity_score: "80",
        status_score: "100",
        base_score: "75",
      },
      notes: "老牌供应商",
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
    };

    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["procurement-suppliers", "", "", 0], {
      items: [sampleSupplier],
      total: 1,
      page: 0,
      size: 50,
    });
    queryClient.setQueryData(["procurement-supplier-profile", sampleSupplier.id], {
      id: sampleSupplier.id,
      name: sampleSupplier.name,
      contact_person: sampleSupplier.contact_person,
      phone: sampleSupplier.phone,
      email: sampleSupplier.email,
      address: sampleSupplier.address,
      main_categories: sampleSupplier.main_categories,
      status: sampleSupplier.status,
      cooperation_status: sampleSupplier.cooperation_status,
      quote_count: 5,
      win_count: 3,
      win_rate: "0.6",
      performance: {
        score: "88.5",
        level: "良好",
        win_rate_score: "60",
        activity_score: "80",
        status_score: "100",
        base_score: "75",
      },
      items: ["热敏标签"],
      recent_quotes: [],
      notes: sampleSupplier.notes,
    });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <SupplierCenter onOpenTask={vi.fn()} />
        </QueryClientProvider>,
      );
    });

    expect(host.textContent).toContain("常州标签实业有限公司");

    // 1. Open drawer profile
    const profileBtn = host.querySelector(`button[aria-label="查看供应商档案 ${sampleSupplier.name}"]`) as HTMLButtonElement;
    expect(profileBtn).toBeTruthy();
    await act(async () => {
      profileBtn.click();
    });
    expect(host.querySelector("#supplier-profile-title")).toBeTruthy();
    expect(host.querySelector("#supplier-profile-title")?.textContent).toContain("常州标签实业有限公司");

    // Close drawer
    const closeDrawerBtn = host.querySelector('button[aria-label="关闭档案"]') as HTMLButtonElement;
    await act(async () => {
      closeDrawerBtn.click();
    });
    expect(host.querySelector("#supplier-profile-title")).toBeNull();

    // 2. Open edit modal
    const editBtn = host.querySelector(`button[aria-label="编辑供应商 ${sampleSupplier.name}"]`) as HTMLButtonElement;
    expect(editBtn).toBeTruthy();
    await act(async () => {
      editBtn.click();
    });
    expect(host.querySelector("#supplier-form-title")).toBeTruthy();
    expect(host.querySelector("#supplier-form-title")?.textContent).toContain("编辑供应商档案");

    // Close edit modal
    const cancelFormBtn = [...host.querySelectorAll("button")].find((btn) => btn.textContent?.includes("取消")) as HTMLButtonElement;
    await act(async () => {
      cancelFormBtn.click();
    });
    expect(host.querySelector("#supplier-form-title")).toBeNull();

    // 3. Open delete modal
    const deleteBtn = host.querySelector(`button[aria-label="删除供应商 ${sampleSupplier.name}"]`) as HTMLButtonElement;
    expect(deleteBtn).toBeTruthy();
    await act(async () => {
      deleteBtn.click();
    });
    expect(host.querySelector("#delete-supplier-title")).toBeTruthy();
    expect(host.querySelector("#delete-supplier-title")?.textContent).toContain("删除供应商");

    await act(async () => root.unmount());
  });

  it("verifies Escape key dismisses modals in OrderCenter", async () => {
    const sampleOrder: OrderView = {
      id: "order-esc",
      task_id: "task-esc",
      order_no: "PO-ESC-001",
      supplier_name: "测试供应商",
      item_name: "测试物料",
      quantity: "500",
      unit: "件",
      landed_total: "2500.00",
      status: "SHIPPED",
      received_quantity: null,
      arrival_date: null,
      notes: null,
      version: 1,
      task_reference: "RFQ-ESC-001",
      task_title: "测试任务",
      artifacts: [],
      invoice_count: 0,
      invoice_status: null,
      settlement: null,
      created_at: "2026-08-15T00:00:00Z",
      updated_at: "2026-08-15T00:00:00Z",
    };

    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["procurement-orders", ""], {
      items: [sampleOrder],
      total: 1,
      page: 0,
      size: 100,
    });
    queryClient.setQueryData(["procurement-settlements"], { items: [], total: 0, page: 0, size: 100 });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <OrderCenter />
        </QueryClientProvider>,
      );
    });

    // Open receive dialog
    const receiveBtn = [...host.querySelectorAll("button")].find((btn) => btn.textContent?.includes("确认收货")) as HTMLButtonElement;
    await act(async () => {
      receiveBtn.click();
    });
    expect(host.querySelector("#receive-title")).toBeTruthy();

    // Press Escape
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(host.querySelector("#receive-title")).toBeNull();

    await act(async () => root.unmount());
  });
});
