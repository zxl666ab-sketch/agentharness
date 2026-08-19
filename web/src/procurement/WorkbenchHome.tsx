import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  FileWarning,
  ListTodo,
  PackageCheck,
  ShieldAlert,
} from "lucide-react";

import { procurementApi } from "./api";
import { NewProcurementConversation } from "./ProcurementConversation";
import { isViewVisible, type DemoRole } from "./roles";
import type { AiTaskView, ProcurementRequestSummary, ReviewView } from "./types";
import type { TaskFilter, WorkbenchView } from "./workbenchUrl";
import { statusLabel } from "./viewModel";

type Props = {
  role: DemoRole;
  requests: ProcurementRequestSummary[];
  aiTasks: AiTaskView[];
  reviews: ReviewView[];
  loading: boolean;
  createBusy: boolean;
  createError?: string | null;
  maxFileBytes: number;
  maxTotalBytes: number;
  maxQuotes: number;
  onStart: (message: string, files: File[]) => Promise<void>;
  onOpenTask: (id: string) => void;
  onOpenTasks: (filter: TaskFilter) => void;
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
  createBusy,
  createError,
  maxFileBytes,
  maxTotalBytes,
  maxQuotes,
  onStart,
  onOpenTask,
  onOpenTasks,
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
  const canOpenTasks = isViewVisible(role, "tasks");
  const canOpenOrders = isViewVisible(role, "orders");
  const canOpenInvoices = isViewVisible(role, "invoices");
  const canOpenReviews = isViewVisible(role, "reviews");

  return (
    <div className="proc-home">
      {canOpenTasks ? <section className="proc-home-intake">
        <NewProcurementConversation
          embedded
          busy={createBusy}
          error={createError}
          maxFileBytes={maxFileBytes}
          maxTotalBytes={maxTotalBytes}
          maxQuotes={maxQuotes}
          onStart={onStart}
        />
        <button className="proc-demo-entry" type="button" onClick={() => onOpenTasks("all")}>
          <span>演示数据</span><strong>先查看一套完整采购与履约示例</strong><ArrowRight size={15} />
        </button>
      </section> : null}

      <section className="proc-todo-cards" aria-label="待办中心">
        {canOpenTasks ? <button type="button" className={agentWaiting ? "attention" : ""} onClick={() => onOpenTasks("attention")}>
          <span className="proc-todo-icon"><Bot size={18} /></span>
          <div><strong>Agent 等待回答</strong><small>补充关键信息后从当前步骤继续</small></div>
          <i>{agentWaiting}</i>
        </button> : null}
        {canOpenTasks ? <button type="button" className={fieldReviews ? "attention" : ""} onClick={() => onOpenTasks("attention")}>
          <span className="proc-todo-icon"><ListTodo size={18} /></span>
          <div><strong>等待字段复核</strong><small>缺失、冲突或低置信度字段</small></div>
          <i>{fieldReviews}</i>
        </button> : null}
        {canOpenReviews ? <button type="button" className={planConfirmations || pendingReviews ? "attention" : ""} onClick={() => onOpenView("reviews")}>
          <span className="proc-todo-icon"><CheckCircle2 size={18} /></span>
          <div><strong>等待确认采购方案</strong><small>查看金额、供应商和风险后确认</small></div>
          <i>{Math.max(planConfirmations, pendingReviews)}</i>
        </button> : null}
        {canOpenOrders ? <button type="button" className={pendingReceipts ? "attention" : ""} onClick={onOpenOrders}>
          <span className="proc-todo-icon"><PackageCheck size={18} /></span>
          <div><strong>待收货订单</strong><small>已发货或部分收货</small></div>
          <i>{pendingReceipts}</i>
        </button> : null}
        {canOpenInvoices ? <button type="button" className={invoiceHolds ? "danger" : ""} onClick={() => onOpenView("invoices")}>
          <span className="proc-todo-icon"><FileWarning size={18} /></span>
          <div><strong>发票差异待处理</strong><small>三单匹配发现结构化差异</small></div>
          <i>{invoiceHolds}</i>
        </button> : null}
        {canOpenOrders ? <button type="button" className={invoiceHolds || overdueTotal ? "danger" : ""} onClick={onOpenOrders}>
          <span className="proc-todo-icon"><ShieldAlert size={18} /></span>
          <div><strong>付款被拦截</strong><small>发票差异或付款逾期需处理</small></div>
          <i>{invoiceHolds + (counts?.overdue_payments ?? 0)}</i>
        </button> : null}
      </section>

      <div className="proc-home-grid">
        {canOpenTasks ? <section className="proc-home-section">
          <header><div><h2>待办任务</h2><span>{attention} 项</span></div><button type="button" onClick={() => onOpenTasks("attention")}>查看全部<ArrowRight size={14} /></button></header>
          {todo.length ? (
            <div className="proc-home-list">
              {todo.map((request) => (
                <button key={request.id} type="button" onClick={() => onOpenTask(request.id)}>
                  <span className="proc-home-list-icon"><Clock3 size={15} /></span>
                  <span><strong>{request.title}</strong><small>{request.reference} · {request.quote_count} 家报价</small></span>
                  <i>{shortDate(request.updated_at)}</i>
                  <ArrowRight size={14} />
                </button>
              ))}
            </div>
          ) : <div className="proc-home-empty"><CheckCircle2 size={22} /><span>{loading ? "正在读取任务" : "当前没有待办任务"}</span></div>}
        </section> : null}

        <section className="proc-home-section">
          <header><div><h2>需要处理</h2><span>{aiIssues + overdueTotal + pendingReviews} 项</span></div></header>
          <div className="proc-home-alerts">
            <button type="button" className={aiIssues ? "danger" : "quiet"} onClick={() => onOpenView("ai")}>
              <AlertTriangle size={17} /><span><strong>{aiIssues} 个 AI 任务需处理</strong><small>失败、取消或输入已过期</small></span><ArrowRight size={14} />
            </button>
            {canOpenOrders ? <button type="button" className={overdueTotal ? "warning" : "quiet"} onClick={onOpenOrders}>
              <Clock3 size={17} /><span><strong>{overdueTotal} 项履约逾期</strong><small>发货逾期 {counts?.overdue_orders ?? 0} · 付款逾期 {counts?.overdue_payments ?? 0}</small></span><ArrowRight size={14} />
            </button> : null}
            <button type="button" className={pendingReviews ? "warning" : "quiet"} onClick={() => onOpenView("reviews")}>
              <CheckCircle2 size={17} /><span><strong>{pendingReviews} 项等待高风险确认</strong><small>采购方案必须单独确认，普通会话不会自动执行</small></span><ArrowRight size={14} />
            </button>
          </div>
        </section>
      </div>

      {canOpenTasks ? <section className="proc-home-section recent">
        <header><div><h2>最近任务</h2><span>{recent.length} 条</span></div><button type="button" onClick={() => onOpenTasks("all")}>采购任务<ArrowRight size={14} /></button></header>
        {recent.length ? (
          <div className="proc-home-table" role="table" aria-label="最近采购任务">
            <div role="row"><span>采购编号</span><span>任务</span><span>报价</span><span>状态</span><span>更新时间</span></div>
            {recent.map((request) => (
              <button role="row" type="button" key={request.id} onClick={() => onOpenTask(request.id)}>
                <code>{request.reference}</code><strong>{request.title}</strong><span>{request.quote_count} 家</span><span>{statusLabel(request.status)}</span><time>{shortDate(request.updated_at)}</time>
              </button>
            ))}
          </div>
        ) : <div className="proc-home-empty"><ListTodo size={22} /><span>{loading ? "正在读取任务" : "尚无采购任务"}</span></div>}
      </section> : null}
    </div>
  );
}
