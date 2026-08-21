import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle, Search } from "lucide-react";
import { useState } from "react";

import { procurementApi } from "./api";

const EVENT_TYPE_LABELS: Record<string, string> = {
  demo_seed_created: "演示数据预置",
  demo_seed_approved: "演示数据审批",
  order_created: "订单派生",
  order_transitioned: "订单流转",
  order_shipment_overdue: "发货逾期",
  settlement_created: "对账派生",
  settlement_settled: "对账确认",
  settlement_paid: "付款登记",
  settlement_payment_overdue: "付款逾期",
  supplier_created: "供应商建档",
  supplier_updated: "供应商更新",
  supplier_status_changed: "供应商状态变更",
  supplier_deleted: "供应商删除",
  supplier_approval_requested: "审批请求",
  procurement_decision_finalized: "审批终决",
  comparison_snapshot_created: "比价快照",
};

function eventLabel(type: string) {
  return EVENT_TYPE_LABELS[type] || type;
}

export function AuditLogCenter() {
  const [type, setType] = useState("");
  const [actor, setActor] = useState("");
  const [businessType, setBusinessType] = useState("");
  const [taskId, setTaskId] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const query = useQuery({
    queryKey: ["procurement-audit-events", type, actor, businessType, taskId, page],
    queryFn: () =>
      procurementApi.auditEvents({
        type: type || undefined,
        actor: actor || undefined,
        business_type: businessType || undefined,
        task_id: taskId || undefined,
        page,
        size: pageSize,
      }),
  });
  const items = query.data?.items || [];
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <Activity className="w-5 h-5 text-accent" />
            审计日志
          </h1>
          <p className="text-xs text-text-muted mt-1">全量事件留痕：类型 / 操作人 / 业务对象 / 任务筛选（V11 通用业务定位）</p>
        </div>
        <span className="proc-page-count text-xs font-medium text-text-secondary bg-surface-subtle px-3 py-1 rounded-full border border-border">共 {total} 条</span>
      </header>

      <div className="proc-toolbar flex flex-wrap items-center gap-3 p-4 rounded-xl glass-panel bg-surface/80 border border-border/80 shadow-sm" role="toolbar">
        <label className="proc-search proc-toolbar-search flex-1 min-w-[200px] flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus-within:border-accent">
          <Search size={15} className="text-text-muted" />
          <input className="w-full bg-transparent border-none outline-none text-xs text-text placeholder:text-text-muted" aria-label="事件类型" value={type} onChange={(event) => { setType(event.target.value); setPage(0); }} placeholder="事件类型，如 order_created" />
        </label>
        <input className="proc-select proc-filter-input px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" aria-label="操作人" value={actor} onChange={(event) => { setActor(event.target.value); setPage(0); }} placeholder="操作人" />
        <select className="proc-select px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" aria-label="业务对象类型" value={businessType} onChange={(event) => { setBusinessType(event.target.value); setPage(0); }}>
          <option value="">全部业务对象</option>
          <option value="task">task</option>
          <option value="supplier">supplier</option>
          <option value="order">order</option>
          <option value="settlement">settlement</option>
        </select>
        <input className="proc-select proc-filter-input px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent font-mono" aria-label="任务ID" value={taskId} onChange={(event) => { setTaskId(event.target.value); setPage(0); }} placeholder="task_id（可选）" />
      </div>

      <div className="proc-audit-list flex flex-col divide-y divide-border/40 rounded-xl border border-border/80 bg-surface/80 glass-panel overflow-hidden shadow-sm" aria-busy={query.isPending}>
        {query.isPending ? (
          <div className="proc-loading-state py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在加载审计日志…</div>
        ) : null}
        {query.isError ? (
          <section className="proc-empty-state compact py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
            <AlertTriangle size={26} className="text-danger" />
            <h2 className="text-sm font-semibold text-text">审计日志加载失败</h2>
            <p className="text-text-muted">{query.error instanceof Error ? query.error.message : "未知错误"}</p>
            <button className="proc-button secondary px-3 py-1.5 rounded-lg border border-border text-xs font-medium hover:bg-surface-subtle" type="button" onClick={() => void query.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!query.isPending && !query.isError && !items.length ? (
          <div className="proc-empty-state py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted"><Activity size={30} className="text-text-muted" /><h2 className="text-sm font-semibold text-text">没有匹配的审计事件</h2><p>调整筛选条件后重试。</p></div>
        ) : null}
        {items.map((event) => (
          <article className="proc-audit-row flex flex-wrap items-center justify-between gap-3 p-3.5 hover:bg-surface-subtle/50 transition-colors text-xs" key={event.id}>
            <span className="proc-audit-type inline-flex items-center gap-1.5 font-semibold text-text"><i className="w-1.5 h-1.5 rounded-full bg-accent" />{eventLabel(event.event_type)}</span>
            <code className="font-mono text-accent bg-accent-soft/40 px-2 py-0.5 rounded text-[11px]">{event.event_type}</code>
            <span className="proc-audit-scope flex items-center gap-2 text-text-muted font-mono text-[11px]">
              {event.business_type ? <small className="bg-surface-subtle px-1.5 py-0.5 rounded border border-border/40">{event.business_type}:{event.business_id?.slice(0, 8)}</small> : null}
              {event.task_reference ? <small className="bg-surface-subtle px-1.5 py-0.5 rounded border border-border/40">{event.task_reference}</small> : event.task_id ? <small className="bg-surface-subtle px-1.5 py-0.5 rounded border border-border/40">task:{event.task_id.slice(0, 8)}</small> : null}
            </span>
            <span className="proc-audit-actor font-medium text-text-secondary">{event.actor}</span>
            <time className="font-mono text-[11px] text-text-muted">{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time>
          </article>
        ))}
        {totalPages > 1 ? (
          <footer className="proc-task-pagination flex items-center justify-center gap-3 p-3 text-xs text-text-muted bg-surface-subtle/30 border-t border-border/40">
            <button className="w-8 h-8 rounded-lg border border-border flex items-center justify-center text-text hover:bg-surface disabled:opacity-40" type="button" title="上一页" aria-label="上一页" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft size={15} /></button>
            <span className="font-medium">{page + 1} / {totalPages}</span>
            <button className="w-8 h-8 rounded-lg border border-border flex items-center justify-center text-text hover:bg-surface disabled:opacity-40" type="button" title="下一页" aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}><ChevronRight size={15} /></button>
          </footer>
        ) : null}
      </div>
    </div>
  );
}
