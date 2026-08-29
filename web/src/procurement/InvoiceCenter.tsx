import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BadgeCheck,
  CheckCircle2,
  FileSpreadsheet,
  LoaderCircle,
  Receipt,
  RotateCcw,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { POLL_FETCH_CAP, pollFetchCount, procurementApi } from "./api";
import {
  Button,
  CenterPage,
  Card,
  CountBadge,
  EmptyState,
  ErrorState,
  Fact,
  FilterChips,
  ListRow,
  MasterDetail,
  Modal,
  NoticeBar,
  PageHeader,
  StatusPill,
  formatMoney,
} from "../components/ui";
import type { InvoiceStatus, InvoiceView } from "./types";

const STATUS_FILTERS: Array<{ value: InvoiceStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "REGISTERED", label: "已登记" },
  { value: "MATCHED", label: "已匹配" },
  { value: "DIFF_HOLD", label: "差异挂起" },
  { value: "RECONCILED", label: "已核销" },
  { value: "VOIDED", label: "已作废" },
];

const STATUS_TONES: Record<InvoiceStatus, string> = {
  REGISTERED: "info",
  MATCHED: "success",
  DIFF_HOLD: "danger",
  VOIDED: "neutral",
  RECONCILED: "success",
};

const STATUS_LABELS: Record<InvoiceStatus, string> = {
  REGISTERED: "已登记",
  MATCHED: "已匹配",
  DIFF_HOLD: "差异挂起",
  VOIDED: "已作废",
  RECONCILED: "已核销",
};

const DIFF_LABELS: Record<string, string> = {
  quantity: "数量",
  unit_price: "单价",
  total_amount: "价税合计",
  tax_rate: "税率",
};

/** 订单状态中文化（下拉选项展示用，避免英文枚举泄漏）。 */
const ORDER_STATUS_LABELS: Record<string, string> = {
  PENDING_SHIPMENT: "待发货",
  SHIPPED: "已发货",
  PARTIALLY_RECEIVED: "部分收货",
  RECEIVED: "已收货",
  CLOSED: "已关闭",
};

/**
 * P-UX⑧：focusOrderId 来自订单卡的「上传发票 / 处理发票」跳转
 * （URL: ?view=invoices&invoice_order=<id>），自动预选订单并打开
 * 该订单最新一张非作废发票的详情，用户落地即可操作。
 */
export function InvoiceCenter({ focusOrderId = null }: { focusOrderId?: string | null }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<InvoiceStatus | "">("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [orderId, setOrderId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [voidTarget, setVoidTarget] = useState<InvoiceView | null>(null);
  const [voidNotes, setVoidNotes] = useState("");
  const [forceTarget, setForceTarget] = useState<InvoiceView | null>(null);
  const [forceNotes, setForceNotes] = useState("");
  const [forceConfirmed, setForceConfirmed] = useState(false);
  const [correctTarget, setCorrectTarget] = useState<InvoiceView | null>(null);
  const [correctForm, setCorrectForm] = useState({ quantity: "", unit_price: "", amount_excluding_tax: "", tax_amount: "", total_amount: "", tax_rate: "" });

  const invoicesQuery = useQuery({
    queryKey: ["procurement-invoices", status],
    queryFn: () => procurementApi.invoices(status || undefined, undefined, 0, 100),
    // 仅存在在途发票（REGISTERED/DIFF_HOLD）时轮询；全部终态后停止。
    // W-M4：一条长期滞留的在途发票不能永续轮询（fetchCount 封顶）。
    refetchInterval: (query) =>
      pollFetchCount(query.state) > POLL_FETCH_CAP ? false
        : query.state.data?.items?.some(
          (item) => item.status === "REGISTERED" || item.status === "DIFF_HOLD"
        )
          ? 5_000
          : false,
  });
  const ordersQuery = useQuery({
    queryKey: ["procurement-invoices-orders"],
    queryFn: () => procurementApi.orders(undefined, 0, 100),
  });
  const invoices = useMemo(() => invoicesQuery.data?.items ?? [], [invoicesQuery.data]);
  const selected = invoices.find((item) => item.id === selectedId) || null;
  const detailQuery = useQuery({
    queryKey: ["procurement-invoice", selectedId],
    queryFn: () => procurementApi.invoice(selectedId!),
    enabled: !!selectedId,
    // 终态（MATCHED/RECONCILED/VOIDED）不再轮询；仅在途态（解析/差异挂起）持续刷新。
    // W-M4：读取 query.state（回调参数）而非渲染闭包里的 `selected`，并封顶 fetchCount。
    refetchInterval: (query) => {
      if (pollFetchCount(query.state) > POLL_FETCH_CAP) return false;
      const state = query.state.data?.status ?? selected?.status;
      return state === "REGISTERED" || state === "DIFF_HOLD" ? 5_000 : false;
    },
  });
  const detail = detailQuery.data ?? selected ?? null;

  // P-UX⑧：跨中心聚焦——预选上传订单下拉，并打开该订单最新的非作废发票详情。
  useEffect(() => {
    if (!focusOrderId) return;
    setOrderId(focusOrderId);
    const target = invoices.find((item) => item.order_id === focusOrderId && item.status !== "VOIDED");
    if (target) setSelectedId(target.id);
  }, [focusOrderId, invoices]);

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-invoices"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-invoice"] }),
    ]);

  async function run<T>(key: string, operation: () => Promise<T>, okMessage: string) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await operation();
      await invalidate();
      setNotice(okMessage);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    if (!orderId) {
      setError("请先选择要开票的采购订单");
      return;
    }
    const ok = await run(`upload:${file.name}`, () => procurementApi.uploadInvoice(orderId, file), "发票已上传，Agent 正在解析并执行三单匹配。");
    if (ok) {
      setOrderId("");
    }
  }

  const voidInvoice = async () => {
    if (!voidTarget) return;
    if (!voidNotes.trim()) {
      setError("作废发票必须填写原因");
      return;
    }
    const ok = await run(`void:${voidTarget.id}`, () =>
      procurementApi.invoiceAction(voidTarget.id, "void", { notes: voidNotes.trim() }), "发票已作废（退回重开）。");
    if (ok) setVoidTarget(null);
  };

  const forceMatch = async () => {
    if (!forceTarget) return;
    if (!forceConfirmed || !forceNotes.trim()) {
      setError("强制通过必须勾选确认并填写人工备注");
      return;
    }
    const ok = await run(`force:${forceTarget.id}`, () =>
      procurementApi.invoiceAction(forceTarget.id, "force_match", { confirmed: true, notes: forceNotes.trim() }), "已强制通过（allow-once，人工备注已记录审计）。");
    if (ok) setForceTarget(null);
  };

  const openCorrect = (invoice: InvoiceView) => {
    setCorrectTarget(invoice);
    setCorrectForm({
      quantity: invoice.quantity != null ? String(invoice.quantity) : "",
      unit_price: invoice.unit_price != null ? String(invoice.unit_price) : "",
      amount_excluding_tax: invoice.amount_excluding_tax != null ? String(invoice.amount_excluding_tax) : "",
      tax_amount: invoice.tax_amount != null ? String(invoice.tax_amount) : "",
      total_amount: invoice.total_amount != null ? String(invoice.total_amount) : "",
      tax_rate: invoice.tax_rate != null ? String(invoice.tax_rate) : "",
    });
    setError(null);
  };

  const correctInvoice = async () => {
    if (!correctTarget) return;
    const numeric = (value: string, fallback?: string | number | null) => {
      const trimmed = value.trim();
      if (trimmed === "") {
        return fallback != null && fallback !== "" ? Number(fallback) : null;
      }
      const parsed = Number(trimmed);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const ok = await run(`correct:${correctTarget.id}`, () =>
      procurementApi.invoiceAction(correctTarget.id, "correct", {
        quantity: numeric(correctForm.quantity, correctTarget.quantity),
        unit_price: numeric(correctForm.unit_price, correctTarget.unit_price),
        amount_excluding_tax: numeric(correctForm.amount_excluding_tax, correctTarget.amount_excluding_tax),
        tax_amount: numeric(correctForm.tax_amount, correctTarget.tax_amount),
        total_amount: numeric(correctForm.total_amount, correctTarget.total_amount),
        tax_rate: numeric(correctForm.tax_rate, correctTarget.tax_rate),
        notes: "手工改单（人工修正发票字段）",
      }), "已手工改单并重新三单匹配。");
    if (ok) setCorrectTarget(null);
  };

  const reconcile = async (invoice: InvoiceView) => {
    const ok = await run(`reconcile:${invoice.id}`, () =>
      procurementApi.invoiceAction(invoice.id, "reconcile", {}), "已核销，可前往订单页完成付款。");
    if (ok) setSelectedId(invoice.id);
  };

  return (
    <CenterPage
      header={
        <PageHeader
          icon={<Receipt size={18} />}
          title="发票中心"
          subtitle="上传发票 → 解析 → 三单匹配（PO/收货/发票）→ 差异挂起处理 → 核销 → 付款"
          aside={<CountBadge>共 {invoicesQuery.data?.total ?? 0} 张</CountBadge>}
        />
      }
      toolbar={
        <>
          <div className="proc-action-bar">
            <select className="proc-input is-grow" aria-label="选择采购订单" value={orderId} onChange={(event) => setOrderId(event.target.value)}>
              <option value="">选择采购订单（优先已收货）…</option>
              {(ordersQuery.data?.items || []).map((order) => (
                <option key={order.id} value={order.id}>
                  {order.order_no} · {order.supplier_name} · {order.item_name}（{ORDER_STATUS_LABELS[order.status] || order.status}）
                </option>
              ))}
            </select>
            <label className={`proc-upload-button ${busy?.startsWith("upload") ? "disabled" : ""}`}>
              {busy?.startsWith("upload") ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
              <span>{busy?.startsWith("upload") ? "上传解析中" : "上传发票"}</span>
              <input
                data-testid="invoice-upload"
                type="file"
                accept=".xlsx,.pdf"
                disabled={busy?.startsWith("upload")}
                onChange={(event) => {
                  void upload(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </label>
          </div>
          <FilterChips options={STATUS_FILTERS} value={status} onChange={setStatus} />
        </>
      }
    >
      <NoticeBar error={error} notice={notice} />
      <MasterDetail
        list={
          <>
            <header className="proc-master-list-head">
              <strong>发票列表</strong>
              <small>点击行查看详情</small>
            </header>
            {invoicesQuery.isPending ? (
              <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载发票…</div>
            ) : null}
            {invoicesQuery.isError ? (
              <ErrorState title="发票加载失败" detail={invoicesQuery.error instanceof Error ? invoicesQuery.error.message : "未知错误"} onRetry={() => void invoicesQuery.refetch()} />
            ) : null}
            {!invoicesQuery.isPending && !invoicesQuery.isError && !invoices.length ? (
              <EmptyState
                variant="inline"
                icon={<Archive size={24} />}
                title={status ? "该状态下没有发票" : "还没有发票"}
                hint="选择已收货订单上传发票，Agent 解析后自动执行三单匹配。"
              />
            ) : null}
            {invoices.map((invoice) => {
              const diffCount = invoice.match_result?.diffs?.length || 0;
              return (
                <ListRow key={invoice.id} selected={selectedId === invoice.id} onClick={() => setSelectedId(invoice.id)}>
                  <span className="proc-list-row-head">
                    <code>{invoice.invoice_no}</code>
                    <StatusPill tone={STATUS_TONES[invoice.status]} size="compact">{STATUS_LABELS[invoice.status]}</StatusPill>
                  </span>
                  <strong className="proc-list-row-title">{invoice.supplier_name}</strong>
                  <span className="proc-list-row-meta">
                    <small className="mono">{invoice.order_no || "—"}</small>
                    <small className="tnum">价税合计 {formatMoney(invoice.total_amount)}</small>
                    {invoice.status === "DIFF_HOLD" ? <em className="proc-risk-chip">{diffCount} 项差异</em> : null}
                  </span>
                </ListRow>
              );
            })}
          </>
        }
        detail={
          <>
            {detailQuery.isError && selectedId ? (
              <ErrorState title="发票详情加载失败" detail={detailQuery.error instanceof Error ? detailQuery.error.message : "未知错误"} onRetry={() => { void detailQuery.refetch(); }} />
            ) : detail ? (
              <>
                <header className="proc-detail-head">
                  <div>
                    <h2><Receipt size={16} /> <code>{detail.invoice_no}</code></h2>
                    <p>{detail.supplier_name}</p>
                  </div>
                  <StatusPill tone={STATUS_TONES[detail.status]}>{STATUS_LABELS[detail.status]}</StatusPill>
                </header>

                {detailQuery.isPending ? <span className="proc-muted">列表快照 — 正在加载完整三单对比…</span> : null}

                <div className="proc-fact-grid">
                  <Fact label="发票代码" mono>{detail.invoice_code || "—"}</Fact>
                  <Fact label="开票日期" mono>{detail.issue_date || "—"}</Fact>
                  <Fact label="数量" mono>{formatMoney(detail.quantity)} {detail.unit || ""}</Fact>
                  <Fact label="单价" mono>{formatMoney(detail.unit_price)}</Fact>
                  <Fact label="不含税金额" mono>{formatMoney(detail.amount_excluding_tax)}</Fact>
                  <Fact label="税额" mono>{formatMoney(detail.tax_amount)}</Fact>
                  <Fact label="价税合计" mono>{formatMoney(detail.total_amount)}</Fact>
                  <Fact label="税率" mono>{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</Fact>
                </div>

                {detail.three_way ? (
                  <Card head={{ icon: <ShieldCheck size={15} />, title: "三单匹配对比（PO / 收货 GRN / 发票）" }}>
                    <div className="proc-table-scroll">
                      <table className="proc-data-table">
                        <thead>
                          <tr>
                            <th>字段</th><th>订单（PO）</th><th>收货（GRN）</th><th>发票</th><th>结果</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>数量</td>
                            <td className="mono">{formatMoney(detail.order_quantity)}</td>
                            <td className="mono">{formatMoney(detail.order_received_quantity)}</td>
                            <td className="mono">{formatMoney(detail.quantity)}</td>
                            <td>{detail.match_result?.matched ? <CheckCircle2 size={14} className="ok" /> : <XCircle size={14} className="bad" />}</td>
                          </tr>
                          <tr>
                            <td>到货总价</td>
                            <td className="mono">{formatMoney(detail.order_landed_total)}</td>
                            <td className="mono">—</td>
                            <td className="mono">{formatMoney(detail.total_amount)}</td>
                            <td>{detail.match_result?.matched ? <CheckCircle2 size={14} className="ok" /> : <XCircle size={14} className="bad" />}</td>
                          </tr>
                          <tr>
                            <td>税率</td>
                            <td className="mono">{detail.expected_tax_rate ? `${(Number(detail.expected_tax_rate) * 100).toFixed(2)}%` : "—"}</td>
                            <td className="mono">—</td>
                            <td className="mono">{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</td>
                            <td>{detail.match_result?.matched ? <CheckCircle2 size={14} className="ok" /> : <XCircle size={14} className="bad" />}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    {detail.match_result?.diffs?.length ? (
                      <ul className="proc-invoice-diffs">
                        {detail.match_result.diffs.map((diff) => (
                          <li key={diff.field}>
                            <strong>{DIFF_LABELS[diff.field] || diff.field}</strong>
                            <span className="mono">期望 {diff.expected}</span>
                            <span className="mono">实际 {diff.actual}</span>
                            <b className="mono">差异 {diff.diff}</b>
                          </li>
                        ))}
                      </ul>
                    ) : detail.match_result?.matched ? (
                      <p className="proc-invoice-matched"><BadgeCheck size={15} />三单匹配通过：数量、单价、总价、税率全部在容差内（Java 确定性规则）。</p>
                    ) : null}
                    {detail.match_explanation ? (
                      <div className="proc-invoice-explanation">
                        <strong><BadgeCheck size={14} />Agent 差异解释</strong>
                        <p>{detail.match_explanation.reason}</p>
                        <ul>
                          {(detail.match_explanation.suggestions || []).map((suggestion) => <li key={suggestion}>{suggestion}</li>)}
                        </ul>
                        <small className="mono">来源：{detail.match_explanation.source}</small>
                      </div>
                    ) : null}
                  </Card>
                ) : null}

                <div className="proc-invoice-actions">
                  {detail.status === "DIFF_HOLD" ? (
                    <>
                      <Button variant="secondary" icon={<RotateCcw size={14} />} onClick={() => openCorrect(detail)}>手工改单</Button>
                      <Button variant="warning" icon={<ShieldCheck size={14} />} onClick={() => { setForceTarget(detail); setForceNotes(""); setForceConfirmed(false); setError(null); }}>强制通过</Button>
                      <Button variant="danger" icon={<XCircle size={14} />} onClick={() => { setVoidTarget(detail); setVoidNotes(""); setError(null); }}>作废（退回重开）</Button>
                    </>
                  ) : null}
                  {detail.status === "MATCHED" ? (
                    <Button variant="primary" icon={<CheckCircle2 size={14} />} loading={busy === `reconcile:${detail.id}`} onClick={() => void reconcile(detail)}>核销</Button>
                  ) : null}
                  {detail.status === "REGISTERED" ? (
                    <span className="proc-muted">Agent 匹配中…（解析结果到达后自动匹配）</span>
                  ) : null}
                </div>
              </>
            ) : (
              <EmptyState
                variant="inline"
                icon={<FileSpreadsheet size={24} />}
                title="选择一张发票"
                hint="查看三单匹配对比与差异处理；差异挂起的发票需要先修正、核销或强制通过后才能付款。"
              />
            )}
          </>
        }
      />

      {voidTarget ? (
        <Modal
          titleId="void-invoice-title"
          title="作废发票（退回重开）"
          icon={<XCircle size={18} />}
          tone="danger"
          busy={busy === `void:${voidTarget.id}`}
          onClose={() => setVoidTarget(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setVoidTarget(null)} disabled={busy === `void:${voidTarget.id}`}>取消</Button>
              <Button variant="danger" icon={<XCircle size={15} />} loading={busy === `void:${voidTarget.id}`} onClick={() => void voidInvoice()}>确认作废</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{voidTarget.invoice_no}</strong>
            <span>{voidTarget.supplier_name} · {formatMoney(voidTarget.total_amount)}</span>
          </div>
          <label className="proc-field">
            <span>作废原因 <b>*</b></span>
            <input className="proc-input" autoFocus value={voidNotes} onChange={(event) => setVoidNotes(event.target.value)} placeholder="例如：发票开具错误，重新开具" />
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {forceTarget ? (
        <Modal
          titleId="force-invoice-title"
          title="强制通过（allow-once 审批）"
          icon={<ShieldCheck size={18} />}
          tone="warning"
          busy={busy === `force:${forceTarget.id}`}
          onClose={() => setForceTarget(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setForceTarget(null)} disabled={busy === `force:${forceTarget.id}`}>取消</Button>
              <Button variant="warning" icon={<ShieldCheck size={15} />} loading={busy === `force:${forceTarget.id}`} onClick={() => void forceMatch()}>确认强制通过</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{forceTarget.invoice_no}</strong>
            <span>差异 {forceTarget.match_result?.diffs?.length || 0} 项</span>
          </div>
          <label className="proc-field">
            <span>人工备注 <b>*</b></span>
            <input className="proc-input" autoFocus value={forceNotes} onChange={(event) => setForceNotes(event.target.value)} placeholder="强制通过原因（写入审计）" />
          </label>
          <label className="proc-confirm-check">
            <input type="checkbox" checked={forceConfirmed} onChange={(event) => setForceConfirmed(event.target.checked)} />
            <span>我已核对差异并确认强制通过（一次性，不能撤销）</span>
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {correctTarget ? (
        <Modal
          titleId="correct-invoice-title"
          title="手工改单（重新三单匹配）"
          icon={<RotateCcw size={18} />}
          size="lg"
          busy={busy === `correct:${correctTarget.id}`}
          onClose={() => setCorrectTarget(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setCorrectTarget(null)} disabled={busy === `correct:${correctTarget.id}`}>取消</Button>
              <Button variant="primary" icon={<RotateCcw size={15} />} loading={busy === `correct:${correctTarget.id}`} onClick={() => void correctInvoice()}>保存并重新匹配</Button>
            </>
          }
        >
          <div className="proc-dialog-form">
            {([["quantity", "数量"], ["unit_price", "单价"], ["amount_excluding_tax", "不含税金额"], ["tax_amount", "税额"], ["total_amount", "价税合计"], ["tax_rate", "税率（小数，如 0.13）"]] as const).map(([key, label], index) => (
              <label className="proc-field" key={key}>
                <span>{label}</span>
                <input className="proc-input mono" autoFocus={index === 0} type="number" step="any" value={correctForm[key]} onChange={(event) => setCorrectForm((current) => ({ ...current, [key]: event.target.value }))} placeholder={String(correctTarget[key] ?? "")} />
              </label>
            ))}
          </div>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}
    </CenterPage>
  );
}
