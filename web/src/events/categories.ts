/** Map technical event types → readable Chinese categories (grouped). */

export type EventGroup = "model" | "tool" | "approval" | "error" | "run" | "other";

export type EventCategory = {
  group: EventGroup;
  label: string;
  groupLabel: string;
};

const GROUP_LABEL: Record<EventGroup, string> = {
  model: "模型",
  tool: "工具",
  approval: "审批",
  error: "错误",
  run: "运行",
  other: "其他",
};

const MAP: Record<string, { group: EventGroup; label: string }> = {
  run_started: { group: "run", label: "运行开始" },
  run_status: { group: "run", label: "状态变更" },
  run_completed: { group: "run", label: "运行完成" },
  run_failed: { group: "error", label: "运行失败" },
  run_cancelled: { group: "run", label: "已取消" },
  run_interrupted: { group: "run", label: "已中断" },
  model_turn_start: { group: "model", label: "模型回合开始" },
  model_turn_end: { group: "model", label: "模型回合结束" },
  text_delta: { group: "model", label: "文本流" },
  tool_call_start: { group: "tool", label: "工具调用开始" },
  tool_call_end: { group: "tool", label: "工具调用结束" },
  tool_result: { group: "tool", label: "工具结果" },
  approval_requested: { group: "approval", label: "请求审批" },
  approval_resolved: { group: "approval", label: "审批结果" },
  checkpoint: { group: "run", label: "检查点" },
  span_start: { group: "other", label: "Span 开始" },
  span_end: { group: "other", label: "Span 结束" },
  child_run_started: { group: "run", label: "子运行开始" },
  child_run_ended: { group: "run", label: "子运行结束" },
  budget_warning: { group: "error", label: "预算警告" },
  redaction: { group: "other", label: "脱敏" },
  heartbeat: { group: "other", label: "心跳" },
  error: { group: "error", label: "错误" },
};

export function categorizeEvent(type: string): EventCategory {
  const hit = MAP[type] || { group: "other" as EventGroup, label: type };
  return {
    group: hit.group,
    label: hit.label,
    groupLabel: GROUP_LABEL[hit.group],
  };
}

export function groupEventsByCategory<T extends { type: string }>(
  events: T[]
): Record<EventGroup, T[]> {
  const out: Record<EventGroup, T[]> = {
    model: [],
    tool: [],
    approval: [],
    error: [],
    run: [],
    other: [],
  };
  for (const ev of events) {
    const c = categorizeEvent(ev.type);
    out[c.group].push(ev);
  }
  return out;
}
