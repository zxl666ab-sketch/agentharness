import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  FileSpreadsheet,
  FileWarning,
  ListTodo,
  PackageCheck,
  Plus,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

import { procurementApi } from "./api";
import { AgentOfflineNotice } from "./AgentOfflineNotice";
import { isViewVisible, type DemoRole } from "./roles";
import type { AiTaskView, ProcurementRequestSummary, ReviewView } from "./types";
import type { TaskFilter, WorkbenchView } from "./workbenchUrl";
import { statusLabel, statusTone } from "./viewModel";

type Props = {
  role: DemoRole;
  requests: ProcurementRequestSummary[];
  aiTasks: AiTaskView[];
  reviews: ReviewView[];
  loading: boolean;
  /** LIVE-1：Agent 不可用时在驾驶舱顶部显示可关闭的降级提示条。 */
  agentDown?: boolean;
  onOpenTask: (id: string) => void;
  onOpenTasks: (filter: TaskFilter) => void;
  onOpenCreate?: () => void;
  onOpenView: (view: WorkbenchView) => void;
  onOpenOrders: () => void;
};

const ATTENTION = new Set(["waiting_human", "review", "ready", "analyzed", "approval_pending"]);
function shortDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function WorkbenchHome({
  role,
  requests,
  aiTasks,
  reviews,
  loading,
  agentDown = false,
  onOpenTask,
  onOpenTasks,
  onOpenCreate,
  onOpenView,
  onOpenOrders,
}: Props) {
  const overviewQuery = useQuery({
    queryKey: ["procurement-insights-overview"],
    queryFn: procurementApi.insightsOverview,
    refetchInterval: 15_000,
  });
  const overview = overviewQuery.data;
  const counts = overview?.counts;
  const invoiceHoldsQuery = useQuery({
    queryKey: ["procurement-home-invoice-holds"],
    queryFn: () => procurementApi.invoices("DIFF_HOLD", undefined, 0, 1),
    refetchInterval: 15_000,
  });

  const attention = requests.filter((item) => ATTENTION.has(item.status)).length;
  const aiIssues = aiTasks.filter((item) => item.status === "FAILED" || item.stale).length;
  const pendingReviews = reviews.filter((item) => item.status === "PENDING").length;
  const recent = requests.slice(0, 8);
  const todo = requests.filter((item) => ATTENTION.has(item.status)).slice(0, 6);

  const overdueTotal = (counts?.overdue_orders ?? 0) + (counts?.overdue_payments ?? 0);
  const fieldReviews = requests.filter((item) => item.status === "review").length;
  const agentWaiting = requests.filter((item) => item.status === "waiting_human").length;
  const planConfirmations = requests.filter((item) => item.status === "analyzed" || item.status === "approval_pending").length;
  const pendingReceipts = (counts?.orders_shipped ?? 0) + (counts?.orders_partially_received ?? 0);
  const invoiceHolds = invoiceHoldsQuery.data?.total ?? 0;
  const paymentBlocks = invoiceHolds + (counts?.overdue_payments ?? 0);
  const humanAttention = agentWaiting + fieldReviews;
  const canOpenTasks = isViewVisible(role, "tasks");
  const canOpenAi = isViewVisible(role, "ai");
  const canOpenOrders = isViewVisible(role, "orders");
  const canOpenInvoices = isViewVisible(role, "invoices");
  const canOpenReviews = isViewVisible(role, "reviews");
  const actionableExceptionCount = (canOpenAi ? aiIssues : 0) + (canOpenOrders ? overdueTotal : 0) + (canOpenReviews ? pendingReviews : 0);
  const hasQuickLinks = (canOpenTasks && humanAttention > 0) ||
    (canOpenReviews && (planConfirmations > 0 || pendingReviews > 0)) ||
    (canOpenOrders && (pendingReceipts > 0 || paymentBlocks > 0)) ||
    (canOpenInvoices && invoiceHolds > 0);

  return (
    <div className="proc-home flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      {/* LIVE-1：Agent 离线降级提示（可关闭），避免用户只看到"处理中"。 */}
      {agentDown ? <AgentOfflineNotice /> : null}
      {/* 顶部驾驶舱 KPI 指标看板 */}
      <section className="proc-cockpit-stats grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" aria-label="核心指标看板">
        <button
          type="button"
          className="proc-stat-card glass-panel bg-surface/80 hover:bg-surface border border-border/70 hover:border-accent/40 rounded-xl p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-150 flex flex-col justify-between text-left group"
          onClick={() => canOpenTasks && onOpenTasks("all")}
        >
          <div className="proc-stat-header flex items-center justify-between gap-2">
            <span className="proc-stat-title text-xs font-medium text-text-muted group-hover:text-text transition-colors">采购总任务</span>
            <span className="proc-stat-icon-wrap primary w-8 h-8 rounded-lg flex items-center justify-center bg-accent-soft text-accent group-hover:scale-105 transition-transform"><ListTodo size={18} /></span>
          </div>
          <div className="proc-stat-body flex flex-col gap-0.5 mt-2">
            <strong className="proc-stat-number text-2xl font-bold font-mono text-text tracking-tight">{requests.length}</strong>
            <span className="proc-stat-sub text-xs text-text-muted">进行中 {requests.filter(r => r.status !== 'approved' && r.status !== 'no_award' && r.status !== 'cancelled').length} 项</span>
          </div>
        </button>

        <button
          type="button"
          className={`proc-stat-card glass-panel bg-surface/80 hover:bg-surface border border-border/70 hover:border-warning/40 rounded-xl p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-150 flex flex-col justify-between text-left group ${attention > 0 ? "highlight-attention ring-1 ring-warning/30" : ""}`}
          onClick={() => canOpenTasks && onOpenTasks("attention")}
        >
          <div className="proc-stat-header flex items-center justify-between gap-2">
            <span className="proc-stat-title text-xs font-medium text-text-muted group-hover:text-text transition-colors">待办决策</span>
            <span className="proc-stat-icon-wrap warning w-8 h-8 rounded-lg flex items-center justify-center bg-warning-soft text-warning group-hover:scale-105 transition-transform"><Clock3 size={18} /></span>
          </div>
          <div className="proc-stat-body flex flex-col gap-0.5 mt-2">
            <strong className="proc-stat-number text-2xl font-bold font-mono text-text tracking-tight">{attention}</strong>
            <span className="proc-stat-sub text-xs text-text-muted">待复核与方案确认</span>
          </div>
        </button>

        {canOpenOrders ? (
          <button
            type="button"
            className="proc-stat-card glass-panel bg-surface/80 hover:bg-surface border border-border/70 hover:border-info/40 rounded-xl p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-150 flex flex-col justify-between text-left group"
            onClick={onOpenOrders}
          >
            <div className="proc-stat-header flex items-center justify-between gap-2">
              <span className="proc-stat-title text-xs font-medium text-text-muted group-hover:text-text transition-colors">履约中订单</span>
              <span className="proc-stat-icon-wrap info w-8 h-8 rounded-lg flex items-center justify-center bg-info-soft text-info group-hover:scale-105 transition-transform"><PackageCheck size={18} /></span>
            </div>
            <div className="proc-stat-body flex flex-col gap-0.5 mt-2">
              <strong className="proc-stat-number text-2xl font-bold font-mono text-text tracking-tight">{pendingReceipts}</strong>
              <span className="proc-stat-sub text-xs text-text-muted">待发货 / 运输收货中</span>
            </div>
          </button>
        ) : null}

        <button
          type="button"
          className={`proc-stat-card glass-panel bg-surface/80 hover:bg-surface border border-border/70 hover:border-danger/40 rounded-xl p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-150 flex flex-col justify-between text-left group ${aiIssues + invoiceHolds + overdueTotal > 0 ? "highlight-danger ring-1 ring-danger/30" : ""}`}
          onClick={() => aiIssues ? onOpenView("ai") : canOpenInvoices && invoiceHolds ? onOpenView("invoices") : canOpenOrders ? onOpenOrders() : canOpenTasks ? onOpenTasks("attention") : undefined}
        >
          <div className="proc-stat-header flex items-center justify-between gap-2">
            <span className="proc-stat-title text-xs font-medium text-text-muted group-hover:text-text transition-colors">风险与异常预警</span>
            <span className="proc-stat-icon-wrap danger w-8 h-8 rounded-lg flex items-center justify-center bg-danger-soft text-danger group-hover:scale-105 transition-transform"><ShieldAlert size={18} /></span>
          </div>
          <div className="proc-stat-body flex flex-col gap-0.5 mt-2">
            <strong className="proc-stat-number text-2xl font-bold font-mono text-text tracking-tight">{aiIssues + invoiceHolds + overdueTotal}</strong>
            <span className="proc-stat-sub text-xs text-text-muted">AI异常 {aiIssues} · 差异发票 {invoiceHolds}</span>
          </div>
        </button>
      </section>

      {/* 快捷操作与场景引导横幅 */}
      {canOpenTasks ? (
        <section className="proc-home-action-banner flex flex-wrap items-center justify-between gap-4 p-4.5 rounded-xl border border-border/80 bg-surface shadow-xs">
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="w-10 h-10 rounded-xl bg-accent-soft text-accent flex items-center justify-center flex-shrink-0 shadow-2xs">
              <Sparkles size={20} />
            </div>
            <div className="flex flex-col gap-0.5 min-w-0">
              <div className="flex items-center gap-2">
                <strong className="text-sm font-bold text-text">采购智能协同看板</strong>
                <span className="text-[10px] font-semibold font-mono bg-accent-soft text-accent px-2 py-0.5 rounded-full border border-accent/20">
                  AI + Java 确定性双引擎
                </span>
              </div>
              <p className="text-xs text-text-muted">
                实时掌控采购寻源、智能比价、履约跟踪与对账结算全链路。如需发起新寻价，请点击右侧创建任务。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2.5 flex-shrink-0">
            <button
              className="proc-button primary inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-strong shadow-xs transition-colors"
              type="button"
              onClick={() => onOpenCreate ? onOpenCreate() : onOpenTasks("all")}
            >
              <Plus size={15} />
              <span>新建采购任务</span>
            </button>
          </div>
        </section>
      ) : null}

      {/* 待办中心快捷过滤药丸条：只展示有待处理事项的入口 */}
      <section className={`proc-todo-quick-strip flex items-center gap-2 overflow-x-auto py-1 scrollbar-none ${hasQuickLinks ? "" : "hidden"}`} aria-label="待办中心">
          {canOpenTasks && humanAttention > 0 ? (
            <button type="button" title={`等待回答 ${agentWaiting} · 字段复核 ${fieldReviews}`} className="proc-todo-chip attention inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-warning/40 bg-warning-soft/30 text-warning transition-all whitespace-nowrap" onClick={() => onOpenTasks("attention")}>
              <span className="proc-todo-chip-icon"><Bot size={15} /></span>
              <span className="proc-todo-chip-label">待人工处理</span>
              <span className="proc-todo-chip-count font-mono font-bold px-1.5 py-0.2 rounded-full bg-surface-subtle text-[11px]">{humanAttention}</span>
            </button>
          ) : null}
          {canOpenReviews && (planConfirmations > 0 || pendingReviews > 0) ? (
            <button type="button" className="proc-todo-chip attention inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-accent/40 bg-accent-soft/30 text-accent transition-all whitespace-nowrap" onClick={() => onOpenView("reviews")}>
              <span className="proc-todo-chip-icon"><CheckCircle2 size={15} /></span>
              <span className="proc-todo-chip-label">等待确认采购方案</span>
              <span className="proc-todo-chip-count font-mono font-bold px-1.5 py-0.2 rounded-full bg-surface-subtle text-[11px]">{Math.max(planConfirmations, pendingReviews)}</span>
            </button>
          ) : null}
          {canOpenOrders && pendingReceipts > 0 ? (
            <button type="button" className="proc-todo-chip attention inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-info/40 bg-info-soft/30 text-info transition-all whitespace-nowrap" onClick={onOpenOrders}>
              <span className="proc-todo-chip-icon"><PackageCheck size={15} /></span>
              <span className="proc-todo-chip-label">待收货订单</span>
              <span className="proc-todo-chip-count font-mono font-bold px-1.5 py-0.2 rounded-full bg-surface-subtle text-[11px]">{pendingReceipts}</span>
            </button>
          ) : null}
          {canOpenInvoices && invoiceHolds > 0 ? (
            <button type="button" className="proc-todo-chip danger inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-danger/40 bg-danger-soft/30 text-danger transition-all whitespace-nowrap" onClick={() => onOpenView("invoices")}>
              <span className="proc-todo-chip-icon"><FileWarning size={15} /></span>
              <span className="proc-todo-chip-label">发票差异待处理</span>
              <span className="proc-todo-chip-count font-mono font-bold px-1.5 py-0.2 rounded-full bg-surface-subtle text-[11px]">{invoiceHolds}</span>
            </button>
          ) : null}
          {canOpenOrders && paymentBlocks > 0 ? (
            <button type="button" className="proc-todo-chip danger inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border border-danger/40 bg-danger-soft/30 text-danger transition-all whitespace-nowrap" onClick={onOpenOrders}>
              <span className="proc-todo-chip-icon"><ShieldAlert size={15} /></span>
              <span className="proc-todo-chip-label">付款被拦截</span>
              <span className="proc-todo-chip-count font-mono font-bold px-1.5 py-0.2 rounded-full bg-surface-subtle text-[11px]">{paymentBlocks}</span>
            </button>
          ) : null}
      </section>

      {/* 待办任务流与需处理告警 */}
      <div className="proc-home-grid grid grid-cols-1 lg:grid-cols-2 gap-6">
        {canOpenTasks ? (
          <section className="proc-home-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 flex flex-col gap-4 shadow-sm">
            <header className="flex items-center justify-between gap-2 pb-2 border-b border-border/40">
              <div className="flex items-center gap-2.5">
                <h2 className="text-base font-semibold text-text tracking-tight">待办任务</h2>
                <span className="proc-pill-count text-xs px-2.5 py-0.5 rounded-full font-medium bg-surface-subtle border border-border text-text-muted">{attention} 项待处理</span>
              </div>
            </header>
            {todo.length ? (
              <div className="proc-home-list flex flex-col gap-2">
                {todo.map((request) => (
                  <button key={request.id} type="button" className="proc-task-row-btn p-3 rounded-lg border border-border/60 bg-surface hover:bg-surface-subtle hover:border-border-strong text-left flex items-center gap-3 transition-all group" onClick={() => onOpenTask(request.id)}>
                    <span className="proc-home-list-icon w-8 h-8 rounded-lg bg-surface-subtle flex items-center justify-center text-text-muted group-hover:text-accent transition-colors flex-shrink-0"><Clock3 size={16} /></span>
                    <div className="proc-task-row-info flex-1 min-w-0">
                      <strong className="block text-xs font-semibold text-text truncate group-hover:text-accent transition-colors">{request.title}</strong>
                      <small className="block text-[11px] text-text-muted truncate"><code className="font-mono">{request.reference}</code> · {request.quote_count} 家报价</small>
                    </div>
                    <span className={`proc-status-tag ${statusTone(request.status)} text-[11px] font-medium px-2 py-0.5 rounded-full border`}>{statusLabel(request.status)}</span>
                    <time className="proc-task-row-time text-[11px] text-text-muted font-mono whitespace-nowrap">{shortDate(request.updated_at)}</time>
                    <ArrowRight size={14} className="proc-row-arrow text-text-muted group-hover:text-text group-hover:translate-x-0.5 transition-all flex-shrink-0" />
                  </button>
                ))}
              </div>
            ) : (
              <div className="proc-home-empty py-10 flex flex-col items-center justify-center gap-2 text-text-muted text-xs">
                <CheckCircle2 size={24} className="text-accent" />
                <span>{loading ? "正在读取任务…" : "当前没有待办任务，所有流程均已推进"}</span>
              </div>
            )}
          </section>
        ) : null}

        {actionableExceptionCount > 0 ? <section className="proc-home-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 flex flex-col gap-4 shadow-sm">
          <header className="flex items-center justify-between gap-2 pb-2 border-b border-border/40">
            <div className="flex items-center gap-2.5">
              <h2 className="text-base font-semibold text-text tracking-tight">需要处理</h2>
              <span className="proc-pill-count danger text-xs px-2.5 py-0.5 rounded-full font-medium bg-danger-soft text-danger border border-danger/20">{actionableExceptionCount} 项异常</span>
            </div>
          </header>
          <div className="proc-home-alerts flex flex-col gap-2.5">
            {canOpenAi && aiIssues > 0 ? <button type="button" className="proc-alert-item danger p-3.5 rounded-xl border text-left flex items-center gap-3.5 transition-all group bg-danger-soft/20 border-danger/30 hover:border-danger/60" onClick={() => onOpenView("ai")}>
              <div className={`proc-alert-icon w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${aiIssues ? "bg-danger-soft text-danger" : "bg-surface-subtle text-text-muted"}`}><AlertTriangle size={18} /></div>
              <div className="proc-alert-content flex-1 min-w-0">
                <strong className="block text-xs font-semibold text-text truncate group-hover:text-danger transition-colors">{aiIssues} 个 AI 任务需处理</strong>
                <small className="block text-[11px] text-text-muted truncate mt-0.5">失败、取消或输入已过期，支持一键重试与人工干预</small>
              </div>
              <ArrowRight size={15} className="text-text-muted group-hover:text-text group-hover:translate-x-0.5 transition-all flex-shrink-0" />
            </button> : null}
            {canOpenOrders && overdueTotal > 0 ? (
              <button type="button" className="proc-alert-item warning p-3.5 rounded-xl border text-left flex items-center gap-3.5 transition-all group bg-warning-soft/20 border-warning/30 hover:border-warning/60" onClick={onOpenOrders}>
                <div className={`proc-alert-icon w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${overdueTotal ? "bg-warning-soft text-warning" : "bg-surface-subtle text-text-muted"}`}><Clock3 size={18} /></div>
                <div className="proc-alert-content flex-1 min-w-0">
                  <strong className="block text-xs font-semibold text-text truncate group-hover:text-warning transition-colors">{overdueTotal} 项履约逾期</strong>
                  <small className="block text-[11px] text-text-muted truncate mt-0.5">发货逾期 {counts?.overdue_orders ?? 0} · 付款逾期 {counts?.overdue_payments ?? 0}</small>
                </div>
                <ArrowRight size={15} className="text-text-muted group-hover:text-text group-hover:translate-x-0.5 transition-all flex-shrink-0" />
              </button>
            ) : null}
            {canOpenReviews && pendingReviews > 0 ? <button type="button" className="proc-alert-item warning p-3.5 rounded-xl border text-left flex items-center gap-3.5 transition-all group bg-warning-soft/20 border-warning/30 hover:border-warning/60" onClick={() => onOpenView("reviews")}>
              <div className={`proc-alert-icon w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${pendingReviews ? "bg-warning-soft text-warning" : "bg-surface-subtle text-text-muted"}`}><CheckCircle2 size={18} /></div>
              <div className="proc-alert-content flex-1 min-w-0">
                <strong className="block text-xs font-semibold text-text truncate group-hover:text-warning transition-colors">{pendingReviews} 项等待高风险确认</strong>
                <small className="block text-[11px] text-text-muted truncate mt-0.5">采购方案必须由人工核对确认，系统不会盲目自动执行</small>
              </div>
              <ArrowRight size={15} className="text-text-muted group-hover:text-text group-hover:translate-x-0.5 transition-all flex-shrink-0" />
            </button> : null}
          </div>
        </section> : null}
      </div>

      {/* 最近采购任务高密度数据表 */}
      {canOpenTasks ? (
        <section className="proc-home-section recent glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 flex flex-col gap-4 shadow-sm">
          <header className="flex items-center justify-between gap-2 pb-2 border-b border-border/40">
            <div className="flex items-center gap-2.5">
              <h2 className="text-base font-semibold text-text tracking-tight">最近任务</h2>
              <span className="proc-pill-count text-xs px-2.5 py-0.5 rounded-full font-medium bg-surface-subtle border border-border text-text-muted">{recent.length} 条记录</span>
            </div>
            <button type="button" className="proc-link-btn text-xs font-medium text-accent hover:text-accent-strong flex items-center gap-1 transition-colors" onClick={() => onOpenTasks("all")}>
              查看全部任务<ArrowRight size={14} />
            </button>
          </header>
          {recent.length ? (
            <div className="proc-pro-table-wrap overflow-x-auto rounded-lg border border-border/60">
              <table className="proc-pro-table w-full text-left text-xs border-collapse" role="table" aria-label="最近采购任务">
                <thead>
                  <tr role="row" className="bg-surface-subtle/80 border-b border-border text-text-muted font-medium">
                    <th className="py-2.5 px-3">采购编号</th>
                    <th className="py-2.5 px-3">物料与需求标题</th>
                    <th className="py-2.5 px-3">报价数量</th>
                    <th className="py-2.5 px-3">当前阶段</th>
                    <th className="py-2.5 px-3 text-right">最近更新</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {recent.map((request) => (
                    <tr key={request.id} role="row" className="proc-pro-tr hover:bg-surface-subtle/60 transition-colors cursor-pointer group" onClick={() => onOpenTask(request.id)}>
                      <td className="py-3 px-3"><code className="font-mono text-xs font-semibold text-accent">{request.reference}</code></td>
                      <td className="py-3 px-3">
                        <div className="proc-table-title-cell flex flex-col gap-0.5">
                          <strong className="text-xs font-semibold text-text group-hover:text-accent transition-colors">{request.title}</strong>
                          <small className="text-[11px] text-text-muted">{request.item_name} · {request.quantity ? `${request.quantity.toLocaleString()} ${request.unit || ""}` : "规格自适应"}</small>
                        </div>
                      </td>
                      <td className="py-3 px-3">
                        <span className="proc-quote-count-badge inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface-subtle border border-border text-[11px] font-medium text-text-secondary">
                          <FileSpreadsheet size={13} className="text-accent" />
                          {request.quote_count} 家
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span className={`proc-status-chip ${statusTone(request.status)} inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border`}>
                          <i className="w-1.5 h-1.5 rounded-full bg-current" />
                          {statusLabel(request.status)}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right"><time className="proc-table-time text-[11px] text-text-muted font-mono">{shortDate(request.updated_at)}</time></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="proc-home-empty py-10 flex flex-col items-center justify-center gap-2 text-text-muted text-xs">
              <ListTodo size={24} className="text-accent" />
              <span>{loading ? "正在读取任务…" : "尚无采购任务"}</span>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
