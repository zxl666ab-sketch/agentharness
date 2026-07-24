import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDot,
  Database,
  GitBranch,
  MessageSquareText,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { Virtuoso } from "react-virtuoso";
import type { EventRow, MessageRow } from "../api/client";
import { categorizeEvent, type EventGroup } from "../events/categories";
import { useSseEventsForRun } from "../store/useSse";
import {
  buildTurnTrace,
  isTerminalStatus,
  type TraceRow,
  type TraceViewMode,
} from "../trace/buildTurnTrace";
import { formatPayloadPreview } from "../trace/formatters";
import { PanelState } from "./PanelState";

type Props = {
  runId: string | null;
  events: EventRow[];
  messages?: MessageRow[];
  selectedId: string | null;
  onSelect: (event: EventRow) => void;
  onSelectRun?: (runId: string) => void;
  runStatus?: string | null;
  loading?: boolean;
  error?: string | null;
};

type Filter = "all" | EventGroup;

const FILTERS: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "model", label: "模型" },
  { id: "verification", label: "验证" },
  { id: "tool", label: "工具" },
  { id: "approval", label: "审批" },
  { id: "error", label: "错误" },
];

function EventIcon({ kind, group, type }: { kind?: string; group: EventGroup; type: string }) {
  if (kind === "checkpoint" || type === "checkpoint") return <Database size={15} aria-hidden="true" />;
  if (type === "run_completed" || kind === "run") return <CheckCircle2 size={15} aria-hidden="true" />;
  if (kind === "tool" || group === "tool") return <Wrench size={15} aria-hidden="true" />;
  if (kind === "result") return <CheckCircle2 size={15} aria-hidden="true" />;
  if (kind === "child_run") return <GitBranch size={15} aria-hidden="true" />;
  if (kind === "approval" || group === "approval") return <ShieldCheck size={15} aria-hidden="true" />;
  if (kind === "verification" || group === "verification") return <ShieldCheck size={15} aria-hidden="true" />;
  if (kind === "error" || group === "error") return <AlertTriangle size={15} aria-hidden="true" />;
  if (kind === "model_output") return <MessageSquareText size={15} aria-hidden="true" />;
  if (kind === "turn" || group === "model") return <Bot size={15} aria-hidden="true" />;
  return <CircleDot size={15} aria-hidden="true" />;
}

function filterTraceRows(rows: TraceRow[], filter: Filter): TraceRow[] {
  if (filter === "all") return rows;
  return rows.filter((row) => {
    if (filter === "model") return row.kind === "turn" || categorizeEvent(row.event.type).group === "model";
    if (filter === "verification") return row.kind === "verification" || categorizeEvent(row.event.type).group === "verification";
    if (filter === "tool") {
      return (
        row.kind === "tool" ||
        row.kind === "result" ||
        row.kind === "child_run" ||
        categorizeEvent(row.event.type).group === "tool"
      );
    }
    if (filter === "approval") return row.kind === "approval";
    if (filter === "error") return row.kind === "error" || row.isError;
    return true;
  });
}

export function Timeline({
  runId,
  events,
  messages = [],
  selectedId,
  onSelect,
  onSelectRun,
  runStatus = null,
  loading = false,
  error = null,
}: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [mode, setMode] = useState<TraceViewMode>("turns");
  const [showCheckpoint, setShowCheckpoint] = useState(false);
  const liveEvents = useSseEventsForRun(runId);
  const allEvents = useMemo(() => {
    const merged = new Map<number, EventRow>();
    for (const event of events) merged.set(event.global_seq, event);
    for (const event of liveEvents) merged.set(event.global_seq, event);
    return [...merged.values()].sort((left, right) => left.global_seq - right.global_seq);
  }, [events, liveEvents]);

  const traceRows = useMemo(
    () =>
      buildTurnTrace(allEvents, {
        messages,
        hideCheckpoint: !showCheckpoint,
        hideSpanNoise: true,
      }),
    [allEvents, messages, showCheckpoint]
  );

  const rawVisible = useMemo(
    () =>
      filter === "all"
        ? allEvents.filter((event) => showCheckpoint || event.type !== "checkpoint")
        : allEvents.filter(
            (event) =>
              (showCheckpoint || event.type !== "checkpoint") &&
              categorizeEvent(event.type).group === filter
          ),
    [allEvents, filter, showCheckpoint]
  );

  const visibleTrace = useMemo(() => filterTraceRows(traceRows, filter), [traceRows, filter]);

  if (!runId) {
    return (
      <div className="timeline-shell">
        <PanelState kind="empty" title="请选择运行" detail="选择左侧记录以查看执行追踪" />
      </div>
    );
  }
  if (loading && !allEvents.length) {
    return (
      <div className="timeline-shell">
        <PanelState kind="loading" title="正在载入追踪" detail="合并历史事件与实时流" />
      </div>
    );
  }
  if (error && !allEvents.length) {
    return (
      <div className="timeline-shell">
        <PanelState kind="error" title="追踪载入失败" detail={error} />
      </div>
    );
  }

  return (
    <div className="timeline-shell">
      {error && <PanelState kind="error" title="实时追踪不完整" detail={error} compact />}
      {runStatus && !isTerminalStatus(runStatus) && (
        <PanelState kind="streaming" title="正在流式接收" detail={statusLabel(runStatus)} compact />
      )}
      {runStatus && isTerminalStatus(runStatus) && !error && (
        <PanelState kind="done" title="运行已结束" detail={statusLabel(runStatus)} compact />
      )}
      <div className="segmented" aria-label="追踪模式">
        <button
          type="button"
          className={mode === "turns" ? "active" : ""}
          onClick={() => setMode("turns")}
          data-testid="trace-mode-turns"
          aria-pressed={mode === "turns"}
        >
          轮次
        </button>
        <button
          type="button"
          className={mode === "raw" ? "active" : ""}
          onClick={() => setMode("raw")}
          data-testid="trace-mode-raw"
          aria-pressed={mode === "raw"}
        >
          调试
        </button>
        <label className="timeline-toggle">
          <input
            type="checkbox"
            checked={showCheckpoint}
            onChange={(event) => setShowCheckpoint(event.target.checked)}
          />
          检查点
        </label>
        <span className="event-count">
          {mode === "turns" ? `${visibleTrace.length} 行` : `${rawVisible.length} 个事件`}
        </span>
      </div>
      <div className="segmented" aria-label="筛选追踪">
        {FILTERS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={filter === item.id ? "active" : ""}
            onClick={() => setFilter(item.id)}
            aria-pressed={filter === item.id}
          >
            {item.label}
          </button>
        ))}
      </div>
      {mode === "turns" ? (
        !visibleTrace.length ? (
          <PanelState kind="empty" title="此视图没有追踪行" detail="调整筛选或等待新事件" />
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
                  onClick={() => {
                    if (row.targetRunId) onSelectRun?.(row.targetRunId);
                    onSelect(row.event);
                  }}
                  style={{ paddingLeft: `${Math.min(row.depth || 0, 6) * 10}px` }}
                  data-testid="timeline-row"
                  data-trace-kind={row.kind}
                  data-run-seq={row.event.run_seq}
                  data-global-seq={row.event.global_seq}
                  aria-pressed={selectedId === row.event.event_id}
                  aria-label={`${row.label}，${row.preview}`}
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
                      {row.actor && <code>{row.actor}</code>}
                      {row.status && <code>{row.status}</code>}
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
        <PanelState kind="empty" title="此视图没有事件" detail="调整筛选或等待新事件" />
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
                data-run-seq={event.run_seq}
                data-global-seq={event.global_seq}
                aria-pressed={selectedId === event.event_id}
                aria-label={`${category.label}，${formatPayloadPreview(event.payload || {})}`}
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
  return formatPayloadPreview(event.payload || {});
}

function statusLabel(status: string): string {
  return {
    pending: "等待启动",
    running: "运行中",
    waiting_approval: "等待审批",
    require_human: "需要人工处理",
    completed: "已完成",
    failed: "失败",
    interrupted: "已中断",
    cancelled: "已取消",
  }[status] || status;
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
