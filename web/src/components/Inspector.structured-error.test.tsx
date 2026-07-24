import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { EventRow, MessageRow, RunRow } from "../api/client";
import { Inspector } from "./Inspector";

describe("Inspector structured tool errors", () => {
  it("shows recovery fields carried by the tool_result event", () => {
    const run: RunRow = {
      id: "run-error",
      session_id: "session",
      root_run_id: "run-error",
      status: "failed",
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:01Z",
    };
    const messages: MessageRow[] = [
      {
        id: "assistant",
        role: "assistant",
        content: "",
        created_at: run.created_at,
        tool_calls: [
          {
            id: "call",
            name: "write_file",
            arguments: { path: "result.txt", expected_version: "old" },
            status: "failed",
          },
        ],
      },
      {
        id: "result",
        role: "tool",
        name: "write_file",
        tool_call_id: "call",
        content: "File version conflict",
        created_at: run.updated_at,
      },
    ];
    const event: EventRow = {
      schema_version: 1,
      event_id: "event",
      global_seq: 1,
      run_seq: 1,
      session_id: run.session_id,
      root_run_id: run.id,
      run_id: run.id,
      type: "tool_result",
      timestamp: run.updated_at,
      payload: {
        tool_call_id: "call",
        error_code: "file_version_conflict",
        error_category: "concurrency",
        retryable: true,
        recovery_hint: "Call read_file again.",
      },
    };

    const html = renderToString(
      <Inspector
        initialTab="detail"
        run={run}
        event={event}
        tree={[run]}
        messages={messages}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(html).toContain("file_version_conflict");
    expect(html).toContain("concurrency");
    expect(html).toContain("Call read_file again.");
    expect(html).toContain("可重试");
  });
});
