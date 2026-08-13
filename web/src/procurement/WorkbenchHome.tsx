import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  CreditCard,
  ListTodo,
  PackageCheck,
  Percent,
  Plus,
  ShoppingCart,
  Users,
} from "lucide-react";

import { procurementApi } from "./api";
import { ROLE_LABELS, type DemoRole } from "./roles";
import type { AiTaskView, ProcurementRequestSummary, ReviewView } from "./types";
import type { TaskFilter, WorkbenchView } from "./workbenchUrl";

type Props = {
  role: DemoRole;
  requests: ProcurementRequestSummary[];
  aiTasks: AiTaskView[];
  reviews: ReviewView[];
  loading: boolean;
  onCreate: () => void;
  onOpenTask: (id: string) => void;
  onOpenTasks: (filter: TaskFilter) => void;
  onOpenView: (view: WorkbenchView) => void;
  onOpenOrders: () => void;
  onOpenSuppliers: () => void;
  onOpenReports: () => void;
};

const ATTENTION = new Set(["review", "ready", "analyzed", "approval_pending"]);
const COMPLETE = new Set(["approved", "no_award", "cancelled"]);

function shortDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function WorkbenchHome({
  role,
  requests,
  aiTasks,
  reviews,
  loading,
  onCreate,
  onOpenTask,
  onOpenTasks,
  onOpenView,
  onOpenOrders,
  onOpenSuppliers,
  onOpenReports,
}: Props) {
  const overviewQuery = useQuery({
    queryKey: ["procurement-insights-overview"],
    queryFn: procurementApi.insightsOverview,
    refetchInterval: 15_000,
  });
  const overview = overviewQuery.data;
  const counts = overview?.counts;
  const savings = overview?.cost_savings;

  const attention = requests.filter((item) => ATTENTION.has(item.status)).length;
  const completed = requests.filter((item) => COMPLETE.has(item.status)).length;
  const aiIssues = aiTasks.filter((item) => item.status === "FAILED" || item.stale).length;
  const pendingReviews = reviews.filter((item) => item.status === "PENDING").length;
  const recent = requests.slice(0, 8);
  const todo = requests.filter((item) => ATTENTION.has(item.status)).slice(0, 6);

  const savingsRate = savings?.rate != null ? `${(Number(savings.rate) * 100).toFixed(2)}%` : "—";
  const overdueTotal = (counts?.overdue_orders ?? 0) + (counts?.overdue_payments ?? 0);

  return (
    <div className="proc-home">
      <header className="proc-page-head">
        <div>
          <span>采购工作台 · {ROLE_LABELS[role]}视角</span>
          <h1>管理驾驶舱</h1>
        </div>
        <button className="proc-button primary" type="button" onClick={onCreate}><Plus size={16} />新建采购任务</button>
      </header>

      <section className="proc-metric-strip" aria-label="工作台指标">
        <button type="button" onClick={onOpenReports}><Percent size={17} /><span>成本节约率</span><strong>{savingsRate}</strong></button>
        <button type="button" onClick={onOpenOrders}><ShoppingCart size={17} /><span>采购订单</span><strong>{counts?.orders ?? "—"}</strong></button>
        <button type="button" onClick={onOpenSuppliers}><Users size={17} /><span>供应商</span><strong>{counts?.suppliers ?? "—"}</strong></button>
        <button type="button" onClick={() => onOpenTasks("all")}><ListTodo size={17} /><span>采购任务</span><strong>{requests.length}</strong></button>
        <button type="button" onClick={() => onOpenTasks("attention")}><Clock3 size={17} /><span>待处理</span><strong>{attention}</strong></button>
        <button type="button" onClick={() => onOpenTasks("completed")}><CheckCircle2 size={17} /><span>已结束</span><strong>{completed}</strong></button>
      </section>

      <section className="proc-todo-cards" aria-label="待办中心">
        <button type="button" className={pendingReviews ? "attention" : ""} onClick={() => onOpenView("reviews")}>
          <span className="proc-todo-icon"><ClipboardCheck size={18} /></span>
          <div><strong>待我审批</strong><small>人工审核队列中的决策</small></div>
          <i>{pendingReviews}</i>
        </button>
        <button type="button" className={counts?.orders_shipped ? "attention" : ""} onClick={onOpenOrders}>
          <span className="proc-todo-icon"><PackageCheck size={18} /></span>
          <div><strong>待收货订单</strong><small>已发货待确认收货</small></div>
          <i>{counts?.orders_shipped ?? 0}</i>
        </button>
        <button type="button" className={overdueTotal ? "danger" : ""} onClick={onOpenOrders}>
          <span className="proc-todo-icon"><Clock3 size={18} /></span>
          <div><strong>逾期订单</strong><small>发货/付款逾期提醒</small></div>
          <i>{overdueTotal}</i>
        </button>
        <button type="button" className={aiIssues ? "danger" : ""} onClick={() => onOpenView("ai")}>
          <span className="proc-todo-icon"><Bot size={18} /></span>
          <div><strong>AI 异常</strong><small>失败、取消或输入已过期</small></div>
          <i>{aiIssues}</i>
        </button>
      </section>

      <div className="proc-home-grid">
        <section className="proc-home-section">
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
        </section>

        <section className="proc-home-section">
          <header><div><h2>异常与风险</h2><span>{aiIssues + (counts?.suppliers_blacklisted ?? 0) + overdueTotal} 项</span></div></header>
          <div className="proc-home-alerts">
            <button type="button" className={aiIssues ? "danger" : "quiet"} onClick={() => onOpenView("ai")}>
              <AlertTriangle size={17} /><span><strong>{aiIssues} 个 AI 任务需处理</strong><small>失败、取消或输入已过期</small></span><ArrowRight size={14} />
            </button>
            <button type="button" className={(counts?.suppliers_blacklisted ?? 0) ? "danger" : "quiet"} onClick={onOpenSuppliers}>
              <Users size={17} /><span><strong>{(counts?.suppliers_blacklisted ?? 0)} 家供应商进入黑名单</strong><small>绩效分已封顶 30，建议复核合作状态</small></span><ArrowRight size={14} />
            </button>
            <button type="button" className={overdueTotal ? "warning" : "quiet"} onClick={onOpenOrders}>
              <CreditCard size={17} /><span><strong>{overdueTotal} 项逾期风险</strong><small>发货逾期 {counts?.overdue_orders ?? 0} · 付款逾期 {counts?.overdue_payments ?? 0}</small></span><ArrowRight size={14} />
            </button>
            <button type="button" className={pendingReviews ? "warning" : "quiet"} onClick={() => onOpenView("reviews")}>
              <ClipboardCheck size={17} /><span><strong>{pendingReviews} 项等待人工审核</strong><small>按风险和等待时间排序</small></span><ArrowRight size={14} />
            </button>
          </div>
        </section>
      </div>

      <section className="proc-home-section recent">
        <header><div><h2>最近任务</h2><span>{recent.length} 条</span></div><button type="button" onClick={() => onOpenTasks("all")}>采购任务<ArrowRight size={14} /></button></header>
        {recent.length ? (
          <div className="proc-home-table" role="table" aria-label="最近采购任务">
            <div role="row"><span>采购编号</span><span>任务</span><span>报价</span><span>状态</span><span>更新时间</span></div>
            {recent.map((request) => (
              <button role="row" type="button" key={request.id} onClick={() => onOpenTask(request.id)}>
                <code>{request.reference}</code><strong>{request.title}</strong><span>{request.quote_count} 家</span><span>{request.status}</span><time>{shortDate(request.updated_at)}</time>
              </button>
            ))}
          </div>
        ) : <div className="proc-home-empty"><ListTodo size={22} /><span>{loading ? "正在读取任务" : "尚无采购任务"}</span></div>}
      </section>
    </div>
  );
}
