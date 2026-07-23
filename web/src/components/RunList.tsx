import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Virtuoso } from "react-virtuoso";
import type { RunRow } from "../api/client";
import { runListSummary } from "../trace/buildTurnTrace";
import { isStaleRunning, runStatusLabel } from "../runs/status";
import { PanelState } from "./PanelState";

type Props = {
  runs: RunRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading?: boolean;
  error?: string | null;
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
    ordered.push({ run, depth: run.depth ?? depth });
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

export function RunList({ runs, selectedId, onSelect, loading = false, error = null }: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return positionRuns(runs).filter(({ run }) => {
      if (status !== "all" && run.status !== status) return false;
      if (!needle) return true;
      const summary = runListSummary({
        userMessage: run.user_summary,
        outputSummary: run.output_summary,
        error: run.error,
        provider: run.provider,
      });
      return [run.id, summary, run.error, run.provider, run.model]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
  }, [query, runs, status]);

  return (
    <div className="run-navigator">
      <div className="run-filters">
        <label className="search-field">
          <Search size={14} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索运行"
            aria-label="搜索运行"
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
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">已中断</option>
          <option value="cancelled">已取消</option>
        </select>
      </div>
      <div
        className="run-list"
        data-testid="run-list"
        data-run-count={visible.length}
      >
        {loading ? (
          <PanelState kind="loading" title="正在载入运行" detail="读取最近的执行记录" />
        ) : error ? (
          <PanelState kind="error" title="运行列表载入失败" detail={error} />
        ) : !runs.length ? (
          <PanelState kind="empty" title="暂无运行" detail="从 CLI 发起任务后会显示在这里" />
        ) : !visible.length ? (
          <PanelState kind="empty" title="没有匹配的运行" detail="调整搜索词或状态筛选" />
        ) : (
          <Virtuoso
            className="run-list-virtual"
            data={visible}
            initialItemCount={Math.min(visible.length, 10)}
            computeItemKey={(_index, item) => item.run.id}
            itemContent={(_index, { run, depth }) => {
              const stale = isStaleRunning(run);
              const summary = runListSummary({
                userMessage: run.user_summary,
                outputSummary: run.output_summary,
                error: run.error,
                provider: run.provider,
              });
              const duration = formatDuration(run.created_at, run.finished_at);
              return (
                <button
                  type="button"
                  key={run.id}
                  className={`run-item ${selectedId === run.id ? "selected" : ""} ${
                    stale ? "stale" : ""
                  }`}
                  onClick={() => onSelect(run.id)}
                  style={{ paddingLeft: `${12 + Math.min(depth, 3) * 18}px` }}
                  data-testid={`run-item-${run.id}`}
                  aria-pressed={selectedId === run.id}
                  aria-label={`${summary}，${runStatusLabel(run)}`}
                >
                  <span className="run-line">
                    <span className={`status-dot ${run.status}`} aria-hidden="true" />
                    <code>{run.id.slice(0, 10)}</code>
                    <span className={`status-text ${run.status}`}>
                      {runStatusLabel(run)}
                    </span>
                  </span>
                  <span className="run-summary" title={summary}>
                    {summary}
                  </span>
                  <span className="run-meta">
                    {run.provider || "-"}
                    {run.model ? `/${run.model}` : ""} · {duration}
                    {typeof run.steps === "number" ? ` · ${run.steps} 步` : ""}
                    {depth > 0 ? " · 子运行" : ""}
                    {run.actor ? ` · ${actorLabel(run.actor)}` : ""}
                    {run.child_count ? ` · ${run.child_count} 个子运行` : ""}
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

function formatDuration(start: string, end?: string | null): string {
  const started = new Date(start).getTime();
  const finished = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(started) || Number.isNaN(finished)) return formatTime(start);
  const ms = Math.max(0, finished - started);
  if (ms < 1000) return `${ms} 毫秒`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} 秒`;
  return `${(ms / 60_000).toFixed(1)} 分钟`;
}

function actorLabel(actor: string): string {
  if (actor === "user") return "用户";
  if (actor === "delegate") return "委派代理";
  return actor;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 19) : date.toLocaleString();
}
