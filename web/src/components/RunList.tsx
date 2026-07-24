import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Virtuoso } from "react-virtuoso";
import type { RunRow, SessionRow } from "../api/client";
import { isStaleRunning, runStatusLabel } from "../runs/status";
import { PanelState } from "./PanelState";

type Props = {
  sessions: SessionRow[];
  runs: RunRow[];
  selectedId: string | null;
  onSelect: (sessionId: string, latestRunId: string | null) => void;
  loading?: boolean;
  error?: string | null;
};

export function RunList({
  sessions,
  runs,
  selectedId,
  onSelect,
  loading = false,
  error = null,
}: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const runsById = useMemo(() => new Map(runs.map((run) => [run.id, run])), [runs]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sessions.filter((session) => {
      if (status !== "all" && session.latest_status !== status) return false;
      if (!needle) return true;
      return [session.id, session.display_title, session.title, session.latest_error]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [query, sessions, status]);

  return (
    <div className="run-navigator">
      <div className="run-filters">
        <label className="search-field">
          <Search size={14} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索任务"
            aria-label="搜索任务"
          />
        </label>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="按状态筛选"
        >
          <option value="all">全部状态</option>
          <option value="running">运行中</option>
          <option value="waiting_approval">等待审批</option>
          <option value="require_human">需要人工处理</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">已中断</option>
          <option value="cancelled">已取消</option>
        </select>
      </div>
      <div className="run-list" data-testid="run-list" data-run-count={visible.length}>
        {loading ? (
          <PanelState kind="loading" title="正在载入任务" detail="读取最近的会话记录" />
        ) : error ? (
          <PanelState kind="error" title="任务列表载入失败" detail={error} />
        ) : !sessions.length ? (
          <PanelState kind="empty" title="暂无任务" detail="从命令行发起任务后会显示在这里" />
        ) : !visible.length ? (
          <PanelState kind="empty" title="没有匹配的任务" detail="调整搜索词或状态筛选" />
        ) : (
          <Virtuoso
            className="run-list-virtual"
            data={visible}
            initialItemCount={Math.min(visible.length, 10)}
            computeItemKey={(_index, session) => session.id}
            itemContent={(_index, session) => {
              const latestRun = session.latest_run_id
                ? runsById.get(session.latest_run_id)
                : undefined;
              const statusValue = session.latest_status || "pending";
              const stale = latestRun ? isStaleRunning(latestRun) : false;
              const title = session.display_title?.trim() || session.title?.trim() || "未命名任务";
              const statusLabel = latestRun
                ? runStatusLabel(latestRun)
                : statusValue === "pending"
                  ? "待运行"
                  : statusValue;
              return (
                <button
                  type="button"
                  key={session.id}
                  className={`run-item ${selectedId === session.id ? "selected" : ""} ${
                    stale ? "stale" : ""
                  }`}
                  onClick={() => onSelect(session.id, session.latest_run_id || null)}
                  data-testid={`session-item-${session.id}`}
                  aria-pressed={selectedId === session.id}
                  aria-label={`${title}，${statusLabel}`}
                >
                  <span className="run-line">
                    <span className={`status-dot ${statusValue}`} aria-hidden="true" />
                    <code>{session.id.slice(0, 10)}</code>
                    <span className={`status-text ${statusValue}`}>{statusLabel}</span>
                  </span>
                  <span className="run-summary" title={title}>{title}</span>
                  <span className="run-meta">
                    {formatTime(session.updated_at)} · {session.run_count ?? 0} 轮
                  </span>
                </button>
              );
            }}
          />
        )}
      </div>
    </div>
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 19) : date.toLocaleString();
}
