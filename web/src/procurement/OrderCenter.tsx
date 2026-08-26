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
import { memo, useCallback, useEffect, useRef, useState } from "react";

import { newIdempotencyKey, procurementApi } from "./api";
import type { OrderStatus, OrderView, SettlementStatus, SettlementView } from "./types";
import { fulfillmentNextStep } from "./viewModel";
import { useModalFocus } from "./useModalFocus";

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

type OrderCardProps = {
  order: OrderView;
  busy: string | null;
  onShip: (order: OrderView) => void;
  onSettle: (orderId: string, settlementId: string) => void;
  onOpenReceive: (order: OrderView) => void;
  onCloseRequest: (order: OrderView, notes: string) => void;
  onOpenPay: (order: OrderView) => void;
};

/** 单张订单卡（memo 化：弹窗表单打字时 props 不变，卡片不重渲）。 */
const OrderCard = memo(function OrderCard({
  order,
  busy,
  onShip,
  onSettle,
  onOpenReceive,
  onCloseRequest,
  onOpenPay,
}: OrderCardProps) {
  const next = fulfillmentNextStep(order);
  return (
    <article className="proc-order-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 hover:border-accent/40 rounded-xl p-5 shadow-sm hover:shadow-md transition-all duration-150 flex flex-col justify-between gap-4">
      <header className="flex items-start justify-between gap-3">
        <div className="proc-order-title flex flex-col gap-1 min-w-0">
          <code className="font-mono text-xs font-semibold text-accent">{order.order_no}</code>
          <strong className="text-sm font-bold text-text truncate">{order.item_name} × {formatQuantity(order.quantity)} {order.unit}</strong>
          <small className="text-[11px] text-text-muted truncate">{order.task_reference} · {order.task_title}</small>
        </div>
        <span className={`proc-status ${statusTone(order.status)} flex-shrink-0 text-xs font-medium px-2.5 py-0.5 rounded-full border inline-flex items-center gap-1.5`}><i className="w-1.5 h-1.5 rounded-full bg-current" />{ORDER_STATUS_LABELS[order.status]}</span>
      </header>
      <div className="proc-order-facts grid grid-cols-2 gap-2 text-xs bg-surface-subtle/50 p-3 rounded-lg border border-border/40">
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">供应商</small><strong className="font-semibold text-text truncate">{order.supplier_name}</strong></span>
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">到货总价</small><strong className="font-semibold text-text font-mono">{formatAmount(order.landed_total)}</strong></span>
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">收货数量</small><strong className="font-semibold text-text font-mono">{formatQuantity(order.received_quantity)}</strong></span>
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">到货日期</small><strong className="font-semibold text-text">{order.arrival_date ? new Date(order.arrival_date).toLocaleDateString("zh-CN") : "—"}</strong></span>
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">发票状态</small><strong className="font-semibold text-text">{order.invoice_count === 0 ? "尚未上传" : order.invoice_status ? INVOICE_STATUS_LABELS[order.invoice_status] : "待确认"}</strong></span>
        <span className="flex flex-col"><small className="text-[11px] text-text-muted">对账状态</small><strong className="font-semibold text-text">{order.settlement ? SETTLEMENT_STATUS_LABELS[order.settlement.status] : "尚未生成"}</strong></span>
        <span className={`proc-order-next col-span-2 p-2 rounded-md text-xs flex flex-col gap-0.5 border ${next.tone === "success" ? "bg-accent-soft text-accent border-accent/30" : next.tone === "warning" ? "bg-warning-soft text-warning border-warning/30" : "bg-info-soft text-info border-info/30"}`}><small className="text-[10px] font-semibold uppercase opacity-80">当前下一步</small><strong className="font-bold">{next.label}</strong><em className="not-italic text-[11px] opacity-90">{next.detail}</em></span>
      </div>
      <div className="proc-order-actions flex flex-wrap items-center gap-2 pt-2 border-t border-border/40">
        {order.status === "PENDING_SHIPMENT" ? (
          <>
            <button className="proc-button inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" disabled={busy === `ship:${order.id}`} onClick={() => void onShip(order)}>
              {busy === `ship:${order.id}` ? <LoaderCircle className="spin" size={14} /> : <Truck size={14} />}标记发货
            </button>
            <button className="proc-button secondary inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => onCloseRequest(order, "")}>
              <X size={14} />取消订单
            </button>
          </>
        ) : null}
        {order.status === "SHIPPED" || order.status === "PARTIALLY_RECEIVED" ? (
          <button className="proc-button inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={() => onOpenReceive(order)}>
            <PackageCheck size={14} />{order.status === "PARTIALLY_RECEIVED" ? "继续收货" : "确认收货"}
          </button>
        ) : null}
        {order.status === "RECEIVED" ? (
          <>
            {order.settlement ? (
              <span className="proc-settlement-inline inline-flex items-center gap-2 text-xs font-medium text-text-secondary bg-surface-subtle px-2.5 py-1 rounded-lg border border-border">
                <CreditCard size={14} className="text-accent" />
                <code>{order.settlement.settlement_no}</code> · {SETTLEMENT_STATUS_LABELS[order.settlement.status]}
                {order.settlement.status === "UNSETTLED" && next.canSettle ? (
                  <button className="proc-link-button text-accent font-semibold hover:underline ml-1" type="button" disabled={busy === `settle:${order.settlement.id}`} onClick={() => void onSettle(order.id, order.settlement!.id)}>
                    确认对账
                  </button>
                ) : null}
                {order.settlement.status === "SETTLED" && next.canPay ? (
                  <button className="proc-link-button text-accent font-semibold hover:underline ml-1" type="button" onClick={() => onOpenPay(order)}>
                    登记付款
                  </button>
                ) : null}
              </span>
            ) : (
              <span className="proc-settlement-inline muted inline-flex items-center gap-1.5 text-xs text-text-muted"><CreditCard size={14} />对账单缺失</span>
            )}
            {next.canClose ? (
              <button className="proc-button secondary inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => onCloseRequest(order, "订单已完成")}>
                <CheckCircle2 size={14} />完成关闭
              </button>
            ) : null}
          </>
        ) : null}
      </div>
      <footer className="proc-order-footer flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-border/40 text-[11px] text-text-muted">
        <div className="flex items-center gap-2">
          {order.artifacts.length ? order.artifacts.map((artifact) => (
            <a
              key={artifact.id}
              className="proc-artifact-link inline-flex items-center gap-1 text-accent hover:text-accent-strong hover:underline"
              href={`/api/artifacts/${artifact.id}/raw`}
              title={artifact.filename}
            >
              <Download size={13} />
              {artifact.kind === "supplier_confirmation_email" ? "供应商确认邮件" : "订单草稿"}
            </a>
          )) : <span className="proc-muted">暂无订单工件</span>}
        </div>
        <small className="font-mono">更新于 {new Date(order.updated_at).toLocaleString("zh-CN")}</small>
      </footer>
    </article>
  );
});

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
  const receiveDialogRef = useRef<HTMLElement | null>(null);
  const receiveQuantityInputRef = useRef<HTMLInputElement | null>(null);
  const closeDialogRef = useRef<HTMLElement | null>(null);
  const closeCancelRef = useRef<HTMLButtonElement | null>(null);
  const payDialogRef = useRef<HTMLElement | null>(null);
  const paidAtInputRef = useRef<HTMLInputElement | null>(null);
  // 收货/付款是表单弹窗：初始焦点落在首个输入；关闭订单是不可回退确认：落在「取消」。
  useModalFocus(!!receiveTarget, receiveDialogRef, receiveQuantityInputRef);
  useModalFocus(!!closeTarget, closeDialogRef, closeCancelRef);
  useModalFocus(!!payTarget, payDialogRef, paidAtInputRef);

  const ordersQuery = useQuery({
    queryKey: ["procurement-orders", status],
    queryFn: () => procurementApi.orders(status || undefined, 0, 100),
    // 全部终态（CLOSED，付款后才能关闭）后停止轮询
    refetchInterval: (query) =>
      query.state.data?.items?.some((item) => item.status !== "CLOSED") ? 10_000 : false,
  });
  const allOrders = ordersQuery.data?.items || [];
  const focused = highlightTaskId ? allOrders.filter((item) => item.task_id === highlightTaskId) : null;
  const orders = focused ?? allOrders;
  const focusTask = highlightTaskId && focused ? focused[0] : null;

  const invalidate = useCallback(
    () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-orders"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-settlements"] }),
      ]),
    [queryClient],
  );

  /** Run an async order/settlement operation; resolves true only on success so
   *  callers can keep dialogs open with user input intact when the API fails. */
  const run = useCallback(async function run<T>(key: string, operation: () => Promise<T>): Promise<boolean> {
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
  }, [invalidate]);

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

  // 以下回调以稳定引用传给 memo 化的 OrderCard：弹窗输入打字时卡片不重渲。
  const ship = useCallback(async (order: OrderView) => {
    const input = { action: "ship" } as const;
    const request = transitionKey(transitionKeys.current, `order:${order.id}`, input);
    const ok = await run(`ship:${order.id}`, () =>
      procurementApi.transitionOrder(order.id, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setNotice(`订单 ${order.order_no} 已标记发货。`);
    }
  }, [run]);

  const openReceive = useCallback((order: OrderView) => {
    const remaining = subtractDecimals(order.quantity, order.received_quantity ?? "0");
    setReceiveTarget(order);
    setReceiveQuantity(remaining ?? order.quantity);
    setArrivalDate(new Date().toISOString().slice(0, 10));
    setError(null);
  }, []);

  const openClose = useCallback((order: OrderView, notes: string) => {
    setCloseTarget(order);
    setCloseNotes(notes);
    setError(null);
  }, []);

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

  const settle = useCallback(async (orderId: string, settlementId: string) => {
    const input = { action: "settle" } as const;
    const request = transitionKey(transitionKeys.current, `settlement:${settlementId}`, input);
    const ok = await run(`settle:${settlementId}`, () =>
      procurementApi.transitionSettlement(settlementId, input, request.key));
    if (ok) {
      transitionKeys.current.delete(request.fingerprint);
      setNotice("已确认对账。");
    }
  }, [run]);

  const openPay = useCallback((order: OrderView) => {
    setPayTarget({ orderId: order.id, settlementId: order.settlement!.id, settlementNo: order.settlement!.settlement_no, orderNo: order.order_no });
    setPaidAt(new Date().toISOString().slice(0, 10));
    setError(null);
    setNotice(null);
  }, []);

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
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <PackageCheck className="w-5 h-5 text-accent" />
            {highlightTaskId ? "任务订单" : "采购订单"}
          </h1>
          <p className="text-xs text-text-muted mt-1">
            {focusTask
              ? `聚焦 ${focusTask.task_reference || "采购任务"} 的订单：待发货 → 已发货 → 部分收货 / 已收货 → 对账 → 付款`
              : "订单状态机：待发货 → 已发货 → 部分收货 / 已收货 → 已关闭；累计收满后自动派生对账单"}
          </p>
        </div>
        <span className="proc-page-count text-xs font-medium text-text-secondary bg-surface-subtle px-3 py-1 rounded-full border border-border">
          {highlightTaskId && onBackToTask ? (
            <button type="button" className="proc-link-button text-accent hover:text-accent-strong transition-colors" onClick={() => onBackToTask(highlightTaskId)}>← 返回采购任务</button>
          ) : `共 ${ordersQuery.data?.total ?? 0} 张`}
        </span>
      </header>

      <div className="proc-toolbar flex flex-wrap items-center gap-2" role="toolbar">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`proc-filter-chip px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${status === option.value ? "active bg-accent text-white border-accent shadow-xs" : "bg-surface text-text-secondary border-border hover:border-border-strong hover:bg-surface-subtle"}`}
            onClick={() => setStatus(option.value)}
          >
            {option.label}
          </button>
        ))}
        {error ? <span className="proc-toolbar-error text-xs text-danger font-medium px-2.5 py-1 rounded-md bg-danger-soft border border-danger/30" role="alert">{error}</span> : null}
        {notice ? <span className="proc-toolbar-success text-xs text-accent font-medium px-2.5 py-1 rounded-md bg-accent-soft border border-accent/30" role="status">{notice}</span> : null}
      </div>

      <div className="proc-order-list grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" aria-busy={ordersQuery.isPending}>
        {ordersQuery.isPending ? (
          <div className="proc-loading-state col-span-full py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在加载订单…</div>
        ) : null}
        {ordersQuery.isError ? (
          <section className="proc-empty-state compact col-span-full py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
            <AlertTriangle size={26} className="text-danger" />
            <h2 className="text-sm font-semibold text-text">订单加载失败</h2>
            <p className="text-text-muted">{ordersQuery.error instanceof Error ? ordersQuery.error.message : "未知错误"}</p>
            <button className="proc-button secondary px-3 py-1.5 rounded-lg border border-border text-xs font-medium hover:bg-surface-subtle" type="button" onClick={() => void ordersQuery.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!ordersQuery.isPending && !ordersQuery.isError && !orders.length ? (
          <div className="proc-empty-state col-span-full py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
            <Archive size={30} className="text-text-muted" />
            <h2 className="text-sm font-semibold text-text">{highlightTaskId ? "该任务还没有订单" : status ? "该状态下没有订单" : "还没有采购订单"}</h2>
            <p className="max-w-md">{highlightTaskId
              ? "正式采购方案确认后会立即生成唯一订单；若仍未显示，请返回任务详情检查审批状态。"
              : "正式采购方案确认后会立即生成唯一订单。"}</p>
          </div>
        ) : null}
        {orders.map((order) => (
          <OrderCard
            key={order.id}
            order={order}
            busy={busy}
            onShip={ship}
            onSettle={settle}
            onOpenReceive={openReceive}
            onCloseRequest={openClose}
            onOpenPay={openPay}
          />
        ))}
      </div>

      {receiveTarget ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `receive:${receiveTarget.id}`) setReceiveTarget(null);
          }}
        >
          <section ref={receiveDialogRef} className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="receive-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60">
              <div className="flex items-center gap-2 text-text font-bold text-base"><PackageCheck size={18} className="text-accent" /><h2 id="receive-title">确认收货</h2></div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" onClick={() => setReceiveTarget(null)} disabled={busy === `receive:${receiveTarget.id}`}><X size={16} /></button>
            </header>
            <div className="proc-supplier-form flex flex-col gap-3">
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text">
                <span>收货数量 <b>*</b></span>
                <input ref={receiveQuantityInputRef} className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent font-mono text-sm" type="number" min="0" step="any" value={receiveQuantity} onChange={(event) => setReceiveQuantity(event.target.value)} />
                <small className="text-[11px] text-text-muted">已收 {formatQuantity(receiveTarget.received_quantity)}，本批不得超过剩余数量 {formatQuantity(subtractDecimals(receiveTarget.quantity, receiveTarget.received_quantity ?? "0"))}</small>
              </label>
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text">
                <span>到货日期 <b>*</b></span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" type="date" value={arrivalDate} onChange={(event) => setArrivalDate(event.target.value)} />
              </label>
              {error ? <p className="proc-form-error proc-span-2 text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            </div>
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setReceiveTarget(null)} disabled={busy === `receive:${receiveTarget.id}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `receive:${receiveTarget.id}`} onClick={() => void receive()}>
                {busy === `receive:${receiveTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <PackageCheck size={15} />}登记本批收货
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {closeTarget ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `close:${closeTarget.id}`) setCloseTarget(null);
          }}
        >
          <section ref={closeDialogRef} className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="close-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60">
              <div className="flex items-center gap-2 text-text font-bold text-base"><X size={18} className="text-danger" /><h2 id="close-title">{closeTarget.status === "PENDING_SHIPMENT" ? "取消订单" : "完成关闭订单"}</h2></div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" onClick={() => setCloseTarget(null)} disabled={busy === `close:${closeTarget.id}`}><X size={16} /></button>
            </header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs">
              <strong className="font-mono text-sm font-bold text-text">{closeTarget.order_no}</strong>
              <span className="text-text-muted">{closeTarget.supplier_name} · {closeTarget.item_name}</span>
            </div>
            <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text">
              <span>备注</span>
              <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={closeNotes} onChange={(event) => setCloseNotes(event.target.value)} placeholder={closeTarget.status === "PENDING_SHIPMENT" ? "取消原因（可选）" : "完成备注（可选）"} />
            </label>
            {error ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button ref={closeCancelRef} className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setCloseTarget(null)} disabled={busy === `close:${closeTarget.id}`}>取消</button>
              <button className={`proc-button px-4 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shadow-xs ${closeTarget.status === "PENDING_SHIPMENT" ? "danger bg-danger text-white hover:bg-rose-700" : "bg-accent text-white hover:bg-accent-strong"}`} type="button" disabled={busy === `close:${closeTarget.id}`} onClick={() => void closeOrder()}>
                {busy === `close:${closeTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}{closeTarget.status === "PENDING_SHIPMENT" ? "确认取消" : "确认完成"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {payTarget ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busy !== `pay:${payTarget.settlementId}`) setPayTarget(null);
          }}
        >
          <section ref={payDialogRef} className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="pay-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60">
              <div className="flex items-center gap-2 text-text font-bold text-base"><CreditCard size={18} className="text-accent" /><h2 id="pay-title">登记付款</h2></div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" onClick={() => setPayTarget(null)} disabled={busy === `pay:${payTarget.settlementId}`}><X size={16} /></button>
            </header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs">
              <strong className="font-mono text-sm font-bold text-text">{payTarget.orderNo || "对账单 " + payTarget.settlementNo}</strong>
              <span className="text-text-muted font-mono">对账单 {payTarget.settlementNo} · {payTarget.settlementId.slice(0, 8)}…</span>
            </div>
            <div className="proc-supplier-form flex flex-col gap-3">
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text">
                <span>付款时间 <b>*</b></span>
                <input ref={paidAtInputRef} className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" type="date" value={paidAt} onChange={(event) => setPaidAt(event.target.value)} />
              </label>
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text">
                <span>备注</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={payNotes} onChange={(event) => setPayNotes(event.target.value)} placeholder="付款方式等（可选）" />
              </label>
              {error ? <p className="proc-form-error proc-span-2 text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            </div>
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setPayTarget(null)} disabled={busy === `pay:${payTarget.settlementId}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `pay:${payTarget.settlementId}`} onClick={() => void pay()}>
                {busy === `pay:${payTarget.settlementId}` ? <LoaderCircle className="spin" size={15} /> : <CreditCard size={15} />}确认付款
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      <div className="proc-settlement-section glass-panel rounded-xl p-5 border border-border/80 bg-surface/80 flex flex-col gap-4 shadow-sm mt-2">
        <header className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-border/40">
          <div className="flex items-center gap-2"><CalendarCheck2 size={16} className="text-accent" /><h3 className="text-base font-semibold text-text">对账单</h3></div>
          <span className="text-xs text-text-muted">累计收满自动派生 · 状态机 UNSETTLED → SETTLED → PAID</span>
        </header>
        <SettlementTable
          onPay={(settlement) => {
            setPayTarget({ orderId: null, settlementId: settlement.id, settlementNo: settlement.settlement_no, orderNo: null });
            setPaidAt(new Date().toISOString().slice(0, 10));
            setError(null);
            setNotice(null);
          }}
        />
      </div>
    </div>
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
    <div className="proc-settlement-table flex flex-col divide-y divide-border/40 rounded-lg border border-border/60 overflow-hidden bg-surface">
      {error ? <p className="proc-form-error text-xs text-danger font-medium p-3 bg-danger-soft" role="alert">{error}</p> : null}
      {settlementsQuery.isPending ? <div className="proc-loading-state py-8 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={16} />正在加载对账单…</div> : null}
      {!settlementsQuery.isPending && !settlements.length ? <p className="proc-muted py-8 text-center text-xs text-text-muted">暂无对账单（订单累计收满后自动派生）</p> : null}
      {settlements.map((settlement) => (
        <div className="proc-settlement-row flex flex-wrap items-center justify-between gap-3 p-3.5 hover:bg-surface-subtle/50 transition-colors text-xs" key={settlement.id}>
          <code className="font-mono text-xs font-semibold text-accent">{settlement.settlement_no}</code>
          <span className="font-semibold text-text">{settlement.supplier_name}</span>
          <strong className="font-mono text-sm text-text">{formatAmount(settlement.total_amount)}</strong>
          <i className={`${settlementTone(settlement.status)} not-italic text-xs font-medium px-2.5 py-0.5 rounded-full border`}>{SETTLEMENT_STATUS_LABELS[settlement.status]}</i>
          {settlement.status === "SETTLED" && settlement.invoice_reconciled ? (
            <button className="proc-link-button text-accent font-semibold hover:underline" type="button" disabled={busy === `row-pay:${settlement.id}`} onClick={() => onPay(settlement)}>
              登记付款
            </button>
          ) : null}
          {settlement.status === "SETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-muted text-text-muted text-[11px]">付款被拦截：请先核销全部有效发票</small>
          ) : null}
          {settlement.status === "UNSETTLED" && settlement.invoice_reconciled ? (
            <button className="proc-link-button text-accent font-semibold hover:underline" type="button" disabled={busy === `row-settle:${settlement.id}`} onClick={() => void settle(settlement.id)}>
              确认对账
            </button>
          ) : null}
          {settlement.status === "UNSETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-muted text-text-muted text-[11px]">发票核销后可对账</small>
          ) : null}
          {settlement.status === "PAID" && settlement.paid_at ? <small className="text-text-muted font-mono text-[11px]">{new Date(settlement.paid_at).toLocaleDateString("zh-CN")}</small> : null}
        </div>
      ))}
    </div>
  );
}
