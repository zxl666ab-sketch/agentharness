import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDot,
  Database,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { Virtuoso } from "react-virtuoso";
import type { EventRow, MessageRow } from "../api/client";
import { categorizeEvent, type EventGroup } from "../events/categories";
import {
  buildTurnTrace,
  type TraceRow,
  type TraceViewMode,
} from "../trace/buildTurnTrace";

type Props = {
  events: EventRow[];
  messages?: MessageRow[];
  selectedId: string | null;
  onSelect: (event: EventRow) => void;
};

type Filter = "all" | EventGroup;

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "All" },
  { id: "model", label: "Model" },
  { id: "tool", label: "Tools" },
  { id: "approval", label: "Approvals" },
  { id: "error", label: "Errors" },
];

function EventIcon({ kind, group, type }: { kind?: string; group: EventGroup; type: string }) {
  if (kind === "checkpoint" || type === "checkpoint") return <Database size={15} />;
  if (type === "run_completed" || kind === "run") return <CheckCircle2 size={15} />;
  if (kind === "tool" || group === "tool") return <Wrench size={15} />;
  if (kind === "approval" || group === "approval") return <ShieldCheck size={15} />;
  if (kind === "error" || group === "error") return <AlertTriangle size={15} />;
  if (kind === "turn" || group === "model") return <Bot size={15} />;
  return <CircleDot size={15} />;
}

function filterTraceRows(rows: TraceRow[], filter: Filter): TraceRow[] {
  if (filter === "all") return rows;
  return rows.filter((row) => {
    if (filter === "model") return row.kind === "turn" || categorizeEvent(row.event.type).group === "model";
    if (filter === "tool") return row.kind === "tool" || categorizeEvent(row.event.type).group === "tool";
    if (filter === "approval") return row.kind === "approval";
    if (filter === "error") return row.kind === "error" || row.isError;
    return true;
  });
}

export function Timeline({ events, messages = [], selectedId, onSelect }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [mode, setMode] = useState<TraceViewMode>("turns");
  const [showCheckpoint, setShowCheckpoint] = useState(false);

  const traceRows = useMemo(
    () =>
      buildTurnTrace(events, {
        messages,
        hideCheckpoint: !showCheckpoint,
        hideSpanNoise: true,
      }),
    [events, messages, showCheckpoint]
  );

  const rawVisible = useMemo(
    () =>
      filter === "all"
        ? events.filter((event) => showCheckpoint || event.type !== "checkpoint")
        : events.filter(
            (event) =>
              (showCheckpoint || event.type !== "checkpoint") &&
              categorizeEvent(event.type).group === filter
          ),
    [events, filter, showCheckpoint]
  );

  const visibleTrace = useMemo(() => filterTraceRows(traceRows, filter), [traceRows, filter]);

  return (
    <div className="timeline-shell">
      <div className="segmented" aria-label="Timeline mode">
        <button
          type="button"
          className={mode === "turns" ? "active" : ""}
          onClick={() => setMode("turns")}
          data-testid="trace-mode-turns"
        >
          Turns
        </button>
        <button
          type="button"
          className={mode === "raw" ? "active" : ""}
          onClick={() => setMode("raw")}
          data-testid="trace-mode-raw"
        >
          Debug
        </button>
        <label className="timeline-toggle">
          <input
            type="checkbox"
            checked={showCheckpoint}
            onChange={(event) => setShowCheckpoint(event.target.checked)}
          />
          checkpoints
        </label>
        <span className="event-count">
          {mode === "turns" ? `${visibleTrace.length} steps` : `${rawVisible.length} events`}
        </span>
      </div>
      <div className="segmented" aria-label="Filter timeline">
        {FILTERS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={filter === item.id ? "active" : ""}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {mode === "turns" ? (
        !visibleTrace.length ? (
          <div className="empty-state">No steps in this view</div>
        ) : (
          <Virtuoso
            className="timeline-list"
            data={visibleTrace}
            itemContent={(_index, row) => {
              const category = categorizeEvent(row.event.type);
              return (
                <button
                  type="button"
                  className={`timeline-row ${row.kind} ${category.group} ${
                    selectedId === row.event.event_id ? "selected" : ""
                  } ${row.isError ? "error" : ""}`}
                  onClick={() => onSelect(row.event)}
                  data-testid="timeline-row"
                  data-trace-kind={row.kind}
                >
                  <span className="timeline-rail" aria-hidden="true">
                    <span className="event-icon">
                      <EventIcon kind={row.kind} group={category.group} type={row.event.type} />
                    </span>
                  </span>
                  <span className="event-main">
                    <span className="event-title">
                      {row.label}
                      <code>{row.event.type}</code>
                      {row.durationMs != null && (
                        <code>{Math.round(row.durationMs)} ms</code>
                      )}
                    </span>
                    <span className="event-preview">{row.preview}</span>
                    {row.argsSummary && row.kind === "tool" && (
                      <span className="event-args" data-testid="tool-args-summary">
                        {row.argsSummary}
                      </span>
                    )}
                  </span>
                  <span className="event-time">
                    {formatEventTime(row.timestamp)}
                    <code>#{row.event.run_seq}</code>
                  </span>
                </button>
              );
            }}
          />
        )
      ) : !rawVisible.length ? (
        <div className="empty-state">No events in this view</div>
      ) : (
        <Virtuoso
          className="timeline-list"
          data={rawVisible}
          itemContent={(_index, event) => {
            const category = categorizeEvent(event.type);
            return (
              <button
                type="button"
                className={`timeline-row ${category.group} ${
                  selectedId === event.event_id ? "selected" : ""
                }`}
                onClick={() => onSelect(event)}
                data-testid="timeline-row"
              >
                <span className="timeline-rail" aria-hidden="true">
                  <span className="event-icon">
                    <EventIcon group={category.group} type={event.type} />
                  </span>
                </span>
                <span className="event-main">
                  <span className="event-title">
                    {category.label}
                    <code>{event.type}</code>
                  </span>
                  <span className="event-preview">{rawPreview(event)}</span>
                </span>
                <span className="event-time">
                  {formatEventTime(event.timestamp)}
                  <code>#{event.run_seq}</code>
                </span>
              </button>
            );
          }}
        />
      )}
    </div>
  );
}

function rawPreview(event: EventRow): string {
  const payload = event.payload || {};
  if (typeof payload.text === "string") return payload.text.slice(0, 180);
  if (typeof payload.arguments_summary === "string") return payload.arguments_summary;
  if (typeof payload.name === "string") return String(payload.name);
  if (typeof payload.tool === "string") return String(payload.tool);
  if (typeof payload.error === "string") return String(payload.error).slice(0, 180);
  if (typeof payload.status === "string") return String(payload.status);
  if (typeof payload.phase === "string") {
    return `phase=${payload.phase} · step=${String(payload.step ?? "-")}`;
  }
  const compact = JSON.stringify(payload);
  return compact === "{}" ? "No payload" : compact.slice(0, 180);
}

function formatEventTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(11, 23);
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
