import type { ContractStatus, OrderView, ProcurementRequest, ProcurementStatus } from "./types";

/**
 * 采购任务视图模型（P1-1/P1-2/P1-3 共用）。
 * 全站状态文案 / 下一步引导 / 闭环进度只从这里取值，禁止任何组件直出英文枚举。
 */

export const STATUS_LABELS: Record<ProcurementStatus, string> = {
  draft: "需求整理中",
  waiting_human: "等待你补充信息",
  collecting: "待上传报价",
  review: "待复核",
  ready: "待比价",
  analyzing: "分析中",
  analyzed: "待审批（比价完成）",
  approval_pending: "审批处理中",
  approved: "已批准",
  no_award: "本轮未选定",
  cancelled: "已取消",
};

export const STATUS_TONES: Record<ProcurementStatus, string> = {
  draft: "info",
  waiting_human: "warning",
  collecting: "neutral",
  review: "warning",
  ready: "info",
  analyzing: "info",
  analyzed: "warning",
  approval_pending: "warning",
  approved: "success",
  no_award: "neutral",
  cancelled: "neutral",
};

/** 定标前只展示采购决策主线，避免把尚不可执行的履约环节提前暴露给用户。 */
export const PROCUREMENT_DECISION_STEPS = [
  "上传与解析",
  "字段复核",
  "供应商比价",
  "确认采购方案",
] as const;

/** 正式采购决定生成后，任务进入独立的履约阶段。 */
export const FULFILLMENT_STEPS = [
  "采购订单",
  "发货",
  "分批收货",
  "发票匹配",
  "对账",
  "付款",
] as const;

/** @deprecated 仅供兼容旧的完整闭环计算；页面不得一次展示这十步。 */
export const CLOSED_LOOP_STEPS = [...PROCUREMENT_DECISION_STEPS, ...FULFILLMENT_STEPS] as const;

export function statusLabel(status: ProcurementStatus): string {
  return STATUS_LABELS[status] ?? status;
}

/** 合同状态文案唯一来源（ContractCenter 与工作台任务详情共用，避免内联重复/兜底误标）。 */
export const CONTRACT_STATUS_LABELS: Record<ContractStatus, string> = {
  DRAFT: "草拟中",
  PENDING_APPROVAL: "待审批",
  EFFECTIVE: "已生效",
  EXECUTING: "执行中",
  CHANGE_REQUEST: "变更审批",
  CLOSED: "已关闭",
};

export function contractStatusLabel(status: ContractStatus): string {
  return CONTRACT_STATUS_LABELS[status] ?? status;
}

export function statusTone(status: ProcurementStatus): string {
  return STATUS_TONES[status] ?? "neutral";
}

/** 状态推进到的闭环步骤（已完成步骤数；0 表示终止态不推进）。 */
export function closedLoopStep(status: ProcurementStatus): number {
  switch (status) {
    case "draft":
    case "collecting":
      return 1;
    case "review":
    case "waiting_human":
      return 2;
    case "ready":
    case "analyzing":
      return 3;
    case "analyzed":
    case "approval_pending":
      return 4;
    case "approved":
      return 4;
    case "no_award":
    case "cancelled":
      return 4;
    default:
      return 1;
  }
}

/** 当前采购决策步骤（1～4）。 */
export function procurementDecisionProgress(status: ProcurementStatus): number {
  return Math.min(closedLoopStep(status), PROCUREMENT_DECISION_STEPS.length);
}

/** 当前履约步骤（1～6）；返回值表示页面应高亮的当前阶段。 */
export function fulfillmentProgress(
  order: {
    status: string;
    settlement_status?: string | null;
    settlement?: { status: string } | null;
    invoice_status?: string | null;
  } | null | undefined,
): number {
  if (!order) return 1;
  const settlementStatus = order.settlement_status ?? order.settlement?.status ?? null;
  if (order.status === "PENDING_SHIPMENT") return 2;
  if (order.status === "SHIPPED" || order.status === "PARTIALLY_RECEIVED") return 3;
  if (order.status === "CLOSED" || settlementStatus === "PAID") return 6;
  if (settlementStatus === "SETTLED") return 6;
  if (order.invoice_status === "MATCHED" || order.invoice_status === "RECONCILED") return 5;
  if (order.status === "RECEIVED") return 4;
  return 1;
}

export type FulfillmentNextStep = {
  label: string;
  detail: string;
  tone: "neutral" | "info" | "warning" | "danger" | "success";
  canSettle: boolean;
  canPay: boolean;
  canClose: boolean;
};

/** 订单、有效发票与对账单共同决定唯一的履约下一步。 */
export function fulfillmentNextStep(order: Pick<
  OrderView,
  "status" | "invoice_count" | "invoice_status" | "settlement"
>): FulfillmentNextStep {
  const settlement = order.settlement?.status ?? null;
  const result = (
    label: string,
    detail: string,
    tone: FulfillmentNextStep["tone"],
    actions: Partial<Pick<FulfillmentNextStep, "canSettle" | "canPay" | "canClose">> = {},
  ): FulfillmentNextStep => ({
    label, detail, tone, canSettle: false, canPay: false, canClose: false, ...actions,
  });
  if (order.status === "CLOSED" || settlement === "PAID") {
    return result("已完成", "付款和履约证据已完成", "success", { canClose: order.status !== "CLOSED" });
  }
  if (order.status === "PENDING_SHIPMENT") {
    return result("待发货", "请确认供应商已发货", "warning");
  }
  if (order.status === "SHIPPED") {
    return result("待确认收货", "登记本批实际到货数量与日期", "info");
  }
  if (order.status === "PARTIALLY_RECEIVED") {
    return result("部分收货", "继续登记后续到货批次", "warning");
  }
  if (order.status !== "RECEIVED") {
    return result("履约状态待确认", "请刷新后核对订单状态", "neutral");
  }
  if (settlement === "SETTLED" && order.invoice_status !== "RECONCILED") {
    return result("付款被拦截", "存在未核销的有效发票，完成处理后才能付款", "danger");
  }
  if (order.invoice_count === 0 || order.invoice_status === null) {
    return result("待上传发票", "上传供应商发票并等待三单匹配", "warning");
  }
  if (order.invoice_status === "REGISTERED") {
    return result("发票匹配处理中", "Agent 正在抽取，Java 将执行三单匹配", "info");
  }
  if (order.invoice_status === "DIFF_HOLD") {
    return result("发票差异待处理", "修正、作废或带原因强制通过差异", "danger");
  }
  if (order.invoice_status === "MATCHED") {
    return result("待核销", "三单匹配已通过，请在发票中心核销", "warning");
  }
  if (settlement === "SETTLED") {
    return result("可付款", "发票已核销且对账完成", "success", { canPay: true });
  }
  return result("待对账", "所有有效发票已核销，可以确认对账", "info", { canSettle: true });
}

/** 已批准任务可依据订单/对账/发票生命周期继续推进（6 订单 → 7 收货 → 8 发票 → 9 对账 → 10 付款）。 */
export function closedLoopProgress(
  status: ProcurementStatus,
  order: {
    status: string;
    settlement_status?: string | null;
    settlement?: { status: string } | null;
    invoice_status?: string | null;
  } | null | undefined,
): number {
  const base = procurementDecisionProgress(status);
  if (status !== "approved" || !order) return base;
  return PROCUREMENT_DECISION_STEPS.length + fulfillmentProgress(order);
}

export type NextStepAction =
  | { kind: "quotes" }
  | { kind: "compare" }
  | { kind: "orders" }
  | { kind: "reviews" }
  | { kind: "none" };

export type NextStepGuide = {
  hint: string;
  blocker: string | null;
  action: NextStepAction;
  actionLabel: string | null;
};

type GuidanceInput = Pick<
  ProcurementRequest,
  "status" | "requirement_confirmed" | "quote_count" | "unresolved_field_count"
>;

/** 状态驱动的「下一步」引导：文案与可执行动作一一对应，卡点原因可见。 */
export function nextStepGuide(request: GuidanceInput): NextStepGuide {
  const { status, requirement_confirmed, quote_count, unresolved_field_count } = request;
  switch (status) {
    case "draft":
      return {
        hint: "等待 Agent 读取需求并整理报价字段",
        blocker: null,
        action: { kind: "quotes" },
        actionLabel: "查看需求整理",
      };
    case "waiting_human":
      return {
        hint: "Agent 正在等待你的回答，回答后将从当前步骤继续",
        blocker: "需要补充会影响资格、成本或交期的关键信息",
        action: { kind: "none" },
        actionLabel: null,
      };
    case "collecting":
      return {
        hint: "上传供应商报价（至少 2 家）",
        blocker: quote_count < 2
          ? `当前 ${quote_count} 家，至少需要 2 家报价才能开始比价`
          : null,
        action: { kind: "quotes" },
        actionLabel: "上传报价",
      };
    case "review":
      return {
        hint: "确认需求并复核报价字段",
        blocker: !requirement_confirmed
          ? "需求待人工确认，请先保存需求确认"
          : unresolved_field_count > 0
            ? `还有 ${unresolved_field_count} 项报价字段待复核`
            : null,
        action: { kind: "quotes" },
        actionLabel: "去复核",
      };
    case "ready":
      return {
        hint: "报价字段已就绪，可以开始比价",
        blocker: null,
        action: { kind: "compare" },
        actionLabel: "开始比价",
      };
    case "analyzing":
      return {
        hint: "Agent 正在分析比价，请稍候",
        blocker: null,
        action: { kind: "none" },
        actionLabel: null,
      };
    case "analyzed":
      return {
        hint: "比价完成，等待人工审批",
        blocker: null,
        action: { kind: "compare" },
        actionLabel: "查看比价并审批",
      };
    case "approval_pending":
      return {
        hint: "审批处理中，可在人工审核中心跟进",
        blocker: null,
        action: { kind: "reviews" },
        actionLabel: "前往人工审核",
      };
    case "approved":
      return {
        hint: "订单已生成，进入订单环节",
        blocker: null,
        action: { kind: "orders" },
        actionLabel: "订单已生成 →",
      };
    case "no_award":
      return {
        hint: "本轮未选定供应商",
        blocker: null,
        action: { kind: "compare" },
        actionLabel: "查看比价结果",
      };
    case "cancelled":
      return {
        hint: "任务已取消",
        blocker: null,
        action: { kind: "none" },
        actionLabel: null,
      };
    default:
      return {
        hint: "请稍候",
        blocker: null,
        action: { kind: "none" },
        actionLabel: null,
      };
  }
}
