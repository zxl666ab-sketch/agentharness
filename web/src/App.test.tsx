import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, it, expect } from "vitest";
import App from "./App";
import { api } from "./api/client";
import { sseStore } from "./store/sseStore";
import { categorizeEvent, groupEventsByCategory } from "./events/categories";

describe("api client shape", () => {
  it("exposes readonly methods only including session transcript", () => {
    expect(typeof api.health).toBe("function");
    expect(typeof api.runs).toBe("function");
    expect(typeof api.events).toBe("function");
    expect(typeof api.tree).toBe("function");
    expect(typeof api.sessions).toBe("function");
    expect(typeof api.session).toBe("function");
    expect(typeof api.transcript).toBe("function");
    expect(typeof api.messages).toBe("function");
    expect(typeof api.approvals).toBe("function");
    expect(typeof api.checkpoint).toBe("function");
    expect((api as { createRun?: unknown }).createRun).toBeUndefined();
  });
});

describe("run inspector shell", () => {
  it("renders runs, timeline, and inspector as primary regions", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: false }),
    });
    const client = new QueryClient();
    const html = renderToString(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    );

    expect(html).toContain('data-testid="runs-panel"');
    expect(html).toContain('data-testid="timeline-panel"');
    expect(html).toContain('data-testid="inspector-panel"');
    expect(html).toContain("Runs");
    expect(html).toContain("Timeline");
    expect(html).toContain("Inspector");
  });
});

describe("sse store", () => {
  it("dedupes by global_seq", () => {
    sseStore.clear();
    expect(sseStore.events).toHaveLength(0);
    expect(sseStore.lastSeq).toBe(0);
  });

  it("keeps a bounded ordered window and ignores old sequence ids", () => {
    sseStore.clear();
    const receive = (
      sseStore as unknown as { handleRaw: (message: MessageEvent) => void }
    ).handleRaw.bind(sseStore);
    for (let sequence = 1; sequence <= 2505; sequence += 1) {
      receive(
        new MessageEvent("message", {
          data: JSON.stringify({
            global_seq: sequence,
            run_id: "run",
            event_id: `event-${sequence}`,
            type: "text_delta",
            payload: {},
          }),
        })
      );
    }
    expect(sseStore.events).toHaveLength(2000);
    expect(sseStore.lastSeq).toBe(2505);
    const before = sseStore.events.length;
    receive(
      new MessageEvent("message", {
        data: JSON.stringify({ global_seq: 10, run_id: "run", payload: {} }),
      })
    );
    expect(sseStore.events).toHaveLength(before);
  });
});

describe("event categories (Chinese)", () => {
  it("maps model/tool/approval/error groups", () => {
    expect(categorizeEvent("text_delta").group).toBe("model");
    expect(categorizeEvent("text_delta").label).toBe("文本流");
    expect(categorizeEvent("tool_result").group).toBe("tool");
    expect(categorizeEvent("approval_requested").group).toBe("approval");
    expect(categorizeEvent("run_failed").group).toBe("error");
    expect(categorizeEvent("run_failed").groupLabel).toBe("错误");
  });

  it("groups mixed events", () => {
    const events = [
      { type: "text_delta" },
      { type: "tool_call_start" },
      { type: "approval_resolved" },
      { type: "error" },
    ];
    const g = groupEventsByCategory(events);
    expect(g.model).toHaveLength(1);
    expect(g.tool).toHaveLength(1);
    expect(g.approval).toHaveLength(1);
    expect(g.error).toHaveLength(1);
  });
});
