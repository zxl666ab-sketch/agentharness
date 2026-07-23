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
import type { EventRow } from "../api/client";
import { categorizeEvent, type EventGroup } from "../events/categories";

type Props = {
  events: EventRow[];
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

function preview(event: EventRow): string {
  const payload = event.payload || {};
  if (typeof payload.text === "string") return payload.text.slice(0, 180);
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

function EventIcon({ group, type }: { group: EventGroup; type: string }) {
  if (type === "checkpoint") return <Database size={15} />;
  if (type === "run_completed") return <CheckCircle2 size={15} />;
  if (group === "model") return <Bot size={15} />;
  if (group === "tool") return <Wrench size={15} />;
  if (group === "approval") return <ShieldCheck size={15} />;
  if (group === "error") return <AlertTriangle size={15} />;
  return <CircleDot size={15} />;
}

export function Timeline({ events, selectedId, onSelect }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const visible = useMemo(
    () =>
      filter === "all"
        ? events
        : events.filter((event) => categorizeEvent(event.type).group === filter),
    [events, filter]
  );

  return (
    <div className="timeline-shell">
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
        <span className="event-count">{visible.length} events</span>
      </div>
      {!visible.length ? (
        <div className="empty-state">No events in this view</div>
      ) : (
        <Virtuoso
          className="timeline-list"
          data={visible}
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
                  <span className="event-preview">{preview(event)}</span>
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

function formatEventTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(11, 23);
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
