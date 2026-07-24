import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type EvidenceRefRow,
  type EventRow,
  type RunRow,
  type TranscriptTurn,
} from "./api/client";
import {
  checkBackendCompatibility,
  REQUIRED_API_SCHEMA_VERSION,
  WEB_BUILD_ID,
  type BackendCompatibility,
} from "./api/compatibility";
import { readAppUrlState, writeAppUrlState, type AppView } from "./app/urlState";
import { AppHeader } from "./components/AppHeader";
import { Workbench, type MobileView } from "./components/Workbench";
import { SseInvalidator } from "./store/SseInvalidator";
import { useSse } from "./store/useSse";
import { isTerminalStatus } from "./trace/buildTurnTrace";
import {
  RunEvalDashboard,
  type EvaluationPayload,
} from "./eval/RunEvalDashboard";

export default function App() {
  const initialUrlState = useMemo(() => readAppUrlState(), []);
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialUrlState.runId);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventRow | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>("runs");
  const [appView, setAppView] = useState<AppView>(initialUrlState.view);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [evaluationFocus, setEvaluationFocus] = useState<{
    run: RunRow;
    evaluation: EvaluationPayload;
    turn?: TranscriptTurn;
  } | null>(null);
  const [pendingEvidence, setPendingEvidence] = useState<EvidenceRefRow | null>(null);
  const [sseStartSeq, setSseStartSeq] = useState<number | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });
  const compatibility = healthQuery.data
    ? checkBackendCompatibility(healthQuery.data)
    : null;
  const backendReady = compatibility?.compatible === true;

  useEffect(() => {
    if (backendReady && sseStartSeq == null && healthQuery.data) {
      setSseStartSeq(healthQuery.data.max_global_seq);
    }
  }, [backendReady, healthQuery.data, sseStartSeq]);

  const { status: sseStatus } = useSse(
    backendReady && sseStartSeq != null,
    sseStartSeq ?? 0
  );

  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(undefined, 500),
    enabled: backendReady,
    // Low-frequency baseline; SSE bumps when needed.
    refetchInterval: 10_000,
  });
  const runs = useMemo(() => runsQuery.data || [], [runsQuery.data]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.sessions(500),
    enabled: backendReady,
    refetchInterval: 10_000,
  });
  const sessions = useMemo(() => sessionsQuery.data || [], [sessionsQuery.data]);

  useEffect(() => {
    if (selectedRunId) {
      const selected = runs.find((run) => run.id === selectedRunId);
      if (selected && selected.session_id !== selectedSessionId) {
        setSelectedSessionId(selected.session_id);
      }
      return;
    }
    if (!selectedSessionId && sessions.length) {
      const first = sessions[0];
      setSelectedSessionId(first.id);
      setSelectedRunId(first.latest_run_id || null);
      return;
    }
    if (selectedSessionId && sessions.length) {
      const selected = sessions.find((session) => session.id === selectedSessionId);
      if (!selected) {
        setSelectedSessionId(sessions[0].id);
        setSelectedRunId(sessions[0].latest_run_id || null);
      }
    }
    if (selectedRunId && runs.length && !runs.some((run) => run.id === selectedRunId)) {
      // Keep deep-linked id until runs load; only reset if clearly missing after load.
      if (runsQuery.isFetched) setSelectedRunId(runs[0]?.id ?? null);
    }
  }, [runs, selectedRunId, selectedSessionId, sessions, runsQuery.isFetched]);

  useEffect(() => {
    writeAppUrlState(selectedRunId, appView);
  }, [appView, selectedRunId]);

  const selectedFromList = runs.find((run) => run.id === selectedRunId) || null;
  const selectedIsTerminal = isTerminalStatus(selectedFromList?.status);
  // Gate child-tree fetches: only runs with known children need the tree endpoint.
  const selectedChildCount = selectedFromList?.child_count ?? 0;
  // Approvals only mutate while a run is actively awaiting a decision.
  const selectedIsWaitingApproval = selectedFromList?.status === "waiting_approval";

  const runQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: () => api.run(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    refetchInterval: selectedIsTerminal ? false : 4_000,
    staleTime: selectedIsTerminal ? Infinity : 0,
  });

  const eventsQuery = useQuery({
    queryKey: ["events", selectedRunId],
    queryFn: () => api.events(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    // Terminal historical runs: single fetch. Active runs rely on SSE + light poll.
    refetchInterval: selectedIsTerminal ? false : 3_000,
    staleTime: selectedIsTerminal ? Infinity : 1_000,
  });

  const treeQuery = useQuery({
    queryKey: ["tree", selectedRunId],
    queryFn: () => api.tree(selectedRunId!),
    // Only runs with children have a meaningful tree; skip the request otherwise.
    enabled: backendReady && !!selectedRunId && selectedChildCount > 0,
    refetchInterval: selectedIsTerminal ? false : 5_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const messagesQuery = useQuery({
    queryKey: ["messages", selectedRunId],
    queryFn: () => api.messages(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    refetchInterval: selectedIsTerminal ? false : 4_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const approvalsQuery = useQuery({
    queryKey: ["approvals", selectedRunId],
    queryFn: () => api.approvals(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    // Poll only while a decision is pending; otherwise treat as immutable.
    refetchInterval: selectedIsWaitingApproval ? 5_000 : false,
    staleTime: selectedIsWaitingApproval ? 2_000 : Infinity,
  });

  const checkpointQuery = useQuery({
    queryKey: ["checkpoint", selectedRunId],
    queryFn: () => api.checkpoint(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    retry: false,
    refetchInterval: selectedIsTerminal ? false : 5_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const contextsQuery = useQuery({
    queryKey: ["contexts", selectedRunId],
    queryFn: () => api.contexts(selectedRunId!),
    enabled: backendReady && !!selectedRunId,
    refetchInterval: selectedIsTerminal ? false : 5_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const evaluationQuery = useQuery({
    queryKey: ["evaluation", selectedRunId],
    queryFn: () => api.evaluation(selectedRunId!),
    enabled: backendReady && !!selectedRunId && appView === "eval",
    retry: false,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const selectedRun: RunRow | null =
    runQuery.data || runs.find((run) => run.id === selectedRunId) || null;

  useEffect(() => {
    if (selectedRun?.session_id && selectedRun.session_id !== selectedSessionId) {
      setSelectedSessionId(selectedRun.session_id);
    }
  }, [selectedRun?.session_id, selectedSessionId]);

  const transcriptQuery = useQuery({
    queryKey: ["transcript", selectedSessionId],
    queryFn: () => api.transcript(selectedSessionId!),
    enabled: backendReady && !!selectedSessionId,
    // Session transcript only grows on new turns (SSE invalidates runs); a terminal
    // selection is stable, so avoid refetching when re-selecting the same run.
    staleTime: selectedIsTerminal ? Infinity : 5_000,
  });

  useEffect(() => {
    if (selectedEvent && selectedEvent.run_id !== selectedRunId) setSelectedEvent(null);
  }, [selectedEvent, selectedRunId]);

  useEffect(() => {
    if (!pendingEvidence || pendingEvidence.run_id !== selectedRunId) return;
    if (eventsQuery.isLoading) return;
    const events = eventsQuery.data || [];
    const target = events.find((event) =>
      (pendingEvidence.event_id && event.event_id === pendingEvidence.event_id) ||
      (pendingEvidence.span_id && event.span_id === pendingEvidence.span_id) ||
      (pendingEvidence.sequence != null && event.run_seq === pendingEvidence.sequence)
    );
    if (target) setSelectedEvent(target);
    setPendingEvidence(null);
  }, [eventsQuery.data, eventsQuery.isLoading, pendingEvidence, selectedRunId]);

  const selectRun = useCallback((id: string) => {
    setSelectedRunId(id);
    const run = runs.find((item) => item.id === id);
    if (run) setSelectedSessionId(run.session_id);
    setSelectedEvent(null);
    setMobileView("timeline");
  }, [runs]);
  const selectSession = useCallback((id: string, latestRunId: string | null) => {
    setSelectedSessionId(id);
    setSelectedRunId(latestRunId);
    setSelectedEvent(null);
    setMobileView("timeline");
  }, []);
  const selectEvent = useCallback((event: EventRow) => {
    setSelectedEvent(event);
    setInspectorCollapsed(false);
    if (window.matchMedia?.("(max-width: 860px)").matches) setMobileView("inspector");
  }, []);
  const handleRunUpdated = useCallback((updated: RunRow) => {
    void runQuery.refetch();
    void runsQuery.refetch();
    if (updated.session_id) void sessionsQuery.refetch();
  }, [runQuery, runsQuery, sessionsQuery]);
  const handleEvaluationComplete = useCallback((
    updated: RunRow,
    evaluation: Record<string, unknown>,
    turn?: TranscriptTurn
  ) => {
    setSelectedRunId(updated.id);
    setSelectedSessionId(updated.session_id);
    setSelectedEvent(null);
    setEvaluationFocus({
      run: updated,
      evaluation: evaluation as EvaluationPayload,
      turn,
    });
    setAppView("eval");
    void evaluationQuery.refetch();
  }, [evaluationQuery]);
  const openEvidence = useCallback((evidence: EvidenceRefRow) => {
    const targetRunId = evidence.run_id || selectedRunId;
    if (targetRunId) {
      setSelectedRunId(targetRunId);
      const run = runs.find((item) => item.id === targetRunId);
      if (run) setSelectedSessionId(run.session_id);
    }
    setSelectedEvent(null);
    setPendingEvidence({ ...evidence, run_id: targetRunId || undefined });
    setInspectorCollapsed(false);
    setMobileView("inspector");
    setAppView("inspector");
  }, [runs, selectedRunId]);

  if (!healthQuery.data && healthQuery.isPending) {
    return <CompatibilityGate state="checking" onRetry={() => void healthQuery.refetch()} />;
  }
  if (healthQuery.isError || !healthQuery.data) {
    return (
      <CompatibilityGate
        state="unreachable"
        detail={errorMessage(healthQuery.error)}
        onRetry={() => void healthQuery.refetch()}
      />
    );
  }
  if (compatibility && !compatibility.compatible) {
    return (
      <CompatibilityGate
        state="incompatible"
        compatibility={compatibility}
        onRetry={() => void healthQuery.refetch()}
      />
    );
  }

  const usage = parseJson(selectedRun?.usage_json);
  const metadata = parseJson(selectedRun?.metadata_json);
  const storedEvaluation = asEvaluation(metadata?.eval);
  const dashboardRun = evaluationFocus?.run || selectedRun;
  const dashboardEvaluation = evaluationFocus?.evaluation || storedEvaluation;
  const dashboardTurn = evaluationFocus?.turn ||
    transcriptQuery.data?.find((turn) => turn.run_id === dashboardRun?.id) || null;
  const budget = asRecord(metadata?._agentharness_budget);
  const duration = selectedRun
    ? formatDuration(selectedRun.created_at, selectedRun.finished_at)
    : "-";
  const connectionStatus = healthQuery.isLoading
    ? "connecting"
    : healthQuery.isError
      ? "error"
      : sseStatus;
  const connectionLabel = healthQuery.isError ? "服务异常" : sseStatusText(connectionStatus);

  return (
    <div className="app-shell">
      <SseInvalidator selectedRunId={selectedRunId} />
      <AppHeader
        view={appView}
        theme={theme}
        connectionStatus={connectionStatus}
        connectionLabel={connectionLabel}
        onViewChange={setAppView}
        onThemeToggle={() => setTheme((value) => (value === "light" ? "dark" : "light"))}
      />

      {appView === "eval" ? (
        <main className="workbench eval-workbench" data-testid="eval-panel">
          <RunEvalDashboard
            run={dashboardRun}
            evaluation={dashboardEvaluation}
            detail={evaluationQuery.data || null}
            loading={evaluationQuery.isLoading}
            error={evaluationQuery.error ? errorMessage(evaluationQuery.error) : null}
            turn={dashboardTurn}
            onBack={() => setAppView("inspector")}
            onEvidenceSelect={openEvidence}
            onRegrade={() => {
              setAppView("inspector");
              setSelectedEvent(null);
              setInspectorCollapsed(false);
              setMobileView("inspector");
            }}
          />
        </main>
      ) : (
        <Workbench
          sessions={sessions}
          runs={runs}
          selectedSessionId={selectedSessionId}
          selectedRun={selectedRun}
          selectedRunId={selectedRunId}
          selectedEvent={selectedEvent}
          events={eventsQuery.data || []}
          messages={messagesQuery.data || []}
          approvals={approvalsQuery.data || []}
          checkpoint={checkpointQuery.data || null}
          tree={treeQuery.data || (selectedRun ? [selectedRun] : [])}
          transcript={transcriptQuery.data || []}
          contexts={contextsQuery.data || []}
          usage={usage}
          budget={budget}
          duration={duration}
          mobileView={mobileView}
          inspectorCollapsed={inspectorCollapsed}
          runsLoading={sessionsQuery.isLoading || runsQuery.isLoading}
          runsError={sessionsQuery.error ? errorMessage(sessionsQuery.error) : null}
          timelineLoading={eventsQuery.isLoading || messagesQuery.isLoading}
          timelineError={eventsQuery.error ? errorMessage(eventsQuery.error) : null}
          inspectorLoading={!!selectedRunId && runQuery.isLoading}
          inspectorError={runQuery.error ? errorMessage(runQuery.error) : null}
          onSelectSession={selectSession}
          onSelectRun={selectRun}
          onSelectEvent={selectEvent}
          onMobileViewChange={setMobileView}
          onInspectorToggle={() => setInspectorCollapsed((value) => !value)}
          onRunUpdated={handleRunUpdated}
          onEvaluationComplete={handleEvaluationComplete}
        />
      )}
    </div>
  );
}

function CompatibilityGate({
  state,
  detail,
  compatibility,
  onRetry,
}: {
  state: "checking" | "unreachable" | "incompatible";
  detail?: string;
  compatibility?: Exclude<BackendCompatibility, { compatible: true }>;
  onRetry: () => void;
}) {
  const checking = state === "checking";
  const incompatible = state === "incompatible";
  const title = checking
    ? "正在核对网页端与后端版本"
    : incompatible
      ? "网页端与后端版本不一致"
      : "无法连接 Agent Harness 后端";
  const message = checking
    ? "验证通过后才会加载运行数据。"
    : incompatible
      ? compatibility?.reason === "web_build"
        ? "后端仍绑定旧的网页构建。请重启网页服务后再重试。"
        : "后端接口版本过旧。请使用当前项目环境重启网页服务。"
      : detail || "请确认网页服务已经启动。";

  return (
    <main className="compatibility-gate" data-testid={`compatibility-${state}`}>
      <section className="compatibility-panel" role={checking ? "status" : "alert"}>
        <span className="compatibility-kicker">AGENT HARNESS · 运行环境检查</span>
        <h1>{title}</h1>
        <p>{message}</p>
        {compatibility ? (
          <dl>
            <div><dt>检查项</dt><dd>{compatibility.reason === "web_build" ? "网页构建 ID" : "接口结构版本"}</dd></div>
            <div><dt>页面要求</dt><dd>{compatibility.expected}</dd></div>
            <div><dt>后端报告</dt><dd>{compatibility.actual}</dd></div>
          </dl>
        ) : null}
        {!checking ? <button type="button" onClick={onRetry}>重新检查</button> : null}
        <small>接口结构版本 {REQUIRED_API_SCHEMA_VERSION} · 网页构建 {WEB_BUILD_ID}</small>
      </section>
    </main>
  );
}

function parseJson(raw?: string): Record<string, unknown> {
  if (!raw) return {};
  try {
    return asRecord(JSON.parse(raw));
  } catch {
    return {};
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asEvaluation(value: unknown): EvaluationPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const evaluation = value as EvaluationPayload;
  return evaluation.schema_version === 1 ? evaluation : null;
}


function formatDuration(start: string, end?: string | null): string {
  const started = new Date(start).getTime();
  const finished = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(started) || Number.isNaN(finished)) return "-";
  const milliseconds = Math.max(0, finished - started);
  if (milliseconds < 1000) return `${milliseconds} 毫秒`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} 秒`;
  return `${(milliseconds / 60_000).toFixed(1)} 分钟`;
}

function sseStatusText(status: string): string {
  return {
    connecting: "连接中",
    live: "实时",
    error: "连接异常",
    closed: "已断开",
  }[status] || status;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
