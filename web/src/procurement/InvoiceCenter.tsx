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
    refetchInterval: 5_000,
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

  const correctInvoice = async () => {
    if (!correctTarget) return;
    const numeric = (value: string) => (value.trim() === "" ? null : Number(value));
    const ok = await run(`correct:${correctTarget.id}`, () =>
      procurementApi.invoiceAction(correctTarget.id, "correct", {
        quantity: numeric(correctForm.quantity),
        unit_price: numeric(correctForm.unit_price),
        amount_excluding_tax: numeric(correctForm.amount_excluding_tax),
        tax_amount: numeric(correctForm.tax_amount),
        total_amount: numeric(correctForm.total_amount),
        tax_rate: numeric(correctForm.tax_rate),
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
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>发票中心</h1>
          <p>上传发票 → 解析 → 三单匹配（PO/收货/发票）→ 差异挂起处理 → 核销 → 付款</p>
        </div>
        <span className="proc-page-count">共 {invoicesQuery.data?.total ?? 0} 张</span>
      </header>

      <div className="proc-invoice-upload">
        <select
          aria-label="选择采购订单"
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
      {error ? <p className="proc-toolbar-error" role="alert">{error}</p> : null}
      {notice ? <p className="proc-toolbar-success" role="status">{notice}</p> : null}

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
      </div>

      <div className="proc-invoice-layout">
        <div className="proc-invoice-list" aria-busy={invoicesQuery.isPending}>
          {invoicesQuery.isPending ? (
            <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载发票…</div>
          ) : null}
          {invoicesQuery.isError ? (
            <section className="proc-empty-state compact" role="alert">
              <AlertTriangle size={26} />
              <h2>发票加载失败</h2>
              <p>{invoicesQuery.error instanceof Error ? invoicesQuery.error.message : "未知错误"}</p>
            </section>
          ) : null}
          {!invoicesQuery.isPending && !invoicesQuery.isError && !invoices.length ? (
            <div className="proc-empty-state">
              <Archive size={30} />
              <h2>{status ? "该状态下没有发票" : "还没有发票"}</h2>
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
                className={`proc-invoice-card ${selectedId === invoice.id ? "selected" : ""}`}
                onClick={() => setSelectedId(invoice.id)}
              >
                <span className="proc-invoice-card-head">
                  <code>{invoice.invoice_no}</code>
                  <i className={`proc-status ${state.tone}`}><span />{state.label}</i>
                </span>
                <strong>{invoice.supplier_name}</strong>
                <span className="proc-invoice-card-facts">
                  <small>{invoice.order_no || "—"}</small>
                  <small>价税合计 {money(invoice.total_amount)}</small>
                  {invoice.status === "DIFF_HOLD" ? <small className="proc-invoice-diff">{diffCount} 项差异</small> : null}
                </span>
              </button>
            );
          })}
        </div>

        <div className="proc-invoice-detail">
          {detailQuery.isError && selectedId ? (
            <section className="proc-empty-state compact" role="alert">
              <AlertTriangle size={26} />
              <h2>发票详情加载失败</h2>
              <p>{detailQuery.error instanceof Error ? detailQuery.error.message : "未知错误"}</p>
              <button type="button" className="proc-button" onClick={() => { void detailQuery.refetch(); }}>重试</button>
            </section>
          ) : detail ? (
            <>
              <header className="proc-panel-head">
                <div><Receipt size={15} /><h2>{detail.invoice_no}</h2><span>{detail.supplier_name}</span></div>
                <span className={`proc-status ${STATUS_LABELS[detail.status].tone}`}><i />{STATUS_LABELS[detail.status].label}</span>
              </header>

              {detailQuery.isPending ? (
                <span className="proc-muted">列表快照 — 正在加载完整三单对比…</span>
              ) : null}

              <div className="proc-invoice-facts">
                <span><small>发票代码</small><strong>{detail.invoice_code || "—"}</strong></span>
                <span><small>开票日期</small><strong>{detail.issue_date || "—"}</strong></span>
                <span><small>数量</small><strong>{money(detail.quantity)} {detail.unit || ""}</strong></span>
                <span><small>单价</small><strong>{money(detail.unit_price)}</strong></span>
                <span><small>不含税金额</small><strong>{money(detail.amount_excluding_tax)}</strong></span>
                <span><small>税额</small><strong>{money(detail.tax_amount)}</strong></span>
                <span><small>价税合计</small><strong>{money(detail.total_amount)}</strong></span>
                <span><small>税率</small><strong>{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</strong></span>
              </div>

              {detail.three_way ? (
                <section className="proc-report-section">
                  <header><div><ShieldCheck size={15} /><h3>三单匹配对比（PO / 收货 GRN / 发票）</h3></div></header>
                  <table className="proc-comparison-table">
                    <thead><tr><th>字段</th><th>订单（PO）</th><th>收货（GRN）</th><th>发票</th><th>期望</th></tr></thead>
                    <tbody>
                      <tr><td>数量</td><td>{money(detail.order_quantity)}</td><td>{money(detail.order_received_quantity)}</td><td>{money(detail.quantity)}</td><td>{detail.match_result?.matched ? <CheckCircle2 size={14} /> : <XCircle size={14} />}</td></tr>
                      <tr><td>到货总价</td><td>{money(detail.order_landed_total)}</td><td>—</td><td>{money(detail.total_amount)}</td><td>{detail.match_result?.matched ? <CheckCircle2 size={14} /> : <XCircle size={14} />}</td></tr>
                      <tr><td>税率</td><td>{detail.expected_tax_rate ? `${(Number(detail.expected_tax_rate) * 100).toFixed(2)}%` : "—"}</td><td>—</td><td>{detail.tax_rate ? `${(Number(detail.tax_rate) * 100).toFixed(2)}%` : "—"}</td><td>{detail.match_result?.matched ? <CheckCircle2 size={14} /> : <XCircle size={14} />}</td></tr>
                    </tbody>
                  </table>
                  {detail.match_result?.diffs?.length ? (
                    <ul className="proc-invoice-diffs">
                      {detail.match_result.diffs.map((diff) => (
                        <li key={diff.field}>
                          <strong>{DIFF_LABELS[diff.field] || diff.field}</strong>
                          <span>期望 {diff.expected}</span><span>实际 {diff.actual}</span>
                          <i>差异 {diff.diff}</i>
                        </li>
                      ))}
                    </ul>
                  ) : detail.match_result?.matched ? (
                    <p className="proc-invoice-matched"><BadgeCheck size={15} />三单匹配通过：数量、单价、总价、税率全部在容差内（Java 确定性规则）。</p>
                  ) : null}
                  {detail.match_explanation ? (
                    <div className="proc-invoice-explanation">
                      <strong>Agent 差异解释</strong>
                      <p>{detail.match_explanation.reason}</p>
                      <ul>
                        {(detail.match_explanation.suggestions || []).map((suggestion) => <li key={suggestion}>{suggestion}</li>)}
                      </ul>
                      <small>来源：{detail.match_explanation.source}</small>
                    </div>
                  ) : null}
                </section>
              ) : null}

              <div className="proc-invoice-actions">
                {detail.status === "DIFF_HOLD" ? (
                  <>
                    <button className="proc-button" type="button" onClick={() => setCorrectTarget(detail)}><RotateCcw size={14} />手工改单</button>
                    <button className="proc-button" type="button" onClick={() => { setForceTarget(detail); setForceNotes(""); setForceConfirmed(false); setError(null); }}><ShieldCheck size={14} />强制通过</button>
                    <button className="proc-button danger" type="button" onClick={() => { setVoidTarget(detail); setVoidNotes(""); setError(null); }}><XCircle size={14} />作废（退回重开）</button>
                  </>
                ) : null}
                {detail.status === "MATCHED" ? (
                  <button className="proc-button primary" type="button" disabled={busy === `reconcile:${detail.id}`} onClick={() => void reconcile(detail)}>
                    {busy === `reconcile:${detail.id}` ? <LoaderCircle className="spin" size={14} /> : <CheckCircle2 size={14} />}核销
                  </button>
                ) : null}
                {detail.status === "REGISTERED" ? (
                  <span className="proc-muted">Agent 匹配中…（解析结果到达后自动匹配）</span>
                ) : null}
              </div>
            </>
          ) : (
            <div className="proc-empty-panel">
              <FileSpreadsheet size={30} />
              <h2>发票详情</h2>
              <p>选择一张发票查看三单匹配对比与差异处理。</p>
            </div>
          )}
        </div>
      </div>

      {voidTarget ? (
        <div className="proc-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `void:${voidTarget.id}`) setVoidTarget(null); }}>
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="void-invoice-title">
            <header><div><XCircle size={17} /><h2 id="void-invoice-title">作废发票（退回重开）</h2></div></header>
            <div className="proc-delete-target"><strong>{voidTarget.invoice_no}</strong><span>{voidTarget.supplier_name} · {money(voidTarget.total_amount)}</span></div>
            <label className="proc-field"><span>作废原因 <b>*</b></span><input autoFocus value={voidNotes} onChange={(event) => setVoidNotes(event.target.value)} placeholder="例如：发票开具错误，重新开具" /></label>
            {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setVoidTarget(null)} disabled={busy === `void:${voidTarget.id}`}>取消</button>
              <button className="proc-button danger" type="button" disabled={busy === `void:${voidTarget.id}`} onClick={() => void voidInvoice()}>
                {busy === `void:${voidTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <XCircle size={15} />}确认作废
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {forceTarget ? (
        <div className="proc-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `force:${forceTarget.id}`) setForceTarget(null); }}>
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="force-invoice-title">
            <header><div><ShieldCheck size={17} /><h2 id="force-invoice-title">强制通过（allow-once 审批）</h2></div></header>
            <div className="proc-delete-target"><strong>{forceTarget.invoice_no}</strong><span>差异 {forceTarget.match_result?.diffs?.length || 0} 项</span></div>
            <label className="proc-field"><span>人工备注 <b>*</b></span><input autoFocus value={forceNotes} onChange={(event) => setForceNotes(event.target.value)} placeholder="强制通过原因（写入审计）" /></label>
            <label className="proc-invoice-confirm"><input type="checkbox" checked={forceConfirmed} onChange={(event) => setForceConfirmed(event.target.checked)} /><span>我已核对差异并确认强制通过（一次性，不能撤销）</span></label>
            {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setForceTarget(null)} disabled={busy === `force:${forceTarget.id}`}>取消</button>
              <button className="proc-button" type="button" disabled={busy === `force:${forceTarget.id}`} onClick={() => void forceMatch()}>
                {busy === `force:${forceTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}确认强制通过
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {correctTarget ? (
        <div className="proc-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `correct:${correctTarget.id}`) setCorrectTarget(null); }}>
          <section className="proc-confirm-dialog wide" role="dialog" aria-modal="true" aria-labelledby="correct-invoice-title">
            <header><div><RotateCcw size={17} /><h2 id="correct-invoice-title">手工改单（重新三单匹配）</h2></div></header>
            <div className="proc-supplier-form">
              {([["quantity", "数量"], ["unit_price", "单价"], ["amount_excluding_tax", "不含税金额"], ["tax_amount", "税额"], ["total_amount", "价税合计"], ["tax_rate", "税率（小数，如 0.13）"]] as const).map(([key, label], index) => (
                <label className="proc-field" key={key}>
                  <span>{label}</span>
                  <input autoFocus={index === 0} type="number" step="any" value={correctForm[key]} onChange={(event) => setCorrectForm((current) => ({ ...current, [key]: event.target.value }))} placeholder={String(correctTarget[key] ?? "")} />
                </label>
              ))}
              {error ? <p className="proc-form-error proc-span-2" role="alert">{error}</p> : null}
            </div>
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setCorrectTarget(null)} disabled={busy === `correct:${correctTarget.id}`}>取消</button>
              <button className="proc-button" type="button" disabled={busy === `correct:${correctTarget.id}`} onClick={() => void correctInvoice()}>
                {busy === `correct:${correctTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}保存并重新匹配
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
