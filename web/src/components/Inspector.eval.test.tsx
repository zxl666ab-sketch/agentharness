import { renderToString } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, it, expect } from "vitest";
import { Inspector } from "./Inspector";
import type { RunRow } from "../api/client";

function baseRun(overrides: Partial<RunRow> = {}): RunRow {
  return {
    id: "run-eval-1",
    session_id: "session",
    root_run_id: "run-eval-1",
    status: "completed",
    provider: "fake",
    model: "fake",
    steps: 1,
    created_at: "2026-07-23T04:36:27.000Z",
    updated_at: "2026-07-23T04:36:28.000Z",
    finished_at: "2026-07-23T04:36:28.000Z",
    ...overrides,
  };
}

describe("Inspector run eval panel", () => {
  it("shows missing eval state when metadata has no eval", () => {
    const run = baseRun({
      metadata_json: JSON.stringify({ note: "x" }),
    });
    const html = renderToString(
      <Inspector
        initialTab="run"
        run={run}
        event={null}
        tree={[run]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(html).toContain('data-testid="run-eval"');
    expect(html).toContain("尚未评测");
    expect(html).toContain("规则与运行健康检查");
    expect(html).not.toContain("disabled");
    expect(html).not.toContain("评估数据版本不受支持");
  });

  it("offers manual grading when eval_assert exists", () => {
    const run = baseRun({
      metadata_json: JSON.stringify({ eval_assert: { contains: ["hello"] } }),
    });
    const html = renderToString(
      <Inspector
        initialTab="run"
        run={run}
        event={null}
        tree={[run]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(html).toContain("尚未评测");
    expect(html).toContain('data-testid="run-grade-button"');
    expect(html).not.toContain("需要 metadata.eval_assert");
  });

  it("renders passed score and reasons container", () => {
    const run = baseRun({
      metadata_json: JSON.stringify({
        eval: {
          schema_version: 1,
          passed: true,
          score: 1,
          reasons: [],
          grader: "composite",
          graded_at: "2026-07-23T04:36:29.000Z",
        },
      }),
    });
    const html = renderToString(
      <Inspector
        initialTab="run"
        run={run}
        event={null}
        tree={[run]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(html).toContain('data-testid="run-eval"');
    expect(html).toContain("通过");
    expect(html).toContain("1");
    expect(html).toContain("composite");
    expect(html).toContain("仅健康检查");
    expect(html).not.toContain("100 / 100");
    expect(html).not.toContain("未配置评估");
  });

  it("renders failed reasons from metadata.eval", () => {
    const run = baseRun({
      metadata_json: JSON.stringify({
        eval: {
          schema_version: 1,
          passed: false,
          score: 0,
          reasons: ["missing substring: 'need-this'"],
          grader: "composite",
        },
      }),
    });
    const html = renderToString(
      <Inspector
        initialTab="run"
        run={run}
        event={null}
        tree={[run]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(html).toContain("未通过");
    expect(html).toContain("missing substring");
    expect(html).toContain('data-testid="run-eval-reasons"');
  });

  it("degrades when schema_version is missing or newer", () => {
    const missingVersion = baseRun({
      metadata_json: JSON.stringify({
        eval: { passed: true, score: 1, reasons: [] },
      }),
    });
    const htmlMissing = renderToString(
      <Inspector
        initialTab="run"
        run={missingVersion}
        event={null}
        tree={[missingVersion]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(htmlMissing).toContain("评估数据版本不受支持");

    const future = baseRun({
      metadata_json: JSON.stringify({
        eval: { schema_version: 2, passed: true, score: 1, reasons: [] },
      }),
    });
    const htmlFuture = renderToString(
      <Inspector
        initialTab="run"
        run={future}
        event={null}
        tree={[future]}
        messages={[]}
        approvals={[]}
        checkpoint={null}
        transcript={[]}
      />
    );
    expect(htmlFuture).toContain("评估数据版本不受支持");
    expect(htmlFuture).not.toContain("未配置评估");
  });

  it("shows a one-click grade control for every transcript turn", async () => {
    window.localStorage.removeItem("agentharness.ai-evaluation");
    const run = baseRun();
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => {
      root.render(
      <Inspector
        initialTab="run"
          run={run}
          event={null}
          tree={[run]}
          messages={[]}
          approvals={[]}
          checkpoint={null}
          transcript={[{
            run_id: run.id,
            session_id: run.session_id,
            user_content: "question",
            assistant_content: "answer",
            status: "completed",
          }]}
        />
      );
    });
    const contextTab = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("上下文")
    );
    await act(async () => contextTab?.click());
    expect(container.textContent).toContain("智能评测");
    expect(container.textContent).toContain("仅规则与运行健康");
    expect(container.querySelector(`[data-testid="turn-grade-${run.id}"]`)).not.toBeNull();
    await act(async () => root.unmount());
  });
});
