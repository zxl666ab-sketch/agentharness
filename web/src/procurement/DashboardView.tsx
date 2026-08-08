import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Clock3,
  Database,
  DollarSign,
  Download,
  FlaskConical,
  TrendingUp,
  Zap,
} from "lucide-react";
import { useMemo } from "react";

import { api } from "../api/client";

function formatCost(value: number | null) {
  if (value == null) return "未知";
  return `$${value.toFixed(4)}`;
}

function formatDuration(ms: number | null) {
  if (ms == null) return "—";
  if (ms < 1_000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} 分钟`;
}

function formatTokens(value: number) {
  return value.toLocaleString("zh-CN");
}

export function buildCsv(rows: Array<Record<string, string | number | boolean | null>>) {
  const header = Object.keys(rows[0] || {}).join(",");
  return [
    header,
    ...rows.map((row) =>
      Object.values(row)
        .map((value) => {
          const text = String(value);
          // CSV formula-injection guard: skip leading whitespace (space, tab,
          // newline, CR) and still prefix cells starting with = + - @.
          const guarded = /^\s*[=+\-@]/.test(text) ? `'${text}` : text;
          return `"${guarded.replaceAll('"', '""')}"`;
        })
        .join(",")
    ),
  ].join("\n");
}

function downloadCsv(rows: Array<Record<string, string | number | boolean | null>>, filename: string) {
  const blob = new Blob([buildCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

const STATUS_LABELS: Record<string, string> = {
  pending: "排队中",
  running: "运行中",
  waiting_approval: "等待审批",
  require_human: "待人工处理",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
  interrupted: "已中断",
  budget_stopped: "预算停",
};

export function DashboardView() {
  const runsQuery = useQuery({ queryKey: ["proc-runs"], queryFn: () => api.runs({ limit: 500 }) });
  const metricsQuery = useQuery({
    queryKey: ["proc-metrics-summary"],
    queryFn: api.metricsSummary,
    staleTime: 30_000,
  });

  const rows = useMemo(() => {
    return (runsQuery.data?.items || []).map((run) => {
      let usage: Record<string, unknown> = {};
      try {
        usage = run.usage_json ? (JSON.parse(run.usage_json) as Record<string, unknown>) : {};
      } catch {
        // malformed usage is ignored
      }
      const cost = usage.estimated_cost_usd;
      const costKnown = typeof cost === "number" && usage.cost_status !== "unknown";
      return {
        run_id: run.id,
        status: run.status,
        status_label: STATUS_LABELS[run.status] || run.status,
        model: run.model || "—",
        created_at: run.created_at,
        total_tokens: Number(usage.total_tokens || 0),
        cost_usd: costKnown ? (cost as number) : 0,
        cost_known: costKnown,
      };
    });
  }, [runsQuery.data]);

  const metrics = metricsQuery.data;

  return (
    <div className="proc-dashboard">
      <header className="proc-dashboard-head">
        <div><BarChart3 size={18} /><h1>运营仪表盘</h1></div>
        <button
          type="button"
          className="proc-button secondary"
          disabled={!rows.length}
          onClick={() => downloadCsv(rows, "runs-summary.csv")}
        >
          <Download size={15} />导出 CSV
        </button>
      </header>

      <section className="proc-dashboard-cards">
        <div className="proc-dash-card"><span><Database size={16} />运行总数</span><strong>{metrics?.runs ?? "—"}</strong></div>
        <div className="proc-dash-card"><span><DollarSign size={16} />估算成本</span><strong>{metrics ? (metrics.cost_unknown_runs > 0 ? "未知" : formatCost(metrics.estimated_cost_usd)) : "—"}</strong></div>
        <div className="proc-dash-card"><span><Zap size={16} />Token 总量</span><strong>{metrics ? formatTokens(metrics.tokens.total) : "—"}</strong></div>
        <div className="proc-dash-card"><span><FlaskConical size={16} />模型回合</span><strong>{metrics?.model_turns ?? "—"}</strong></div>
        <div className="proc-dash-card"><span><TrendingUp size={16} />缓存命中率</span><strong>{metrics?.cache_hit_rate != null ? `${(metrics.cache_hit_rate * 100).toFixed(1)}%` : "—"}</strong></div>
        <div className="proc-dash-card"><span><Clock3 size={16} />平均耗时</span><strong>{formatDuration(metrics?.avg_duration_ms ?? null)}</strong></div>
      </section>

      {metrics ? (
        <section className="proc-dashboard-groups">
          <div>
            <h3>按状态</h3>
            <ul>
              {Object.entries(metrics.by_status).map(([status, count]) => (
                <li key={status}><span>{STATUS_LABELS[status] || status}</span><strong>{count}</strong></li>
              ))}
            </ul>
          </div>
          <div>
            <h3>按模型</h3>
            <ul>
              {Object.entries(metrics.by_model).map(([model, count]) => (
                <li key={model}><span>{model}</span><strong>{count}</strong></li>
              ))}
            </ul>
          </div>
        </section>
      ) : null}

      {runsQuery.isError ? (
        <p className="proc-inline-error" role="alert">运行列表加载失败</p>
      ) : null}

      <div className="proc-dashboard-table-wrap">
        <table className="proc-dashboard-table">
          <thead>
            <tr>
              <th>运行 ID</th><th>状态</th><th>模型</th><th>创建时间</th><th>Token</th><th>成本（USD）</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.run_id}>
                <td><code>{row.run_id.slice(0, 12)}</code></td>
                <td><span className={`proc-run-status ${row.status}`}>{row.status_label}</span></td>
                <td>{row.model}</td>
                <td>{new Date(row.created_at).toLocaleString("zh-CN")}</td>
                <td>{formatTokens(row.total_tokens)}</td>
                <td>{row.cost_known ? formatCost(row.cost_usd) : "未知"}</td>
              </tr>
            ))}
            {!rows.length && !runsQuery.isPending ? (
              <tr><td colSpan={6} className="proc-dashboard-empty">暂无运行记录</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

