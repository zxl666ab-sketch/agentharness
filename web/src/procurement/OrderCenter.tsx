import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  CalendarCheck2,
  CheckCircle2,
  CreditCard,
  Download,
  LoaderCircle,
  PackageCheck,
  Receipt,
  Truck,
  X,
} from "lucide-react";
import { memo, useCallback, useEffect, useRef, useState } from "react";

import { newIdempotencyKey, procurementApi } from "./api";
import {
  Button,
  CenterPage,
  CountBadge,
  EmptyState,
  ErrorState,
  Fact,
  FilterChips,
  Modal,
  NoticeBar,
  PageHeader,
  StatusPill,
  formatMoney,
} from "../components/ui";
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
  /** P-UX⑧：发票阶段动作跨中心直达（携带订单聚焦） */
  onOpenInvoice?: (order: OrderView) => void;
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
  onOpenInvoice?: (order: OrderView) => void;
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
  onOpenInvoice,
}: OrderCardProps) {
  const next = fulfillmentNextStep(order);
  const closed = order.status === "CLOSED" || order.settlement?.status === "PAID";
  return (
    <article className={`proc-order-card${closed ? " is-closed" : ""}`}>
      <header>
        <div className="proc-order-title">
          <code>{order.order_no}</code>
          <strong>{order.item_name} × {formatQuantity(order.quantity)} {order.unit}</strong>
          <small>{order.task_reference} · {order.task_title}</small>
        </div>
        <StatusPill tone={statusTone(order.status)}>{ORDER_STATUS_LABELS[order.status]}</StatusPill>
      </header>
      <div className="proc-order-facts">
        <Fact label="供应商">{order.supplier_name}</Fact>
        <Fact label="到货总价" mono>{order.landed_total == null ? "未补录成本" : formatMoney(order.landed_total)}</Fact>
        <Fact label="收货数量" mono>{order.received_quantity == null ? "—" : `${formatQuantity(order.received_quantity)} / ${formatQuantity(order.quantity)}`}</Fact>
        <Fact label="到货日期">{order.arrival_date ? new Date(order.arrival_date).toLocaleDateString("zh-CN") : "—"}</Fact>
        <Fact label="发票状态">{order.invoice_count === 0 ? "尚未上传" : order.invoice_status ? INVOICE_STATUS_LABELS[order.invoice_status] : "待确认"}</Fact>
        <Fact label="对账状态">{order.settlement ? SETTLEMENT_STATUS_LABELS[order.settlement.status] : "尚未生成"}</Fact>
      </div>
      {/* 痛点⑩：完结单不再显示"当前下一步"噪音；在途单动作直接给按钮 */}
      {closed ? (
        <footer className="proc-order-footer">
          <div className="proc-order-artifacts">
            <span className="proc-order-closed-note"><CheckCircle2 size={14} />已完成：付款与履约证据齐备</span>
            {order.artifacts.map((artifact) => (
              <a key={artifact.id} className="proc-artifact-link" href={`/api/artifacts/${artifact.id}/raw`} title={artifact.filename}>
                <Download size={13} />
                {artifact.kind === "supplier_confirmation_email" ? "供应商确认邮件" : "订单草稿"}
              </a>
            ))}
          </div>
          <small className="mono">更新于 {new Date(order.updated_at).toLocaleString("zh-CN")}</small>
        </footer>
      ) : (
        <>
          <div className={`proc-order-next is-${next.tone}`}>
            <span className="proc-order-next-label">下一步</span>
            <strong>{next.label}</strong>
            <small>{next.detail}</small>
          </div>
          <div className="proc-order-actions">
            {order.status === "PENDING_SHIPMENT" ? (
              <>
                <Button variant="primary" icon={busy === `ship:${order.id}` ? undefined : <Truck size={14} />} loading={busy === `ship:${order.id}`} onClick={() => void onShip(order)}>标记发货</Button>
                <Button variant="secondary" icon={<X size={14} />} onClick={() => onCloseRequest(order, "")}>取消订单</Button>
              </>
            ) : null}
            {order.status === "SHIPPED" || order.status === "PARTIALLY_RECEIVED" ? (
              <Button variant="primary" icon={<PackageCheck size={14} />} onClick={() => onOpenReceive(order)}>
                {order.status === "PARTIALLY_RECEIVED" ? "继续收货" : "确认收货"}
              </Button>
            ) : null}
            {order.status === "RECEIVED" ? (
              <>
                {/* P-UX⑧：发票阶段动作跨中心直达 */}
                {order.invoice_status !== "RECONCILED" && onOpenInvoice ? (
                  <Button
                    variant={order.invoice_status === "DIFF_HOLD" ? "danger" : order.invoice_status === "MATCHED" ? "warning" : "primary"}
                    icon={<Receipt size={14} />}
                    onClick={() => onOpenInvoice(order)}
                  >
                    {order.invoice_count === 0 ? "上传发票" : order.invoice_status === "REGISTERED" ? "查看匹配进度" : order.invoice_status === "DIFF_HOLD" ? "处理发票差异" : order.invoice_status === "MATCHED" ? "前往核销" : "上传发票"}
                  </Button>
                ) : null}
                {order.settlement ? (
                  <>
                    <span className="proc-settlement-inline">
                      <CreditCard size={14} />
                      <code>{order.settlement.settlement_no}</code> · {SETTLEMENT_STATUS_LABELS[order.settlement.status]}
                    </span>
                    {order.settlement.status === "UNSETTLED" && next.canSettle ? (
                      <Button variant="plain" size="sm" loading={busy === `settle:${order.settlement.id}`} onClick={() => void onSettle(order.id, order.settlement!.id)}>确认对账</Button>
                    ) : null}
                    {order.settlement.status === "SETTLED" && next.canPay ? (
                      <Button variant="plain" size="sm" onClick={() => onOpenPay(order)}>登记付款</Button>
                    ) : null}
                  </>
                ) : (
                  <span className="proc-settlement-inline is-muted"><CreditCard size={14} />对账单缺失</span>
                )}
                {next.canClose ? (
                  <Button variant="secondary" icon={<CheckCircle2 size={14} />} onClick={() => onCloseRequest(order, "订单已完成")}>完成关闭</Button>
                ) : null}
              </>
            ) : null}
          </div>
          <footer className="proc-order-footer">
            <div className="proc-order-artifacts">
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
              )) : null}
            </div>
            <small className="mono">更新于 {new Date(order.updated_at).toLocaleString("zh-CN")}</small>
          </footer>
        </>
      )}
    </article>
  );
});

export function OrderCenter({ highlightTaskId = null, onBackToTask, onOpenInvoice }: Props) {
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

  // W-M3：聚焦某采购任务时优先用 task_id 让后端精确过滤订单；一旦探测到
  // 旧后端忽略该参数（返回项里出现别的 task_id），自动回退"取 100 条前端
  // find"的旧行为。前端 filter 仍作为兜底，保证列表始终只含目标任务。
  const ordersTaskFilterSupported = useRef(true);
  const ordersQuery = useQuery({
    queryKey: highlightTaskId
      ? ["procurement-orders", status, "task", highlightTaskId]
      : ["procurement-orders", status],
    queryFn: async () => {
      if (!highlightTaskId || !ordersTaskFilterSupported.current) {
        return procurementApi.orders(status || undefined, 0, 100);
      }
      const filtered = await procurementApi.orders(status || undefined, 0, 100, highlightTaskId);
      if (!filtered.items.some((item) => item.task_id !== highlightTaskId)) {
        return filtered;
      }
      ordersTaskFilterSupported.current = false;
      return procurementApi.orders(status || undefined, 0, 100);
    },
    // 全部终态（CLOSED，付款后才能关闭）后停止轮询
    refetchInterval: (query) =>
      query.state.data?.items?.some((item) => item.status !== "CLOSED") ? 10_000 : false,
  });
  const allOrders = ordersQuery.data?.items || [];
  const focused = highlightTaskId ? allOrders.filter((item) => item.task_id === highlightTaskId) : null;
  const orders = focused ?? allOrders;
  const focusTask = highlightTaskId && focused ? focused[0] : null;
  // 痛点⑩：可操作单优先排序（在途 → 已关闭沉底），同组内按更新时间新在前。
  const actionableFirst = [...orders].sort((left, right) => {
    const leftClosed = left.status === "CLOSED" || left.settlement?.status === "PAID";
    const rightClosed = right.status === "CLOSED" || right.settlement?.status === "PAID";
    if (leftClosed !== rightClosed) return leftClosed ? 1 : -1;
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
  });

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
    <CenterPage
      header={
        <PageHeader
          icon={<PackageCheck size={18} />}
          title={highlightTaskId ? "任务订单" : "采购订单"}
          subtitle={focusTask
            ? `聚焦 ${focusTask.task_reference || "采购任务"} 的订单：待发货 → 已发货 → 部分收货 / 已收货 → 对账 → 付款`
            : "订单状态机：待发货 → 已发货 → 部分收货 / 已收货 → 已关闭；累计收满后自动派生对账单"}
          aside={highlightTaskId && onBackToTask ? (
            <Button variant="plain" size="sm" onClick={() => onBackToTask(highlightTaskId)}>← 返回采购任务</Button>
          ) : (
            <CountBadge>共 {ordersQuery.data?.total ?? 0} 张</CountBadge>
          )}
        />
      }
      toolbar={<FilterChips options={STATUS_FILTERS} value={status} onChange={setStatus} />}
    >
      <NoticeBar error={error} notice={notice} />
      <div className="proc-order-list" aria-busy={ordersQuery.isPending}>
        {ordersQuery.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载订单…</div>
        ) : null}
        {ordersQuery.isError ? (
          <ErrorState
            title="订单加载失败"
            detail={ordersQuery.error instanceof Error ? ordersQuery.error.message : "未知错误"}
            onRetry={() => void ordersQuery.refetch()}
          />
        ) : null}
        {!ordersQuery.isPending && !ordersQuery.isError && !orders.length ? (
          <EmptyState
            icon={<Archive size={26} />}
            title={highlightTaskId ? "该任务还没有订单" : status ? "该状态下没有订单" : "还没有采购订单"}
            hint={highlightTaskId
              ? "正式采购方案确认后会立即生成唯一订单；若仍未显示，请返回任务详情检查审批状态。"
              : "正式采购方案确认后会立即生成唯一订单。"}
          />
        ) : null}
        {actionableFirst.map((order) => (
          <OrderCard
            key={order.id}
            order={order}
            busy={busy}
            onShip={ship}
            onSettle={settle}
            onOpenReceive={openReceive}
            onCloseRequest={openClose}
            onOpenPay={openPay}
            onOpenInvoice={onOpenInvoice}
          />
        ))}
      </div>

      {receiveTarget ? (
        <Modal
          titleId="receive-title"
          title="登记本批收货"
          icon={<PackageCheck size={18} />}
          busy={busy === `receive:${receiveTarget.id}`}
          onClose={() => setReceiveTarget(null)}
          dialogRef={receiveDialogRef}
          initialFocusRef={receiveQuantityInputRef}
          footer={
            <>
              <Button variant="secondary" onClick={() => setReceiveTarget(null)} disabled={busy === `receive:${receiveTarget.id}`}>取消</Button>
              <Button variant="primary" icon={<PackageCheck size={15} />} loading={busy === `receive:${receiveTarget.id}`} onClick={() => void receive()}>登记本批收货</Button>
            </>
          }
        >
          <div className="proc-dialog-form is-1">
            <label className="proc-field">
              <span>收货数量 <b>*</b></span>
              <input ref={receiveQuantityInputRef} className="proc-input mono" type="number" min="0" step="any" value={receiveQuantity} onChange={(event) => setReceiveQuantity(event.target.value)} />
              <small className="proc-field-hint">已收 {formatQuantity(receiveTarget.received_quantity)}，本批不得超过剩余数量 {formatQuantity(subtractDecimals(receiveTarget.quantity, receiveTarget.received_quantity ?? "0"))}</small>
            </label>
            <label className="proc-field">
              <span>到货日期 <b>*</b></span>
              <input className="proc-input" type="date" value={arrivalDate} onChange={(event) => setArrivalDate(event.target.value)} />
            </label>
          </div>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {closeTarget ? (
        <Modal
          titleId="close-title"
          title={closeTarget.status === "PENDING_SHIPMENT" ? "取消订单" : "完成关闭订单"}
          icon={closeTarget.status === "PENDING_SHIPMENT" ? <X size={18} /> : <CheckCircle2 size={18} />}
          tone={closeTarget.status === "PENDING_SHIPMENT" ? "danger" : "accent"}
          busy={busy === `close:${closeTarget.id}`}
          onClose={() => setCloseTarget(null)}
          dialogRef={closeDialogRef}
          initialFocusRef={closeCancelRef}
          footer={
            <>
              <Button variant="secondary" ref={closeCancelRef} onClick={() => setCloseTarget(null)} disabled={busy === `close:${closeTarget.id}`}>取消</Button>
              <Button
                variant={closeTarget.status === "PENDING_SHIPMENT" ? "danger" : "primary"}
                icon={<CheckCircle2 size={15} />}
                loading={busy === `close:${closeTarget.id}`}
                onClick={() => void closeOrder()}
              >
                {closeTarget.status === "PENDING_SHIPMENT" ? "确认取消" : "确认完成"}
              </Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{closeTarget.order_no}</strong>
            <span>{closeTarget.supplier_name} · {closeTarget.item_name}</span>
          </div>
          <label className="proc-field">
            <span>备注</span>
            <input className="proc-input" value={closeNotes} onChange={(event) => setCloseNotes(event.target.value)} placeholder={closeTarget.status === "PENDING_SHIPMENT" ? "取消原因（可选）" : "完成备注（可选）"} />
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {payTarget ? (
        <Modal
          titleId="pay-title"
          title="登记付款"
          icon={<CreditCard size={18} />}
          busy={busy === `pay:${payTarget.settlementId}`}
          onClose={() => setPayTarget(null)}
          dialogRef={payDialogRef}
          initialFocusRef={paidAtInputRef}
          footer={
            <>
              <Button variant="secondary" onClick={() => setPayTarget(null)} disabled={busy === `pay:${payTarget.settlementId}`}>取消</Button>
              <Button variant="primary" icon={<CreditCard size={15} />} loading={busy === `pay:${payTarget.settlementId}`} onClick={() => void pay()}>确认付款</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{payTarget.orderNo || `对账单 ${payTarget.settlementNo}`}</strong>
            <span className="mono">对账单 {payTarget.settlementNo} · {payTarget.settlementId.slice(0, 8)}…</span>
          </div>
          <div className="proc-dialog-form is-1">
            <label className="proc-field">
              <span>付款时间 <b>*</b></span>
              <input ref={paidAtInputRef} className="proc-input" type="date" value={paidAt} onChange={(event) => setPaidAt(event.target.value)} />
            </label>
            <label className="proc-field">
              <span>备注</span>
              <input className="proc-input" value={payNotes} onChange={(event) => setPayNotes(event.target.value)} placeholder="付款方式等（可选）" />
            </label>
          </div>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      <SettlementSection
        focusTaskId={highlightTaskId}
        onPay={(settlement) => {
          setPayTarget({ orderId: null, settlementId: settlement.id, settlementNo: settlement.settlement_no, orderNo: null });
          setPaidAt(new Date().toISOString().slice(0, 10));
          setError(null);
          setNotice(null);
        }}
      />
    </CenterPage>
  );
}

function SettlementSection({ focusTaskId, onPay }: { focusTaskId?: string | null; onPay: (settlement: SettlementView) => void }) {
  return (
    <section className="proc-settlement-section">
      <header>
        <h3><CalendarCheck2 size={16} /> 对账单</h3>
        <small>{focusTaskId
          ? "仅显示聚焦任务的对账单（订单累计收满自动派生）"
          : "累计收满自动派生 · 状态机 UNSETTLED → SETTLED → PAID"}</small>
      </header>
      <SettlementTable focusTaskId={focusTaskId} onPay={onPay} />
    </section>
  );
}

function SettlementTable({ focusTaskId, onPay }: { focusTaskId?: string | null; onPay: (settlement: SettlementView) => void }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const transitionKeys = useRef(new Map<string, string>());
  const settlementsQuery = useQuery({
    queryKey: ["procurement-settlements"],
    queryFn: () => procurementApi.settlements(undefined, 0, 100),
  });
  const settlementsAll = settlementsQuery.data?.items || [];
  // 深链聚焦某任务时，对账单区与上方订单卡保持同一范围（task_id 为空的旧数据在聚焦态隐藏）
  const settlements = focusTaskId
    ? settlementsAll.filter((settlement) => settlement.task_id === focusTaskId)
    : settlementsAll;

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
      {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
      {settlementsQuery.isPending ? <div className="proc-loading-state"><LoaderCircle className="spin" size={16} />正在加载对账单…</div> : null}
      {!settlementsQuery.isPending && !settlements.length ? <p className="proc-muted">暂无对账单（订单累计收满后自动派生）</p> : null}
      {settlements.map((settlement) => (
        <div className="proc-settlement-row" key={settlement.id}>
          <code>{settlement.settlement_no}</code>
          <strong>{settlement.supplier_name}</strong>
          <b className="proc-settlement-amount tnum">{formatMoney(settlement.total_amount)}</b>
          <StatusPill tone={settlementTone(settlement.status)} size="compact">{SETTLEMENT_STATUS_LABELS[settlement.status]}</StatusPill>
          {settlement.status === "SETTLED" && settlement.invoice_reconciled ? (
            <Button variant="plain" size="sm" disabled={busy === `row-pay:${settlement.id}`} onClick={() => onPay(settlement)}>登记付款</Button>
          ) : null}
          {settlement.status === "SETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-settlement-blocked">付款被拦截：请先核销全部有效发票</small>
          ) : null}
          {settlement.status === "UNSETTLED" && settlement.invoice_reconciled ? (
            <Button variant="plain" size="sm" disabled={busy === `row-settle:${settlement.id}`} onClick={() => void settle(settlement.id)}>确认对账</Button>
          ) : null}
          {settlement.status === "UNSETTLED" && !settlement.invoice_reconciled ? (
            <small className="proc-muted">发票核销后可对账</small>
          ) : null}
          {settlement.status === "PAID" && settlement.paid_at ? <small className="mono">{new Date(settlement.paid_at).toLocaleDateString("zh-CN")}</small> : null}
        </div>
      ))}
    </div>
  );
}
