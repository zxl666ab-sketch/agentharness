import type { ContractStatus, ProcurementRequest, ProcurementStatus } from "./types";

/**
 * 采购任务视图模型（P1-1/P1-2/P1-3 共用）。
 * 全站状态文案 / 下一步引导 / 闭环进度只从这里取值，禁止任何组件直出英文枚举。
 */

export const STATUS_LABELS: Record<ProcurementStatus, string> = {
  draft: "需求整理中",
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

/** 完整业务闭环：创建需求 → 报价 → 复核 → 比价 → 审批 → 订单 → 收货 → 发票 → 对账 → 付款 */
export const CLOSED_LOOP_STEPS = [
  "创建需求",
  "上传报价",
  "字段复核",
  "供应商比价",
  "人工审批",
  "采购订单",
  "收货确认",
  "发票匹配",
  "对账",
  "付款",
] as const;

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
      return 1;
    case "collecting":
      return 2;
    case "review":
      return 3;
    case "ready":
    case "analyzing":
      return 4;
    case "analyzed":
    case "approval_pending":
      return 5;
    case "approved":
      return 6;
    case "no_award":
    case "cancelled":
      return 5;
    default:
      return 1;
  }
}

/** 已批准任务可依据订单/对账/发票生命周期继续推进（6 订单 → 7 收货 → 8 发票 → 9 对账 → 10 付款）。 */
export function closedLoopProgress(
  status: ProcurementStatus,
  order: { status: string; settlement_status?: string | null; invoice_status?: string | null } | null | undefined,
): number {
  const base = closedLoopStep(status);
  if (status !== "approved" || !order) return base;
  switch (order.status) {
    case "SHIPPED":
      return 7;
    case "RECEIVED":
      if (order.invoice_status === "RECONCILED" || order.invoice_status === "MATCHED") {
        return order.settlement_status === "PAID" ? 10 : 9;
      }
      if (order.settlement_status === "PAID") return 10;
      if (order.settlement_status === "SETTLED") return 9;
      return 8;
    case "CLOSED":
      return 10;
    default:
      return 6;
  }
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
