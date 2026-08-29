import { describe, expect, it } from "vitest";

import { PROCUREMENT_STATUSES, type ProcurementStatus } from "./types";
import {
  CLOSED_LOOP_STEPS,
  FULFILLMENT_STEPS,
  PROCUREMENT_DECISION_STEPS,
  STATUS_LABELS,
  closedLoopProgress,
  closedLoopStep,
  fulfillmentProgress,
  fulfillmentNextStep,
  procurementDecisionProgress,
  nextStepGuide,
  statusLabel,
  statusLabelFor,
  statusTone,
} from "./viewModel";

const ALL_STATUSES: ProcurementStatus[] = [...PROCUREMENT_STATUSES, "analyzing"];

describe("procurement view model", () => {
  it("labels every status with actionable Chinese labels and never leaks raw enums", () => {
    expect(ALL_STATUSES).toHaveLength(PROCUREMENT_STATUSES.length + 1);
    for (const status of ALL_STATUSES) {
      const label = statusLabel(status);
      expect(label).not.toBe(status);
      expect(label.length).toBeGreaterThan(1);
      expect(label).not.toMatch(/^[a-z_]+$/);
    }
    expect(statusLabel("analyzed")).toContain("待审批");
    expect(statusLabel("approval_pending")).toContain("审批处理中");
    expect(statusLabel("waiting_human")).toBe("等待你补充信息");
    // 同列表曾共存「待审批/等待审批」无法区分：两个状态文案必须不同
    expect(STATUS_LABELS.analyzed).not.toBe(STATUS_LABELS.approval_pending);
    expect(STATUS_LABELS.analyzed).toContain("比价完成");
  });

  it("maps every status to a tone and a closed-loop step", () => {
    for (const status of ALL_STATUSES) {
      expect(statusTone(status).length).toBeGreaterThan(0);
      expect(closedLoopStep(status)).toBeGreaterThanOrEqual(0);
      expect(closedLoopStep(status)).toBeLessThanOrEqual(CLOSED_LOOP_STEPS.length);
    }
    expect(CLOSED_LOOP_STEPS).toEqual([
      "上传与解析",
      "字段复核",
      "供应商比价",
      "确认采购方案",
      "采购订单",
      "发货",
      "分批收货",
      "发票匹配",
      "对账",
      "付款",
    ]);
    expect(closedLoopStep("draft")).toBe(1);
    expect(PROCUREMENT_DECISION_STEPS).toHaveLength(4);
    expect(FULFILLMENT_STEPS).toHaveLength(6);
    expect(closedLoopStep("collecting")).toBe(1);
    expect(closedLoopStep("review")).toBe(2);
    expect(closedLoopStep("ready")).toBe(3);
    expect(closedLoopStep("analyzed")).toBe(4);
    expect(closedLoopStep("approved")).toBe(4);
    expect(procurementDecisionProgress("review")).toBe(2);
  });

  it("extends approved tasks through order / invoice / settlement lifecycle", () => {
    expect(closedLoopProgress("approved", null)).toBe(4);
    expect(closedLoopProgress("approved", { status: "PENDING_SHIPMENT" })).toBe(6);
    expect(closedLoopProgress("approved", { status: "SHIPPED" })).toBe(7);
    expect(closedLoopProgress("approved", { status: "PARTIALLY_RECEIVED" })).toBe(7);
    expect(closedLoopProgress("approved", { status: "RECEIVED" })).toBe(8);
    expect(closedLoopProgress("approved", { status: "RECEIVED", invoice_status: "MATCHED" })).toBe(9);
    expect(closedLoopProgress("approved", { status: "RECEIVED", invoice_status: "RECONCILED" })).toBe(9);
    expect(closedLoopProgress("approved", { status: "RECEIVED", settlement_status: "SETTLED" })).toBe(10);
    expect(closedLoopProgress("approved", { status: "RECEIVED", settlement_status: "PAID" })).toBe(10);
    expect(closedLoopProgress("approved", { status: "RECEIVED", invoice_status: "MATCHED", settlement_status: "PAID" })).toBe(10);
    expect(closedLoopProgress("approved", { status: "CLOSED" })).toBe(10);
    expect(closedLoopProgress("analyzed", { status: "SHIPPED" })).toBe(4);
    expect(fulfillmentProgress({ status: "PENDING_SHIPMENT" })).toBe(2);
    expect(fulfillmentProgress({ status: "SHIPPED" })).toBe(3);
    expect(fulfillmentProgress({ status: "RECEIVED", settlement: { status: "SETTLED" } })).toBe(6);
  });

  it("projects every fulfillment todo from authoritative order, invoice and settlement facts", () => {
    const state = (overrides: Record<string, unknown>) => fulfillmentNextStep({
      status: "RECEIVED", invoice_count: 0, invoice_status: null, settlement: null, ...overrides,
    } as Parameters<typeof fulfillmentNextStep>[0]);
    expect(state({ status: "PENDING_SHIPMENT" }).label).toBe("待发货");
    expect(state({ status: "SHIPPED" }).label).toBe("待确认收货");
    expect(state({ status: "PARTIALLY_RECEIVED" }).label).toBe("部分收货");
    expect(state({}).label).toBe("待上传发票");
    expect(state({ invoice_count: 1, invoice_status: "REGISTERED" }).label).toBe("发票匹配处理中");
    expect(state({ invoice_count: 1, invoice_status: "DIFF_HOLD" }).label).toBe("发票差异待处理");
    expect(state({ invoice_count: 1, invoice_status: "MATCHED" }).label).toBe("待核销");
    expect(state({ invoice_count: 1, invoice_status: "RECONCILED", settlement: { status: "UNSETTLED" } }).label).toBe("待对账");
    expect(state({ invoice_count: 1, invoice_status: "RECONCILED", settlement: { status: "UNSETTLED" } }).canSettle).toBe(true);
    expect(state({ invoice_count: 1, invoice_status: "RECONCILED", settlement: { status: "SETTLED" } }).label).toBe("可付款");
    expect(state({ invoice_count: 1, invoice_status: "RECONCILED", settlement: { status: "SETTLED" } }).canPay).toBe(true);
    expect(state({ invoice_count: 2, invoice_status: "MATCHED", settlement: { status: "SETTLED" } }).label).toBe("付款被拦截");
    expect(state({ invoice_count: 1, invoice_status: "RECONCILED", settlement: { status: "PAID" } }).label).toBe("已完成");
    expect(state({ status: "CLOSED" }).label).toBe("已完成");
  });

  it("guides the next action per status with a visible blocker reason", () => {
    const base = { requirement_confirmed: true, quote_count: 2, unresolved_field_count: 0 };
    expect(nextStepGuide({ ...base, status: "ready" }).action).toEqual({ kind: "analyze" });
    expect(nextStepGuide({ ...base, status: "ready" }).actionLabel).toBe("开始比价");
    expect(nextStepGuide({ ...base, status: "analyzed" }).action).toEqual({ kind: "compare" });
    // P-UX④：全部淘汰时不得再引导"审批"，必须指向需求调整
    const allExcluded = {
      ...base,
      status: "analyzed",
      comparison: { id: "snap", result: { eligible_count: 0 } },
    };
    const excludedGuide = nextStepGuide(allExcluded as unknown as Parameters<typeof nextStepGuide>[0]);
    expect(excludedGuide.action).toEqual({ kind: "quotes" });
    expect(excludedGuide.hint).toContain("无合格报价");
    expect(excludedGuide.blocker).toContain("淘汰");
    expect(statusLabelFor({ status: "analyzed" } as never)).toBe("待审批（比价完成）");
    expect(statusLabelFor(allExcluded as unknown as Parameters<typeof statusLabelFor>[0])).toBe("比价完成（无合格报价）");
    expect(nextStepGuide({ ...base, status: "approved" }).action).toEqual({ kind: "orders" });
    expect(nextStepGuide({ ...base, status: "approved" }).actionLabel).toContain("订单已生成");
    expect(nextStepGuide({ ...base, status: "approval_pending" }).action).toEqual({ kind: "reviews" });
    expect(nextStepGuide({ ...base, status: "cancelled" }).action).toEqual({ kind: "none" });

    // 卡点原因可见（不再是只有 hover 才显示的 title）
    const collecting = nextStepGuide({
      ...base,
      status: "collecting",
      quote_count: 1,
    });
    expect(collecting.blocker).toContain("至少需要 2 家报价");

    const parsing = nextStepGuide({
      ...base,
      status: "collecting",
      quote_count: 0,
      attachments: [{ artifact_id: "artifact", filename: "quote.xlsx", sha256: "a", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size_bytes: 1 }],
    });
    expect(parsing.hint).toContain("并发解析");
    expect(parsing.actionLabel).toBe("查看解析进度");

    const reviewUnconfirmed = nextStepGuide({
      status: "review",
      requirement_confirmed: false,
      quote_count: 2,
      unresolved_field_count: 3,
    });
    expect(reviewUnconfirmed.blocker).toContain("需求待人工确认");

    const reviewUnresolved = nextStepGuide({
      status: "review",
      requirement_confirmed: true,
      quote_count: 2,
      unresolved_field_count: 3,
    });
    expect(reviewUnresolved.blocker).toContain("3 项报价字段待复核");
  });
});
