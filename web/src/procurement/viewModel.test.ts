import { describe, expect, it } from "vitest";

import { PROCUREMENT_STATUSES, type ProcurementStatus } from "./types";
import {
  CLOSED_LOOP_STEPS,
  STATUS_LABELS,
  closedLoopProgress,
  closedLoopStep,
  nextStepGuide,
  statusLabel,
  statusTone,
} from "./viewModel";

const ALL_STATUSES: ProcurementStatus[] = [...PROCUREMENT_STATUSES, "analyzing"];

describe("procurement view model", () => {
  it("labels all 10 statuses with actionable Chinese labels and never leaks raw enums", () => {
    expect(ALL_STATUSES).toHaveLength(10);
    for (const status of ALL_STATUSES) {
      const label = statusLabel(status);
      expect(label).not.toBe(status);
      expect(label.length).toBeGreaterThan(1);
      expect(label).not.toMatch(/^[a-z_]+$/);
    }
    expect(statusLabel("analyzed")).toContain("待审批");
    expect(statusLabel("approval_pending")).toContain("审批处理中");
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
      "创建需求",
      "上传报价",
      "字段复核",
      "供应商比价",
      "人工审批",
      "采购订单",
      "收货确认",
      "对账",
      "付款",
    ]);
    expect(closedLoopStep("draft")).toBe(1);
    expect(closedLoopStep("collecting")).toBe(2);
    expect(closedLoopStep("review")).toBe(3);
    expect(closedLoopStep("ready")).toBe(4);
    expect(closedLoopStep("analyzed")).toBe(5);
    expect(closedLoopStep("approved")).toBe(6);
  });

  it("extends approved tasks through order / settlement lifecycle", () => {
    expect(closedLoopProgress("approved", null)).toBe(6);
    expect(closedLoopProgress("approved", { status: "PENDING_SHIPMENT" })).toBe(6);
    expect(closedLoopProgress("approved", { status: "SHIPPED" })).toBe(7);
    expect(closedLoopProgress("approved", { status: "RECEIVED" })).toBe(8);
    expect(closedLoopProgress("approved", { status: "RECEIVED", settlement_status: "SETTLED" })).toBe(8);
    expect(closedLoopProgress("approved", { status: "RECEIVED", settlement_status: "PAID" })).toBe(9);
    expect(closedLoopProgress("approved", { status: "CLOSED" })).toBe(9);
    expect(closedLoopProgress("analyzed", { status: "SHIPPED" })).toBe(5);
  });

  it("guides the next action per status with a visible blocker reason", () => {
    const base = { requirement_confirmed: true, quote_count: 2, unresolved_field_count: 0 };
    expect(nextStepGuide({ ...base, status: "ready" }).action).toEqual({ kind: "compare" });
    expect(nextStepGuide({ ...base, status: "ready" }).actionLabel).toBe("开始比价");
    expect(nextStepGuide({ ...base, status: "analyzed" }).action).toEqual({ kind: "compare" });
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
