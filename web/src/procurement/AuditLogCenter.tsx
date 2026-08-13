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
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>审计日志</h1>
          <p>全量事件留痕：类型 / 操作人 / 业务对象 / 任务筛选（V11 通用业务定位）</p>
        </div>
        <span className="proc-page-count">共 {total} 条</span>
      </header>

      <div className="proc-toolbar" role="toolbar">
        <label className="proc-search proc-toolbar-search">
          <Search size={15} />
          <input aria-label="事件类型" value={type} onChange={(event) => { setType(event.target.value); setPage(0); }} placeholder="事件类型，如 order_created" />
        </label>
        <input className="proc-select proc-filter-input" aria-label="操作人" value={actor} onChange={(event) => { setActor(event.target.value); setPage(0); }} placeholder="操作人" />
        <select className="proc-select" aria-label="业务对象类型" value={businessType} onChange={(event) => { setBusinessType(event.target.value); setPage(0); }}>
          <option value="">全部业务对象</option>
          <option value="task">task</option>
          <option value="supplier">supplier</option>
          <option value="order">order</option>
          <option value="settlement">settlement</option>
        </select>
        <input className="proc-select proc-filter-input" aria-label="任务ID" value={taskId} onChange={(event) => { setTaskId(event.target.value); setPage(0); }} placeholder="task_id（可选）" />
      </div>

      <div className="proc-audit-list" aria-busy={query.isPending}>
        {query.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载审计日志…</div>
        ) : null}
        {query.isError ? (
          <section className="proc-empty-state compact" role="alert">
            <AlertTriangle size={26} />
            <h2>审计日志加载失败</h2>
            <p>{query.error instanceof Error ? query.error.message : "未知错误"}</p>
            <button className="proc-button secondary" type="button" onClick={() => void query.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!query.isPending && !query.isError && !items.length ? (
          <div className="proc-empty-state"><Activity size={30} /><h2>没有匹配的审计事件</h2><p>调整筛选条件后重试。</p></div>
        ) : null}
        {items.map((event) => (
          <article className="proc-audit-row" key={event.id}>
            <span className="proc-audit-type"><i />{eventLabel(event.event_type)}</span>
            <code>{event.event_type}</code>
            <span className="proc-audit-scope">
              {event.business_type ? <small>{event.business_type}:{event.business_id?.slice(0, 8)}</small> : null}
              {event.task_reference ? <small>{event.task_reference}</small> : event.task_id ? <small>task:{event.task_id.slice(0, 8)}</small> : null}
            </span>
            <span className="proc-audit-actor">{event.actor}</span>
            <time>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })}</time>
          </article>
        ))}
        {totalPages > 1 ? (
          <footer className="proc-task-pagination">
            <button type="button" title="上一页" aria-label="上一页" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft size={15} /></button>
            <span>{page + 1} / {totalPages}</span>
            <button type="button" title="下一页" aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}><ChevronRight size={15} /></button>
          </footer>
        ) : null}
      </div>
    </section>
  );
}
