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
});
