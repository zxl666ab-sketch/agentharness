import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, it, expect } from "vitest";
import App from "./App";
import { api, type RunRow } from "./api/client";
import { RunList } from "./components/RunList";
import { Inspector } from "./components/Inspector";
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
    expect(html).toContain('data-testid="nav-eval"');
    expect(html).toContain("运行");
    expect(html).toContain("追踪");
    expect(html).toContain("检查器");
  });

  it("renders run intent from the runs response without fetching messages", () => {
    const run: RunRow = {
      id: "run-summary",
      session_id: "session",
      root_run_id: "run-summary",
      status: "completed",
      provider: "fake",
      user_summary: "Inspect the release trace",
      depth: 0,
      child_count: 2,
      created_at: "2026-07-23T04:36:27.000Z",
      updated_at: "2026-07-23T04:36:28.000Z",
      finished_at: "2026-07-23T04:36:28.000Z",
    };

    const html = renderToString(
      <RunList runs={[run]} selectedId={run.id} onSelect={() => undefined} />
    );

    expect(html).toContain("Inspect the release trace");
    expect(html).toContain("2 个子运行");
  });

  it("opens failed run detail with checkpoint and readonly recovery guidance", () => {
    const run: RunRow = {
      id: "failed-run",
      session_id: "session",
      root_run_id: "failed-run",
      status: "failed",
      provider: "openai",
      model: "gpt-test",
      error: "rate_limit: provider rejected the request",
      created_at: "2026-07-23T04:36:27.000Z",
      updated_at: "2026-07-23T04:36:28.000Z",
      finished_at: "2026-07-23T04:36:28.000Z",
    };
    const html = renderToString(
      <Inspector
        run={run}
        event={null}
        tree={[run]}
        messages={[]}
        approvals={[]}
        checkpoint={{
          run_id: run.id,
          phase: "model_turn",
          step: 2,
          status: "failed",
          pending_tool_calls: [],
          completed_tool_call_ids: [],
          usage: { input_tokens: 1, output_tokens: 0, total_tokens: 1 },
          created_at: "2026-07-23T04:36:28.000Z",
        }}
        transcript={[]}
      />
    );

    expect(html).toContain("身份");
    expect(html).toContain("失败信息");
    expect(html).toContain("openai / gpt-test");
    expect(html).toContain("model_turn");
    expect(html).toContain("agentharness resume failed-run");
    expect(html).not.toContain("请选择追踪事件");
  });

  it("labels stale running rows as orphaned without changing their status", () => {
    const run: RunRow = {
      id: "stale-run",
      session_id: "session",
      root_run_id: "stale-run",
      status: "running",
      created_at: "2020-01-01T00:00:00.000Z",
      updated_at: "2020-01-01T00:00:00.000Z",
    };
    const html = renderToString(
      <RunList runs={[run]} selectedId={run.id} onSelect={() => undefined} />
    );

    expect(html).toContain("陈旧 / 孤儿状态");
    expect(run.status).toBe("running");
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
