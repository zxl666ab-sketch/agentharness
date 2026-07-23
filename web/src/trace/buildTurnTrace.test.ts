import { describe, it, expect } from "vitest";
import {
  buildTurnTrace,
  extractUserMessageFromEvents,
  runListSummary,
  summarizeArgs,
  isTerminalStatus,
} from "./buildTurnTrace";
import type { EventRow, MessageRow } from "../api/client";

function ev(
  partial: Partial<EventRow> & Pick<EventRow, "type" | "event_id" | "run_seq" | "global_seq">
): EventRow {
  return {
    schema_version: 1,
    session_id: "s",
    root_run_id: "r",
    run_id: "r",
    timestamp: "2026-07-23T04:36:27.000Z",
    payload: {},
    ...partial,
  };
}

describe("buildTurnTrace", () => {
  it("collapses text deltas and hides checkpoints by default", () => {
    const events = [
      ev({
        type: "run_started",
        event_id: "e0",
        run_seq: 1,
        global_seq: 1,
        payload: { message: "find the blogger" },
      }),
      ev({ type: "model_turn_start", event_id: "e1", run_seq: 2, global_seq: 2, payload: { step: 0 } }),
      ev({
        type: "text_delta",
        event_id: "e2",
        run_seq: 3,
        global_seq: 3,
        payload: { text: "Hello " },
      }),
      ev({
        type: "text_delta",
        event_id: "e3",
        run_seq: 4,
        global_seq: 4,
        payload: { text: "world" },
      }),
      ev({ type: "model_turn_end", event_id: "e4", run_seq: 5, global_seq: 5, payload: { step: 0 } }),
      ev({
        type: "checkpoint",
        event_id: "e5",
        run_seq: 6,
        global_seq: 6,
        payload: { phase: "tools", step: 0 },
      }),
      ev({
        type: "tool_call_start",
        event_id: "e6",
        run_seq: 7,
        global_seq: 7,
        payload: { tool_call_id: "c1", name: "browser" },
      }),
      ev({
        type: "tool_result",
        event_id: "e7",
        run_seq: 8,
        global_seq: 8,
        payload: {
          tool_call_id: "c1",
          name: "browser",
          content_preview: "Navigated to https://example.com",
          duration_ms: 1000,
        },
      }),
    ];
    const messages: MessageRow[] = [
      {
        id: "m1",
        role: "assistant",
        content: "",
        created_at: "2026-07-23T04:36:27.000Z",
        tool_calls: [
          {
            id: "c1",
            name: "browser",
            arguments: { action: "goto", url: "https://example.com" },
            status: "pending",
          },
        ],
      },
    ];
    const rows = buildTurnTrace(events, { messages });
    expect(rows.some((row) => row.kind === "checkpoint")).toBe(false);
    const turns = rows.filter((row) => row.kind === "turn");
    expect(turns).toHaveLength(1);
    expect(turns[0].preview).toContain("Hello world");
    const tools = rows.filter((row) => row.kind === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0].argsSummary).toContain("action=goto");
    expect(tools[0].argsSummary).toContain("example.com");
    // Far fewer rows than raw event count
    expect(rows.length).toBeLessThan(events.length);
  });

  it("summarizes browser args", () => {
    expect(summarizeArgs({ action: "goto", url: "https://bilibili.com/x" })).toContain("action=goto");
  });

  it("summarizeArgs output is byte-stable across branches (anti-drift snapshot)", () => {
    // Locks the exact format that must stay identical to the Python summarizer
    // (agentharness/tools/summary.py::summarize_tool_arguments).
    expect(summarizeArgs(null)).toBe("");
    expect(summarizeArgs(undefined)).toBe("");
    // Non-object → stringified, truncated at maxLen with ellipsis.
    expect(summarizeArgs(42)).toBe("42");
    expect(summarizeArgs("x".repeat(200))).toBe("x".repeat(159) + "…");
    // Preferred keys, in preferred order, regardless of insertion order.
    expect(summarizeArgs({ url: "https://e.com", action: "goto" })).toBe(
      "action=goto url=https://e.com"
    );
    // context_id is a preferred key (parity with Python).
    expect(summarizeArgs({ context_id: "ctx-9", name: "child" })).toBe(
      "name=child context_id=ctx-9"
    );
    // Long preferred value truncated at 80 chars.
    expect(summarizeArgs({ path: "p".repeat(100) })).toBe(`path=${"p".repeat(79)}…`);
    // No preferred key → first 4 entries, secrets redacted.
    expect(
      summarizeArgs({ api_key: "sk-secret", foo: "bar", auth_token: "t", baz: 1 })
    ).toBe("api_key=[REDACTED] foo=bar auth_token=[REDACTED] baz=1");
    // Empty / null preferred values are skipped.
    expect(summarizeArgs({ url: "", path: "keep" })).toBe("path=keep");
  });

  it("prefers user message for list summary", () => {
    expect(
      runListSummary({
        userMessage: "你去b站帮我找神烦老狗",
        outputSummary: "long assistant output ".repeat(20),
      })
    ).toContain("神烦老狗");
  });

  it("extracts run_started message", () => {
    expect(
      extractUserMessageFromEvents([
        ev({
          type: "run_started",
          event_id: "e0",
          run_seq: 1,
          global_seq: 1,
          payload: { message: "hello task" },
        }),
      ])
    ).toBe("hello task");
  });

  it("detects terminal statuses", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("running")).toBe(false);
  });

  // Goal 7 edge case 1: tool_call event has tool_call_id but no matching message
  // (async message-write lag). Must fall back to event args_summary, never "undefined".
  it("falls back to event args_summary when the message is missing", () => {
    const events = [
      ev({
        type: "tool_call_start",
        event_id: "t1",
        run_seq: 1,
        global_seq: 1,
        payload: {
          tool_call_id: "orphan-call",
          name: "read_file",
          arguments_summary: "path=notes.md",
        },
      }),
    ];
    // No messages → argsIndex has no entry for "orphan-call".
    const rows = buildTurnTrace(events, { messages: [] });
    const tool = rows.find((row) => row.kind === "tool");
    expect(tool).toBeTruthy();
    expect(tool?.argsSummary).toBe("path=notes.md");
    expect(tool?.preview).toBe("path=notes.md");
    // Guard against the "undefined" string leaking into the UI.
    expect(tool?.argsSummary).not.toContain("undefined");
    expect(String(tool?.preview)).not.toContain("undefined");
  });

  it("renders empty (not undefined) args when neither event nor message has a summary", () => {
    const events = [
      ev({
        type: "tool_call_start",
        event_id: "t1",
        run_seq: 1,
        global_seq: 1,
        payload: { tool_call_id: "c1", name: "shell" },
      }),
    ];
    const rows = buildTurnTrace(events, { messages: [] });
    const tool = rows.find((row) => row.kind === "tool");
    expect(tool?.argsSummary).toBe("");
    // preview falls back to the tool name, never "undefined".
    expect(tool?.preview).toBe("shell");
  });

  // Goal 7 edge case 2: child_run whose parent tool_call is not in the event list
  // (parent paged out). The child row must still attach — to the run root — not drop.
  it("attaches an orphan child_run to the run root when parent tool is absent", () => {
    const events = [
      ev({
        type: "run_started",
        event_id: "run-start",
        run_seq: 1,
        global_seq: 1,
        payload: { message: "delegate" },
      }),
      ev({
        type: "child_run_started",
        event_id: "child-start",
        run_seq: 2,
        global_seq: 2,
        payload: {
          child_run_id: "child-9",
          parent_tool_call_id: "missing-call",
          actor: "researcher",
          status: "running",
        },
      }),
    ];
    const rows = buildTurnTrace(events);
    const run = rows.find((row) => row.kind === "run");
    const child = rows.find((row) => row.kind === "child_run");
    expect(child).toBeTruthy();
    // Parent tool is absent → falls back to run root, not undefined/dropped.
    expect(child?.parentId).toBe(run?.id);
    expect(child?.targetRunId).toBe("child-9");
    expect(child?.preview).not.toContain("undefined");
  });

  // Goal 7 edge case 3: multiple parallel tool calls pending simultaneously.
  // Each must produce a distinct tool row keyed by its own tool_call_id.
  it("tracks multiple parallel pending tool calls independently", () => {
    const events = [
      ev({
        type: "tool_call_start",
        event_id: "t1",
        run_seq: 1,
        global_seq: 1,
        payload: { tool_call_id: "call-a", name: "read_file", arguments_summary: "path=a" },
      }),
      ev({
        type: "tool_call_start",
        event_id: "t2",
        run_seq: 2,
        global_seq: 2,
        payload: { tool_call_id: "call-b", name: "shell", arguments_summary: "command=ls" },
      }),
      ev({
        type: "tool_call_start",
        event_id: "t3",
        run_seq: 3,
        global_seq: 3,
        payload: { tool_call_id: "call-c", name: "http", arguments_summary: "url=x" },
      }),
      // Only call-b resolves; a and c stay pending.
      ev({
        type: "tool_call_end",
        event_id: "t2-end",
        run_seq: 4,
        global_seq: 4,
        payload: { tool_call_id: "call-b", name: "shell", arguments_summary: "command=ls" },
      }),
    ];
    const rows = buildTurnTrace(events);
    const tools = rows.filter((row) => row.kind === "tool");
    expect(tools).toHaveLength(3);
    const byCall = new Map(tools.map((t) => [t.toolCallId, t]));
    expect(byCall.get("call-a")?.argsSummary).toBe("path=a");
    expect(byCall.get("call-b")?.argsSummary).toBe("command=ls");
    expect(byCall.get("call-c")?.argsSummary).toBe("url=x");
    // Each parallel call keeps a unique row id.
    expect(new Set(tools.map((t) => t.id)).size).toBe(3);
  });

  it("builds run, turn, output, tool, approval, result, and child hierarchy", () => {
    const events = [
      ev({
        type: "run_started",
        event_id: "run-start",
        run_seq: 1,
        global_seq: 1,
        payload: { message: "Coordinate the release", actor: "user" },
      }),
      ev({
        type: "model_turn_start",
        event_id: "turn-start",
        run_seq: 2,
        global_seq: 2,
        span_id: "model-span",
        payload: { step: 0 },
      }),
      ev({
        type: "text_delta",
        event_id: "output",
        run_seq: 3,
        global_seq: 3,
        span_id: "model-span",
        payload: { text: "I will delegate." },
      }),
      ev({
        type: "model_turn_end",
        event_id: "turn-end",
        run_seq: 4,
        global_seq: 4,
        span_id: "model-span",
        payload: { step: 0 },
      }),
      ev({
        type: "tool_call_start",
        event_id: "tool",
        run_seq: 5,
        global_seq: 5,
        span_id: "tool-call-span",
        parent_span_id: "model-span",
        payload: { tool_call_id: "call-1", name: "delegate" },
      }),
      ev({
        type: "approval_requested",
        event_id: "approval-request",
        run_seq: 6,
        global_seq: 6,
        payload: { tool_call_id: "call-1", tool: "delegate", effect: "pure" },
      }),
      ev({
        type: "approval_resolved",
        event_id: "approval-result",
        run_seq: 7,
        global_seq: 7,
        payload: { tool_call_id: "call-1", decision: "allow_once" },
      }),
      ev({
        type: "tool_result",
        event_id: "result",
        run_seq: 8,
        global_seq: 8,
        span_id: "tool-exec-span",
        payload: {
          tool_call_id: "call-1",
          name: "delegate",
          content_preview: "child completed",
        },
      }),
      ev({
        type: "child_run_started",
        event_id: "child-start",
        run_seq: 9,
        global_seq: 9,
        payload: {
          child_run_id: "child-run",
          parent_tool_call_id: "call-1",
          actor: "researcher",
          depth: 1,
          status: "running",
        },
      }),
      ev({
        type: "child_run_ended",
        event_id: "child-end",
        run_seq: 10,
        global_seq: 10,
        payload: {
          child_run_id: "child-run",
          parent_tool_call_id: "call-1",
          actor: "researcher",
          depth: 1,
          status: "completed",
        },
      }),
    ];

    const rows = buildTurnTrace(events);
    const run = rows.find((row) => row.kind === "run");
    const turn = rows.find((row) => row.kind === "turn");
    const output = rows.find((row) => row.kind === "model_output");
    const tool = rows.find((row) => row.kind === "tool");
    const approval = rows.find((row) => row.kind === "approval");
    const result = rows.find((row) => row.kind === "result");
    const child = rows.find((row) => row.kind === "child_run");

    expect(run?.depth).toBe(0);
    expect(turn).toMatchObject({ parentId: run?.id, depth: 1 });
    expect(output).toMatchObject({ parentId: turn?.id, depth: 2 });
    expect(tool).toMatchObject({ parentId: turn?.id, depth: 2, toolCallId: "call-1" });
    expect(approval).toMatchObject({ parentId: tool?.id, depth: 3 });
    expect(result).toMatchObject({ parentId: tool?.id, depth: 3 });
    expect(child).toMatchObject({
      parentId: tool?.id,
      depth: 3,
      targetRunId: "child-run",
      actor: "researcher",
      status: "completed",
    });
  });
});
