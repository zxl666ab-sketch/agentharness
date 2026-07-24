import type { RunRow } from "../api/client";

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  waiting_approval: "等待审批",
  require_human: "需要人工处理",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const STALE_RUNNING_MS = 30 * 60 * 1000;

export function isStaleRunning(run: RunRow): boolean {
  if (run.status !== "running" && run.status !== "pending") return false;
  const updated = new Date(run.updated_at || run.created_at).getTime();
  return !Number.isNaN(updated) && Date.now() - updated > STALE_RUNNING_MS;
}

export function runStatusLabel(run: RunRow): string {
  return isStaleRunning(run) ? "陈旧 / 孤儿状态" : STATUS_LABEL[run.status] || run.status;
}
