import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
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
import { useState } from "react";

import { procurementApi } from "./api";
import type { InvoiceStatus, InvoiceView } from "./types";
import { useEscape } from "./useEscape";

const STATUS_FILTERS: Array<{ value: InvoiceStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "REGISTERED", label: "已登记" },
  { value: "MATCHED", label: "已匹配" },
  { value: "DIFF_HOLD", label: "差异挂起" },
  { value: "RECONCILED", label: "已核销" },
  { value: "VOIDED", label: "已作废" },
];

const STATUS_LABELS: Record<InvoiceStatus, { label: string; tone: string }> = {
  REGISTERED: { label: "已登记", tone: "info" },
  MATCHED: { label: "已匹配", tone: "success" },
  DIFF_HOLD: { label: "差异挂起", tone: "danger" },
  VOIDED: { label: "已作废", tone: "neutral" },
  RECONCILED: { label: "已核销", tone: "accent" },
};

const DIFF_LABELS: Record<string, string> = {
  quantity: "数量",
  unit_price: "单价",
  total_amount: "价税合计",
  tax_rate: "税率",
};

function money(value: string | null | undefined) {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(parsed)
    : String(value);
}

export function InvoiceCenter() {
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

  useEscape(!!voidTarget, () => setVoidTarget(null), busy?.startsWith("void:") ?? false);
  useEscape(!!forceTarget, () => setForceTarget(null), busy?.startsWith("force:") ?? false);
  useEscape(!!correctTarget, () => setCorrectTarget(null), busy?.startsWith("correct:") ?? false);

  const invoicesQuery = useQuery({
    queryKey: ["procurement-invoices", status],
    queryFn: () => procurementApi.invoices(status || undefined, undefined, 0, 100),
    // 仅存在在途发票（REGISTERED/DIFF_HOLD）时轮询；全部终态后停止
    refetchInterval: (query) =>
      query.state.data?.items?.some(
        (item) => item.status === "REGISTERED" || item.status === "DIFF_HOLD"
      )
        ? 5_000
        : false,
  });
  const ordersQuery = useQuery({
    queryKey: ["procurement-invoices-orders"],
    queryFn: () => procurementApi.orders(undefined, 0, 100),
  });
  const invoices = invoicesQuery.data?.items || [];
  const selected = invoices.find((item) => item.id === selectedId) || null;
  const detailQuery = useQuery({
    queryKey: ["procurement-invoice", selectedId],
    queryFn: () => procurementApi.invoice(selectedId!),
    enabled: !!selectedId,
    // 终态（MATCHED/RECONCILED/VOIDED）不再轮询；仅在途态（解析/差异挂起）持续刷新
    refetchInterval: () =>
      selected?.status === "REGISTERED" || selected?.status === "DIFF_HOLD" ? 5_000 : false,
  });
  const detail = detailQuery.data ?? selected ?? null;

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
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <Receipt className="w-5 h-5 text-accent" />
            发票中心
          </h1>
          <p className="text-xs text-text-muted mt-1">上传发票 → 解析 → 三单匹配（PO/收货/发票）→ 差异挂起处理 → 核销 → 付款</p>
        </div>
        <span className="proc-page-count text-xs font-medium text-text-secondary bg-surface-subtle px-3 py-1 rounded-full border border-border">共 {invoicesQuery.data?.total ?? 0} 张</span>
      </header>

      <div className="proc-invoice-upload glass-panel bg-surface/80 p-4 rounded-xl border border-border/80 flex flex-wrap items-center gap-3 shadow-sm">
        <select
          aria-label="选择采购订单"
          className="flex-1 min-w-[240px] px-3 py-2 rounded-lg border border-border bg-surface-subtle text-xs text-text focus:outline-accent"
          value={orderId}
          onChange={(event) => setOrderId(event.target.value)}
        >
          <option value="">选择采购订单（优先已收货）…</option>
          {(ordersQuery.data?.items || []).map((order) => (
            <option key={order.id} value={order.id}>
              {order.order_no} · {order.supplier_name} · {order.item_name}（{order.status}）
            </option>
          ))}
        </select>
        <label className={`proc-upload-button inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs cursor-pointer ${busy?.startsWith("upload") ? "disabled opacity-60 pointer-events-none" : ""}`}>
          {busy?.startsWith("upload") ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
          <span>{busy?.startsWith("upload") ? "上传解析中" : "上传发票"}</span>
          <input
            data-testid="invoice-upload"
            className="sr-only"
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
      {error ? <p className="proc-toolbar-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
      {notice ? <p className="proc-toolbar-success text-xs text-accent font-medium p-2.5 rounded-lg bg-accent-soft border border-accent/30" role="status">{notice}</p> : null}

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
      </div>

      <div className="proc-invoice-layout grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="proc-invoice-list lg:col-span-4 flex flex-col gap-3" aria-busy={invoicesQuery.isPending}>
          {invoicesQuery.isPending ? (
            <div className="proc-loading-state py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在加载发票…</div>
          ) : null}
          {invoicesQuery.isError ? (
            <section className="proc-empty-state compact py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
              <AlertTriangle size={26} className="text-danger" />
              <h2 className="text-sm font-semibold text-text">发票加载失败</h2>
              <p className="text-text-muted">{invoicesQuery.error instanceof Error ? invoicesQuery.error.message : "未知错误"}</p>
            </section>
          ) : null}
          {!invoicesQuery.isPending && !invoicesQuery.isError && !invoices.length ? (
            <div className="proc-empty-state py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
              <Archive size={30} className="text-text-muted" />
              <h2 className="text-sm font-semibold text-text">{status ? "该状态下没有发票" : "还没有发票"}</h2>
              <p>选择已收货订单上传发票，Agent 解析后自动执行三单匹配。</p>
            </div>
          ) : null}
          {invoices.map((invoice) => {
            const state = STATUS_LABELS[invoice.status];
            const diffCount = invoice.match_result?.diffs?.length || 0;
            return (
              <button
                type="button"
                key={invoice.id}
                className={`proc-invoice-card glass-panel text-left p-4 rounded-xl border transition-all duration-150 flex flex-col gap-2 ${selectedId === invoice.id ? "selected border-accent bg-accent-soft/30 shadow-xs ring-1 ring-accent/30" : "bg-surface/80 border-border/80 hover:border-border-strong hover:bg-surface"}`}
                onClick={() => setSelectedId(invoice.id)}
              >
                <span className="proc-invoice-card-head flex items-center justify-between gap-2">
                  <code className="font-mono text-xs font-semibold text-accent">{invoice.invoice_no}</code>
                  <i className={`proc-status ${state.tone} not-italic text-[11px] font-medium px-2 py-0.5 rounded-full border inline-flex items-center gap-1.5`}><span className="w-1.5 h-1.5 rounded-full bg-current" />{state.label}</i>
                </span>
                <strong className="text-xs font-semibold text-text truncate">{invoice.supplier_name}</strong>
                <span className="proc-invoice-card-facts flex items-center justify-between gap-2 text-[11px] text-text-muted">
                  <small className="font-mono">{invoice.order_no || "—"}</small>
                  <small className="font-mono font-medium">价税合计 {money(invoice.total_amount)}</small>
                  {invoice.status === "DIFF_HOLD" ? <small className="proc-invoice-diff text-danger font-medium bg-danger-soft px-1.5 py-0.5 rounded border border-danger/20">{diffCount} 项差异</small> : null}
                </span>
              </button>
            );
          })}
        </div>

        <div className="proc-invoice-detail lg:col-span-8 glass-panel rounded-xl p-6 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-5">
          {detailQuery.isError && selectedId ? (
            <section className="proc-empty-state compact py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
              <AlertTriangle size={26} className="text-danger" />
              <h2 className="text-sm font-semibold text-text">发票详情加载失败</h2>
              <p className="text-text-muted">{detailQuery.error instanceof Error ? detailQuery.error.message : "未知错误"}</p>
              <button type="button" className="proc-button px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" onClick={() => { void detailQuery.refetch(); }}>重试</button>
            </section>
          ) : detail ? (
            <>
              <header className="proc-panel-head flex items-start justify-between gap-3 pb-3 border-b border-border/60">
                <div className="flex items-center gap-2"><Receipt size={18} className="text-accent" /><div><h2 className="text-base font-bold text-text font-mono">{detail.invoice_no}</h2><span className="text-xs text-text-muted">{detail.supplier_name}</span></div></div>
                <span className={`proc-status ${STATUS_LABELS[detail.status].tone} text-xs font-medium px-2.5 py-0.5 rounded-full border inline-flex items-center gap-1.5`}><i className="w-1.5 h-1.5 rounded-full bg-current" />{STATUS_LABELS[detail.status].label}</span>
              </header>

              {detailQuery.isPending ? (
                <span className="proc-muted text-xs text-text-muted">列表快照 — 正在加载完整三单对比…</span>
              ) : null}

              <div className="proc-invoice-facts grid grid-cols-2 sm:grid-cols-4 gap-3 bg-surface-subtle/60 p-3.5 rounded-lg border border-border/40 text-xs">
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">发票代码</small><strong className="font-mono text-text mt-0.5">{detail.invoice_code || "—"}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">开票日期</small><strong className="font-mono text-text mt-0.5">{detail.issue_date || "—"}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">数量</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.quantity)} {detail.unit || ""}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">单价</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.unit_price)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">不含税金额</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.amount_excluding_tax)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">税额</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.tax_amount)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">价税合计</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.total_amount)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">税率</small><strong className="font-mono font-bold text-text mt-0.5">{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</strong></span>
              </div>

              {detail.three_way ? (
                <section className="proc-report-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                  <header className="flex items-center gap-2 pb-1 border-b border-border/30"><ShieldCheck size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">三单匹配对比（PO / 收货 GRN / 发票）</h3></header>
                  <table className="proc-comparison-table w-full text-left text-xs border-collapse rounded-lg overflow-hidden border border-border/60">
                    <thead><tr className="bg-surface-subtle/80 border-b border-border text-text-muted"><th className="p-2.5">字段</th><th className="p-2.5">订单（PO）</th><th className="p-2.5">收货（GRN）</th><th className="p-2.5">发票</th><th className="p-2.5">期望</th></tr></thead>
                    <tbody className="divide-y divide-border/40 font-mono">
                      <tr><td className="p-2.5 font-sans font-medium text-text">数量</td><td className="p-2.5">{money(detail.order_quantity)}</td><td className="p-2.5">{money(detail.order_received_quantity)}</td><td className="p-2.5">{money(detail.quantity)}</td><td className="p-2.5">{detail.match_result?.matched ? <CheckCircle2 size={14} className="text-accent inline" /> : <XCircle size={14} className="text-danger inline" />}</td></tr>
                      <tr><td className="p-2.5 font-sans font-medium text-text">到货总价</td><td className="p-2.5">{money(detail.order_landed_total)}</td><td className="p-2.5">—</td><td className="p-2.5">{money(detail.total_amount)}</td><td className="p-2.5">{detail.match_result?.matched ? <CheckCircle2 size={14} className="text-accent inline" /> : <XCircle size={14} className="text-danger inline" />}</td></tr>
                      <tr><td className="p-2.5 font-sans font-medium text-text">税率</td><td className="p-2.5">{detail.expected_tax_rate ? `${(Number(detail.expected_tax_rate) * 100).toFixed(2)}%` : "—"}</td><td className="p-2.5">—</td><td className="p-2.5">{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</td><td className="p-2.5">{detail.match_result?.matched ? <CheckCircle2 size={14} className="text-accent inline" /> : <XCircle size={14} className="text-danger inline" />}</td></tr>
                    </tbody>
                  </table>
                  {detail.match_result?.diffs?.length ? (
                    <ul className="proc-invoice-diffs flex flex-col gap-2 text-xs">
                      {detail.match_result.diffs.map((diff) => (
                        <li className="flex items-center gap-3 p-2.5 rounded-lg bg-danger-soft/30 border border-danger/30 text-danger" key={diff.field}>
                          <strong className="font-semibold">{DIFF_LABELS[diff.field] || diff.field}</strong>
                          <span className="font-mono text-text-muted">期望 {diff.expected}</span>
                          <span className="font-mono text-text-muted">实际 {diff.actual}</span>
                          <i className="not-italic font-mono font-bold ml-auto">差异 {diff.diff}</i>
                        </li>
                      ))}
                    </ul>
                  ) : detail.match_result?.matched ? (
                    <p className="proc-invoice-matched flex items-center gap-2 text-xs font-medium text-accent p-3 rounded-lg bg-accent-soft border border-accent/30"><BadgeCheck size={15} />三单匹配通过：数量、单价、总价、税率全部在容差内（Java 确定性规则）。</p>
                  ) : null}
                  {detail.match_explanation ? (
                    <div className="proc-invoice-explanation p-4 rounded-xl bg-surface-subtle/80 border border-border/60 flex flex-col gap-2 text-xs">
                      <strong className="font-semibold text-text flex items-center gap-1.5"><BadgeCheck size={14} className="text-accent" />Agent 差异解释</strong>
                      <p className="text-text-secondary leading-relaxed">{detail.match_explanation.reason}</p>
                      <ul className="list-disc list-inside space-y-1 text-text-muted pl-1">
                        {(detail.match_explanation.suggestions || []).map((suggestion) => <li key={suggestion}>{suggestion}</li>)}
                      </ul>
                      <small className="text-text-muted mt-1 font-mono">来源：{detail.match_explanation.source}</small>
                    </div>
                  ) : null}
                </section>
              ) : null}

              <div className="proc-invoice-actions flex flex-wrap items-center gap-2.5 pt-3 border-t border-border/60">
                {detail.status === "DIFF_HOLD" ? (
                  <>
                    <button className="proc-button inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => openCorrect(detail)}><RotateCcw size={14} />手工改单</button>
                    <button className="proc-button inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold bg-warning text-white hover:bg-amber-600 transition-all shadow-xs" type="button" onClick={() => { setForceTarget(detail); setForceNotes(""); setForceConfirmed(false); setError(null); }}><ShieldCheck size={14} />强制通过</button>
                    <button className="proc-button danger inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold bg-danger text-white hover:bg-rose-700 transition-all shadow-xs" type="button" onClick={() => { setVoidTarget(detail); setVoidNotes(""); setError(null); }}><XCircle size={14} />作废（退回重开）</button>
                  </>
                ) : null}
                {detail.status === "MATCHED" ? (
                  <button className="proc-button primary inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" disabled={busy === `reconcile:${detail.id}`} onClick={() => void reconcile(detail)}>
                    {busy === `reconcile:${detail.id}` ? <LoaderCircle className="spin" size={14} /> : <CheckCircle2 size={14} />}核销
                  </button>
                ) : null}
                {detail.status === "REGISTERED" ? (
                  <span className="proc-muted text-xs text-text-muted">Agent 匹配中…（解析结果到达后自动匹配）</span>
                ) : null}
              </div>
            </>
          ) : (
            <div className="proc-empty-panel py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
              <FileSpreadsheet size={30} className="text-text-muted" />
              <h2 className="text-sm font-semibold text-text">发票详情</h2>
              <p>选择一张发票查看三单匹配对比与差异处理。</p>
            </div>
          )}
        </div>
      </div>

      {voidTarget ? (
        <div className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `void:${voidTarget.id}`) setVoidTarget(null); }}>
          <section className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="void-invoice-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base"><XCircle size={18} className="text-danger" /><h2 id="void-invoice-title">作废发票（退回重开）</h2></div></header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs"><strong className="font-mono text-sm font-bold text-text">{voidTarget.invoice_no}</strong><span className="text-text-muted">{voidTarget.supplier_name} · {money(voidTarget.total_amount)}</span></div>
            <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>作废原因 <b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" autoFocus value={voidNotes} onChange={(event) => setVoidNotes(event.target.value)} placeholder="例如：发票开具错误，重新开具" /></label>
            {error ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setVoidTarget(null)} disabled={busy === `void:${voidTarget.id}`}>取消</button>
              <button className="proc-button danger px-4 py-1.5 rounded-lg text-xs font-semibold bg-danger text-white hover:bg-rose-700 inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `void:${voidTarget.id}`} onClick={() => void voidInvoice()}>
                {busy === `void:${voidTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <XCircle size={15} />}确认作废
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {forceTarget ? (
        <div className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `force:${forceTarget.id}`) setForceTarget(null); }}>
          <section className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="force-invoice-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base"><ShieldCheck size={18} className="text-warning" /><h2 id="force-invoice-title">强制通过（allow-once 审批）</h2></div></header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs"><strong className="font-mono text-sm font-bold text-text">{forceTarget.invoice_no}</strong><span className="text-text-muted">差异 {forceTarget.match_result?.diffs?.length || 0} 项</span></div>
            <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>人工备注 <b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" autoFocus value={forceNotes} onChange={(event) => setForceNotes(event.target.value)} placeholder="强制通过原因（写入审计）" /></label>
            <label className="proc-invoice-confirm flex items-center gap-2 text-xs text-text cursor-pointer"><input type="checkbox" className="rounded border-border text-accent focus:ring-accent" checked={forceConfirmed} onChange={(event) => setForceConfirmed(event.target.checked)} /><span>我已核对差异并确认强制通过（一次性，不能撤销）</span></label>
            {error ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setForceTarget(null)} disabled={busy === `force:${forceTarget.id}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-warning text-white hover:bg-amber-600 inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `force:${forceTarget.id}`} onClick={() => void forceMatch()}>
                {busy === `force:${forceTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}确认强制通过
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {correctTarget ? (
        <div className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `correct:${correctTarget.id}`) setCorrectTarget(null); }}>
          <section className="proc-confirm-dialog wide glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-xl w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="correct-invoice-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base"><RotateCcw size={18} className="text-accent" /><h2 id="correct-invoice-title">手工改单（重新三单匹配）</h2></div></header>
            <div className="proc-supplier-form grid grid-cols-1 sm:grid-cols-2 gap-3">
              {([["quantity", "数量"], ["unit_price", "单价"], ["amount_excluding_tax", "不含税金额"], ["tax_amount", "税额"], ["total_amount", "价税合计"], ["tax_rate", "税率（小数，如 0.13）"]] as const).map(([key, label], index) => (
                <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text" key={key}>
                  <span>{label}</span>
                  <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm font-mono" autoFocus={index === 0} type="number" step="any" value={correctForm[key]} onChange={(event) => setCorrectForm((current) => ({ ...current, [key]: event.target.value }))} placeholder={String(correctTarget[key] ?? "")} />
                </label>
              ))}
              {error ? <p className="proc-form-error proc-span-2 col-span-full text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            </div>
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setCorrectTarget(null)} disabled={busy === `correct:${correctTarget.id}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `correct:${correctTarget.id}`} onClick={() => void correctInvoice()}>
                {busy === `correct:${correctTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}保存并重新匹配
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
