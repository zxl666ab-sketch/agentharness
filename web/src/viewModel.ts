import type { EventRow } from "./api/client";

export const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "require_human",
]);

export function statusLabel(status?: string | null): string {
  return {
    pending: "等待中",
    running: "运行中",
    waiting_approval: "等待批准",
    completed: "已完成",
    failed: "失败",
    cancelled: "已停止",
    interrupted: "已中断",
    require_human: "需要人工处理",
  }[status || ""] || "未运行";
}

export function eventLabel(event: EventRow): string {
  if (event.type === "run_started") return "开始运行";
  if (event.type === "run_status") {
    return `状态：${statusLabel(String(event.payload.status || ""))}`;
  }
  if (event.type.startsWith("run_")) {
    return statusLabel(event.type.replace("run_", ""));
  }
  if (event.type === "tool_call_start") {
    return `调用 ${String(event.payload.name || event.payload.tool || "工具")}`;
  }
  if (event.type === "tool_call_validated") return "工具参数已验证";
  if (event.type === "tool_execution_queued") return "工具已进入执行队列";
  if (event.type === "tool_execution_started") return "工具开始执行";
  if (event.type === "tool_retry") {
    return `工具重试（第 ${String(event.payload.attempt || "?")} 次）`;
  }
  if (event.type === "tool_execution_cancelled") return "工具执行已取消";
  if (event.type === "tool_execution_indeterminate") return "工具结果需要人工确认";
  if (event.type === "tool_recovery_resolved") {
    const decision = String(event.payload.decision || "");
    if (decision === "mark_succeeded") return "工具结果已确认完成";
    if (decision === "skip") return "工具调用已跳过";
    return "工具调用已批准重试";
  }
  if (event.type === "tool_call_end") return "工具调用已结束";
  if (event.type === "tool_result") {
    return `工具 ${event.payload.is_error ? "失败" : "完成"}`;
  }
  if (event.type === "approval_requested") return "等待操作批准";
  if (event.type === "approval_resolved") {
    const decision = String(event.payload.decision || "");
    if (decision === "deny") return "操作已拒绝";
    if (decision === "allow_run") return "本次运行已允许";
    return "操作已允许一次";
  }
  if (event.type === "verification_started") return "开始验证";
  if (event.type === "verification_result") {
    if (event.payload.passed === true) return "验证通过";
    if (event.payload.passed === false) return "验证未通过";
    return `验证 ${String(event.payload.action || "完成")}`;
  }
  if (event.type === "provider_retry") return "模型请求重试";
  if (event.type === "budget_warning") return "预算预警";
  if (event.type === "error") return "运行错误";
  return "运行事件";
}

export function eventTone(event: EventRow): "success" | "warning" | "danger" | "neutral" {
  const type = event.type;
  if (type === "tool_result") {
    return event.payload.is_error ? "danger" : "success";
  }
  if (type === "verification_result") {
    return event.payload.passed === false ? "danger" : "success";
  }
  if (type === "approval_resolved") {
    return event.payload.decision === "deny" ? "danger" : "success";
  }
  if (type === "run_completed") {
    return "success";
  }
  if (["approval_requested", "tool_retry", "provider_retry", "budget_warning"].includes(type)) {
    return "warning";
  }
  if (["run_failed", "run_cancelled", "run_interrupted", "tool_execution_cancelled", "tool_execution_indeterminate", "error"].includes(type)) {
    return "danger";
  }
  return "neutral";
}
