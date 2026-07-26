import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
  Hash,
  Moon,
  Sparkles,
  Sun,
  Wifi,
  WifiOff,
  Workflow,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  api,
  type ApprovalRow,
  type CreateRunInput,
  type RuntimeInfo,
  type ToolInvocationRow,
  type ToolRecoveryDecision,
} from "./api/client";
import { checkBackendCompatibility } from "./api/compatibility";
import { EffectBadge } from "./components/EffectBadge";
import { MessageContent } from "./components/MessageContent";
import { RunComposer, type ComposerPrefill } from "./components/RunComposer";
import { SessionSidebar } from "./components/SessionSidebar";
import { ToolTimeline } from "./components/ToolTimeline";
import { useAgentStream } from "./useAgentStream";
import { eventLabel, statusLabel, TERMINAL_STATUSES } from "./viewModel";

export { eventLabel } from "./viewModel";

const NOTABLE_EVENT_TYPES = new Set([
  "run_started",
  "tool_call_start",
  "tool_call_validated",
  "tool_execution_queued",
  "tool_execution_started",
  "tool_retry",
  "tool_execution_cancelled",
  "tool_execution_indeterminate",
  "tool_recovery_resolved",
  "tool_result",
  "approval_requested",
  "approval_resolved",
  "verification_started",
  "verification_result",
  "provider_retry",
]);

const EXAMPLE_TASKS = [
  "梳理这个工作区的目录结构，总结每个模块的职责",
  "搜索代码里的 TODO 和 FIXME，整理成一份清单",
  "读取 README，然后解释如何启动这个项目",
];

export default function App() {
  const queryClient = useQueryClient();
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [draftNewTask, setDraftNewTask] = useState(false);
  const [prefill, setPrefill] = useState<ComposerPrefill | null>(null);
  const lastHandledSequence = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.health, retry: 1 });
  const compatibility = healthQuery.data
    ? checkBackendCompatibility(healthQuery.data)
    : null;
  const backendReady = compatibility?.compatible === true;
  const runtimeQuery = useQuery({
    queryKey: ["runtime"],
    queryFn: api.runtime,
    enabled: backendReady,
  });
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.sessions(),
    enabled: backendReady,
    refetchInterval: 5_000,
  });
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(),
    enabled: backendReady,
    refetchInterval: 5_000,
  });
  const sessions = useMemo(() => sessionsQuery.data || [], [sessionsQuery.data]);
  const runs = useMemo(() => runsQuery.data || [], [runsQuery.data]);

  useEffect(() => {
    if (draftNewTask || selectedSessionId || !sessions.length) return;
    setSelectedSessionId(sessions[0].id);
    setSelectedRunId(sessions[0].latest_run_id || null);
  }, [draftNewTask, selectedSessionId, sessions]);

  const selectedFromList = runs.find((run) => run.id === selectedRunId) || null;
  const shouldPollRun =
    !!selectedFromList && !TERMINAL_STATUSES.has(selectedFromList.status);
  const runQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: () => api.run(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    refetchInterval: shouldPollRun ? 2_000 : false,
  });
  const selectedRun = runQuery.data || selectedFromList;
  const active = !!selectedRun && !TERMINAL_STATUSES.has(selectedRun.status);
  const transcriptQuery = useQuery({
    queryKey: ["transcript", selectedSessionId],
    queryFn: () => api.transcript(selectedSessionId!),
    enabled: backendReady && !!selectedSessionId,
  });
  const messagesQuery = useQuery({
    queryKey: ["messages", selectedRunId],
    queryFn: () => api.messages(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
  });
  const approvalsQuery = useQuery({
    queryKey: ["approvals", selectedRunId],
    queryFn: () => api.approvals(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
  });
  const toolInvocationsQuery = useQuery({
    queryKey: ["tool-invocations", selectedRunId],
    queryFn: () => api.toolInvocations(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
  });

  const stream = useAgentStream(
    backendReady && !!healthQuery.data,
    healthQuery.data?.max_global_seq || 0
  );
  useEffect(() => {
    const fresh = stream.events.filter(
      (event) => event.global_seq > lastHandledSequence.current
    );
    if (!fresh.length) return;
    lastHandledSequence.current = Math.max(...fresh.map((event) => event.global_seq));
    // text_delta streams through stream.events directly — only lifecycle events
    // should invalidate queries, or streaming floods the API with list refetches.
    const meaningful = fresh.filter((event) => event.type !== "text_delta");
    if (!meaningful.length) return;
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
    void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    for (const event of meaningful) {
      void queryClient.invalidateQueries({ queryKey: ["run", event.run_id] });
      if (event.type.startsWith("tool_")) {
        void queryClient.invalidateQueries({
          queryKey: ["tool-invocations", event.run_id],
        });
      }
      if (["tool_result", "run_completed", "run_failed"].includes(event.type)) {
        void queryClient.invalidateQueries({ queryKey: ["messages", event.run_id] });
      }
      if (["approval_requested", "approval_resolved"].includes(event.type)) {
        void queryClient.invalidateQueries({ queryKey: ["approvals", event.run_id] });
      }
      if (TERMINAL_STATUSES.has(event.type.replace("run_", ""))) {
        void queryClient.invalidateQueries({
          queryKey: ["transcript", event.session_id],
        });
      }
    }
  }, [queryClient, stream.events]);

  const liveEvents = stream.events.filter((event) => event.run_id === selectedRunId);
  const pendingApproval =
    approvalsQuery.data?.find(
      (approval) => !approval.decision && (!approval.status || approval.status === "pending")
    ) || null;
  const streamingText = liveEvents
    .filter((event) => event.type === "text_delta")
    .map((event) => String(event.payload.text || ""))
    .join("");
  const storedAssistant = (messagesQuery.data || [])
    .filter((message) => message.role === "assistant")
    .map((message) => message.content)
    .join("");
  const transcript = transcriptQuery.data || [];
  const hasSelectedTurn =
    !!selectedRunId && transcript.some((turn) => turn.run_id === selectedRunId);
  const currentOutput = storedAssistant || streamingText;
  const latestActivity = liveEvents
    .filter((event) => NOTABLE_EVENT_TYPES.has(event.type))
    .at(-1);
  const toolInvocations = toolInvocationsQuery.data || [];

  useEffect(() => {
    stickToBottom.current = true;
  }, [selectedRunId]);

  useEffect(() => {
    const element = scrollRef.current;
    if (element && stickToBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [transcript.length, currentOutput, toolInvocations.length, selectedRunId]);

  const handleScroll = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    stickToBottom.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 120;
  }, []);

  const selectSession = useCallback((sessionId: string, runId?: string | null) => {
    setDraftNewTask(false);
    setSelectedSessionId(sessionId);
    setSelectedRunId(runId || null);
  }, []);

  const createNewTask = useCallback(() => {
    setDraftNewTask(true);
    setSelectedSessionId(null);
    setSelectedRunId(null);
  }, []);

  const pickExample = useCallback((text: string) => {
    setPrefill({ text, nonce: Date.now() });
  }, []);

  const createRun = useCallback(
    async (input: CreateRunInput) => {
      const accepted = await api.createRun(input);
      setDraftNewTask(false);
      setSelectedSessionId(accepted.session_id);
      setSelectedRunId(accepted.run_id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      ]);
    },
    [queryClient]
  );

  const cancelRun = useCallback(
    async (runId: string) => {
      await api.cancelRun(runId);
      await queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
    [queryClient]
  );

  const resumeRun = useCallback(
    async (runId: string, input?: string) => {
      const accepted = await api.resumeRun(runId, input);
      setSelectedSessionId(accepted.session_id);
      setSelectedRunId(runId);
      await queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
    [queryClient]
  );

  const decideApproval = useCallback(
    async (
      approval: ApprovalRow,
      decision: "deny" | "allow_once" | "allow_run"
    ) => {
      const resolved = await api.decideApproval(approval, decision);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approvals", resolved.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["run", resolved.run_id] }),
      ]);
    },
    [queryClient]
  );

  const resolveToolRecovery = useCallback(
    async (invocation: ToolInvocationRow, decision: ToolRecoveryDecision) => {
      const resolved = await api.resolveToolRecovery(invocation, decision);
      try {
        const accepted = await api.resumeRun(resolved.run_id);
        setSelectedSessionId(accepted.session_id);
        setSelectedRunId(resolved.run_id);
      } finally {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["tool-invocations", resolved.run_id] }),
          queryClient.invalidateQueries({ queryKey: ["run", resolved.run_id] }),
        ]);
      }
    },
    [queryClient]
  );

  if (healthQuery.isPending) return <Gate title="正在连接 Agent Runtime" />;
  if (healthQuery.isError || !healthQuery.data) {
    return <Gate title="无法连接 Agent Runtime" detail={errorText(healthQuery.error)} />;
  }
  if (compatibility && !compatibility.compatible) {
    return (
      <Gate
        title="网页与 Runtime 版本不一致"
        detail={`需要 ${compatibility.expected}，当前 ${compatibility.actual}`}
      />
    );
  }

  const selectedSession = sessions.find((session) => session.id === selectedSessionId);
  const workspaceName = runtimeQuery.data?.workspaces[0]?.name || "Workspace";
  const modelName = [selectedRun?.provider, selectedRun?.model]
    .filter(Boolean)
    .join(" / ");
  const usage = formatUsage(selectedRun?.usage_json);
  const activityLabel = latestActivity ? eventLabel(latestActivity) : null;

  return (
    <div className="agent-app">
      <header className="agent-header">
        <div className="header-leading">
          <div className="brand">
            <span><Sparkles size={18} /></span>
            <div><strong>Agent Harness</strong><small>Web Runtime</small></div>
          </div>
          <div className="header-workspace">
            <span>工作区</span>
            <strong>{workspaceName}</strong>
          </div>
        </div>
        <div className="header-actions">
          <div className={`connection ${stream.status}`}>
            {stream.status === "live" ? <Wifi size={14} /> : <WifiOff size={14} />}
            <span>
              {stream.status === "live"
                ? "实时连接"
                : stream.status === "connecting"
                  ? "连接中"
                  : "连接异常"}
            </span>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="切换主题"
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          >
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
      </header>

      <main className="agent-workspace">
        <SessionSidebar
          sessions={sessions}
          selectedSessionId={selectedSessionId}
          onNew={createNewTask}
          onSelect={selectSession}
        />

        <section className="conversation-panel">
          <div className="conversation-heading">
            <div className="conversation-title-row">
              <h1>{selectedSession?.display_title || selectedSession?.title || "新任务"}</h1>
              {selectedRun ? (
                <span className={`status-pill ${selectedRun.status}`}>
                  <i />{statusLabel(selectedRun.status)}
                </span>
              ) : null}
            </div>
            {selectedRun ? (
              <div className="run-metadata">
                <span><Cpu size={13} />{modelName || "未指定模型"}</span>
                <span><Workflow size={13} />{selectedRun.steps || 0} 步</span>
                {usage ? <span>{usage}</span> : null}
                <span className="run-id" title={selectedRun.id}>
                  <Hash size={12} />{selectedRun.id.slice(0, 10)}
                </span>
              </div>
            ) : (
              <p className="conversation-subtitle">描述目标，Agent 会实时汇报每一步。</p>
            )}
          </div>

          <div
            className="conversation-scroll"
            data-testid="conversation"
            ref={scrollRef}
            onScroll={handleScroll}
          >
            <div className="conversation-content">
              {transcript.map((turn) => {
                const isLive = turn.run_id === selectedRunId;
                return (
                  <div
                    className={`turn${isLive && active ? " active-turn" : ""}`}
                    key={turn.run_id}
                  >
                    <UserMessage content={turn.user_content} />
                    {isLive ? (
                      <ToolTimeline
                        invocations={toolInvocations}
                        onResolve={resolveToolRecovery}
                      />
                    ) : null}
                    <AssistantMessage
                      content={
                        isLive
                          ? currentOutput ||
                            (!active
                              ? turn.assistant_content ||
                                selectedRun?.output_summary ||
                                "没有返回内容"
                              : "")
                          : turn.assistant_content || "没有返回内容"
                      }
                      error={isLive ? turn.error || selectedRun?.error : turn.error}
                      thinking={isLive && active && !currentOutput}
                      activity={isLive ? activityLabel : undefined}
                    />
                  </div>
                );
              })}

              {selectedRun && !hasSelectedTurn ? (
                <div className="turn active-turn">
                  <UserMessage content={selectedRun.user_summary || ""} />
                  <ToolTimeline
                    invocations={toolInvocations}
                    onResolve={resolveToolRecovery}
                  />
                  <AssistantMessage
                    content={
                      currentOutput ||
                      (!active ? selectedRun.output_summary || "没有返回内容" : "")
                    }
                    error={selectedRun.error}
                    thinking={active && !currentOutput}
                    activity={activityLabel}
                  />
                </div>
              ) : null}

              {!transcript.length && !selectedRun ? (
                <Welcome runtime={runtimeQuery.data || null} onPick={pickExample} />
              ) : null}
            </div>
          </div>

          <RunComposer
            runtime={runtimeQuery.data || null}
            selectedSessionId={selectedSessionId || null}
            selectedRun={selectedRun || null}
            pendingApproval={pendingApproval}
            prefill={prefill}
            onCreate={createRun}
            onCancel={cancelRun}
            onResume={resumeRun}
            onDecision={decideApproval}
          />
        </section>
      </main>
    </div>
  );
}

function UserMessage({ content }: { content: string }) {
  return (
    <article className="message message-user">
      <div className="message-bubble"><p>{content}</p></div>
    </article>
  );
}

function AssistantMessage({
  content,
  error,
  thinking = false,
  activity,
}: {
  content: string;
  error?: string | null;
  thinking?: boolean;
  activity?: string | null;
}) {
  if (thinking) {
    return (
      <article className="message message-assistant">
        <div className="thinking-indicator" aria-label="Agent 正在处理">
          <span className="thinking-dots"><i /><i /><i /></span>
          <span>{activity || "Agent 正在处理任务"}</span>
        </div>
      </article>
    );
  }
  return (
    <article className="message message-assistant">
      <div className="message-bubble">
        <MessageContent content={content} />
        {error ? <small className="run-error">{error}</small> : null}
      </div>
    </article>
  );
}

function Welcome({
  runtime,
  onPick,
}: {
  runtime: RuntimeInfo | null;
  onPick: (text: string) => void;
}) {
  return (
    <div className="welcome">
      <div className="welcome-mark"><Sparkles size={24} /></div>
      <h2>把目标交给 Agent</h2>
      <p>
        任务在你授权的工作区内执行；每一次工具调用都带效果标记，写入和高风险操作由你审批。
      </p>
      <div className="welcome-examples" aria-label="示例任务">
        {EXAMPLE_TASKS.map((task) => (
          <button key={task} type="button" onClick={() => onPick(task)}>
            {task}
          </button>
        ))}
      </div>
      {runtime?.tools.length ? (
        <div className="welcome-capabilities" aria-label="可用工具">
          {runtime.tools.map((tool) => (
            <span key={tool.name} title={tool.description}>
              <EffectBadge effect={tool.effect} />
              {tool.name}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatUsage(value?: string): string | null {
  if (!value) return null;
  try {
    const usage = JSON.parse(value) as { total_tokens?: number };
    if (!usage.total_tokens) return null;
    return `${new Intl.NumberFormat("zh-CN").format(usage.total_tokens)} tokens`;
  } catch {
    return null;
  }
}

function Gate({ title, detail }: { title: string; detail?: string }) {
  return (
    <main className="gate">
      <div className="gate-mark"><Sparkles size={26} /></div>
      <h1>{title}</h1>
      {detail ? <p>{detail}</p> : null}
    </main>
  );
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "未知错误");
}
