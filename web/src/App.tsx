import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Clock3,
  ListTree,
  Moon,
  PanelRight,
  Sun,
  TerminalSquare,
  Zap,
} from "lucide-react";
import { api, type EventRow, type RunRow } from "./api/client";
import { Inspector } from "./components/Inspector";
import { RunList } from "./components/RunList";
import { Timeline } from "./components/Timeline";
import { SseInvalidator } from "./store/SseInvalidator";
import { useSse } from "./store/useSse";
import { isTerminalStatus } from "./trace/buildTurnTrace";
import { runStatusLabel } from "./runs/status";

type MobileView = "runs" | "timeline" | "inspector";

function readRunFromUrl(): string | null {
  try {
    const value = new URLSearchParams(window.location.search).get("run");
    return value && value.trim() ? value.trim() : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  const [selectedRunId, setSelectedRunId] = useState<string | null>(() => readRunFromUrl());
  const [selectedEvent, setSelectedEvent] = useState<EventRow | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>("runs");
  const [sseStartSeq, setSseStartSeq] = useState<number | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (sseStartSeq == null && healthQuery.data) {
      setSseStartSeq(healthQuery.data.max_global_seq);
    }
  }, [healthQuery.data, sseStartSeq]);

  const { status: sseStatus } = useSse(
    sseStartSeq != null,
    sseStartSeq ?? 0
  );

  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(undefined, 500),
    // Low-frequency baseline; SSE bumps when needed.
    refetchInterval: 10_000,
  });
  const runs = useMemo(() => runsQuery.data || [], [runsQuery.data]);

  useEffect(() => {
    if (!selectedRunId && runs.length) setSelectedRunId(runs[0].id);
    if (selectedRunId && runs.length && !runs.some((run) => run.id === selectedRunId)) {
      // Keep deep-linked id until runs load; only reset if clearly missing after load.
      if (runsQuery.isFetched) setSelectedRunId(runs[0]?.id ?? null);
    }
  }, [runs, selectedRunId, runsQuery.isFetched]);

  useEffect(() => {
    if (!selectedRunId) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("run") === selectedRunId) return;
    url.searchParams.set("run", selectedRunId);
    window.history.replaceState({}, "", url.toString());
  }, [selectedRunId]);

  const selectedFromList = runs.find((run) => run.id === selectedRunId) || null;
  const selectedIsTerminal = isTerminalStatus(selectedFromList?.status);
  // Gate child-tree fetches: only runs with known children need the tree endpoint.
  const selectedChildCount = selectedFromList?.child_count ?? 0;
  // Approvals only mutate while a run is actively awaiting a decision.
  const selectedIsWaitingApproval = selectedFromList?.status === "waiting_approval";

  const runQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: () => api.run(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: selectedIsTerminal ? false : 4_000,
    staleTime: selectedIsTerminal ? Infinity : 0,
  });

  const eventsQuery = useQuery({
    queryKey: ["events", selectedRunId],
    queryFn: () => api.events(selectedRunId!),
    enabled: !!selectedRunId,
    // Terminal historical runs: single fetch. Active runs rely on SSE + light poll.
    refetchInterval: selectedIsTerminal ? false : 3_000,
    staleTime: selectedIsTerminal ? Infinity : 1_000,
  });

  const treeQuery = useQuery({
    queryKey: ["tree", selectedRunId],
    queryFn: () => api.tree(selectedRunId!),
    // Only runs with children have a meaningful tree; skip the request otherwise.
    enabled: !!selectedRunId && selectedChildCount > 0,
    refetchInterval: selectedIsTerminal ? false : 5_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const messagesQuery = useQuery({
    queryKey: ["messages", selectedRunId],
    queryFn: () => api.messages(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: selectedIsTerminal ? false : 4_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const approvalsQuery = useQuery({
    queryKey: ["approvals", selectedRunId],
    queryFn: () => api.approvals(selectedRunId!),
    enabled: !!selectedRunId,
    // Poll only while a decision is pending; otherwise treat as immutable.
    refetchInterval: selectedIsWaitingApproval ? 5_000 : false,
    staleTime: selectedIsWaitingApproval ? 2_000 : Infinity,
  });

  const checkpointQuery = useQuery({
    queryKey: ["checkpoint", selectedRunId],
    queryFn: () => api.checkpoint(selectedRunId!),
    enabled: !!selectedRunId,
    retry: false,
    refetchInterval: selectedIsTerminal ? false : 5_000,
    staleTime: selectedIsTerminal ? Infinity : 2_000,
  });

  const selectedRun: RunRow | null =
    runQuery.data || runs.find((run) => run.id === selectedRunId) || null;

  const transcriptQuery = useQuery({
    queryKey: ["transcript", selectedRun?.session_id],
    queryFn: () => api.transcript(selectedRun!.session_id),
    enabled: !!selectedRun?.session_id,
    // Session transcript only grows on new turns (SSE invalidates runs); a terminal
    // selection is stable, so avoid refetching when re-selecting the same run.
    staleTime: selectedIsTerminal ? Infinity : 5_000,
  });

  useEffect(() => {
    if (selectedEvent && selectedEvent.run_id !== selectedRunId) setSelectedEvent(null);
  }, [selectedEvent, selectedRunId]);

  const selectRun = useCallback((id: string) => {
    setSelectedRunId(id);
    setSelectedEvent(null);
    setMobileView("timeline");
  }, []);
  const selectEvent = useCallback((event: EventRow) => {
    setSelectedEvent(event);
    if (window.matchMedia?.("(max-width: 860px)").matches) setMobileView("inspector");
  }, []);

  const usage = parseJson(selectedRun?.usage_json);
  const metadata = parseJson(selectedRun?.metadata_json);
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
      <header className="app-header">
        <div className="product-mark">
          <TerminalSquare size={17} aria-hidden="true" />
          <div>
            <h1>Agent Harness</h1>
            <span>运行检查器</span>
          </div>
        </div>
        <div className="header-context" title={healthQuery.data?.data_dir || ""}>
          {selectedRun ? (
            <>
              <code>{selectedRun.id.slice(0, 12)}</code>
              <span className={`status-text ${selectedRun.status}`}>
                {runStatusLabel(selectedRun)}
              </span>
            </>
          ) : (
            <span>未选择运行</span>
          )}
        </div>
        <div className="header-actions">
          <span
            className={`live-state ${connectionStatus}`}
            role="status"
            aria-live="polite"
          >
            <span aria-hidden="true" /> {connectionLabel}
          </span>
          <button
            type="button"
            className="icon-button"
            onClick={() => setTheme((value) => (value === "light" ? "dark" : "light"))}
            aria-label="切换主题"
            title="切换主题"
          >
            {theme === "light" ? (
              <Moon size={16} aria-hidden="true" />
            ) : (
              <Sun size={16} aria-hidden="true" />
            )}
          </button>
        </div>
      </header>

      <main className="workbench">
        <section
          className={`workspace-panel runs-panel ${mobileView === "runs" ? "mobile-visible" : ""}`}
          data-testid="runs-panel"
        >
          <PanelHeader title="运行" meta={`共 ${runs.length} 个`} />
          <RunList
            runs={runs}
            selectedId={selectedRunId}
            onSelect={selectRun}
            loading={runsQuery.isLoading}
            error={runsQuery.error ? errorMessage(runsQuery.error) : null}
          />
        </section>

        <section
          className={`workspace-panel timeline-panel ${
            mobileView === "timeline" ? "mobile-visible" : ""
          }`}
          data-testid="timeline-panel"
        >
          <PanelHeader title="追踪" meta={selectedRun ? selectedRun.id.slice(0, 12) : "-"} />
          <div className="run-overview" data-testid="run-overview">
            <Metric
              icon={<Activity size={14} />}
              label="状态"
              value={selectedRun ? runStatusLabel(selectedRun) : "-"}
            />
            <Metric icon={<Clock3 size={14} />} label="耗时" value={duration} />
            <Metric
              icon={<Zap size={14} />}
              label="令牌"
              value={`${numberValue(usage?.total_tokens)} / ${numberValue(budget?.max_tokens)}`}
            />
            <Metric
              icon={<ListTree size={14} />}
              label="步骤"
              value={`${selectedRun?.steps || 0} / ${numberValue(budget?.max_steps)}`}
            />
          </div>
          <Timeline
            runId={selectedRunId}
            events={eventsQuery.data || []}
            messages={messagesQuery.data || []}
            selectedId={selectedEvent?.event_id || null}
            onSelect={selectEvent}
            onSelectRun={selectRun}
            runStatus={selectedRun?.status || null}
            loading={eventsQuery.isLoading || messagesQuery.isLoading}
            error={eventsQuery.error ? errorMessage(eventsQuery.error) : null}
          />
        </section>

        <aside
          className={`workspace-panel inspector-panel ${
            mobileView === "inspector" ? "mobile-visible" : ""
          }`}
          data-testid="inspector-panel"
        >
          <PanelHeader title="检查器" meta={selectedEvent?.type || "run"} />
          <Inspector
            run={selectedRun}
            event={selectedEvent}
            tree={treeQuery.data || (selectedRun ? [selectedRun] : [])}
            messages={messagesQuery.data || []}
            approvals={approvalsQuery.data || []}
            checkpoint={checkpointQuery.data || null}
            transcript={transcriptQuery.data || []}
            onSelectRun={selectRun}
            loading={!!selectedRunId && runQuery.isLoading}
            error={runQuery.error ? errorMessage(runQuery.error) : null}
          />
        </aside>
      </main>

      <nav className="mobile-tabs" data-testid="mobile-tabs" aria-label="工作区视图">
        <MobileTab
          active={mobileView === "runs"}
          onClick={() => setMobileView("runs")}
          icon={<ListTree size={16} />}
          label="运行"
        />
        <MobileTab
          active={mobileView === "timeline"}
          onClick={() => setMobileView("timeline")}
          icon={<Activity size={16} />}
          label="追踪"
        />
        <MobileTab
          active={mobileView === "inspector"}
          onClick={() => setMobileView("inspector")}
          icon={<PanelRight size={16} />}
          label="检查器"
        />
      </nav>
    </div>
  );
}

function PanelHeader({ title, meta }: { title: string; meta: string }) {
  return (
    <div className="panel-header">
      <h2>{title}</h2>
      <span>{meta}</span>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="metric">
      <span className="metric-icon" aria-hidden="true">{icon}</span>
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

function MobileTab({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      className={active ? "active" : ""}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
    >
      <span aria-hidden="true">{icon}</span>
      {label}
    </button>
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

function numberValue(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString() : "-";
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
