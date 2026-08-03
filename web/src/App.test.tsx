import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App, { eventLabel } from "./App";
import {
  api,
  type EventRow,
  type RunReport as RunReportData,
} from "./api/client";
import { REQUIRED_API_SCHEMA_VERSION } from "./api/compatibility";
import { RunReport } from "./components/RunReport";
import { AGENT_EVENT_TYPES } from "./useAgentStream";
import { eventTone, statusLabel } from "./viewModel";

function event(type: string, payload: Record<string, unknown> = {}): EventRow {
  return {
    event_id: `event-${type}`,
    global_seq: 1,
    run_seq: 1,
    session_id: "session",
    run_id: "run",
    type,
    timestamp: "2026-07-25T00:00:00Z",
    payload,
  };
}

function report(
  conclusion: RunReportData["conclusion"] = {
    status: "passed",
    label: "已完成",
    verified: true,
    reason: "所有验收规则均已通过。",
  }
): RunReportData {
  return {
    schema_version: 1,
    run_id: "run",
    session_id: "session",
    as_of: "2026-07-25T00:00:04Z",
    evidence_sha256: "a".repeat(64),
    run: {
      id: "run",
      session_id: "session",
      root_run_id: "run",
      status: conclusion.status === "passed" ? "completed" : "failed",
      steps: 2,
      created_at: "2026-07-25T00:00:00Z",
      updated_at: "2026-07-25T00:00:04Z",
    },
    conclusion,
    verification: {
      configured: true,
      policy: {
        validators: [
          { kind: "output", assertions: { contains: ["DONE"] } },
          { kind: "file", path: "result.txt", exists: true },
        ],
        max_retries: 0,
        on_exhausted: "failed",
      },
      attempts: [{
        attempt: 0,
        step: 2,
        validators: ["output", "file"],
        max_retries: 0,
        started_at: "2026-07-25T00:00:02Z",
        finished_at: "2026-07-25T00:00:03Z",
        action: conclusion.status === "passed" ? "pass" : "stop",
        passed: conclusion.status === "passed",
        failures: conclusion.status === "passed" ? [] : [{
          validator: "output",
          error_code: "assertion_failed",
          message: "缺少 DONE",
          recovery_hint: "补充输出标记",
        }],
        evidence: { "0:output": { passed: conclusion.status === "passed" } },
        result_event_id: "verification-result",
      }],
      failure_reasons: conclusion.status === "passed" ? [] : ["缺少 DONE"],
    },
    workspace_changes: [{
      invocation_id: "write",
      tool: "write_file",
      path: "result.txt",
      status: "succeeded",
      changed: true,
      resulting_version: "b".repeat(64),
      arguments_sha256: "c".repeat(64),
    }],
    tools: [{
      id: "write",
      run_id: "run",
      step: 1,
      ordinal: 0,
      provider_call_id: "call",
      tool_name: "write_file",
      tool_version: "1",
      status: "succeeded",
      effect: "workspace_write",
      replay_policy: "reconcile",
      arguments: { path: "result.txt" },
      arguments_sha256: "c".repeat(64),
      attempt_count: 1,
      created_at: "2026-07-25T00:00:01Z",
      updated_at: "2026-07-25T00:00:02Z",
    }],
    approvals: [{
      id: "approval",
      run_id: "run",
      tool_call_id: "tool",
      tool_name: "write_file",
      effect: "workspace_write",
      requires_confirmation: false,
      decision: "allow_once",
      invocation_id: "write",
      arguments_sha256: "c".repeat(64),
      created_at: "2026-07-25T00:00:01Z",
      resolved_at: "2026-07-25T00:00:02Z",
    }],
    artifacts: [{
      id: "artifact",
      sha256: "d".repeat(64),
      content_type: "text/plain",
      size_bytes: 128,
      summary: "result evidence",
      created_at: "2026-07-25T00:00:03Z",
    }],
    usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 },
    events: [event("verification_result", { passed: conclusion.status === "passed" })],
    source: {
      max_global_seq: 10,
      event_count: 10,
      tool_count: 1,
      approval_count: 1,
      artifact_count: 1,
    },
  };
}

describe("Procurement sourcing workspace", () => {
  it("exposes the complete run control surface", () => {
    expect(typeof api.runtime).toBe("function");
    expect(typeof api.createRun).toBe("function");
    expect(typeof api.cancelRun).toBe("function");
    expect(typeof api.resumeRun).toBe("function");
    expect(typeof api.resolveToolRecovery).toBe("function");
    expect(typeof api.decideApproval).toBe("function");
    expect(typeof api.toolInvocations).toBe("function");
    expect(typeof api.report).toBe("function");
    expect(AGENT_EVENT_TYPES).toEqual(
      expect.arrayContaining([
        "tool_call_validated",
        "tool_execution_queued",
        "tool_execution_started",
        "tool_retry",
        "tool_result",
        "tool_execution_cancelled",
        "tool_execution_indeterminate",
        "tool_recovery_resolved",
        "verification_started",
        "provider_retry",
        "budget_warning",
      ])
    );
  });

  it("renders the procurement conversation as the product entry", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ matches: false }),
    });
    const client = new QueryClient();
    client.setQueryData(["health"], {
      service: "agentharness",
      status: "ok",
      backend_version: "0.3.0",
      api_schema_version: REQUIRED_API_SCHEMA_VERSION,
      api_capabilities: ["run_execution_v1", "interactive_approval_v1"],
      data_dir: "/tmp/data",
      max_global_seq: 0,
    });
    client.setQueryData(["sessions"], []);
    client.setQueryData(["runs"], []);
    client.setQueryData(["procurement-requests"], []);

    const html = renderToString(
      <QueryClientProvider client={client}><App /></QueryClientProvider>
    );

    expect(html).toContain("采价台");
    expect(html).toContain("采购询价与供应商比价");
    expect(html).not.toContain("采购询价与供应商比价 Agent");
    expect(html).toContain('aria-label="搜索采购任务"');
    expect(html).toContain('aria-label="API / 模型配置"');
    expect(html).toContain("新建采购决策");
    expect(html).toContain('aria-label="采购目标"');
    expect(html).toContain('data-testid="conversation-upload"');
    expect(html).toContain("报价附件");
    expect(html).not.toContain('data-testid="run-composer"');
    expect(html).not.toContain("把目标交给 Agent");
  });

  it("labels operational activity without falling back to run status text", () => {
    expect(eventLabel(event("run_started"))).toBe("开始运行");
    expect(eventLabel(event("tool_call_start", { name: "write_file" }))).toBe(
      "调用 write_file"
    );
    expect(eventLabel(event("approval_resolved", { decision: "allow_once" }))).toBe(
      "操作已允许一次"
    );
    expect(eventLabel(event("tool_call_validated"))).toBe("工具参数已验证");
    expect(eventLabel(event("tool_execution_queued"))).toBe("工具已进入执行队列");
    expect(eventLabel(event("tool_execution_started"))).toBe("工具开始执行");
    expect(eventLabel(event("tool_retry", { attempt: 1 }))).toBe(
      "工具重试（第 1 次）"
    );
    expect(eventLabel(event("tool_execution_cancelled"))).toBe("工具执行已取消");
    expect(eventLabel(event("tool_execution_indeterminate"))).toBe(
      "工具结果需要人工确认"
    );
    expect(
      eventLabel(event("context_compacted", { status: "applied", messages_covered: 12 }))
    ).toBe("上下文已压缩（12 条消息并入摘要）");
    expect(eventLabel(event("context_compacted", { status: "skipped" }))).toBe(
      "上下文压缩已跳过"
    );
    expect(eventTone(event("tool_result", { is_error: true }))).toBe("danger");
    expect(eventTone(event("approval_resolved", { decision: "deny" }))).toBe(
      "danger"
    );
    expect(eventTone(event("verification_result", { passed: false }))).toBe(
      "danger"
    );
    expect(eventTone(event("verification_result", { action: "stop" }))).toBe(
      "danger"
    );
  });

  it("reserves the completed label for runs with persisted passing evidence", () => {
    expect(statusLabel("completed")).toBe("运行结束");
    const passed = renderToString(<RunReport report={report()} />);
    const unverified = renderToString(
      <RunReport
        report={report({
          status: "unverified",
          label: "运行结束",
          verified: false,
          reason: "没有配置验收规则。",
        })}
      />
    );

    expect(passed).toContain("已完成");
    expect(passed).toContain("结果与证据");
    expect(unverified).not.toContain("已完成");
    expect(unverified).toContain("运行结束");
  });

  it("keeps recovered verification failures in history without a final failure alert", () => {
    const recovered = report();
    recovered.verification.failure_reasons = ["第一次验收尚未取得人工审批"];
    recovered.verification.attempts.unshift({
      ...recovered.verification.attempts[0],
      passed: false,
      action: "require_human",
      failures: [{
        validator: "output",
        error_code: "assertion_failed",
        message: "第一次验收尚未取得人工审批",
        recovery_hint: "完成人工审批后继续",
      }],
    });

    const html = renderToString(<RunReport report={recovered} />);

    expect(html).not.toContain("失败原因");
    expect(html).toContain("第一次验收尚未取得人工审批");
  });

  it("renders failure evidence, file changes, audits, artifacts, and usage", () => {
    const html = renderToString(
      <RunReport
        report={report({
          status: "failed",
          label: "失败",
          verified: false,
          reason: "缺少 DONE",
        })}
      />
    );

    expect(html).toContain("失败原因");
    expect(html).toContain("缺少 DONE");
    expect(html).toContain("result.txt");
    expect(html).toContain("工具与审批");
    expect(html).toContain("allow_once");
    expect(html).toContain("result evidence");
    expect(html).toContain("120");
    expect(html).toContain("事件追踪");
  });

});
