import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { api, type EventRow } from "./api/client";
import { Inspector } from "./components/Inspector";
import { RunList } from "./components/RunList";
import { Timeline } from "./components/Timeline";
import { useSse } from "./store/useSse";

type MobileView = "runs" | "timeline" | "inspector";

export default function App() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventRow | null>(null);
  const [mobileView, setMobileView] = useState<MobileView>("runs");
  const queryClient = useQueryClient();
  const { status: sseStatus, events: liveEvents } = useSse(true);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 10_000,
  });
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(undefined, 500),
    refetchInterval: 2_500,
  });
  const runs = useMemo(() => runsQuery.data || [], [runsQuery.data]);

  useEffect(() => {
    if (!selectedRunId && runs.length) setSelectedRunId(runs[0].id);
    if (selectedRunId && runs.length && !runs.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(runs[0].id);
    }
  }, [runs, selectedRunId]);

  const runQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: () => api.run(selectedRunId!),
    enabled: !!selectedRunId,
  });
  const eventsQuery = useQuery({
    queryKey: ["events", selectedRunId],
    queryFn: () => api.events(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: 2_000,
  });
  const treeQuery = useQuery({
    queryKey: ["tree", selectedRunId],
    queryFn: () => api.tree(selectedRunId!),
    enabled: !!selectedRunId,
  });
  const messagesQuery = useQuery({
    queryKey: ["messages", selectedRunId],
    queryFn: () => api.messages(selectedRunId!),
    enabled: !!selectedRunId,
  });
  const approvalsQuery = useQuery({
    queryKey: ["approvals", selectedRunId],
    queryFn: () => api.approvals(selectedRunId!),
    enabled: !!selectedRunId,
  });
  const checkpointQuery = useQuery({
    queryKey: ["checkpoint", selectedRunId],
    queryFn: () => api.checkpoint(selectedRunId!),
    enabled: !!selectedRunId,
    retry: false,
  });
  const selectedRun = runQuery.data || runs.find((run) => run.id === selectedRunId) || null;
  const transcriptQuery = useQuery({
    queryKey: ["transcript", selectedRun?.session_id],
    queryFn: () => api.transcript(selectedRun!.session_id),
    enabled: !!selectedRun?.session_id,
  });

  useEffect(() => {
    if (!liveEvents.length) return;
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
    if (selectedRunId) {
      for (const key of ["run", "events", "tree", "messages", "approvals", "checkpoint"]) {
        void queryClient.invalidateQueries({ queryKey: [key, selectedRunId] });
      }
    }
  }, [liveEvents.length, queryClient, selectedRunId]);

  const events = useMemo(() => {
    const merged = new Map<number, EventRow>();
    for (const event of eventsQuery.data || []) merged.set(event.global_seq, event);
    for (const event of liveEvents) {
      if (event.run_id === selectedRunId) merged.set(event.global_seq, event);
    }
    return [...merged.values()].sort((left, right) => left.global_seq - right.global_seq);
  }, [eventsQuery.data, liveEvents, selectedRunId]);

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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="product-mark">
          <TerminalSquare size={17} aria-hidden="true" />
          <div>
            <h1>Agent Harness</h1>
            <span>Run inspector</span>
          </div>
        </div>
        <div className="header-context" title={healthQuery.data?.data_dir || ""}>
          {selectedRun ? (
            <>
              <code>{selectedRun.id.slice(0, 12)}</code>
              <span className={`status-text ${selectedRun.status}`}>{selectedRun.status}</span>
            </>
          ) : (
            <span>No run selected</span>
          )}
        </div>
        <div className="header-actions">
          <span className={`live-state ${sseStatus}`}>
            <span aria-hidden="true" /> {sseStatus === "live" ? "Live" : sseStatus}
          </span>
          <button
            type="button"
            className="icon-button"
            onClick={() => setTheme((value) => (value === "light" ? "dark" : "light"))}
            aria-label="Toggle theme"
            title="Toggle theme"
          >
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
      </header>

      <main className="workbench">
        <section
          className={`workspace-panel runs-panel ${mobileView === "runs" ? "mobile-visible" : ""}`}
          data-testid="runs-panel"
        >
          <PanelHeader title="Runs" meta={`${runs.length} total`} />
          <RunList runs={runs} selectedId={selectedRunId} onSelect={selectRun} />
        </section>

        <section
          className={`workspace-panel timeline-panel ${
            mobileView === "timeline" ? "mobile-visible" : ""
          }`}
          data-testid="timeline-panel"
        >
          <PanelHeader title="Timeline" meta={selectedRun ? selectedRun.id.slice(0, 12) : "-"} />
          <div className="run-overview" data-testid="run-overview">
            <Metric icon={<Activity size={14} />} label="Status" value={selectedRun?.status || "-"} />
            <Metric icon={<Clock3 size={14} />} label="Duration" value={duration} />
            <Metric
              icon={<Zap size={14} />}
              label="Tokens"
              value={`${numberValue(usage?.total_tokens)} / ${numberValue(budget?.max_tokens)}`}
            />
            <Metric
              icon={<ListTree size={14} />}
              label="Steps"
              value={`${selectedRun?.steps || 0} / ${numberValue(budget?.max_steps)}`}
            />
          </div>
          <Timeline
            events={events}
            selectedId={selectedEvent?.event_id || null}
            onSelect={selectEvent}
          />
        </section>

        <aside
          className={`workspace-panel inspector-panel ${
            mobileView === "inspector" ? "mobile-visible" : ""
          }`}
          data-testid="inspector-panel"
        >
          <PanelHeader title="Inspector" meta={selectedEvent?.type || "run"} />
          <Inspector
            run={selectedRun}
            event={selectedEvent}
            tree={treeQuery.data || (selectedRun ? [selectedRun] : [])}
            messages={messagesQuery.data || []}
            approvals={approvalsQuery.data || []}
            checkpoint={checkpointQuery.data || null}
            transcript={transcriptQuery.data || []}
          />
        </aside>
      </main>

      <nav className="mobile-tabs" data-testid="mobile-tabs" aria-label="Workspace view">
        <MobileTab
          active={mobileView === "runs"}
          onClick={() => setMobileView("runs")}
          icon={<ListTree size={16} />}
          label="Runs"
        />
        <MobileTab
          active={mobileView === "timeline"}
          onClick={() => setMobileView("timeline")}
          icon={<Activity size={16} />}
          label="Timeline"
        />
        <MobileTab
          active={mobileView === "inspector"}
          onClick={() => setMobileView("inspector")}
          icon={<PanelRight size={16} />}
          label="Inspector"
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
      <span className="metric-icon">{icon}</span>
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
    <button type="button" className={active ? "active" : ""} onClick={onClick}>
      {icon}
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
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} s`;
  return `${(milliseconds / 60_000).toFixed(1)} min`;
}
