import { useQuery } from "@tanstack/react-query";
import { BarChart3, PieChart, TrendingUp, Trophy } from "lucide-react";

import { procurementApi } from "./api";

const STATUS_LABELS: Record<string, string> = {
  draft: "Agent 读取中",
  collecting: "待上传报价",
  review: "待复核",
  ready: "待比价",
  analyzing: "分析中",
  analyzed: "待审批",
  approval_pending: "等待审批",
  approved: "已批准",
  no_award: "本轮流标",
  cancelled: "已取消",
};

const STATUS_TONES: Record<string, string> = {
  approved: "success",
  review: "warning",
  analyzed: "warning",
  approval_pending: "warning",
  analyzing: "info",
};

export function ReportsCenter() {
  const overviewQuery = useQuery({
    queryKey: ["procurement-insights-overview"],
    queryFn: procurementApi.insightsOverview,
    refetchInterval: 15_000,
  });
  const trendQuery = useQuery({
    queryKey: ["procurement-insights-trend"],
    queryFn: () => procurementApi.insightsTrend(6),
  });
  const rankingQuery = useQuery({
    queryKey: ["procurement-insights-ranking"],
    queryFn: () => procurementApi.insightsSupplierRanking(10),
  });
  const categoriesQuery = useQuery({
    queryKey: ["procurement-insights-categories"],
    queryFn: procurementApi.insightsCategories,
  });
  const evaluationQuery = useQuery({
    queryKey: ["procurement-evaluation"],
    queryFn: procurementApi.evaluation,
  });

  const overview = overviewQuery.data;
  const funnel = overview?.status_funnel || [];
  const savings = overview?.cost_savings;
  const trend = trendQuery.data || [];
  const ranking = rankingQuery.data || [];
  const categories = categoriesQuery.data || [];
  const evaluation = evaluationQuery.data;

  return (
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-accent" />
            统计报表
          </h1>
          <p className="text-xs text-text-muted mt-1">状态漏斗 / 月度趋势 / 供应商中标排行 / 品类分布 / 成本节约率 / AI 评测</p>
        </div>
      </header>
      <div className="proc-reports-body flex flex-col gap-6">
        <div className="proc-report-kpis grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="proc-kpi-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-1.5 transition-all duration-150">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">成本节约率</span>
            <strong className="text-2xl font-bold font-mono text-accent">{savings?.rate != null ? `${(Number(savings.rate) * 100).toFixed(2)}%` : "—"}</strong>
            <small className="text-[11px] text-text-secondary truncate">预算 {savings?.budget_total ?? "—"} → 到货 {savings?.landed_total ?? "—"}（节约 {savings?.savings ?? "—"}）</small>
          </div>
          <div className="proc-kpi-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-1.5 transition-all duration-150">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">已批准任务</span>
            <strong className="text-2xl font-bold font-mono text-text">{overview?.counts.approved_tasks ?? "—"}</strong>
            <small className="text-[11px] text-text-secondary">共 {overview?.counts.tasks ?? "—"} 个任务</small>
          </div>
          <div className="proc-kpi-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-1.5 transition-all duration-150">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">采购订单</span>
            <strong className="text-2xl font-bold font-mono text-text">{overview?.counts.orders ?? "—"}</strong>
            <small className="text-[11px] text-text-secondary">已收货 {overview?.counts.orders_received ?? 0} · 已付款 {overview?.counts.settlements_paid ?? 0}</small>
          </div>
          <div className="proc-kpi-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 rounded-xl p-5 shadow-sm flex flex-col gap-1.5 transition-all duration-150">
            <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">供应商</span>
            <strong className="text-2xl font-bold font-mono text-text">{overview?.counts.suppliers ?? "—"}</strong>
            <small className="text-[11px] text-text-secondary">黑名单 {overview?.counts.suppliers_blacklisted ?? 0} · 逾期订单 {overview?.counts.overdue_orders ?? 0}</small>
          </div>
        </div>

        <div className="proc-reports-grid grid grid-cols-1 md:grid-cols-2 gap-6">
          <section className="proc-report-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-4">
            <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><BarChart3 size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">状态漏斗</h3></div><span className="text-xs text-text-muted">按任务状态分组</span></header>
            {overviewQuery.isPending ? <div className="proc-loading-line h-1 bg-accent/30 animate-pulse rounded" /> : null}
            <div className="proc-funnel flex flex-col gap-2.5">
              {funnel.map((entry) => (
                <div key={entry.status} className={`flex items-center gap-3 text-xs ${STATUS_TONES[entry.status] || ""}`}>
                  <span className="w-24 text-[11px] text-text-secondary truncate flex-shrink-0">{STATUS_LABELS[entry.status] || entry.status}</span>
                  <div className="flex-1 bg-surface-subtle rounded-full h-2 overflow-hidden border border-border/40">
                    <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${funnel.length ? (entry.count / Math.max(1, funnel[0].count)) * 100 : 0}%` }} />
                  </div>
                  <strong className="font-mono text-text w-8 text-right font-bold">{entry.count}</strong>
                </div>
              ))}
              {!funnel.length && !overviewQuery.isPending ? <p className="proc-muted py-6 text-center text-xs text-text-muted">暂无任务数据</p> : null}
            </div>
          </section>

          <section className="proc-report-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-4">
            <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><TrendingUp size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">月度趋势</h3></div><span className="text-xs text-text-muted">近 6 个月任务数与批准金额</span></header>
            <div className="proc-trend-bars flex items-end justify-between gap-3 h-40 pt-4 pb-2 px-3 bg-surface-subtle/50 rounded-lg border border-border/40">
              {trend.map((row) => (
                <div className="flex-1 flex flex-col items-center h-full justify-end gap-1.5" key={row.month} title={`${row.month}：${row.task_count} 个任务，批准 ${row.approved_amount}`}>
                  <div className="w-full max-w-[32px] bg-accent/20 hover:bg-accent/40 rounded-t-md transition-all flex items-end justify-center h-full">
                    <i className="block w-full bg-accent rounded-t-md transition-all" style={{ height: `${Math.min(100, Math.max(4, row.task_count * 18))}%` }} />
                  </div>
                  <small className="text-[10px] text-text-muted font-mono">{row.month.slice(5)}</small>
                </div>
              ))}
              {!trend.length ? <p className="proc-muted w-full text-center text-xs text-text-muted self-center">暂无趋势数据</p> : null}
            </div>
            <p className="proc-eval-note text-[11px] text-text-muted leading-relaxed">批准金额 = 比价快照中批准报价的到货总价（基准币种，BigDecimal 口径）。</p>
          </section>

          <section className="proc-report-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-4">
            <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><Trophy size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">供应商中标排行</h3></div><span className="text-xs text-text-muted">按中标次数 / 中标率 + 绩效分</span></header>
            <div className="proc-ranking flex flex-col divide-y divide-border/40">
              {ranking.map((row, index) => (
                <div className="flex items-center justify-between gap-3 py-2.5 text-xs" key={row.id}>
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="proc-rank-no w-5 h-5 rounded-full bg-surface-subtle text-text-muted text-[11px] font-mono font-bold flex items-center justify-center flex-shrink-0">{index + 1}</span>
                    <strong className="font-semibold text-text truncate">{row.name}</strong>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <small className="text-text-muted text-[11px]">{row.win_count}/{row.quote_count} 中标 · 绩效 {Number(row.performance.score).toFixed(1)}</small>
                    <i className={`not-italic text-[10px] font-medium px-2 py-0.5 rounded-full border ${row.performance.level === "黑名单" ? "danger bg-danger-soft text-danger border-danger/30" : "bg-accent-soft text-accent border-accent/30"}`}>{row.performance.level}</i>
                  </div>
                </div>
              ))}
              {!ranking.length ? <p className="proc-muted py-6 text-center text-xs text-text-muted">暂无中标记录（供应商档案按名称关联报价）</p> : null}
            </div>
          </section>

          <section className="proc-report-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-4">
            <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><PieChart size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">品类分布</h3></div><span className="text-xs text-text-muted">按任务品类分组</span></header>
            <div className="proc-categories flex flex-col gap-2.5">
              {categories.map((entry) => (
                <div className="flex items-center gap-3 text-xs" key={entry.category}>
                  <span className="w-24 text-[11px] text-text-secondary truncate flex-shrink-0">{entry.category}</span>
                  <div className="flex-1 bg-surface-subtle rounded-full h-2 overflow-hidden border border-border/40">
                    <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${categories.length ? (entry.count / Math.max(1, categories[0].count)) * 100 : 0}%` }} />
                  </div>
                  <strong className="font-mono text-text w-8 text-right font-bold">{entry.count}</strong>
                </div>
              ))}
              {!categories.length ? <p className="proc-muted py-6 text-center text-xs text-text-muted">暂无品类数据</p> : null}
            </div>
          </section>
        </div>

        <section className="proc-report-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-4">
          <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><BarChart3 size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">AI 评测指标</h3></div><span className="text-xs text-text-muted">冻结评测资源（frozen-evaluation.json，不变）</span></header>
          {evaluationQuery.isPending ? <div className="proc-loading-line h-1 bg-accent/30 animate-pulse rounded" /> : null}
          {evaluationQuery.isError ? (
            <p className="proc-muted text-xs text-danger" role="alert">评测数据加载失败</p>
          ) : evaluation ? (
            <EvaluationBand evaluation={evaluation} />
          ) : null}
        </section>
      </div>
    </div>
  );
}

function EvaluationBand({ evaluation }: { evaluation: Record<string, unknown> }) {
  const metrics = (evaluation.metrics || evaluation.result || evaluation) as Record<string, unknown>;
  const keys = ["field_extraction", "post_review_fields", "item_matching", "cost_calculation", "hard_constraint_miss"];
  return (
    <div className="proc-eval-proof grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
      {keys.map((key) => {
        const metric = metrics[key] as Record<string, unknown> | undefined;
        if (!metric) return null;
        const accuracy = Number(metric.accuracy ?? metric.miss_rate ?? 0);
        const label = ({ field_extraction: "字段抽取", post_review_fields: "复核后字段", item_matching: "物料匹配", cost_calculation: "成本计算", hard_constraint_miss: "硬约束漏检率" } as Record<string, string>)[key];
        return (
          <div className="p-3 rounded-lg bg-surface-subtle/60 border border-border/40 flex flex-col gap-1.5 text-xs" key={key}>
            <span className="text-[11px] text-text-muted">{label}</span>
            <div className="bg-surface rounded-full h-1.5 overflow-hidden border border-border/40">
              <i className="block bg-accent h-full rounded-full" style={{ width: `${Math.min(100, accuracy * 100)}%` }} />
            </div>
            <strong className="font-mono text-sm font-bold text-text mt-0.5">{key === "hard_constraint_miss" ? `${(accuracy * 100).toFixed(2)}%` : `${(accuracy * 100).toFixed(2)}%`}</strong>
          </div>
        );
      })}
    </div>
  );
}
