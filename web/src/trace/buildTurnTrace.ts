/** Turn/Step projection over raw event logs (readonly inspector). */

import type { EventRow, MessageRow } from "../api/client";
import { formatOutputPreview, formatToolName, summarizeArgs } from "./formatters";

export { summarizeArgs } from "./formatters";

export type TraceViewMode = "turns" | "raw";

export type TraceRowKind =
  | "run"
  | "turn"
  | "model_output"
  | "verification"
  | "tool"
  | "approval"
  | "result"
  | "child_run"
  | "error"
  | "checkpoint"
  | "span"
  | "other";

export type TraceRow = {
  id: string;
  kind: TraceRowKind;
  label: string;
  preview: string;
  timestamp: string;
  event: EventRow;
  /** Collapsed text deltas for a model turn */
  text?: string;
  step?: number | null;
  durationMs?: number | null;
  argsSummary?: string;
  resultPreview?: string;
  toolName?: string;
  isError?: boolean;
  children?: TraceRow[];
  parentId?: string | null;
  depth?: number;
  toolCallId?: string;
  targetRunId?: string;
  actor?: string;
  status?: string;
};

const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "require_human",
]);

export function isTerminalStatus(status?: string | null): boolean {
  return !!status && TERMINAL_STATUSES.has(status);
}

export function buildToolArgsIndex(messages: MessageRow[]): Map<string, string> {
  const index = new Map<string, string>();
  for (const message of messages) {
    for (const call of message.tool_calls || []) {
      if (!call?.id) continue;
      index.set(call.id, summarizeArgs(call.arguments));
    }
  }
  return index;
}

function payloadString(payload: Record<string, unknown>, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" ? value : undefined;
}

function payloadNumber(payload: Record<string, unknown>, key: string): number | undefined {
  const value = payload[key];
  return typeof value === "number" ? value : undefined;
}

/**
 * Project a flat event log into Turn/Step rows.
 *
 * Default hides checkpoint noise and collapses text_delta streams into one
 * model turn row. Tool rows join message args when event payloads lack them.
 */
export function buildTurnTrace(
  events: EventRow[],
  options?: {
    messages?: MessageRow[];
    hideCheckpoint?: boolean;
    hideSpanNoise?: boolean;
    includeRawNoise?: boolean;
  }
): TraceRow[] {
  const hideCheckpoint = options?.hideCheckpoint !== false;
  const hideSpanNoise = options?.hideSpanNoise !== false;
  const argsIndex = buildToolArgsIndex(options?.messages || []);
  const rows: TraceRow[] = [];
  const rootByRun = new Map<string, TraceRow>();
  const turnBySpan = new Map<string, TraceRow>();
  const activeTurnByRun = new Map<string, TraceRow>();
  const verificationByRun = new Map<string, TraceRow>();
  const verificationBySpan = new Map<string, TraceRow>();
  const outputByTurn = new Map<
    string,
    { start: EventRow; last: EventRow; texts: string[] }
  >();
  const toolByCall = new Map<string, TraceRow>();
  const approvalByCall = new Map<string, TraceRow>();
  const childByRun = new Map<string, TraceRow>();

  const add = (row: TraceRow): TraceRow => {
    rows.push(row);
    return row;
  };

  const rootFor = (event: EventRow): TraceRow => {
    const existing = rootByRun.get(event.run_id);
    if (existing) return existing;
    const root = add({
      id: `run:${event.run_id}`,
      kind: "run",
      label: "运行",
      preview: event.run_id,
      timestamp: event.timestamp,
      event,
      parentId: null,
      depth: 0,
      actor: event.parent_run_id ? "委派代理" : "用户",
      status: payloadString(event.payload || {}, "status") || "running",
    });
    rootByRun.set(event.run_id, root);
    return root;
  };

  const update = (row: TraceRow, values: Partial<TraceRow>) => {
    Object.assign(row, values);
  };

  const flushOutput = (turn: TraceRow, end?: EventRow) => {
    const buffer = outputByTurn.get(turn.id);
    if (!buffer) return;
    const text = buffer.texts.join("");
    update(turn, {
      preview: text.trim().slice(0, 180) || turn.preview,
      event: end || buffer.last,
      text,
    });
    add({
      id: `output:${turn.id}`,
      kind: "model_output",
      label: "模型输出",
      preview: text.trim().slice(0, 180) || "无文本输出",
      timestamp: buffer.start.timestamp,
      event: buffer.last,
      text,
      parentId: turn.id,
      depth: (turn.depth || 0) + 1,
    });
    outputByTurn.delete(turn.id);
  };

  const latestTool = (name?: string): TraceRow | undefined => {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      const row = rows[index];
      if (row.kind === "tool" && (!name || row.toolName === name)) return row;
    }
    return undefined;
  };

  for (const event of events) {
    const type = event.type;
    const payload = event.payload || {};
    const root = rootFor(event);

    if (type === "run_started") {
      update(root, {
        id: event.event_id,
        label: "运行",
        preview: payloadString(payload, "message") || event.run_id,
        event,
        actor: payloadString(payload, "actor") || root.actor,
        status: "running",
      });
      rootByRun.set(event.run_id, root);
      continue;
    }

    if (type === "text_delta") {
      let turn = event.span_id ? turnBySpan.get(event.span_id) : undefined;
      turn ||= activeTurnByRun.get(event.run_id);
      if (!turn) {
        turn = add({
          id: `turn:${event.event_id}`,
          kind: "turn",
          label: "模型轮次",
          preview: "模型轮次",
          timestamp: event.timestamp,
          event,
          parentId: root.id,
          depth: (root.depth || 0) + 1,
        });
        activeTurnByRun.set(event.run_id, turn);
      }
      const buffer = outputByTurn.get(turn.id) || {
        start: event,
        last: event,
        texts: [],
      };
      const text = payloadString(payload, "text") || "";
      if (text) buffer.texts.push(text);
      buffer.last = event;
      outputByTurn.set(turn.id, buffer);
      continue;
    }

    if (type === "model_turn_start") {
      const prior = activeTurnByRun.get(event.run_id);
      if (prior) flushOutput(prior);
      const step = payloadNumber(payload, "step") ?? null;
      const turn = add({
        id: event.event_id,
        kind: "turn",
        label: step != null ? `轮次 · 第 ${step} 步` : "模型轮次",
        preview: step != null ? `第 ${step} 步` : "模型轮次",
        timestamp: event.timestamp,
        event,
        step,
        parentId: root.id,
        depth: (root.depth || 0) + 1,
      });
      activeTurnByRun.set(event.run_id, turn);
      if (event.span_id) turnBySpan.set(event.span_id, turn);
      continue;
    }

    if (type === "model_turn_end") {
      const turn =
        (event.span_id ? turnBySpan.get(event.span_id) : undefined) ||
        activeTurnByRun.get(event.run_id);
      if (turn) {
        if (!outputByTurn.has(turn.id)) {
          outputByTurn.set(turn.id, { start: event, last: event, texts: [] });
        }
        flushOutput(turn, event);
      }
      continue;
    }

    if (type === "verification_started") {
      const turn = activeTurnByRun.get(event.run_id);
      const attempt = payloadNumber(payload, "attempt") ?? 0;
      const verification = add({
        id: event.event_id,
        kind: "verification",
        label: `验证 · 第 ${attempt + 1} 次`,
        preview: Array.isArray(payload.validators)
          ? payload.validators.map(String).join(" · ")
          : "验证候选结果",
        timestamp: event.timestamp,
        event,
        parentId: turn?.id || root.id,
        depth: (turn?.depth ?? root.depth ?? 0) + 1,
        status: "running",
      });
      verificationByRun.set(event.run_id, verification);
      if (event.span_id) verificationBySpan.set(event.span_id, verification);
      continue;
    }

    if (type === "verification_result") {
      const verification =
        (event.span_id ? verificationBySpan.get(event.span_id) : undefined) ||
        verificationByRun.get(event.run_id);
      const action = payloadString(payload, "action") || "unknown";
      const failures = Array.isArray(payload.failures) ? payload.failures : [];
      const failurePreview = failures
        .map((failure) =>
          failure && typeof failure === "object" && "message" in failure
            ? String((failure as { message?: unknown }).message || "")
            : ""
        )
        .filter(Boolean)
        .join("; ");
      if (verification) {
        update(verification, {
          event,
          status: action,
          preview: failurePreview || `决策 · ${action}`,
          isError: action !== "pass",
        });
      }
      continue;
    }

    if (type === "verification_feedback") {
      const verification =
        (event.span_id ? verificationBySpan.get(event.span_id) : undefined) ||
        verificationByRun.get(event.run_id);
      add({
        id: event.event_id,
        kind: "verification",
        label: "纠正反馈",
        preview: payloadString(payload, "feedback") || "结构化反馈已重新注入 Agent",
        timestamp: event.timestamp,
        event,
        parentId: verification?.id || root.id,
        depth: (verification?.depth ?? root.depth ?? 0) + 1,
        status: payloadString(payload, "action") || "retry",
        isError: true,
      });
      continue;
    }

    if (type === "checkpoint") {
      if (hideCheckpoint) continue;
      add({
        id: event.event_id,
        kind: "checkpoint",
        label: "检查点",
        preview: `phase=${String(payload.phase ?? "-")} · step=${String(payload.step ?? "-")}`,
        timestamp: event.timestamp,
        event,
        step: payloadNumber(payload, "step") ?? null,
        parentId: root.id,
        depth: (root.depth || 0) + 1,
      });
      continue;
    }

    if (type === "span_start" || type === "span_end") {
      if (hideSpanNoise) continue;
      const parent = event.parent_span_id
        ? turnBySpan.get(event.parent_span_id)
        : undefined;
      add({
        id: event.event_id,
        kind: "span",
        label: type === "span_start" ? "跨度开始" : "跨度结束",
        preview: `${String(payload.kind || "")} ${String(payload.name || "")}`.trim() || type,
        timestamp: event.timestamp,
        event,
        parentId: parent?.id || root.id,
        depth: (parent?.depth ?? root.depth ?? 0) + 1,
      });
      continue;
    }

    if (type === "tool_call_start") {
      const toolCallId = payloadString(payload, "tool_call_id") || event.event_id;
      const name = formatToolName(payloadString(payload, "name"));
      const argsSummary =
        payloadString(payload, "arguments_summary") ||
        argsIndex.get(toolCallId) ||
        "";
      const turn =
        (event.parent_span_id ? turnBySpan.get(event.parent_span_id) : undefined) ||
        activeTurnByRun.get(event.run_id);
      const tool = add({
        id: event.event_id,
        kind: "tool",
        label: `工具 · ${name}`,
        preview: argsSummary || name,
        timestamp: event.timestamp,
        event,
        toolName: name,
        argsSummary,
        toolCallId,
        parentId: turn?.id || root.id,
        depth: (turn?.depth ?? root.depth ?? 0) + 1,
      });
      toolByCall.set(toolCallId, tool);
      continue;
    }

    if (type === "tool_result" || type === "tool_call_end") {
      const toolCallId = payloadString(payload, "tool_call_id") || "";
      const name = formatToolName(payloadString(payload, "name"));
      const argsSummary =
        payloadString(payload, "arguments_summary") ||
        argsIndex.get(toolCallId) ||
        "";
      const resultPreview = formatOutputPreview(payloadString(payload, "content_preview") || "");
      const durationMs = payloadNumber(payload, "duration_ms") ?? null;
      const isError = Boolean(payload.is_error);
      const tool = toolByCall.get(toolCallId) || latestTool(name);
      if (type === "tool_call_end" && tool) {
        update(tool, {
          argsSummary: argsSummary || tool.argsSummary,
          preview: argsSummary || tool.preview,
          isError: isError || tool.isError,
        });
        continue;
      }
      add({
        id: event.event_id,
        kind: "result",
        label: `结果 · ${name}`,
        preview: resultPreview || (isError ? "工具执行失败" : "工具执行完成"),
        timestamp: event.timestamp,
        event,
        toolName: name,
        argsSummary,
        resultPreview,
        durationMs,
        isError,
        toolCallId,
        parentId: tool?.id || root.id,
        depth: (tool?.depth ?? root.depth ?? 0) + 1,
      });
      continue;
    }

    if (type === "run_started" || type === "run_completed" || type === "run_status") {
      update(root, {
        event,
        status:
          payloadString(payload, "status") ||
          (type === "run_completed" ? "completed" : root.status),
      });
      continue;
    }

    if (type === "run_failed" || type === "run_cancelled" || type === "run_interrupted" || type === "error") {
      update(root, { status: type.replace("run_", ""), event });
      add({
        id: event.event_id,
        kind: "error",
        label: type.replaceAll("_", " "),
        preview: payloadString(payload, "error") || payloadString(payload, "message") || type,
        timestamp: event.timestamp,
        event,
        isError: true,
        parentId: root.id,
        depth: (root.depth || 0) + 1,
      });
      continue;
    }

    if (type === "approval_requested" || type === "approval_resolved") {
      const toolCallId = payloadString(payload, "tool_call_id") || "";
      const tool = toolByCall.get(toolCallId) || latestTool(payloadString(payload, "tool"));
      const existing = toolCallId ? approvalByCall.get(toolCallId) : undefined;
      if (type === "approval_resolved" && existing) {
        update(existing, {
          event,
          preview: payloadString(payload, "decision") || existing.preview,
          status: payloadString(payload, "decision"),
        });
        continue;
      }
      const approval = add({
        id: event.event_id,
        kind: "approval",
        label: type.replaceAll("_", " "),
        preview:
          payloadString(payload, "arguments_summary") ||
          payloadString(payload, "tool") ||
          payloadString(payload, "tool_name") ||
          type,
        timestamp: event.timestamp,
        event,
        toolCallId,
        parentId: tool?.id || root.id,
        depth: (tool?.depth ?? root.depth ?? 0) + 1,
        status: payloadString(payload, "decision") || "pending",
      });
      if (toolCallId) approvalByCall.set(toolCallId, approval);
      continue;
    }

    if (type === "child_run_started" || type === "child_run_ended") {
      const childRunId = payloadString(payload, "child_run_id") || "unknown-child";
      const toolCallId =
        payloadString(payload, "parent_tool_call_id") ||
        payloadString(payload, "tool_call_id") ||
        "";
      const tool = toolByCall.get(toolCallId) || latestTool("delegate");
      const existing = childByRun.get(childRunId);
      if (existing) {
        update(existing, {
          event,
          actor: payloadString(payload, "actor") || existing.actor,
          status: payloadString(payload, "status") || existing.status,
          preview: `${payloadString(payload, "status") || existing.status || "running"} · ${childRunId}`,
        });
        continue;
      }
      const child = add({
        id: event.event_id,
        kind: "child_run",
        label: "子运行",
        preview: `${payloadString(payload, "status") || "running"} · ${childRunId}`,
        timestamp: event.timestamp,
        event,
        parentId: tool?.id || root.id,
        depth: (tool?.depth ?? root.depth ?? 0) + 1,
        toolCallId,
        targetRunId: childRunId,
        actor: payloadString(payload, "actor") || "委派代理",
        status: payloadString(payload, "status") || "running",
      });
      childByRun.set(childRunId, child);
      continue;
    }

    if (type === "heartbeat") continue;

    add({
      id: event.event_id,
      kind: "other",
      label: type,
      preview: JSON.stringify(payload).slice(0, 180),
      timestamp: event.timestamp,
      event,
      parentId: root.id,
      depth: (root.depth || 0) + 1,
    });
  }
  for (const turn of activeTurnByRun.values()) flushOutput(turn);
  return rows;
}

/** Prefer user intent for run list cards. */
export function runListSummary(input: {
  userMessage?: string | null;
  outputSummary?: string | null;
  error?: string | null;
  provider?: string | null;
  maxLen?: number;
}): string {
  const maxLen = input.maxLen ?? 120;
  const user = (input.userMessage || "").trim();
  if (user) return user.length <= maxLen ? user : `${user.slice(0, maxLen - 1)}…`;
  const out = (input.outputSummary || "").trim();
  if (out) return out.length <= maxLen ? out : `${out.slice(0, maxLen - 1)}…`;
  if (input.error) return String(input.error).slice(0, maxLen);
  return `${input.provider || "未知"} provider`;
}

export function extractUserMessageFromEvents(events: EventRow[]): string | null {
  const started = events.find((event) => event.type === "run_started");
  const message = started?.payload?.message;
  return typeof message === "string" && message.trim() ? message.trim() : null;
}
