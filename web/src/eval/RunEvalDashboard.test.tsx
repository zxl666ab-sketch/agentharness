import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RunEvaluationDetail, RunRow } from "../api/client";
import { RunEvalDashboard } from "./RunEvalDashboard";

describe("single-run evaluation dashboard", () => {
  it("renders a localized three-layer score report", () => {
    const run: RunRow = {
      id: "run-dashboard",
      session_id: "session",
      root_run_id: "run-dashboard",
      status: "completed",
      user_summary: "解释自注意力",
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:01Z",
    };
    const html = renderToString(
      <RunEvalDashboard
        run={run}
        evaluation={{
          schema_version: 1,
          passed: true,
          score: 0.86,
          mode: "ai",
          confidence: 0.9,
          failure_category: "none",
          dimensions: {
            task_completion: { score: 0.9, reason: "完成任务", applicable: true },
            correctness: { score: 0.8, reason: "内容正确", applicable: true },
            tool_use: { score: 0, reason: "无需工具", applicable: false },
          },
          evidence: ["回答覆盖核心概念"],
          improvements: ["补充公式"],
        }}
        turn={null}
        onBack={() => undefined}
      />
    );
    expect(html).toContain('data-testid="run-eval-dashboard"');
    expect(html).toContain("86");
    expect(html).toContain("结果质量");
    expect(html).toContain("执行过程");
    expect(html).toContain("系统与体验");
    expect(html).toContain("判分证据");
    expect(html).toContain("改进建议");
    expect(html).toContain('data-testid="eval-tab-overview"');
    expect(html).toContain('data-testid="eval-tab-trajectory"');
    expect(html).toContain('data-testid="eval-tab-diagnosis"');
    expect(html).toContain('data-testid="eval-tab-judge"');
    expect(html).toContain('data-testid="eval-tab-regression"');
    expect(html).not.toContain("选择 JSON 报告");
  });

  it("explains that deterministic grading does not judge answer quality", () => {
    const run: RunRow = {
      id: "run-health",
      session_id: "session",
      root_run_id: "run-health",
      status: "completed",
      user_summary: "检查运行",
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:01Z",
    };
    const html = renderToString(
      <RunEvalDashboard
        run={run}
        evaluation={{ schema_version: 1, passed: true, score: 1, mode: "deterministic" }}
        turn={null}
        onBack={() => undefined}
        onRegrade={() => undefined}
      />
    );
    expect(html).toContain('data-testid="deterministic-explanation"');
    expect(html).toContain("本次仅检查运行健康");
    expect(html).toContain("不评价回答的正确性与表达质量");
    expect(html).toContain("重新评测");
    expect(html).toContain("健康检查");
    expect(html).not.toContain("/ 100");
  });

  it("localizes empty runtime configuration values in diagnostic text", () => {
    const run: RunRow = {
      id: "run-diagnosis",
      session_id: "session",
      root_run_id: "run-diagnosis",
      status: "failed",
      user_summary: "检查运行配置",
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:01Z",
    };
    const report: NonNullable<RunEvaluationDetail["report"]> = {
      schema_version: 2,
      report_id: "report",
      trace_id: "trace",
      run_id: run.id,
      policy_id: "policy",
      policy_version: "1",
      mode: "scored",
      passed: false,
      score: 0,
      checks: [{
        id: "runtime.config",
        category: "health",
        status: "failed",
        message: "Runtime configuration fingerprint=abc123; provider=unknown, model=None, cwd=null.",
      }],
      hard_failures: 1,
      passed_count: 0,
      failed_count: 1,
      not_configured_count: 0,
    };
    const detail: RunEvaluationDetail = {
      schema_version: 2,
      available: true,
      run_id: run.id,
      trace: {
        trace_id: "trace",
        run_id: run.id,
        status: "failed",
        completeness: "complete",
        steps: 1,
        event_count: 1,
        spans: [],
      },
      report,
      diagnosis: null,
      judge: { status: "unverified", samples: [] },
      replay: {},
      regression: {},
      ids: {},
    };

    const html = renderToString(
      <RunEvalDashboard
        run={run}
        evaluation={null}
        detail={detail}
        turn={null}
        onBack={() => undefined}
      />
    );

    expect(html).toContain("服务商：未知；模型：未设置；工作目录：未设置");
    expect(html).not.toContain("model=None");
  });
});
