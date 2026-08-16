import type { WorkbenchView } from "./workbenchUrl";

/** 演示角色（K9）：不做登录鉴权，纯前端视角控制，localStorage 持久化。 */
export const ROLES = ["buyer", "approver", "admin"] as const;
export type DemoRole = (typeof ROLES)[number];

export const ROLE_LABELS: Record<DemoRole, string> = {
  buyer: "采购员",
  approver: "审批人",
  admin: "管理员",
};

const STORAGE_KEY = "procurement.demo-role";

export function readRole(): DemoRole {
  const value = localStorage.getItem(STORAGE_KEY);
  return ROLES.includes(value as DemoRole) ? (value as DemoRole) : "buyer";
}

export function writeRole(role: DemoRole) {
  localStorage.setItem(STORAGE_KEY, role);
}

/**
 * 角色决定侧边栏可见项（冻结设计 4.7）：
 * 采购员：工作台/采购任务/AI 任务/人工审核/供应商/订单/报表
 * 审批人：待审批优先（人工审核/AI 任务/订单收货确认）
 * 管理员：全部 + 系统管理（审计日志/系统信息）
 */
const VISIBLE_VIEWS: Record<DemoRole, WorkbenchView[]> = {
  buyer: ["workbench", "tasks", "ai", "reviews", "suppliers", "orders", "invoices", "contracts", "reports"],
  approver: ["workbench", "ai", "reviews"],
  admin: [
    "workbench",
    "tasks",
    "ai",
    "reviews",
    "suppliers",
    "orders",
    "invoices",
    "contracts",
    "reports",
    "audit",
    "system",
  ],
};

export function visibleViews(role: DemoRole): WorkbenchView[] {
  return VISIBLE_VIEWS[role];
}

export function isViewVisible(role: DemoRole, view: WorkbenchView): boolean {
  return VISIBLE_VIEWS[role].includes(view);
}

export function visibleViewOrDefault(role: DemoRole, view: WorkbenchView): WorkbenchView {
  return isViewVisible(role, view) ? view : "workbench";
}
