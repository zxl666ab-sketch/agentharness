import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App, { eventLabel } from "./App";
import { api, type EventRow, type RuntimeInfo } from "./api/client";
import { REQUIRED_API_SCHEMA_VERSION } from "./api/compatibility";
import { MessageContent } from "./components/MessageContent";
import { RunComposer } from "./components/RunComposer";
import { ToolTimeline } from "./components/ToolTimeline";
import { AGENT_EVENT_TYPES } from "./useAgentStream";
import { eventTone } from "./viewModel";

const runtime: RuntimeInfo = {
  execution_enabled: true,
  default_provider: "openai",
  providers: [{ name: "openai", configured: true, default_model: "gpt-4o-mini" }],
  tools: [],
  workspaces: [{ id: "default", name: "workspace" }],
  defaults: { approval: "ask", allow_write: false },
};

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

describe("Web-first Agent workspace", () => {
  it("exposes the complete run control surface", () => {
    expect(typeof api.runtime).toBe("function");
    expect(typeof api.createRun).toBe("function");
    expect(typeof api.cancelRun).toBe("function");
    expect(typeof api.resumeRun).toBe("function");
    expect(typeof api.resolveToolRecovery).toBe("function");
    expect(typeof api.decideApproval).toBe("function");
    expect(typeof api.toolInvocations).toBe("function");
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

  it("renders the task workspace instead of the old evaluation inspector", () => {
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
    client.setQueryData(["runtime"], runtime);
    client.setQueryData(["sessions"], []);
    client.setQueryData(["runs"], []);

    const html = renderToString(
      <QueryClientProvider client={client}><App /></QueryClientProvider>
    );

    expect(html).toContain("把目标交给 Agent");
    expect(html).toContain('data-testid="run-composer"');
    expect(html).toContain('aria-label="搜索任务"');
    expect(html).toContain("允许写入");
    expect(html).toContain("OpenAI");
    expect(html).toContain("运行 Agent");
    expect(html).not.toContain("模型服务");
    expect(html).not.toContain('aria-label="打开任务列表"');
    expect(html).not.toContain('aria-label="关闭任务列表"');
    expect(html).not.toContain("sidebar-scrim");
    expect(html).not.toContain("智能评测");
    expect(html).not.toContain("检查器");
  });

  it("shows governed approval actions in the composer", () => {
    const html = renderToString(
      <RunComposer
        runtime={runtime}
        selectedSessionId="session"
        selectedRun={null}
        pendingApproval={{
          id: "approval",
          run_id: "run",
          tool_call_id: "tool",
          tool_name: "write_file",
          effect: "workspace_write",
          requires_confirmation: false,
          arguments_summary: '{"path":"result.txt"}',
          invocation_id: "invocation",
          arguments_sha256: "a".repeat(64),
          created_at: "2026-07-25T00:00:00Z",
        }}
        onCreate={async () => undefined}
        onCancel={async () => undefined}
        onResume={async () => undefined}
        onDecision={async () => undefined}
      />
    );

    expect(html).toContain("需要你的批准");
    expect(html).toContain("允许一次");
    expect(html).toContain("拒绝");
    expect(html).toContain("本次运行允许");
  });

  it("does not offer run-wide approval for actions requiring confirmation", () => {
    const html = renderToString(
      <RunComposer
        runtime={runtime}
        selectedSessionId="session"
        selectedRun={null}
        pendingApproval={{
          id: "approval",
          run_id: "run",
          tool_call_id: "tool",
          tool_name: "memory_store",
          effect: "external_write",
          requires_confirmation: true,
          arguments_summary: '{"content":"remember this"}',
          invocation_id: "invocation",
          arguments_sha256: "b".repeat(64),
          created_at: "2026-07-25T00:00:00Z",
        }}
        onCreate={async () => undefined}
        onCancel={async () => undefined}
        onResume={async () => undefined}
        onDecision={async () => undefined}
      />
    );

    expect(html).toContain("允许一次");
    expect(html).not.toContain("本次运行允许");
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
  });

  it("renders assistant markdown as structured content", () => {
    const html = renderToString(
      <MessageContent content={"- **文件读写**\n- `shell`"} />
    );

    expect(html).toContain("<ul>");
    expect(html).toContain("<strong>文件读写</strong>");
    expect(html).toContain("<code>shell</code>");
    expect(html).not.toContain("**文件读写**");
  });

  it("renders persisted tool execution state and recovery evidence", () => {
    const html = renderToString(
      <ToolTimeline
        invocations={[{
          id: "invocation",
          run_id: "run",
          step: 0,
          ordinal: 0,
          provider_call_id: "call",
          tool_name: "read_file",
          tool_version: "1",
          status: "succeeded",
          effect: "workspace_read",
          replay_policy: "safe",
          arguments: { path: "README.md" },
          arguments_sha256: "1234567890abcdef",
          attempt_count: 1,
          result: {
            content: "file contents",
            is_error: false,
            attempts: 1,
            retryable: false,
            duration_ms: 12,
          },
          created_at: "2026-07-25T00:00:00Z",
          updated_at: "2026-07-25T00:00:01Z",
        }]}
      />
    );

    expect(html).toContain("read_file");
    expect(html).toContain("path=README.md");
    expect(html).toContain("workspace_read");
  });

  it("renders explicit actions for an indeterminate tool result", () => {
    const html = renderToString(
      <ToolTimeline
        onResolve={async () => undefined}
        invocations={[{
          id: "indeterminate-invocation",
          run_id: "run",
          step: 0,
          ordinal: 0,
          provider_call_id: "call",
          tool_name: "shell",
          tool_version: "1",
          status: "indeterminate",
          effect: "destructive",
          replay_policy: "never",
          arguments: { command: "deploy" },
          arguments_sha256: "a".repeat(64),
          attempt_count: 1,
          created_at: "2026-07-25T00:00:00Z",
          updated_at: "2026-07-25T00:00:01Z",
        }]}
      />
    );

    expect(html).toContain("确认已完成");
    expect(html).toContain("跳过");
    expect(html).toContain("重新执行");
  });
});
