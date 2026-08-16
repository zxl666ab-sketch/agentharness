export const WORKBENCH_VIEWS = [
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
] as const;
export type WorkbenchView = (typeof WORKBENCH_VIEWS)[number];

export const TASK_TABS = ["quotes", "compare", "report", "audit"] as const;
export type TaskTab = (typeof TASK_TABS)[number];

export const TASK_FILTERS = ["all", "attention", "active", "completed"] as const;
export type TaskFilter = (typeof TASK_FILTERS)[number];

export type WorkbenchUrlState = {
  view: WorkbenchView;
  task: string | null;
  ai: string | null;
  review: string | null;
  tab: TaskTab;
  status: TaskFilter;
  q: string;
  page: number;
  /** 订单视图聚焦的采购任务（P1-3 闭环衔接入口） */
  orderTask: string | null;
};

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value && allowed.includes(value as T) ? value as T : fallback;
}

export function readWorkbenchUrl(search?: string): WorkbenchUrlState {
  const raw = search ?? (typeof window === "undefined" ? "" : window.location.search);
  const query = new URLSearchParams(raw);
  const task = query.get("task");
  const ai = query.get("ai");
  const review = query.get("review");
  return {
    view: oneOf(
      query.get("view"),
      WORKBENCH_VIEWS,
      review ? "reviews" : ai ? "ai" : task ? "tasks" : "workbench",
    ),
    task: task?.trim() || null,
    ai: ai?.trim() || null,
    review: review?.trim() || null,
    tab: oneOf(query.get("tab"), TASK_TABS, "quotes"),
    status: oneOf(query.get("status"), TASK_FILTERS, "all"),
    q: query.get("q") || "",
    page: Math.max(0, Number.parseInt(query.get("page") || "0", 10) || 0),
    orderTask: query.get("order_task")?.trim() || null,
  };
}

export function workbenchSearch(state: WorkbenchUrlState) {
  const query = new URLSearchParams();
  if (state.view !== "workbench") query.set("view", state.view);
  if (state.task) query.set("task", state.task);
  if (state.ai) query.set("ai", state.ai);
  if (state.review) query.set("review", state.review);
  if (state.tab !== "quotes") query.set("tab", state.tab);
  if (state.status !== "all") query.set("status", state.status);
  if (state.q.trim()) query.set("q", state.q.trim());
  if (state.page > 0) query.set("page", String(state.page));
  if (state.orderTask) query.set("order_task", state.orderTask);
  const value = query.toString();
  return value ? `?${value}` : "";
}

export function writeWorkbenchUrl(state: WorkbenchUrlState, push = false) {
  if (typeof window === "undefined") return;
  const next = `${window.location.pathname}${workbenchSearch(state)}${window.location.hash}`;
  window.history[push ? "pushState" : "replaceState"]({}, "", next);
}
