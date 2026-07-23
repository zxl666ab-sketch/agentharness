import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { EventRow, RunRow } from "../api/client";
import { extractUserMessageFromEvents, runListSummary } from "../trace/buildTurnTrace";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  waiting_approval: "Approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

const STALE_RUNNING_MS = 30 * 60 * 1000;

type Props = {
  runs: RunRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Optional map of run_id -> first events/user message for list summaries */
  userMessageByRunId?: Record<string, string>;
};

type PositionedRun = { run: RunRow; depth: number };

function positionRuns(runs: RunRow[]): PositionedRun[] {
  const byParent = new Map<string, RunRow[]>();
  for (const run of runs) {
    const parent = run.parent_run_id || "root";
    byParent.set(parent, [...(byParent.get(parent) || []), run]);
  }
  const ordered: PositionedRun[] = [];
  const seen = new Set<string>();
  const visit = (run: RunRow, depth: number) => {
    if (seen.has(run.id)) return;
    seen.add(run.id);
    ordered.push({ run, depth });
    for (const child of byParent.get(run.id) || []) visit(child, depth + 1);
  };
  for (const run of runs) {
    if (!run.parent_run_id || !runs.some((item) => item.id === run.parent_run_id)) {
      visit(run, 0);
    }
  }
  for (const run of runs) visit(run, run.parent_run_id ? 1 : 0);
  return ordered;
}

function isStaleRunning(run: RunRow): boolean {
  if (run.status !== "running" && run.status !== "pending") return false;
  const updated = new Date(run.updated_at || run.created_at).getTime();
  if (Number.isNaN(updated)) return false;
  return Date.now() - updated > STALE_RUNNING_MS;
}

export function RunList({ runs, selectedId, onSelect, userMessageByRunId = {} }: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return positionRuns(runs).filter(({ run }) => {
      if (status !== "all" && run.status !== status) return false;
      if (!needle) return true;
      const summary = runListSummary({
        userMessage: userMessageByRunId[run.id],
        outputSummary: run.output_summary,
        error: run.error,
        provider: run.provider,
      });
      return [run.id, summary, run.error, run.provider, run.model]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [query, runs, status, userMessageByRunId]);

  return (
    <div className="run-navigator">
      <div className="run-filters">
        <label className="search-field">
          <Search size={14} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search runs"
            aria-label="Search runs"
          />
        </label>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="Filter by status"
        >
          <option value="all">All status</option>
          <option value="running">Running</option>
          <option value="waiting_approval">Approval</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="interrupted">Interrupted</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      <div className="run-list" data-testid="run-list">
        {!visible.length && <div className="empty-state">No matching runs</div>}
        {visible.map(({ run, depth }) => {
          const stale = isStaleRunning(run);
          const summary = runListSummary({
            userMessage: userMessageByRunId[run.id],
            outputSummary: run.output_summary,
            error: run.error,
            provider: run.provider,
          });
          const duration = formatDuration(run.created_at, run.finished_at);
          return (
            <button
              type="button"
              key={run.id}
              className={`run-item ${selectedId === run.id ? "selected" : ""} ${stale ? "stale" : ""}`}
              onClick={() => onSelect(run.id)}
              style={{ paddingLeft: `${12 + Math.min(depth, 3) * 18}px` }}
              data-testid={`run-item-${run.id}`}
            >
              <span className="run-line">
                <span className={`status-dot ${run.status}`} aria-hidden="true" />
                <code>{run.id.slice(0, 10)}</code>
                <span className={`status-text ${run.status}`}>
                  {STATUS_LABEL[run.status] || run.status}
                  {stale ? " · stale" : ""}
                </span>
              </span>
              <span className="run-summary" title={summary}>
                {summary}
              </span>
              <span className="run-meta">
                {run.provider || "-"}
                {run.model ? `/${run.model}` : ""} · {duration}
                {typeof run.steps === "number" ? ` · ${run.steps} steps` : ""}
                {depth > 0 ? " · child" : ""}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Exported for tests — derive user message from a small event sample. */
export function userMessageFromEvents(events: EventRow[]): string | null {
  return extractUserMessageFromEvents(events);
}

function formatDuration(start: string, end?: string | null): string {
  const started = new Date(start).getTime();
  const finished = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(started) || Number.isNaN(finished)) return formatTime(start);
  const ms = Math.max(0, finished - started);
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 19) : date.toLocaleString();
}
