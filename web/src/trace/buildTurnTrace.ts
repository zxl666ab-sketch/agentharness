/** Turn/Step projection over raw event logs (readonly inspector). */

import type { EventRow, MessageRow } from "../api/client";

export type TraceViewMode = "turns" | "raw";

export type TraceRowKind =
  | "run"
  | "turn"
  | "tool"
  | "approval"
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
};

const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
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

export function summarizeArgs(argumentsValue: unknown, maxLen = 160): string {
  if (argumentsValue == null) return "";
  if (typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    const text = String(argumentsValue);
    return text.length <= maxLen ? text : `${text.slice(0, maxLen - 1)}…`;
  }
  const args = argumentsValue as Record<string, unknown>;
  const preferred = [
    "action",
    "url",
    "path",
    "command",
    "query",
    "method",
    "selector",
    "name",
    "skill",
    "memory",
    "context_id",
  ];
  const secretKeys = new Set(["api_key", "token", "password", "authorization", "secret", "key"]);
  const parts: string[] = [];
  for (const key of preferred) {
    if (args[key] != null && args[key] !== "") {
      let val = args[key];
      if (typeof val === "string" && val.length > 80) val = `${val.slice(0, 79)}…`;
      parts.push(`${key}=${String(val)}`);
    }
  }
  if (!parts.length) {
    for (const [key, val] of Object.entries(args).slice(0, 4)) {
      if (secretKeys.has(key.toLowerCase()) || key.toLowerCase().includes("token")) {
        parts.push(`${key}=[REDACTED]`);
      } else {
        let rendered = val;
        if (typeof rendered === "string" && rendered.length > 60) {
          rendered = `${rendered.slice(0, 59)}…`;
        }
        parts.push(`${key}=${String(rendered)}`);
      }
    }
  }
  const text = parts.join(" ");
  return text.length <= maxLen ? text : `${text.slice(0, maxLen - 1)}…`;
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

  // Pending model turn accumulator
  let turnBuffer: {
    start: EventRow;
    texts: string[];
    step: number | null;
    last: EventRow;
  } | null = null;

  const flushTurn = () => {
    if (!turnBuffer) return;
    const text = turnBuffer.texts.join("");
    const preview =
      text.trim().slice(0, 180) ||
      (turnBuffer.step != null ? `step ${turnBuffer.step}` : "model turn");
    rows.push({
      id: turnBuffer.start.event_id,
      kind: "turn",
      label: turnBuffer.step != null ? `Turn · step ${turnBuffer.step}` : "Model turn",
      preview,
      timestamp: turnBuffer.start.timestamp,
      event: turnBuffer.last,
      text,
      step: turnBuffer.step,
    });
    turnBuffer = null;
  };

  // Tool open map for duration pairing
  const toolStarts = new Map<string, EventRow>();

  for (const event of events) {
    const type = event.type;
    const payload = event.payload || {};

    if (type === "text_delta") {
      if (!turnBuffer) {
        turnBuffer = {
          start: event,
          texts: [],
          step: null,
          last: event,
        };
      }
      const text = payloadString(payload, "text") || "";
      if (text) turnBuffer.texts.push(text);
      turnBuffer.last = event;
      continue;
    }

    if (type === "model_turn_start") {
      flushTurn();
      turnBuffer = {
        start: event,
        texts: [],
        step: payloadNumber(payload, "step") ?? null,
        last: event,
      };
      continue;
    }

    if (type === "model_turn_end") {
      if (turnBuffer) {
        turnBuffer.last = event;
        if (turnBuffer.step == null && payloadNumber(payload, "step") != null) {
          turnBuffer.step = payloadNumber(payload, "step") ?? null;
        }
      }
      flushTurn();
      continue;
    }

    // Non-text events break an open text-only buffer without model_turn_start.
    if (turnBuffer && type !== "text_delta") {
      // Keep buffer across span noise only when mid-stream deltas.
      if (!(type === "span_start" || type === "span_end" || type === "checkpoint")) {
        flushTurn();
      }
    }

    if (type === "checkpoint") {
      if (hideCheckpoint) continue;
      rows.push({
        id: event.event_id,
        kind: "checkpoint",
        label: "Checkpoint",
        preview: `phase=${String(payload.phase ?? "-")} · step=${String(payload.step ?? "-")}`,
        timestamp: event.timestamp,
        event,
        step: payloadNumber(payload, "step") ?? null,
      });
      continue;
    }

    if (type === "span_start" || type === "span_end") {
      if (hideSpanNoise) continue;
      rows.push({
        id: event.event_id,
        kind: "span",
        label: type === "span_start" ? "Span start" : "Span end",
        preview: `${String(payload.kind || "")} ${String(payload.name || "")}`.trim() || type,
        timestamp: event.timestamp,
        event,
      });
      continue;
    }

    if (type === "tool_call_start") {
      flushTurn();
      const toolCallId = payloadString(payload, "tool_call_id") || event.event_id;
      toolStarts.set(toolCallId, event);
      const name = payloadString(payload, "name") || "tool";
      const argsSummary =
        payloadString(payload, "arguments_summary") ||
        argsIndex.get(toolCallId) ||
        "";
      rows.push({
        id: event.event_id,
        kind: "tool",
        label: `Tool · ${name}`,
        preview: argsSummary || name,
        timestamp: event.timestamp,
        event,
        toolName: name,
        argsSummary,
      });
      continue;
    }

    if (type === "tool_result" || type === "tool_call_end") {
      flushTurn();
      const toolCallId = payloadString(payload, "tool_call_id") || "";
      const name = payloadString(payload, "name") || "tool";
      const argsSummary =
        payloadString(payload, "arguments_summary") ||
        argsIndex.get(toolCallId) ||
        "";
      const resultPreview = payloadString(payload, "content_preview") || "";
      const durationMs = payloadNumber(payload, "duration_ms") ?? null;
      const isError = Boolean(payload.is_error);
      // Prefer a single tool row: update last matching tool start preview if present.
      const existingIdx = [...rows]
        .map((row, index) => ({ row, index }))
        .reverse()
        .find(
          ({ row }) =>
            row.kind === "tool" &&
            (row.event.payload?.tool_call_id === toolCallId ||
              (toolCallId && row.argsSummary === argsSummary && row.toolName === name))
        )?.index;
      if (existingIdx != null && type === "tool_result") {
        const existing = rows[existingIdx];
        rows[existingIdx] = {
          ...existing,
          preview:
            [argsSummary || existing.argsSummary, resultPreview].filter(Boolean).join(" → ") ||
            name,
          resultPreview,
          durationMs: durationMs ?? existing.durationMs,
          isError: isError || existing.isError,
          argsSummary: argsSummary || existing.argsSummary,
          event, // point selection at result (richer payload)
        };
        continue;
      }
      if (type === "tool_call_end" && existingIdx != null) {
        // Keep the start/result row; attach end-only summary if missing.
        const existing = rows[existingIdx];
        if (!existing.argsSummary && argsSummary) {
          rows[existingIdx] = { ...existing, argsSummary, preview: argsSummary };
        }
        if (payload.is_error != null) {
          rows[existingIdx] = {
            ...rows[existingIdx],
            isError: Boolean(payload.is_error),
          };
        }
        continue;
      }
      rows.push({
        id: event.event_id,
        kind: "tool",
        label: `Tool · ${name}`,
        preview:
          [argsSummary, resultPreview].filter(Boolean).join(" → ") || name,
        timestamp: event.timestamp,
        event,
        toolName: name,
        argsSummary,
        resultPreview,
        durationMs,
        isError,
      });
      continue;
    }

    if (type === "run_started" || type === "run_completed" || type === "run_status") {
      flushTurn();
      const message = payloadString(payload, "message");
      rows.push({
        id: event.event_id,
        kind: "run",
        label: type.replaceAll("_", " "),
        preview:
          message?.slice(0, 180) ||
          payloadString(payload, "status") ||
          payloadString(payload, "error") ||
          type,
        timestamp: event.timestamp,
        event,
      });
      continue;
    }

    if (type === "run_failed" || type === "run_cancelled" || type === "run_interrupted" || type === "error") {
      flushTurn();
      rows.push({
        id: event.event_id,
        kind: "error",
        label: type.replaceAll("_", " "),
        preview: payloadString(payload, "error") || payloadString(payload, "message") || type,
        timestamp: event.timestamp,
        event,
        isError: true,
      });
      continue;
    }

    if (type === "approval_requested" || type === "approval_resolved") {
      flushTurn();
      rows.push({
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
      });
      continue;
    }

    if (type === "heartbeat") continue;

    flushTurn();
    rows.push({
      id: event.event_id,
      kind: "other",
      label: type,
      preview: JSON.stringify(payload).slice(0, 180),
      timestamp: event.timestamp,
      event,
    });
  }
  flushTurn();
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
  return `${input.provider || "unknown"} provider`;
}

export function extractUserMessageFromEvents(events: EventRow[]): string | null {
  const started = events.find((event) => event.type === "run_started");
  const message = started?.payload?.message;
  return typeof message === "string" && message.trim() ? message.trim() : null;
}

export function extractUserMessageFromMessages(messages: MessageRow[]): string | null {
  const user = messages.find((message) => message.role === "user" && message.content?.trim());
  return user?.content?.trim() || null;
}
