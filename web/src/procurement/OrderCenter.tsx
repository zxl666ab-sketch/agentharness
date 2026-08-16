import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  CalendarCheck2,
  CheckCircle2,
  CreditCard,
  Download,
  LoaderCircle,
  PackageCheck,
  Truck,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { procurementApi } from "./api";
import type { OrderStatus, OrderView, SettlementStatus, SettlementView } from "./types";

const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  PENDING_SHIPMENT: "待发货",
  SHIPPED: "已发货",
  RECEIVED: "已收货",
  CLOSED: "已关闭",
};

const SETTLEMENT_STATUS_LABELS: Record<SettlementStatus, string> = {
  UNSETTLED: "未对账",
  SETTLED: "已对账",
  PAID: "已付款",
};

const STATUS_FILTERS: Array<{ value: OrderStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "PENDING_SHIPMENT", label: "待发货" },
  { value: "SHIPPED", label: "已发货" },
  { value: "RECEIVED", label: "已收货" },
  { value: "CLOSED", label: "已关闭" },
];

function statusTone(status: OrderStatus) {
  return {
    PENDING_SHIPMENT: "warning",
    SHIPPED: "info",
    RECEIVED: "success",
    CLOSED: "neutral",
  }[status];
}

function settlementTone(status: SettlementStatus) {
  return {
    UNSETTLED: "warning",
    SETTLED: "info",
    PAID: "success",
  }[status];
}

function formatAmount(value: string | null) {
  if (value === null) return "未补录成本";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatQuantity(value: string | null) {
  if (value === null) return "—";
  const quantity = Number(value);
  if (!Number.isFinite(quantity)) return value;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 3 }).format(quantity);
}

type Props = {
  /** 闭环衔接：聚焦指定采购任务的订单（P1-3） */
  highlightTaskId?: string | null;
  onBackToTask?: (taskId: string) => void;
};

export function OrderCenter({ highlightTaskId = null, onBackToTask }: Props) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<OrderStatus | "">("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [receiveTarget, setReceiveTarget] = useState<OrderView | null>(null);
  const [receiveQuantity, setReceiveQuantity] = useState("");
  const [arrivalDate, setArrivalDate] = useState("");
  const [closeTarget, setCloseTarget] = useState<OrderView | null>(null);
  const [closeNotes, setCloseNotes] = useState("");
  const [payTarget, setPayTarget] = useState<{ orderId: string | null; settlementId: string; settlementNo: string; orderNo: string | null } | null>(null);
  const [paidAt, setPaidAt] = useState("");
  const [payNotes, setPayNotes] = useState("");

  const ordersQuery = useQuery({
    queryKey: ["procurement-orders", status],
    queryFn: () => procurementApi.orders(status || undefined, 0, 100),
    refetchInterval: 10_000,
  });
  const allOrders = ordersQuery.data?.items || [];
  const focused = highlightTaskId ? allOrders.filter((item) => item.task_id === highlightTaskId) : null;
  const orders = focused ?? allOrders;
  const focusTask = highlightTaskId && focused ? focused[0] : null;

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-orders"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-settlements"] }),
    ]);

  /** Run an async order/settlement operation; resolves true only on success so
   *  callers can keep dialogs open with user input intact when the API fails. */
  async function run<T>(key: string, operation: () => Promise<T>): Promise<boolean> {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await operation();
      await invalidate();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy(null);
    }
  }

  function parseDatePart(value: string) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  useEffect(() => {
    if (!receiveTarget && !closeTarget && !payTarget) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (receiveTarget && busy !== `receive:${receiveTarget.id}`) setReceiveTarget(null);
      if (closeTarget && busy !== `close:${closeTarget.id}`) setCloseTarget(null);
      if (payTarget && busy !== `pay:${payTarget.settlementId}`) setPayTarget(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, closeTarget, payTarget, receiveTarget]);

  const ship = async (order: OrderView) => {
    const ok = await run(`ship:${order.id}`, () =>
      procurementApi.transitionOrder(order.id, { action: "ship" }));
    if (ok) setNotice(`订单 ${order.order_no} 已标记发货。`);
  };

  const receive = async () => {
    if (!receiveTarget) return;
    const quantity = receiveQuantity.trim();
    const date = arrivalDate.trim();
    if (!quantity || !date) {
      setError("收货必须填写收货数量与到货日期");
      return;
    }
    const qty = Number(quantity);
    const orderQty = Number(receiveTarget.quantity);
    if (!Number.isFinite(qty) || qty <= 0) {
      setError("收货数量必须大于 0");
      return;
    }
    if (Number.isFinite(orderQty) && qty > orderQty) {
      setError(`收货数量不得超过订单数量 ${orderQty.toLocaleString("zh-CN")}`);
      return;
    }
    const parsedDate = parseDatePart(date);
    if (!parsedDate) {
      setError("到货日期格式无效");
      return;
    }
    const ok = await run(`receive:${receiveTarget.id}`, () =>
      procurementApi.transitionOrder(receiveTarget.id, {
        action: "receive",
        received_quantity: quantity,
        arrival_date: parsedDate.toISOString(),
      }));
    if (ok) {
      setReceiveTarget(null);
      setNotice(`已确认收货 ${qty.toLocaleString("zh-CN")}，对账单已派生。`);
    }
  };

  const closeOrder = async () => {
    if (!closeTarget) return;
    const ok = await run(`close:${closeTarget.id}`, () =>
      procurementApi.transitionOrder(closeTarget.id, {
        action: "close",
        notes: closeNotes.trim() || null,
      }));
    if (ok) {
      setCloseTarget(null);
      setNotice(closeTarget.status === "PENDING_SHIPMENT" ? "订单已取消。" : "订单已完成关闭。");
    }
  };

  const settle = async (orderId: string, settlementId: string) => {
    const ok = await run(`settle:${settlementId}`, () =>
      procurementApi.transitionSettlement(settlementId, { action: "settle" }));
    if (ok) setNotice("已确认对账。");
  };

  const pay = async () => {
    if (!payTarget) return;
    const date = paidAt.trim();
    if (!date) {
      setError("付款必须填写付款时间");
      return;
    }
    const parsedDate = parseDatePart(date);
    if (!parsedDate) {
      setError("付款时间格式无效");
      return;
    }
    const ok = await run(`pay:${payTarget.settlementId}`, () =>
      procurementApi.transitionSettlement(payTarget.settlementId, {
        action: "pay",
        paid_at: parsedDate.toISOString(),
        notes: payNotes.trim() || null,
      }));
    if (ok) {
      setPayTarget(null);
      setNotice("已登记付款。");
    }
  };

  return (
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>{highlightTaskId ? "任务订单" : "采购订单"}</h1>
          <p>
            {focusTask
              ? `聚焦 ${focusTask.task_reference || "采购任务"} 的订单：待发货 → 已发货 → 已收货 → 对账 → 付款`
              : "订单状态机：待发货 → 已发货 → 已收货 → 已关闭；收货自动派生对账单"}
          </p>
        </div>
        <span className="proc-page-count">
          {highlightTaskId && onBackToTask ? (
            <button type="button" className="proc-link-button" onClick={() => onBackToTask(highlightTaskId)}>← 返回采购任务</button>
          ) : `共 ${ordersQuery.data?.total ?? 0} 张`}
        </span>
      </header>

      <div className="proc-toolbar" role="toolbar">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`proc-filter-chip ${status === option.value ? "active" : ""}`}
            onClick={() => setStatus(option.value)}
          >
            {option.label}
          </button>
        ))}
        {error ? <span className="proc-toolbar-error" role="alert">{error}</span> : null}
        {notice ? <span className="proc-toolbar-success" role="status">{notice}</span> : null}
      </div>

      <div className="proc-order-list" aria-busy={ordersQuery.isPending}>
        {ordersQuery.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载订单…</div>
        ) : null}
        {ordersQuery.isError ? (
          <section className="proc-empty-state compact" role="alert">
            <AlertTriangle size={26} />
            <h2>订单加载失败</h2>
            <p>{ordersQuery.error instanceof Error ? ordersQuery.error.message : "未知错误"}</p>
            <button className="proc-button secondary" type="button" onClick={() => void ordersQuery.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!ordersQuery.isPending && !ordersQuery.isError && !orders.length ? (
          <div className="proc-empty-state">
            <Archive size={30} />
            <h2>{highlightTaskId ? "该任务还没有订单" : status ? "该状态下没有订单" : "还没有采购订单"}</h2>
            <p>{highlightTaskId
              ? "已批准任务会在打开本页时自动派生订单（惰性派生，幂等），刷新后重试。"
              : "已批准任务会在打开本页时自动派生订单（惰性派生，幂等）。"}</p>
          </div>
        ) : null}
        {orders.map((order) => (
          <article className="proc-order-card" key={order.id}>
            <header>
              <div className="proc-order-title">
                <code>{order.order_no}</code>
                <strong>{order.item_name} × {formatQuantity(order.quantity)} {order.unit}</strong>
                <small>{order.task_reference} · {order.task_title}</small>
              </div>
              <span className={`proc-status ${statusTone(order.status)}`}><i />{ORDER_STATUS_LABELS[order.status]}</span>
            </header>
            <div className="proc-order-facts">
              <span><small>供应商</small><strong>{order.supplier_name}</strong></span>
              <span><small>到货总价</small><strong>{formatAmount(order.landed_total)}</strong></span>
              <span><small>收货数量</small><strong>{formatQuantity(order.received_quantity)}</strong></span>
              <span><small>到货日期</small><strong>{order.arrival_date ? new Date(order.arrival_date).toLocaleDateString("zh-CN") : "—"}</strong></span>
            </div>
            <div className="proc-order-actions">
              {order.status === "PENDING_SHIPMENT" ? (
                <>
                  <button className="proc-button" type="button" disabled={busy === `ship:${order.id}`} onClick={() => void ship(order)}>
                    {busy === `ship:${order.id}` ? <LoaderCircle className="spin" size={14} /> : <Truck size={14} />}标记发货
                  </button>
                  <button className="proc-button secondary" type="button" onClick={() => { setCloseTarget(order); setCloseNotes(""); setError(null); }}>
                    <X size={14} />取消订单
                  </button>
                </>
              ) : null}
              {order.status === "SHIPPED" ? (
                <button className="proc-button" type="button" onClick={() => { setReceiveTarget(order); setReceiveQuantity(order.quantity); setArrivalDate(new Date().toISOString().slice(0, 10)); setError(null); }}>
                  <PackageCheck size={14} />确认收货
                </button>
              ) : null}
              {order.status === "RECEIVED" ? (
                <>
                  {order.settlement ? (
                    <span className="proc-settlement-inline">
                      <CreditCard size={14} />
                      {order.settlement.settlement_no} · {SETTLEMENT_STATUS_LABELS[order.settlement.status]}
                      {order.settlement.status === "UNSETTLED" ? (
                        <button className="proc-link-button" type="button" disabled={busy === `settle:${order.settlement.id}`} onClick={() => void settle(order.id, order.settlement!.id)}>
                          确认对账
                        </button>
                      ) : null}
                      {order.settlement.status === "SETTLED" ? (
                        <button className="proc-link-button" type="button" onClick={() => { setPayTarget({ orderId: order.id, settlementId: order.settlement!.id, settlementNo: order.settlement!.settlement_no, orderNo: order.order_no }); setPaidAt(new Date().toISOString().slice(0, 10)); setError(null); setNotice(null); }}>
                          登记付款
                        </button>
                      ) : null}
                    </span>
                  ) : (
                    <span className="proc-settlement-inline muted"><CreditCard size={14} />对账单缺失</span>
                  )}
                  <button className="proc-button secondary" type="button" onClick={() => { setCloseTarget(order); setCloseNotes("订单已完成"); setError(null); }}>
                    <CheckCircle2 size={14} />完成关闭
                  </button>
                </>
              ) : null}
            </div>
            <footer className="proc-order-footer">
              {order.artifacts.length ? order.artifacts.map((artifact) => (
                <a
                  key={artifact.id}
                  className="proc-artifact-link"
                  href={`/api/artifacts/${artifact.id}/raw`}
                  title={artifact.filename}
                >
                  <Download size={13} />
                  {artifact.kind === "supplier_confirmation_email" ? "供应商确认邮件" : "订单草稿"}
                </a>
              )) : <span className="proc-muted">暂无订单工件</span>}
              <small>更新于 {new Date(order.updated_at).toLocaleString("zh-CN")}</small>
            </footer>
          </article>
        ))}
      </div>

      {receiveTarget ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `receive:${receiveTarget.id}`) setReceiveTarget(null);
          }}
        >
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="receive-title">
            <header>
              <div><PackageCheck size={17} /><h2 id="receive-title">确认收货</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setReceiveTarget(null)} disabled={busy === `receive:${receiveTarget.id}`}><X size={16} /></button>
            </header>
            <div className="proc-supplier-form">
              <label className="proc-field">
                <span>收货数量 <b>*</b></span>
                <input type="number" min="0" step="any" value={receiveQuantity} onChange={(event) => setReceiveQuantity(event.target.value)} />
                <small>不得超过订单数量 {formatQuantity(receiveTarget.quantity)}</small>
              </label>
              <label className="proc-field">
                <span>到货日期 <b>*</b></span>
                <input type="date" value={arrivalDate} onChange={(event) => setArrivalDate(event.target.value)} />
              </label>
              {error ? <p className="proc-form-error proc-span-2" role="alert">{error}</p> : null}
            </div>
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setReceiveTarget(null)} disabled={busy === `receive:${receiveTarget.id}`}>取消</button>
              <button className="proc-button" type="button" disabled={busy === `receive:${receiveTarget.id}`} onClick={() => void receive()}>
                {busy === `receive:${receiveTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <PackageCheck size={15} />}确认收货并派生对账单
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {closeTarget ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `close:${closeTarget.id}`) setCloseTarget(null);
          }}
        >
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="close-title">
            <header>
              <div><X size={17} /><h2 id="close-title">{closeTarget.status === "PENDING_SHIPMENT" ? "取消订单" : "完成关闭订单"}</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setCloseTarget(null)} disabled={busy === `close:${closeTarget.id}`}><X size={16} /></button>
            </header>
            <div className="proc-delete-target">
              <strong>{closeTarget.order_no}</strong>
              <span>{closeTarget.supplier_name} · {closeTarget.item_name}</span>
            </div>
            <label className="proc-field">
              <span>备注</span>
              <input value={closeNotes} onChange={(event) => setCloseNotes(event.target.value)} placeholder={closeTarget.status === "PENDING_SHIPMENT" ? "取消原因（可选）" : "完成备注（可选）"} />
            </label>
            {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setCloseTarget(null)} disabled={busy === `close:${closeTarget.id}`}>取消</button>
              <button className={`proc-button ${closeTarget.status === "PENDING_SHIPMENT" ? "danger" : ""}`} type="button" disabled={busy === `close:${closeTarget.id}`} onClick={() => void closeOrder()}>
                {busy === `close:${closeTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}{closeTarget.status === "PENDING_SHIPMENT" ? "确认取消" : "确认完成"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {payTarget ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `pay:${payTarget.settlementId}`) setPayTarget(null);
          }}
        >
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="pay-title">
            <header>
              <div><CreditCard size={17} /><h2 id="pay-title">登记付款</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setPayTarget(null)} disabled={busy === `pay:${payTarget.settlementId}`}><X size={16} /></button>
            </header>
            <div className="proc-delete-target">
              <strong>{payTarget.orderNo || "对账单 " + payTarget.settlementNo}</strong>
              <span>对账单 {payTarget.settlementNo} · {payTarget.settlementId.slice(0, 8)}…</span>
            </div>
            <div className="proc-supplier-form">
              <label className="proc-field">
                <span>付款时间 <b>*</b></span>
                <input type="date" value={paidAt} onChange={(event) => setPaidAt(event.target.value)} />
              </label>
              <label className="proc-field">
                <span>备注</span>
                <input value={payNotes} onChange={(event) => setPayNotes(event.target.value)} placeholder="付款方式等（可选）" />
              </label>
              {error ? <p className="proc-form-error proc-span-2" role="alert">{error}</p> : null}
            </div>
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setPayTarget(null)} disabled={busy === `pay:${payTarget.settlementId}`}>取消</button>
              <button className="proc-button" type="button" disabled={busy === `pay:${payTarget.settlementId}`} onClick={() => void pay()}>
                {busy === `pay:${payTarget.settlementId}` ? <LoaderCircle className="spin" size={15} /> : <CreditCard size={15} />}确认付款
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      <div className="proc-settlement-section">
        <header><div><CalendarCheck2 size={15} /><h3>对账单</h3></div><span>收货自动派生 · 状态机 UNSETTLED → SETTLED → PAID</span></header>
        <SettlementTable
          onPay={(settlement) => {
            setPayTarget({ orderId: null, settlementId: settlement.id, settlementNo: settlement.settlement_no, orderNo: null });
            setPaidAt(new Date().toISOString().slice(0, 10));
            setError(null);
            setNotice(null);
          }}
        />
      </div>
    </section>
  );
}

function SettlementTable({ onPay }: { onPay: (settlement: SettlementView) => void }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const settlementsQuery = useQuery({
    queryKey: ["procurement-settlements"],
    queryFn: () => procurementApi.settlements(undefined, 0, 100),
  });
  const settlements = settlementsQuery.data?.items || [];

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["procurement-settlements"] });

  async function run<T>(key: string, operation: () => Promise<T>) {
    setBusy(key);
    setError(null);
    try {
      await operation();
      await invalidate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="proc-settlement-table">
      {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
      {settlementsQuery.isPending ? <div className="proc-loading-state"><LoaderCircle className="spin" size={16} />正在加载对账单…</div> : null}
      {!settlementsQuery.isPending && !settlements.length ? <p className="proc-muted">暂无对账单（订单收货后自动派生）</p> : null}
      {settlements.map((settlement) => (
        <div className="proc-settlement-row" key={settlement.id}>
          <code>{settlement.settlement_no}</code>
          <span>{settlement.supplier_name}</span>
          <strong>{formatAmount(settlement.total_amount)}</strong>
          <i className={settlementTone(settlement.status)}>{SETTLEMENT_STATUS_LABELS[settlement.status]}</i>
          {settlement.status === "SETTLED" ? (
            <button className="proc-link-button" type="button" disabled={busy === `row-pay:${settlement.id}`} onClick={() => onPay(settlement)}>
              登记付款
            </button>
          ) : null}
          {settlement.status === "UNSETTLED" ? (
            <button className="proc-link-button" type="button" disabled={busy === `row-settle:${settlement.id}`} onClick={() => void run(`row-settle:${settlement.id}`, () => procurementApi.transitionSettlement(settlement.id, { action: "settle" }))}>
              确认对账
            </button>
          ) : null}
          {settlement.status === "PAID" && settlement.paid_at ? <small>{new Date(settlement.paid_at).toLocaleDateString("zh-CN")}</small> : null}
        </div>
      ))}
    </div>
  );
}
