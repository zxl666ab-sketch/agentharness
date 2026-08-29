import { useQuery } from "@tanstack/react-query";
import { BarChart3, Bell, PieChart, TrendingUp, Trophy } from "lucide-react";

import { procurementApi } from "./api";
import { CenterPage, EmptyState, ErrorState, PageHeader, categoryLabel } from "../components/ui";

const STATUS_LABELS: Record<string, string> = {
  draft: "需求整理中",
  waiting_human: "等待补充信息",
  collecting: "报价解析中",
  review: "待复核",
  ready: "待比价",
  analyzing: "分析中",
  analyzed: "待审批",
  approval_pending: "审批处理中",
  approved: "已批准",
  no_award: "本轮流标",
  cancelled: "已取消",
};

const FUNNEL_TONES: Record<string, string> = {
  approved: "success",
  review: "warning",
  analyzed: "warning",
  approval_pending: "warning",
  waiting_human: "warning",
  analyzing: "info",
  collecting: "info",
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
  const trendMax = Math.max(1, ...trend.map((row) => row.task_count));

  return (
    <CenterPage
      header={
        <PageHeader
          icon={<BarChart3 size={18} />}
          title="统计报表"
          subtitle="状态漏斗 / 月度趋势 / 供应商中标排行 / 品类分布 / 成本节约率 / AI 评测"
        />
      }
    >
      <div className="proc-report-kpis">
        <div className="proc-kpi-card">
          <span>成本节约率</span>
          <strong className="tnum is-accent">{savings?.rate != null ? `${(Number(savings.rate) * 100).toFixed(2)}%` : "—"}</strong>
          <small>预算 {savings?.budget_total ?? "—"} → 到货 {savings?.landed_total ?? "—"}（节约 {savings?.savings ?? "—"}）</small>
        </div>
        <div className="proc-kpi-card">
          <span>已批准任务</span>
          <strong className="tnum">{overview?.counts.approved_tasks ?? "—"}</strong>
          <small>共 {overview?.counts.tasks ?? "—"} 个任务</small>
        </div>
        <div className="proc-kpi-card">
          <span>采购订单</span>
          <strong className="tnum">{overview?.counts.orders ?? "—"}</strong>
          <small>已收货 {overview?.counts.orders_received ?? 0} · 已付款 {overview?.counts.settlements_paid ?? 0}</small>
        </div>
        <div className="proc-kpi-card">
          <span>供应商</span>
          <strong className="tnum">{overview?.counts.suppliers ?? "—"}</strong>
          <small>黑名单 {overview?.counts.suppliers_blacklisted ?? 0} · 逾期订单 {overview?.counts.overdue_orders ?? 0}</small>
        </div>
      </div>

      <div className="proc-reports-grid">
        <section className="proc-report-block">
          <header>
            <h3><BarChart3 size={15} /> 状态漏斗</h3>
            <small>按任务状态分组</small>
          </header>
          {overviewQuery.isPending ? <div className="proc-loading-line"><BarChart3 size={15} />正在加载…</div> : null}
          {overviewQuery.isError ? <ErrorState title="概览数据加载失败" detail={overviewQuery.error instanceof Error ? overviewQuery.error.message : "未知错误"} onRetry={() => void overviewQuery.refetch()} /> : null}
          <div className="proc-funnel">
            {funnel.map((entry) => (
              <div key={entry.status} className={`proc-funnel-row ${FUNNEL_TONES[entry.status] || ""}`}>
                <span>{STATUS_LABELS[entry.status] || entry.status}</span>
                <div className="proc-bar-track"><i style={{ width: `${(entry.count / Math.max(1, funnel[0].count)) * 100}%` }} /></div>
                <b className="tnum">{entry.count}</b>
              </div>
            ))}
            {!funnel.length && !overviewQuery.isPending ? (
              <EmptyState variant="inline" icon={<Bell size={22} />} title="还没有任务数据" hint="创建第一个采购任务后，漏斗会展示各环节的分布。" />
            ) : null}
          </div>
        </section>

        <section className="proc-report-block">
          <header>
            <h3><TrendingUp size={15} /> 月度趋势</h3>
            <small>近 6 个月任务数</small>
          </header>
          {trend.length ? (
            <>
              <div className="proc-trend-bars">
                {trend.map((row) => (
                  <div key={row.month} title={`${row.month}：${row.task_count} 个任务，批准 ${row.approved_amount}`} style={{ height: "100%" }}>
                    <div className="proc-trend-col">
                      <em className="tnum">{row.task_count}</em>
                      <i style={{ height: `${Math.max(6, (row.task_count / trendMax) * 100)}%` }} />
                    </div>
                    <small className="mono">{row.month.slice(5)} 月</small>
                  </div>
                ))}
              </div>
              {trend.length < 3 ? (
                <p className="proc-sparse-hint">数据月份较少，趋势仅供参考；持续使用两个月以上后趋势更有意义。</p>
              ) : null}
            </>
          ) : (
            <EmptyState variant="inline" icon={<TrendingUp size={22} />} title="暂无趋势数据" hint="任务按创建月份自动汇入趋势图。" />
          )}
          <p className="proc-eval-note">口径：批准金额 = 已批准任务所选报价的到货总价，统一折算为基准币种。</p>
        </section>

        <section className="proc-report-block">
          <header>
            <h3><Trophy size={15} /> 供应商中标排行</h3>
            <small>按中标次数排序</small>
          </header>
          {ranking.length ? (
            <div className="proc-ranking">
              {ranking.map((row, index) => (
                <div className="proc-rank-row" key={row.id}>
                  <span className={`proc-rank-no${index < 3 ? " is-top" : ""}`}>{index + 1}</span>
                  <strong>{row.name}</strong>
                  <small>{row.win_count}/{row.quote_count} 中标 · 绩效 {Number(row.performance.score).toFixed(1)}</small>
                  <span className={`proc-rank-level is-${row.performance.level === "黑名单" ? "danger" : "accent"}`}>{row.performance.level}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState variant="inline" icon={<Trophy size={22} />} title="暂无中标记录" hint="供应商档案按名称自动关联报价与中标记录。" />
          )}
        </section>

        <section className="proc-report-block">
          <header>
            <h3><PieChart size={15} /> 品类分布</h3>
            <small>按任务品类分组</small>
          </header>
          {categories.length ? (
            <div className="proc-funnel">
              {categories.map((entry) => (
                <div className="proc-funnel-row" key={entry.category}>
                  <span>{categoryLabel(entry.category)}</span>
                  <div className="proc-bar-track"><i style={{ width: `${(entry.count / Math.max(1, categories[0].count)) * 100}%` }} /></div>
                  <b className="tnum">{entry.count}</b>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState variant="inline" icon={<PieChart size={22} />} title="暂无品类数据" hint="任务创建后按品类自动分组统计。" />
          )}
        </section>
      </div>

      <section className="proc-report-block">
        <header>
          <h3><BarChart3 size={15} /> AI 评测指标</h3>
          <small>冻结评测集（结果可复算，口径固定）</small>
        </header>
        {evaluationQuery.isPending ? <div className="proc-loading-line"><BarChart3 size={15} />正在运行冻结评测…</div> : null}
        {evaluationQuery.isError ? <p className="proc-muted" role="alert">评测数据加载失败</p> : null}
        {evaluation ? <EvaluationBand evaluation={evaluation} /> : null}
      </section>
    </CenterPage>
  );
}

const EVAL_LABELS: Record<string, { label: string; hint: string }> = {
  field_extraction: { label: "字段抽取", hint: "报价字段抽取正确率" },
  post_review_fields: { label: "复核后字段", hint: "人工复核后的字段正确率" },
  item_matching: { label: "物料匹配", hint: "物料名称匹配正确率" },
  cost_calculation: { label: "成本计算", hint: "到货成本计算正确率" },
  hard_constraint_miss: { label: "硬约束漏检率", hint: "应淘汰却未淘汰的比例，越低越好" },
};

function EvaluationBand({ evaluation }: { evaluation: Record<string, unknown> }) {
  const metrics = (evaluation.metrics || evaluation.result || evaluation) as Record<string, unknown>;
  const keys = ["field_extraction", "post_review_fields", "item_matching", "cost_calculation", "hard_constraint_miss"];
  return (
    <div className="proc-eval-cards">
      {keys.map((key) => {
        const metric = metrics[key] as Record<string, unknown> | undefined;
        if (!metric) return null;
        const accuracy = Number(metric.accuracy ?? metric.miss_rate ?? 0);
        const { label, hint } = EVAL_LABELS[key];
        const inverse = key === "hard_constraint_miss";
        return (
          <div className="proc-eval-card" key={key} title={hint}>
            <span>{label}</span>
            <div className="proc-bar-track"><i className={inverse ? "is-inverse" : undefined} style={{ width: `${Math.min(100, accuracy * 100)}%` }} /></div>
            <strong className="tnum">{(accuracy * 100).toFixed(2)}%</strong>
          </div>
        );
      })}
    </div>
  );
}
