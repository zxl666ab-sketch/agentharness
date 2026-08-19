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
import { useEffect, useRef, useState } from "react";

import { newIdempotencyKey, procurementApi } from "./api";
import type { OrderStatus, OrderView, SettlementStatus, SettlementView } from "./types";
import { fulfillmentNextStep } from "./viewModel";

const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  PENDING_SHIPMENT: "待发货",
  SHIPPED: "已发货",
  PARTIALLY_RECEIVED: "部分收货",
  RECEIVED: "已收货",
  CLOSED: "已关闭",
};

const SETTLEMENT_STATUS_LABELS: Record<SettlementStatus, string> = {
  UNSETTLED: "未对账",
  SETTLED: "已对账",
  PAID: "已付款",
};

const INVOICE_STATUS_LABELS = {
  REGISTERED: "匹配处理中",
  MATCHED: "已匹配待核销",
  DIFF_HOLD: "差异挂起",
  VOIDED: "已作废",
  RECONCILED: "已核销",
} as const;

const STATUS_FILTERS: Array<{ value: OrderStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "PENDING_SHIPMENT", label: "待发货" },
  { value: "SHIPPED", label: "已发货" },
  { value: "PARTIALLY_RECEIVED", label: "部分收货" },
  { value: "RECEIVED", label: "已收货" },
  { value: "CLOSED", label: "已关闭" },
];

function statusTone(status: OrderStatus) {
  return {
    PENDING_SHIPMENT: "warning",
    SHIPPED: "info",
    PARTIALLY_RECEIVED: "warning",
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

type DecimalValue = { coefficient: bigint; scale: number };

function parseDecimal(value: string): DecimalValue | null {
  const match = value.trim().match(/^([+-]?)(\d+)(?:\.(\d+))?$/);
  if (!match) return null;
  const fraction = match[3] || "";
  const digits = `${match[2]}${fraction}`.replace(/^0+(?=\d)/, "");
  const sign = match[1] === "-" ? -1n : 1n;
  return { coefficient: sign * BigInt(digits), scale: fraction.length };
}

function alignDecimals(left: DecimalValue, right: DecimalValue) {
  const scale = Math.max(left.scale, right.scale);
  return {
    left: left.coefficient * 10n ** BigInt(scale - left.scale),
    right: right.coefficient * 10n ** BigInt(scale - right.scale),
    scale,
  };
}

function formatDecimal(coefficient: bigint, scale: number): string {
  if (coefficient === 0n) return "0";
  const sign = coefficient < 0n ? "-" : "";
  const absolute = (coefficient < 0n ? -coefficient : coefficient).toString().padStart(scale + 1, "0");
  if (scale === 0) return `${sign}${absolute}`;
  const integer = absolute.slice(0, -scale);
  const fraction = absolute.slice(-scale).replace(/0+$/, "");
  return fraction ? `${sign}${integer}.${fraction}` : `${sign}${integer}`;
}

function subtractDecimals(leftValue: string, rightValue: string): string | null {
  const left = parseDecimal(leftValue);
  const right = parseDecimal(rightValue);
  if (!left || !right) return null;
  const aligned = alignDecimals(left, right);
  return formatDecimal(aligned.left - aligned.right, aligned.scale);
}

function compareDecimals(leftValue: string, rightValue: string): number | null {
  const left = parseDecimal(leftValue);
  const right = parseDecimal(rightValue);
  if (!left || !right) return null;
  const aligned = alignDecimals(left, right);
  return aligned.left < aligned.right ? -1 : aligned.left > aligned.right ? 1 : 0;
}

type Props = {
  /** 闭环衔接：聚焦指定采购任务的订单（P1-3） */
  highlightTaskId?: string | null;
  onBackToTask?: (taskId: string) => void;
};

function transitionKey(keys: Map<string, string>, scope: string, payload: unknown) {
  const fingerprint = JSON.stringify([scope, payload]);
  const existing = keys.get(fingerprint);
  if (existing) return { fingerprint, key: existing };
  const key = newIdempotencyKey();
  keys.set(fingerprint, key);
  return { fingerprint, key };
}

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
  const transitionKeys = useRef(new Map<string, string>());

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
    const input = { action: "ship" } as const;
    const request = transitionKey(transitionKeys.current, `order:${order.id}`, input);
    const ok = await run(`ship:${order.id}`, () =>
      procurementApi.transitionOrder(order.id, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setNotice(`订单 ${order.order_no} 已标记发货。`);
    }
  };

  const receive = async () => {
    if (!receiveTarget) return;
    const quantity = receiveQuantity.trim();
    const date = arrivalDate.trim();
    if (!quantity || !date) {
      setError("收货必须填写收货数量与到货日期");
      return;
    }
    const remainingQuantity = subtractDecimals(
      receiveTarget.quantity,
      receiveTarget.received_quantity ?? "0",
    );
    const quantitySign = compareDecimals(quantity, "0");
    if (quantitySign === null || quantitySign <= 0) {
      setError("收货数量必须大于 0");
      return;
    }
    if (remainingQuantity !== null && compareDecimals(quantity, remainingQuantity)! > 0) {
      setError(`本批收货数量不得超过剩余数量 ${formatQuantity(remainingQuantity)}`);
      return;
    }
    const parsedDate = parseDatePart(date);
    if (!parsedDate) {
      setError("到货日期格式无效");
      return;
    }
    const input = {
        action: "receive",
        received_quantity: quantity,
        arrival_date: parsedDate.toISOString(),
      } as const;
    const request = transitionKey(transitionKeys.current, `order:${receiveTarget.id}`, input);
    const ok = await run(`receive:${receiveTarget.id}`, () =>
      procurementApi.transitionOrder(receiveTarget.id, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setReceiveTarget(null);
      const completed = remainingQuantity !== null && compareDecimals(quantity, remainingQuantity) === 0;
      setNotice(completed
        ? `已确认最后一批收货 ${formatQuantity(quantity)}，对账单已派生。`
        : `已登记本批收货 ${formatQuantity(quantity)}，订单等待剩余到货。`);
    }
  };

  const closeOrder = async () => {
    if (!closeTarget) return;
    const input = { action: "close", notes: closeNotes.trim() || null } as const;
    const request = transitionKey(transitionKeys.current, `order:${closeTarget.id}`, input);
    const ok = await run(`close:${closeTarget.id}`, () =>
      procurementApi.transitionOrder(closeTarget.id, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setCloseTarget(null);
      setNotice(closeTarget.status === "PENDING_SHIPMENT" ? "订单已取消。" : "订单已完成关闭。");
    }
  };

  const settle = async (orderId: string, settlementId: string) => {
    const input = { action: "settle" } as const;
    const request = transitionKey(transitionKeys.current, `settlement:${settlementId}`, input);
    const ok = await run(`settle:${settlementId}`, () =>
      procurementApi.transitionSettlement(settlementId, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setNotice("已确认对账。");
    }
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
    const input = {
        action: "pay",
        paid_at: parsedDate.toISOString(),
        notes: payNotes.trim() || null,
      } as const;
    const request = transitionKey(transitionKeys.current, `settlement:${payTarget.settlementId}`, input);
    const ok = await run(`pay:${payTarget.settlementId}`, () =>
      procurementApi.transitionSettlement(payTarget.settlementId, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
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
              ? `聚焦 ${focusTask.task_reference || "采购任务"} 的订单：待发货 → 已发货 → 部分收货 / 已收货 → 对账 → 付款`
              : "订单状态机：待发货 → 已发货 → 部分收货 / 已收货 → 已关闭；累计收满后自动派生对账单"}
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
              ? "正式采购方案确认后会立即生成唯一订单；若仍未显示，请返回任务详情检查审批状态。"
              : "正式采购方案确认后会立即生成唯一订单。"}</p>
          </div>
        ) : null}
        {orders.map((order) => {
          const next = fulfillmentNextStep(order);
          return (
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
              <span><small>发票状态</small><strong>{order.invoice_count === 0 ? "尚未上传" : order.invoice_status ? INVOICE_STATUS_LABELS[order.invoice_status] : "待确认"}</strong></span>
              <span><small>对账状态</small><strong>{order.settlement ? SETTLEMENT_STATUS_LABELS[order.settlement.status] : "尚未生成"}</strong></span>
              <span className={`proc-order-next ${next.tone}`}><small>当前下一步</small><strong>{next.label}</strong><em>{next.detail}</em></span>
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
              {order.status === "SHIPPED" || order.status === "PARTIALLY_RECEIVED" ? (
                <button className="proc-button" type="button" onClick={() => {
                  const remaining = subtractDecimals(order.quantity, order.received_quantity ?? "0");
                  setReceiveTarget(order);
                  setReceiveQuantity(remaining ?? order.quantity);
                  setArrivalDate(new Date().toISOString().slice(0, 10));
                  setError(null);
                }}>
                  <PackageCheck size={14} />{order.status === "PARTIALLY_RECEIVED" ? "继续收货" : "确认收货"}
                </button>
              ) : null}
              {order.status === "RECEIVED" ? (
                <>
                  {order.settlement ? (
                    <span className="proc-settlement-inline">
                      <CreditCard size={14} />
                      {order.settlement.settlement_no} · {SETTLEMENT_STATUS_LABELS[order.settlement.status]}
                      {order.settlement.status === "UNSETTLED" && next.canSettle ? (
                        <button className="proc-link-button" type="button" disabled={busy === `settle:${order.settlement.id}`} onClick={() => void settle(order.id, order.settlement!.id)}>
                          确认对账
                        </button>
                      ) : null}
                      {order.settlement.status === "SETTLED" && next.canPay ? (
                        <button className="proc-link-button" type="button" onClick={() => { setPayTarget({ orderId: order.id, settlementId: order.settlement!.id, settlementNo: order.settlement!.settlement_no, orderNo: order.order_no }); setPaidAt(new Date().toISOString().slice(0, 10)); setError(null); setNotice(null); }}>
                          登记付款
                        </button>
                      ) : null}
                    </span>
                  ) : (
                    <span className="proc-settlement-inline muted"><CreditCard size={14} />对账单缺失</span>
                  )}
                  {next.canClose ? (
                    <button className="proc-button secondary" type="button" onClick={() => { setCloseTarget(order); setCloseNotes("订单已完成"); setError(null); }}>
                      <CheckCircle2 size={14} />完成关闭
                    </button>
                  ) : null}
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
          );
        })}
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
                <small>已收 {formatQuantity(receiveTarget.received_quantity)}，本批不得超过剩余数量 {formatQuantity(subtractDecimals(receiveTarget.quantity, receiveTarget.received_quantity ?? "0"))}</small>
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
                {busy === `receive:${receiveTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <PackageCheck size={15} />}登记本批收货
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
        <header><div><CalendarCheck2 size={15} /><h3>对账单</h3></div><span>累计收满自动派生 · 状态机 UNSETTLED → SETTLED → PAID</span></header>
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
  const transitionKeys = useRef(new Map<string, string>());
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
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function settle(settlementId: string) {
    const input = { action: "settle" } as const;
    const request = transitionKey(transitionKeys.current, `settlement:${settlementId}`, input);
    const ok = await run(`row-settle:${settlementId}`, () =>
      procurementApi.transitionSettlement(settlementId, input, request.key));
    if (ok) transitionKeys.current.delete(request.fingerprint);
  }

  return (
    <div className="proc-settlement-table">
      {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
      {settlementsQuery.isPending ? <div className="proc-loading-state"><LoaderCircle className="spin" size={16} />正在加载对账单…</div> : null}
      {!settlementsQuery.isPending && !settlements.length ? <p className="proc-muted">暂无对账单（订单累计收满后自动派生）</p> : null}
      {settlements.map((settlement) => (
        <div className="proc-settlement-row" key={settlement.id}>
          <code>{settlement.settlement_no}</code>
          <span>{settlement.supplier_name}</span>
          <strong>{formatAmount(settlement.total_amount)}</strong>
          <i className={settlementTone(settlement.status)}>{SETTLEMENT_STATUS_LABELS[settlement.status]}</i>
          {settlement.status === "SETTLED" && settlement.invoice_reconciled ? (
            <button className="proc-link-button" type="button" disabled={busy === `row-pay:${settlement.id}`} onClick={() => onPay(settlement)}>
              登记付款
            </button>
          ) : null}
          {settlement.status === "SETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-muted">付款被拦截：请先核销全部有效发票</small>
          ) : null}
          {settlement.status === "UNSETTLED" && settlement.invoice_reconciled ? (
            <button className="proc-link-button" type="button" disabled={busy === `row-settle:${settlement.id}`} onClick={() => void settle(settlement.id)}>
              确认对账
            </button>
          ) : null}
          {settlement.status === "UNSETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-muted">发票核销后可对账</small>
          ) : null}
          {settlement.status === "PAID" && settlement.paid_at ? <small>{new Date(settlement.paid_at).toLocaleDateString("zh-CN")}</small> : null}
        </div>
      ))}
    </div>
  );
}
