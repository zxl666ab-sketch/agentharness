import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock3,
  ListTodo,
  PackageCheck,
  Plus,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { procurementApi } from "./api";
import { AgentOfflineNotice } from "./AgentOfflineNotice";
import { Button, EmptyState, StatusPill, formatShortDateTime, unitLabel } from "../components/ui";
import { isViewVisible, type DemoRole } from "./roles";
import type { ReactNode } from "react";
import type { AiTaskView, ProcurementRequestSummary, ReviewView } from "./types";
import type { TaskFilter, WorkbenchView } from "./workbenchUrl";
import { actionablePendingReviewCount, statusLabel, statusTone } from "./viewModel";

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

function quantityText(request: ProcurementRequestSummary) {
  if (request.quantity == null || String(request.quantity).trim() === "") return null;
  const value = typeof request.quantity === "number"
    ? request.quantity.toLocaleString("zh-CN")
    : String(request.quantity);
  return `${value} ${unitLabel(request.unit)}`.trim();
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
  const inFlight = requests.filter((item) => item.status !== "approved" && item.status !== "no_award" && item.status !== "cancelled").length;
  const aiIssues = aiTasks.filter((item) => item.status === "FAILED" || item.stale).length;
  const pendingReviews = actionablePendingReviewCount(reviews, requests);
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
  const riskItems = [
    canOpenAi && aiIssues > 0 ? {
      key: "ai", tone: "danger" as const, icon: <AlertTriangle size={16} />,
      title: `${aiIssues} 个 AI 任务需处理`,
      detail: "失败、取消或输入已过期，支持一键重试与人工干预",
      go: () => onOpenView("ai"),
    } : null,
    canOpenOrders && overdueTotal > 0 ? {
      key: "overdue", tone: "warning" as const, icon: <Clock3 size={16} />,
      title: `${overdueTotal} 项履约逾期`,
      detail: `发货逾期 ${counts?.overdue_orders ?? 0} · 付款逾期 ${counts?.overdue_payments ?? 0}`,
      go: onOpenOrders,
    } : null,
    canOpenReviews && pendingReviews > 0 ? {
      key: "reviews", tone: "warning" as const, icon: <ShieldCheck size={16} />,
      title: `${pendingReviews} 项等待高风险确认`,
      detail: "采购方案必须由人工核对确认，系统不会盲目自动执行",
      go: () => onOpenView("reviews"),
    } : null,
    canOpenInvoices && invoiceHolds > 0 ? {
      key: "invoice-diff", tone: "danger" as const, icon: <AlertTriangle size={16} />,
      title: `${invoiceHolds} 张发票差异挂起`,
      detail: "存在未核销差异时付款会被拦截，请前往发票中心处理",
      go: () => onOpenView("invoices"),
    } : null,
  ].filter(Boolean) as Array<{ key: string; tone: "danger" | "warning"; icon: ReactNode; title: string; detail: string; go: () => void }>;
  const riskTotal = riskItems.reduce((sum, item) => sum + Number(item.title.match(/^\d+/)?.[0] || 0), 0);
  const hasQuickLinks = (canOpenTasks && humanAttention > 0) ||
    (canOpenReviews && (planConfirmations > 0 || pendingReviews > 0)) ||
    (canOpenOrders && (pendingReceipts > 0 || paymentBlocks > 0)) ||
    (canOpenInvoices && invoiceHolds > 0);

  return (
    <div className="proc-home" aria-label="采购驾驶舱">
      {/* LIVE-1：Agent 离线降级提示（可关闭），避免用户只看到"处理中"。 */}
      {agentDown ? <AgentOfflineNotice /> : null}

      {/* 1️⃣ 核心指标：非零才成卡，零值收敛为徽标（痛点①） */}
      <section className="proc-cockpit-stats" aria-label="核心指标看板">
        <div className="proc-cockpit-numbers">
          {canOpenTasks ? (
            <button type="button" className="proc-stat-card" onClick={() => onOpenTasks("all")}>
              <span className="proc-stat-label">采购总任务</span>
              <strong className="proc-stat-number">{requests.length}</strong>
              <small className="proc-stat-sub">进行中 {inFlight} 项</small>
            </button>
          ) : null}
          {canOpenTasks && attention > 0 ? (
            <button type="button" className="proc-stat-card is-warning" onClick={() => onOpenTasks("attention")}>
              <span className="proc-stat-label">待你决策</span>
              <strong className="proc-stat-number">{attention}</strong>
              <small className="proc-stat-sub">待复核与方案确认</small>
            </button>
          ) : null}
          {canOpenOrders && pendingReceipts > 0 ? (
            <button type="button" className="proc-stat-card is-info" onClick={onOpenOrders}>
              <span className="proc-stat-label">履约中订单</span>
              <strong className="proc-stat-number">{pendingReceipts}</strong>
              <small className="proc-stat-sub">待发货 / 运输收货中</small>
            </button>
          ) : null}
        </div>
        <div className="proc-cockpit-badges" aria-label="已清零的指标">
          {canOpenTasks && attention === 0 ? (
            <span className="proc-zero-badge is-clear"><CheckCircle2 size={13} />{loading ? "正在读取…" : "决策零待办"}</span>
          ) : null}
          {canOpenOrders && pendingReceipts === 0 ? (
            <span className="proc-zero-badge"><PackageCheck size={13} />无在途履约</span>
          ) : null}
        </div>
      </section>

      {/* 2️⃣ 风险与异常：一行摘要 + 展开明细（痛点②） */}
      <details className={`proc-risk-strip ${riskTotal > 0 ? (riskItems.some((item) => item.tone === "danger") ? "is-danger" : "is-warning") : "is-clear"}`} aria-label="风险与异常预警">
        <summary>
          <span className="proc-risk-strip-icon">
            {riskTotal > 0 ? <ShieldAlert size={16} /> : <ShieldCheck size={16} />}
          </span>
          {riskTotal > 0 ? (
            <>
              <strong className="proc-risk-strip-count tnum">{riskTotal}</strong>
              <span className="proc-risk-strip-label">项风险与异常</span>
              <span className="proc-risk-strip-sep" aria-hidden>·</span>
              <span className="proc-risk-strip-summary">
                {riskItems.map((item) => item.title).join(" · ")}
              </span>
            </>
          ) : (
            <span className="proc-risk-strip-label">{loading ? "正在核对风险与异常…" : "暂无风险与异常，履约与审批均在正常推进"}</span>
          )}
          {riskTotal > 0 ? <ChevronDown size={14} className="proc-risk-strip-chevron" aria-hidden /> : null}
        </summary>
        {riskTotal > 0 ? (
          <ul className="proc-risk-strip-items">
            {riskItems.map((item) => (
              <li key={item.key}>
                <button type="button" className={`proc-alert-item is-${item.tone}`} onClick={item.go}>
                  <span className="proc-alert-icon">{item.icon}</span>
                  <span className="proc-alert-content">
                    <strong>{item.title}</strong>
                    <small>{item.detail}</small>
                  </span>
                  <ArrowRight size={14} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </details>

      {/* 3️⃣ 发起入口：常驻一行动作条（原营销横幅默认收起，痛点③） */}
      {canOpenTasks ? (
        <section className="proc-home-action-banner" aria-label="采购智能协同看板">
          <div className="proc-home-banner-copy">
            <span className="proc-home-banner-icon"><Sparkles size={16} /></span>
            <strong>采购智能协同看板</strong>
            <small>AI + Java 双引擎 · 寻源、比价、履约、对账全链路实时掌控</small>
          </div>
          <div className="proc-home-banner-actions">
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => (onOpenCreate ? onOpenCreate() : onOpenTasks("all"))}>
              新建采购任务
            </Button>
          </div>
        </section>
      ) : null}

      {/* 待办快捷入口：只展示有待处理事项的胶囊（零值不占位） */}
      <section className={`proc-todo-quick-strip${hasQuickLinks ? "" : " is-empty"}`} aria-label="待办中心">
        {canOpenTasks && humanAttention > 0 ? (
          <button type="button" title={`等待回答 ${agentWaiting} · 字段复核 ${fieldReviews}`} className="proc-todo-chip is-warning" onClick={() => onOpenTasks("attention")}>
            <Bot size={14} />
            <span className="proc-todo-chip-label">待人工处理</span>
            <b className="proc-todo-chip-count tnum">{humanAttention}</b>
          </button>
        ) : null}
        {canOpenReviews && (planConfirmations > 0 || pendingReviews > 0) ? (
          <button type="button" className="proc-todo-chip is-accent" onClick={() => onOpenView("reviews")}>
            <CheckCircle2 size={14} />
            <span className="proc-todo-chip-label">等待确认采购方案</span>
            <b className="proc-todo-chip-count tnum">{Math.max(planConfirmations, pendingReviews)}</b>
          </button>
        ) : null}
        {canOpenOrders && pendingReceipts > 0 ? (
          <button type="button" className="proc-todo-chip is-info" onClick={onOpenOrders}>
            <PackageCheck size={14} />
            <span className="proc-todo-chip-label">待收货订单</span>
            <b className="proc-todo-chip-count tnum">{pendingReceipts}</b>
          </button>
        ) : null}
        {canOpenInvoices && invoiceHolds > 0 ? (
          <button type="button" className="proc-todo-chip is-danger" onClick={() => onOpenView("invoices")}>
            <AlertTriangle size={14} />
            <span className="proc-todo-chip-label">发票差异待处理</span>
            <b className="proc-todo-chip-count tnum">{invoiceHolds}</b>
          </button>
        ) : null}
        {canOpenOrders && paymentBlocks > 0 ? (
          <button type="button" className="proc-todo-chip is-danger" onClick={onOpenOrders}>
            <ShieldAlert size={14} />
            <span className="proc-todo-chip-label">付款被拦截</span>
            <b className="proc-todo-chip-count tnum">{paymentBlocks}</b>
          </button>
        ) : null}
        {hasQuickLinks ? null : <span className="proc-todo-strip-hint">当前没有需要你立即处理的待办入口</span>}
      </section>

      {/* 4️⃣ 待办任务 + 最近任务：行内副字段差异化（痛点④） */}
      <div className="proc-home-grid">
        {canOpenTasks ? (
          <section className="proc-home-section" aria-label="待办任务">
            <header className="proc-home-section-head">
              <h2>待办任务</h2>
              <span className={`proc-pill-count${attention > 0 ? " is-warning" : ""}`}>{attention} 项待处理</span>
            </header>
            {todo.length ? (
              <div className="proc-home-list">
                {todo.map((item) => {
                  const qty = quantityText(item);
                  return (
                    <button key={item.id} type="button" className="proc-task-row-btn" onClick={() => onOpenTask(item.id)}>
                      <span className="proc-task-row-main">
                        <strong>{item.title}</strong>
                        <small>
                          <code>{item.reference}</code>
                          {item.item_name && !item.title.includes(item.item_name) ? <> · {item.item_name}</> : null}
                          {qty ? <> · {qty}</> : null}
                          {" "}· {item.quote_count} 家报价
                        </small>
                      </span>
                      <StatusPill tone={statusTone(item.status)} size="compact">{statusLabel(item.status)}</StatusPill>
                      <time className="proc-task-row-time" dateTime={item.updated_at}>{formatShortDateTime(item.updated_at)}</time>
                      <ArrowRight size={14} className="proc-row-arrow" aria-hidden />
                    </button>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                variant="inline"
                icon={loading ? undefined : <CheckCircle2 size={24} />}
                title="当前没有待办任务，所有流程均已推进"
                hint={loading ? "正在读取任务…" : "有新的复核、比价确认或审批请求时会出现在这里"}
              />
            )}
          </section>
        ) : null}

        {canOpenTasks ? (
          <section className="proc-home-section recent" aria-label="最近任务">
            <header className="proc-home-section-head">
              <h2>最近任务</h2>
              <button type="button" className="proc-link-btn" onClick={() => onOpenTasks("all")}>
                查看全部任务 <ArrowRight size={13} />
              </button>
            </header>
            {recent.length ? (
              <div className="proc-pro-table-wrap">
                <table className="proc-pro-table" role="table" aria-label="最近采购任务">
                  <thead>
                    <tr>
                      <th>采购编号</th>
                      <th>物料与需求标题</th>
                      <th>当前阶段</th>
                      <th className="is-end">最近更新</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((item) => {
                      const qty = quantityText(item);
                      return (
                        <tr key={item.id} className="proc-pro-tr" onClick={() => onOpenTask(item.id)}>
                          <td><code>{item.reference}</code></td>
                          <td>
                            <div className="proc-table-title-cell">
                              <strong>{item.title}</strong>
                              <small>
                                {item.item_name && !item.title.includes(item.item_name) ? `${item.item_name} · ` : ""}
                                {qty ? `${qty} · ` : ""}
                                {`${item.quote_count} 家报价`}
                              </small>
                            </div>
                          </td>
                          <td><StatusPill tone={statusTone(item.status)} size="compact">{statusLabel(item.status)}</StatusPill></td>
                          <td className="is-end"><time className="proc-table-time" dateTime={item.updated_at}>{formatShortDateTime(item.updated_at)}</time></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                variant="inline"
                icon={loading ? undefined : <ListTodo size={24} />}
                title="尚无采购任务"
                hint={loading ? "正在读取任务…" : "发起第一次询价后，任务进展会实时汇总在这里"}
                action={onOpenCreate ? <Button variant="primary" size="sm" icon={<Plus size={14} />} onClick={onOpenCreate}>新建采购任务</Button> : undefined}
              />
            )}
          </section>
        ) : null}
      </div>
    </div>
  );
}
