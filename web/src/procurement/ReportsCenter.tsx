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
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>统计报表</h1>
          <p>状态漏斗 / 月度趋势 / 供应商中标排行 / 品类分布 / 成本节约率 / AI 评测</p>
        </div>
      </header>
      <div className="proc-reports-body">
        <div className="proc-report-kpis">
          <div className="proc-kpi-card">
            <span>成本节约率</span>
            <strong>{savings?.rate != null ? `${(Number(savings.rate) * 100).toFixed(2)}%` : "—"}</strong>
            <small>预算 {savings?.budget_total ?? "—"} → 到货 {savings?.landed_total ?? "—"}（节约 {savings?.savings ?? "—"}）</small>
          </div>
          <div className="proc-kpi-card">
            <span>已批准任务</span>
            <strong>{overview?.counts.approved_tasks ?? "—"}</strong>
            <small>共 {overview?.counts.tasks ?? "—"} 个任务</small>
          </div>
          <div className="proc-kpi-card">
            <span>采购订单</span>
            <strong>{overview?.counts.orders ?? "—"}</strong>
            <small>已收货 {overview?.counts.orders_received ?? 0} · 已付款 {overview?.counts.settlements_paid ?? 0}</small>
          </div>
          <div className="proc-kpi-card">
            <span>供应商</span>
            <strong>{overview?.counts.suppliers ?? "—"}</strong>
            <small>黑名单 {overview?.counts.suppliers_blacklisted ?? 0} · 逾期订单 {overview?.counts.overdue_orders ?? 0}</small>
          </div>
        </div>

        <div className="proc-reports-grid">
          <section className="proc-report-section">
            <header><div><BarChart3 size={15} /><h3>状态漏斗</h3></div><span>按任务状态分组</span></header>
            {overviewQuery.isPending ? <div className="proc-loading-line" /> : null}
            <div className="proc-funnel">
              {funnel.map((entry) => (
                <div key={entry.status} className={STATUS_TONES[entry.status] || ""}>
                  <span>{STATUS_LABELS[entry.status] || entry.status}</span>
                  <i style={{ width: `${funnel.length ? (entry.count / Math.max(1, funnel[0].count)) * 100 : 0}%` }} />
                  <strong>{entry.count}</strong>
                </div>
              ))}
              {!funnel.length && !overviewQuery.isPending ? <p className="proc-muted">暂无任务数据</p> : null}
            </div>
          </section>

          <section className="proc-report-section">
            <header><div><TrendingUp size={15} /><h3>月度趋势</h3></div><span>近 6 个月任务数与批准金额</span></header>
            <div className="proc-trend-bars">
              {trend.map((row) => (
                <div key={row.month} title={`${row.month}：${row.task_count} 个任务，批准 ${row.approved_amount}`}>
                  <i style={{ height: `${Math.min(100, Math.max(4, row.task_count * 18))}%` }} />
                  <small>{row.month.slice(5)}</small>
                </div>
              ))}
              {!trend.length ? <p className="proc-muted">暂无趋势数据</p> : null}
            </div>
            <p className="proc-eval-note">批准金额 = 比价快照中批准报价的到货总价（基准币种，BigDecimal 口径）。</p>
          </section>

          <section className="proc-report-section">
            <header><div><Trophy size={15} /><h3>供应商中标排行</h3></div><span>按中标次数 / 中标率 + 绩效分</span></header>
            <div className="proc-ranking">
              {ranking.map((row, index) => (
                <div key={row.id}>
                  <span className="proc-rank-no">{index + 1}</span>
                  <strong>{row.name}</strong>
                  <small>{row.win_count}/{row.quote_count} 中标 · 绩效 {Number(row.performance.score).toFixed(1)}</small>
                  <i className={row.performance.level === "黑名单" ? "danger" : ""}>{row.performance.level}</i>
                </div>
              ))}
              {!ranking.length ? <p className="proc-muted">暂无中标记录（供应商档案按名称关联报价）</p> : null}
            </div>
          </section>

          <section className="proc-report-section">
            <header><div><PieChart size={15} /><h3>品类分布</h3></div><span>按任务品类分组</span></header>
            <div className="proc-categories">
              {categories.map((entry) => (
                <div key={entry.category}>
                  <span>{entry.category}</span>
                  <i style={{ width: `${categories.length ? (entry.count / Math.max(1, categories[0].count)) * 100 : 0}%` }} />
                  <strong>{entry.count}</strong>
                </div>
              ))}
              {!categories.length ? <p className="proc-muted">暂无品类数据</p> : null}
            </div>
          </section>
        </div>

        <section className="proc-report-section">
          <header><div><BarChart3 size={15} /><h3>AI 评测指标</h3></div><span>冻结评测资源（frozen-evaluation.json，不变）</span></header>
          {evaluationQuery.isPending ? <div className="proc-loading-line" /> : null}
          {evaluationQuery.isError ? (
            <p className="proc-muted" role="alert">评测数据加载失败</p>
          ) : evaluation ? (
            <EvaluationBand evaluation={evaluation} />
          ) : null}
        </section>
      </div>
    </section>
  );
}

function EvaluationBand({ evaluation }: { evaluation: Record<string, unknown> }) {
  const metrics = (evaluation.metrics || evaluation.result || evaluation) as Record<string, unknown>;
  const keys = ["field_extraction", "post_review_fields", "item_matching", "cost_calculation", "hard_constraint_miss"];
  return (
    <div className="proc-eval-proof">
      {keys.map((key) => {
        const metric = metrics[key] as Record<string, unknown> | undefined;
        if (!metric) return null;
        const accuracy = Number(metric.accuracy ?? metric.miss_rate ?? 0);
        const label = ({ field_extraction: "字段抽取", post_review_fields: "复核后字段", item_matching: "物料匹配", cost_calculation: "成本计算", hard_constraint_miss: "硬约束漏检率" } as Record<string, string>)[key];
        return (
          <div key={key}>
            <span>{label}</span>
            <i style={{ width: `${Math.min(100, accuracy * 100)}%` }} />
            <strong>{key === "hard_constraint_miss" ? `${(accuracy * 100).toFixed(2)}%` : `${(accuracy * 100).toFixed(2)}%`}</strong>
          </div>
        );
      })}
    </div>
  );
}
