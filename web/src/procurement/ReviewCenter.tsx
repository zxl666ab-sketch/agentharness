import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  ClipboardCheck,
  ClipboardEdit,
  Clock3,
  ExternalLink,
  Fingerprint,
  LoaderCircle,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { procurementApi } from "./api";
import { useEscape } from "./useEscape";
import { useModalFocus } from "./useModalFocus";
import type {
  ProcurementRequestSummary,
  ReviewAction,
  ReviewDetail,
  ReviewStatus,
  ReviewView,
} from "./types";

type Props = {
  requests: ProcurementRequestSummary[];
  reviews: ReviewView[];
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (id: string | null, push?: boolean) => void;
  onOpenTask: (id: string) => void;
};

const STATUS_LABELS: Record<ReviewStatus, string> = {
  PENDING: "待审核",
  APPROVED: "已批准",
  REJECTED: "已驳回",
  NO_AWARD: "已流标",
  STALE: "已过期",
};

const ACTION_LABELS: Record<ReviewAction, string> = {
  APPROVE_SUGGESTION: "确认 AI 建议",
  REVISE_AND_APPROVE: "修改后通过",
  REJECT_AND_RETRY: "驳回重跑",
  NO_AWARD: "本轮流标",
};

const RISK_LABELS: Record<string, string> = {
  NO_ELIGIBLE_QUOTES: "无合格报价",
  UNRESOLVED_FIELDS: "字段待复核",
  INSUFFICIENT_QUOTES: "报价不足",
  LOW_CONFIDENCE: "低置信度",
  CONFLICTING_FIELDS: "字段冲突",
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "操作失败");
}

function timeText(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function waitText(value: string) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} 分钟`;
  if (minutes < 2_880) return `${Math.floor(minutes / 60)} 小时`;
  return `${Math.floor(minutes / 1_440)} 天`;
}

function money(value: string, currency: string) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(Number(value));
}

function riskText(value: string) {
  return RISK_LABELS[value] || value;
}

function quoteById(detail: ReviewDetail | null, id?: string | null) {
  return detail?.comparison.result.quotes.find((item) => item.quote_id === id) || null;
}

export function ReviewCenter({
  requests,
  reviews,
  loading,
  error,
  selectedId,
  onSelect,
  onOpenTask,
}: Props) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ReviewStatus | "ALL">(selectedId ? "ALL" : "PENDING");
  const [risk, setRisk] = useState("ALL");
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<ReviewAction>("APPROVE_SUGGESTION");
  const [actor, setActor] = useState("采购员");
  const [finalQuoteId, setFinalQuoteId] = useState("");
  const [reason, setReason] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const confirmDialogRef = useRef<HTMLElement | null>(null);
  const confirmCancelRef = useRef<HTMLButtonElement | null>(null);
  const requestMap = useMemo(() => new Map(requests.map((item) => [item.id, item])), [requests]);
  const riskOptions = useMemo(() => [...new Set(reviews.flatMap((item) => item.risk_flags))], [reviews]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return [...reviews]
      .filter((item) => (status === "ALL" || item.status === status)
        && (risk === "ALL" || item.risk_flags.includes(risk))
        && (!query || [requestMap.get(item.business_id)?.reference, requestMap.get(item.business_id)?.title, item.evidence_sha256]
          .filter(Boolean).join(" ").toLowerCase().includes(query)))
      .sort((left, right) => right.priority - left.priority
        || new Date(left.waiting_since).getTime() - new Date(right.waiting_since).getTime());
  }, [requestMap, reviews, risk, search, status]);

  useEffect(() => {
    if (loading) return;
    if (!filtered.length) {
      if (selectedId && !reviews.some((item) => item.review_id === selectedId)) onSelect(null, false);
      return;
    }
    if (!selectedId || !reviews.some((item) => item.review_id === selectedId)) {
      onSelect(filtered[0].review_id, false);
    }
  }, [filtered, loading, onSelect, reviews, selectedId]);

  const detailQuery = useQuery({
    queryKey: ["procurement-review", selectedId],
    queryFn: () => procurementApi.review(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => query.state.data?.status === "PENDING" && query.state.data.action
      ? 1_500 : false,
  });
  const detail = detailQuery.data || null;
  const request = detail ? requestMap.get(detail.business_id) : null;
  const suggestion = quoteById(detail, detail?.suggested_quote_id);
  const finalQuote = quoteById(detail, detail?.final_quote_id);
  const quotes = useMemo(() => [...(detail?.comparison.result.quotes || [])].sort((left, right) => {
    if (left.eligible !== right.eligible) return left.eligible ? -1 : 1;
    return (left.rank || 999) - (right.rank || 999);
  }), [detail?.comparison.result.quotes]);
  const pending = detail?.status === "PENDING" && !detail.action;
  const submittingDecision = detail?.status === "PENDING" && !!detail.action;
  const selectedQuote = quoteById(detail, finalQuoteId);
  const hasEligibleQuotes = Boolean(detail?.comparison.result.eligible_count);
  const hasAlternativeQuote = quotes.some((quote) => quote.eligible && quote.quote_id !== detail?.suggested_quote_id);
  const needsReason = action !== "APPROVE_SUGGESTION";
  const formValid = Boolean(actor.trim()
    && (!needsReason || reason.trim())
    && (action !== "REVISE_AND_APPROVE" || (selectedQuote?.eligible && finalQuoteId !== detail?.suggested_quote_id))
    && (action !== "APPROVE_SUGGESTION" || suggestion)
    && (action !== "NO_AWARD" || !hasEligibleQuotes));

  useEffect(() => {
    if (!selectedId || filtered.some((item) => item.review_id === selectedId)) return;
    if (!reviews.some((item) => item.review_id === selectedId)) return;
    setStatus("ALL");
    setRisk("ALL");
    setSearch("");
  }, [filtered, reviews, selectedId]);

  useEffect(() => {
    if (!detail) return;
    const nextAction: ReviewAction = detail.suggested_quote_id ? "APPROVE_SUGGESTION" : "NO_AWARD";
    setAction(nextAction);
    setFinalQuoteId(
      detail.comparison.result.quotes.find((item) => item.eligible && item.quote_id !== detail.suggested_quote_id)?.quote_id
      || detail.suggested_quote_id
      || detail.comparison.result.quotes.find((item) => item.eligible)?.quote_id
      || "",
    );
    setReason("");
    setConfirmed(false);
    setConfirmOpen(false);
    setActionError(null);
    setNotice(null);
  }, [detail]);

  useEscape(confirmOpen && !!detail, () => setConfirmOpen(false), busy);
  // 审批提交是不可回退动作：初始焦点放在「取消」上，Tab 循环限制在弹窗内。
  useModalFocus(confirmOpen && !!detail, confirmDialogRef, confirmCancelRef);

  async function submit() {
    if (!detail || !pending || !formValid || busy) return;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const revisions = action === "REVISE_AND_APPROVE"
        ? { supplier_selection: { from: detail.suggested_quote_id, to: finalQuoteId } }
        : {};
      const updated = await procurementApi.submitReview(detail.review_id, {
        action,
        expected_version: detail.version,
        actor: actor.trim(),
        final_quote_id: action === "REVISE_AND_APPROVE" ? finalQuoteId : null,
        revisions,
        reason: reason.trim() || null,
      });
      queryClient.setQueryData(["procurement-review", detail.review_id], updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-reviews"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-request", detail.business_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      ]);
      setConfirmOpen(false);
      setConfirmed(false);
      setNotice(updated.status === "PENDING" ? "审核动作已提交，正在形成正式决定。" : "审核动作已完成并写入审计记录。");
    } catch (cause) {
      // Keep the confirmation dialog open so the user can retry after a
      // transient failure; the detail refetch below updates the panel state
      // (e.g. to STALE) when the server actually rejected the submission.
      setActionError(errorText(cause));
      await queryClient.invalidateQueries({ queryKey: ["procurement-review", detail.review_id] });
    } finally {
      setBusy(false);
    }
  }

  const confirmQuote = action === "APPROVE_SUGGESTION" ? suggestion : action === "REVISE_AND_APPROVE" ? selectedQuote : null;

  return (
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <span className="text-xs font-semibold text-accent uppercase tracking-wider">决策队列</span>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2 mt-0.5">
            <ShieldCheck className="w-5 h-5 text-accent" />
            人工审核
          </h1>
        </div>
        <div className="proc-page-summary flex items-center gap-2 bg-surface-subtle px-3.5 py-1.5 rounded-full border border-border">
          <strong className="text-sm font-mono font-bold text-accent">{reviews.filter((item) => item.status === "PENDING").length}</strong>
          <span className="text-xs text-text-secondary">等待审核</span>
        </div>
      </header>

      <div className="proc-center-filters reviews flex flex-wrap items-center gap-3 p-4 rounded-xl glass-panel bg-surface/80 border border-border/80 shadow-sm" aria-label="人工审核筛选">
        <label className="proc-filter-search flex-1 min-w-[240px] flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus-within:border-accent">
          <Search size={14} className="text-text-muted" />
          <input className="w-full bg-transparent border-none outline-none text-xs text-text placeholder:text-text-muted" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或证据指纹" aria-label="搜索人工审核" />
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">状态</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus | "ALL")}>
            <option value="ALL">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">风险</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" value={risk} onChange={(event) => setRisk(event.target.value)}>
            <option value="ALL">全部风险</option>
            {riskOptions.map((value) => <option value={value} key={value}>{riskText(value)}</option>)}
          </select>
        </label>
      </div>

      <div className="proc-center-layout grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <section className="proc-center-list review-list lg:col-span-4 flex flex-col gap-3" aria-label="人工审核列表">
          <header className="flex items-center justify-between text-xs text-text-muted pb-1">
            <strong className="font-semibold text-text">{filtered.length} 项审核</strong>
            <span>按优先级与等待时间排序</span>
          </header>
          {loading ? <div className="proc-center-empty py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={22} />正在读取审核队列</div> : null}
          {error ? <div className="proc-center-empty danger p-4 rounded-xl bg-danger-soft border border-danger/30 text-xs text-danger flex items-center gap-2" role="alert"><AlertTriangle size={22} />{error}</div> : null}
          {!loading && !error ? filtered.map((item) => {
            const owner = requestMap.get(item.business_id);
            return (
              <button type="button" key={item.review_id} className={`proc-invoice-card glass-panel text-left p-4 rounded-xl border transition-all duration-150 flex flex-col gap-2 ${selectedId === item.review_id ? "selected border-accent bg-accent-soft/30 shadow-xs ring-1 ring-accent/30" : "bg-surface/80 border-border/80 hover:border-border-strong hover:bg-surface"}`} onClick={() => onSelect(item.review_id, true)}>
                <div className="flex items-center justify-between gap-2">
                  <span className={`proc-queue-status not-italic text-[11px] font-medium px-2 py-0.5 rounded-full border ${item.status === "PENDING" ? "bg-warning-soft text-warning border-warning/30" : item.status === "APPROVED" ? "bg-accent-soft text-accent border-accent/30" : "bg-surface-subtle text-text-muted border-border"}`}>{STATUS_LABELS[item.status]}</span>
                  <span className={`proc-priority font-mono text-[11px] font-bold px-1.5 py-0.5 rounded ${item.priority >= 70 ? "high bg-danger-soft text-danger border border-danger/30" : "bg-surface-subtle text-text-muted"}`}>P{item.priority}</span>
                </div>
                <strong className="text-xs font-semibold text-text truncate">{owner?.title || item.business_id}</strong>
                <small className="text-[11px] text-text-muted font-mono truncate">{owner?.reference || item.evidence_sha256}</small>
                <span className="proc-risk-row flex flex-wrap gap-1 mt-0.5">{item.risk_flags.length ? item.risk_flags.map((value) => <em className="not-italic text-[10px] font-medium px-1.5 py-0.5 rounded bg-warning-soft text-warning border border-warning/20" key={value}>{riskText(value)}</em>) : <em className="not-italic text-[10px] text-text-muted">常规复核</em>}</span>
                <span className="proc-center-list-meta flex items-center justify-between pt-2 border-t border-border/40 text-[11px] text-text-muted"><span className="inline-flex items-center gap-1"><Clock3 size={12} />{waitText(item.waiting_since)}</span><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></span>
              </button>
            );
          }) : null}
          {!loading && !error && !filtered.length ? <div className="proc-center-empty py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted"><CheckCircle2 size={22} className="text-accent" />当前筛选没有审核事项</div> : null}
        </section>

        <section className="proc-center-detail lg:col-span-8 glass-panel rounded-xl p-6 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-5" aria-label="人工审核详情">
          {detailQuery.isPending && selectedId ? <div className="proc-center-empty py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={22} />正在读取审核详情</div> : null}
          {detailQuery.isError ? <div className="proc-center-empty danger p-4 rounded-xl bg-danger-soft border border-danger/30 text-xs text-danger flex items-center gap-2" role="alert"><AlertTriangle size={22} />{errorText(detailQuery.error)}</div> : null}
          {!selectedId && !detailQuery.isPending ? <div className="proc-center-empty py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted"><ClipboardCheck size={25} />选择一项审核查看证据和操作</div> : null}
          {detail ? (
            <div className="proc-review-detail flex flex-col gap-5">
              <header className="proc-detail-head flex items-start justify-between gap-3 pb-3 border-b border-border/60">
                <div>
                  <span className="font-mono text-xs text-accent font-semibold">{request?.reference || detail.business_id}</span>
                  <h2 className="text-base font-bold text-text mt-0.5">{request?.title || "采购人工审核"}</h2>
                  <p className="text-xs text-text-muted mt-0.5">{STATUS_LABELS[detail.status]} · 优先级 P{detail.priority} · 已等待 {waitText(detail.waiting_since)}</p>
                </div>
                <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => onOpenTask(detail.business_id)}>采购详情<ArrowRight size={15} /></button>
              </header>

              {detail.status === "STALE" ? <div className="proc-review-banner stale flex items-center gap-2.5 p-3 rounded-lg bg-warning-soft text-warning border border-warning/30 text-xs" role="alert"><AlertTriangle size={17} /><span><strong>审核证据已过期</strong><small className="block opacity-90">{detail.stale_reason || "采购输入或比价快照已经变化"}</small></span></div> : null}
              {submittingDecision ? <div className="proc-review-banner pending flex items-center gap-2.5 p-3 rounded-lg bg-accent-soft text-accent border border-accent/30 text-xs"><LoaderCircle className="spin" size={17} /><span><strong>审核动作已提交</strong><small className="block opacity-90">正式决定正在异步确认，页面会自动刷新。</small></span></div> : null}
              {detail.decision_id ? <div className="proc-review-banner success flex items-center gap-2.5 p-3 rounded-lg bg-accent-soft text-accent border border-accent/30 text-xs"><CheckCircle2 size={17} /><span><strong>正式决定已形成</strong><small className="block opacity-90">决定 ID {detail.decision_id}</small></span></div> : null}
              {detail.status === "REJECTED" ? <div className="proc-review-banner rejected flex items-center gap-2.5 p-3 rounded-lg bg-danger-soft text-danger border border-danger/30 text-xs"><RotateCcw size={17} /><span><strong>已驳回并返回分析</strong><small className="block opacity-90">{detail.reason}</small></span></div> : null}
              {notice ? <div className="proc-review-banner success flex items-center gap-2.5 p-3 rounded-lg bg-accent-soft text-accent border border-accent/30 text-xs" role="status"><CheckCircle2 size={17} /><span><strong>{notice}</strong></span></div> : null}

              <section className="proc-detail-section ai-advice glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                <header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><ShieldCheck size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">AI 建议</h3></div><span className="text-[11px] text-text-muted">不可人工覆盖</span></header>
                <div className="proc-advice-grid grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs bg-surface-subtle/60 p-3.5 rounded-lg border border-border/40">
                  <span className="flex flex-col"><small className="text-[11px] text-text-muted">建议供应商</small><strong className="font-semibold text-text mt-0.5">{suggestion?.supplier_name || "无合格建议"}</strong></span>
                  <span className="flex flex-col"><small className="text-[11px] text-text-muted">AI 摘要</small><strong className="font-semibold text-text mt-0.5">{String(detail.ai_result.structured_result.summary || "已生成结构化解释")}</strong></span>
                  <span className="flex flex-col"><small className="text-[11px] text-text-muted">模型 / Prompt</small><strong className="font-mono text-text mt-0.5">{detail.ai_result.model || "deterministic"} / {detail.ai_result.prompt_version}</strong></span>
                  <span className="flex flex-col"><small className="text-[11px] text-text-muted">风险</small><strong className="font-semibold text-text mt-0.5">{detail.risk_flags.length ? detail.risk_flags.map(riskText).join("、") : "未标记额外风险"}</strong></span>
                </div>
              </section>

              <section className="proc-detail-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                <header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><ClipboardCheck size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">报价与规则证据</h3></div><a className="inline-flex items-center gap-1 text-xs text-accent hover:underline font-mono" href={`/api/artifacts/${detail.comparison.artifact_id}`} target="_blank" rel="noreferrer"><Fingerprint size={14} />快照 v{detail.comparison.version}<ExternalLink size={13} /></a></header>
                <div className="proc-review-quotes-wrap overflow-x-auto"><table className="proc-review-quotes w-full text-left text-xs border-collapse rounded-lg overflow-hidden border border-border/60"><thead><tr className="bg-surface-subtle/80 border-b border-border text-text-muted"><th className="p-2.5">供应商</th><th className="p-2.5">资格</th><th className="p-2.5">总到货成本</th><th className="p-2.5">到货单价</th><th className="p-2.5">起订量（MOQ）</th><th className="p-2.5">交期</th><th className="p-2.5">依据</th></tr></thead><tbody className="divide-y divide-border/40">{quotes.map((quote) => (
                  <tr key={quote.quote_id} className={`hover:bg-surface-subtle/50 transition-colors ${quote.eligible ? "eligible" : "excluded opacity-70"} ${detail.suggested_quote_id === quote.quote_id ? "suggested bg-accent-soft/20 font-medium" : ""}`}>
                    <td className="p-2.5"><strong>{quote.supplier_name}</strong>{detail.suggested_quote_id === quote.quote_id ? <small className="block text-[10px] text-accent font-semibold">AI 建议</small> : null}</td>
                    <td className="p-2.5">{quote.eligible ? <span className="proc-eligibility pass inline-flex items-center gap-1 text-accent font-semibold"><CheckCircle2 size={13} />通过</span> : <span className="proc-eligibility fail inline-flex items-center gap-1 text-danger font-semibold"><XCircle size={13} />淘汰</span>}</td>
                    <td className="p-2.5 font-mono"><strong>{money(quote.cost.landed_total_base, quote.cost.base_currency)}</strong></td>
                    <td className="p-2.5 font-mono">{money(quote.cost.landed_unit_base, quote.cost.base_currency)}</td>
                    <td className="p-2.5 font-mono">{quote.commercial.moq.toLocaleString("zh-CN")}</td>
                    <td className="p-2.5 font-mono">{quote.commercial.lead_time_days} 天</td>
                    <td className="p-2.5"><span className="proc-quote-reason text-text-muted">{quote.eligible ? quote.warnings.join("；") || `成本排名 ${quote.rank || "-"}` : quote.exclusion_reasons.map((item) => item.message).join("；")}</span></td>
                  </tr>
                ))}</tbody></table></div>
                <div className="proc-evidence-strip flex flex-wrap items-center gap-4 pt-2 border-t border-border/40 text-[11px] text-text-muted"><span><small>审核证据：</small><code className="font-mono text-text ml-1" title={detail.evidence_sha256 || "-"}>{detail.evidence_sha256?.slice(0, 20) || "-"}</code></span><span><small>比价输入：</small><code className="font-mono text-text ml-1" title={detail.comparison.input_sha256}>{detail.comparison.input_sha256.slice(0, 20)}</code></span><span><small>AI 结果：</small><code className="font-mono text-text ml-1" title={detail.ai_result.result_sha256}>{detail.ai_result.result_sha256.slice(0, 20)}</code></span></div>
              </section>

              {pending ? <section className="proc-detail-section proc-review-action-panel glass-panel rounded-xl p-5 border border-border/80 bg-surface/90 flex flex-col gap-4 shadow-sm">
                <header className="flex items-center justify-between pb-2 border-b border-border/40"><div className="flex items-center gap-2"><ClipboardEdit size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">人工决定</h3></div><span className="text-[11px] text-text-muted">提交后不可修改</span></header>
                <div className="proc-review-actions flex flex-wrap gap-2" role="group" aria-label="审核动作">
                  <button type="button" className={`px-3.5 py-2 rounded-lg text-xs font-semibold border transition-all inline-flex items-center gap-1.5 ${action === "APPROVE_SUGGESTION" ? "active bg-accent text-white border-accent shadow-xs" : "bg-surface border-border hover:bg-surface-subtle text-text"}`} disabled={!suggestion} onClick={() => setAction("APPROVE_SUGGESTION")}><ShieldCheck size={15} />确认建议</button>
                  <button type="button" className={`px-3.5 py-2 rounded-lg text-xs font-semibold border transition-all inline-flex items-center gap-1.5 ${action === "REVISE_AND_APPROVE" ? "active bg-accent text-white border-accent shadow-xs" : "bg-surface border-border hover:bg-surface-subtle text-text"}`} disabled={!hasAlternativeQuote} title={!hasAlternativeQuote ? "没有其他合格供应商可供修改" : "修改最终供应商后通过"} onClick={() => setAction("REVISE_AND_APPROVE")}><ClipboardEdit size={15} />修改后通过</button>
                  <button type="button" className={`px-3.5 py-2 rounded-lg text-xs font-semibold border transition-all inline-flex items-center gap-1.5 ${action === "REJECT_AND_RETRY" ? "active bg-warning text-white border-warning shadow-xs" : "bg-surface border-border hover:bg-surface-subtle text-text"}`} onClick={() => setAction("REJECT_AND_RETRY")}><RotateCcw size={15} />驳回重跑</button>
                  <button type="button" className={`danger px-3.5 py-2 rounded-lg text-xs font-semibold border transition-all inline-flex items-center gap-1.5 ${action === "NO_AWARD" ? "active danger bg-danger text-white border-danger shadow-xs" : "bg-surface border-danger/30 text-danger hover:bg-danger-soft"}`} disabled={hasEligibleQuotes} title={hasEligibleQuotes ? "存在合格报价时不能流标" : "结束本轮询价"} onClick={() => setAction("NO_AWARD")}><Ban size={15} />流标</button>
                </div>
                <div className="proc-review-form grid grid-cols-1 gap-3 text-xs">
                  <label className="proc-field flex flex-col gap-1 font-medium text-text"><span>审核人</span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={actor} onChange={(event) => setActor(event.target.value)} maxLength={100} /></label>
                  {action === "REVISE_AND_APPROVE" ? <label className="proc-field flex flex-col gap-1 font-medium text-text"><span>最终供应商</span><select className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={finalQuoteId} onChange={(event) => setFinalQuoteId(event.target.value)}>{quotes.filter((quote) => quote.eligible).map((quote) => <option value={quote.quote_id} key={quote.quote_id}>{quote.supplier_name}{quote.quote_id === detail.suggested_quote_id ? "（AI 建议）" : ""} · {money(quote.cost.landed_total_base, quote.cost.base_currency)}</option>)}</select></label> : null}
                  <label className="proc-field wide flex flex-col gap-1 font-medium text-text"><span>{action === "APPROVE_SUGGESTION" ? "审核说明（选填）" : action === "NO_AWARD" ? "流标原因" : action === "REJECT_AND_RETRY" ? "驳回原因" : "修改理由"}</span><textarea className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={3} placeholder={action === "APPROVE_SUGGESTION" ? "可记录核对结论" : "请填写可审计的具体原因"} /></label>
                </div>
                {actionError ? <p className="proc-inline-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{actionError}</p> : null}
                <footer className="flex items-center justify-end pt-2 border-t border-border/40"><button className={`proc-button px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shadow-xs ${action === "NO_AWARD" ? "danger bg-danger text-white hover:bg-rose-700" : "primary bg-accent text-white hover:bg-accent-strong"}`} type="button" disabled={!formValid || busy} onClick={() => { setConfirmed(false); setConfirmOpen(true); }}><ClipboardCheck size={15} />提交审核</button></footer>
              </section> : null}

              {!pending ? <section className="proc-detail-section proc-review-outcome glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3"><header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><ClipboardCheck size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">人工审核记录</h3></div><span className="text-xs font-medium text-text-secondary">{detail.action ? ACTION_LABELS[detail.action] : STATUS_LABELS[detail.status]}</span></header><div className="proc-advice-grid grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs bg-surface-subtle/60 p-3.5 rounded-lg border border-border/40"><span className="flex flex-col"><small className="text-[11px] text-text-muted">人工动作</small><strong className="font-semibold text-text mt-0.5">{detail.action ? ACTION_LABELS[detail.action] : "未提交"}</strong></span><span className="flex flex-col"><small className="text-[11px] text-text-muted">最终供应商</small><strong className="font-semibold text-text mt-0.5">{finalQuote?.supplier_name || (detail.status === "NO_AWARD" ? "本轮流标" : "-")}</strong></span><span className="flex flex-col"><small className="text-[11px] text-text-muted">审核人 / 时间</small><strong className="text-text mt-0.5">{detail.actor || "-"} · {timeText(detail.acted_at)}</strong></span><span className="flex flex-col"><small className="text-[11px] text-text-muted">理由</small><strong className="text-text mt-0.5">{detail.reason || "未填写"}</strong></span></div></section> : null}

              <section className="proc-detail-section compact-history glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3"><header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><Clock3 size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">审核历史</h3></div><span className="text-xs text-text-muted">{detail.history.length} 条</span></header><ol className="flex flex-col gap-2 text-xs">{detail.history.map((item) => <li className="flex items-center justify-between p-2 rounded-lg bg-surface-subtle/50 border border-border/40" key={item.review_id}><div className="flex items-center gap-2"><span className={`proc-queue-status not-italic text-[10px] font-medium px-2 py-0.5 rounded-full border ${item.status === "PENDING" ? "bg-warning-soft text-warning border-warning/30" : "bg-accent-soft text-accent border-accent/30"}`}>{STATUS_LABELS[item.status]}</span><strong className="text-text">{item.action ? ACTION_LABELS[item.action] : "进入审核队列"}</strong><small className="text-text-muted">{item.actor || "系统"}</small></div><time className="font-mono text-[11px] text-text-muted" dateTime={item.updated_at}>{timeText(item.updated_at)}</time></li>)}</ol></section>
            </div>
          ) : null}
        </section>
      </div>

      {confirmOpen && detail ? <div className="proc-modal-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation"><section ref={confirmDialogRef} className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="review-confirm-title"><header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base">{action === "NO_AWARD" ? <Ban size={18} className="text-danger" /> : <ShieldCheck size={18} className="text-accent" />}<h2 id="review-confirm-title">确认提交：{ACTION_LABELS[action]}</h2></div><button className="proc-icon-button w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" disabled={busy} onClick={() => setConfirmOpen(false)}><X size={18} /></button></header><p className="proc-confirm-warning text-xs text-text-muted leading-relaxed">本次操作绑定当前采购版本、AI 结果和比价快照。提交后会写入不可变审核历史；证据变化时服务端会拒绝旧页面提交。</p><div className="proc-confirm-supplier p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs"><span className="text-text-muted">{confirmQuote?.supplier_name || (action === "NO_AWARD" ? "本轮不选定供应商" : "返回补充资料与重跑")}</span><strong className="text-sm font-bold text-text">{ACTION_LABELS[action]}</strong><small className="text-text-secondary">{reason.trim() || "未填写额外说明"}</small></div><label className="proc-check approval-confirm flex items-start gap-2.5 text-xs text-text cursor-pointer p-3 rounded-lg bg-surface-subtle/60 border border-border/60"><input type="checkbox" className="rounded border-border text-accent focus:ring-accent mt-0.5" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span><strong className="block font-semibold">我已核对 AI 建议、报价原件与确定性比价证据</strong><small className="block text-text-muted mt-0.5">确认使用当前证据指纹提交人工决定</small></span></label>{actionError ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30 flex items-center gap-1.5" role="alert"><AlertTriangle size={14} />{actionError}</p> : null}<footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60"><button type="button" ref={confirmCancelRef} className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" disabled={busy} onClick={() => setConfirmOpen(false)}>取消</button><button type="button" className={`proc-button px-4 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shadow-xs ${action === "NO_AWARD" ? "danger bg-danger text-white hover:bg-rose-700" : "primary bg-accent text-white hover:bg-accent-strong"}`} disabled={!confirmed || busy} onClick={() => void submit()}>{busy ? <LoaderCircle className="spin" size={16} /> : <ClipboardCheck size={16} />}确认提交</button></footer></section></div> : null}
    </div>
  );
}
