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
import { useEffect, useMemo, useState } from "react";

import { procurementApi } from "./api";
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
      setActionError(errorText(cause));
      setConfirmOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["procurement-review", detail.review_id] });
    } finally {
      setBusy(false);
    }
  }

  const confirmQuote = action === "APPROVE_SUGGESTION" ? suggestion : action === "REVISE_AND_APPROVE" ? selectedQuote : null;

  return (
    <div className="proc-center-page">
      <header className="proc-page-head">
        <div><span>决策队列</span><h1>人工审核</h1></div>
        <div className="proc-page-summary"><strong>{reviews.filter((item) => item.status === "PENDING").length}</strong><span>等待审核</span></div>
      </header>

      <div className="proc-center-filters reviews" aria-label="人工审核筛选">
        <label className="proc-filter-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或证据指纹" aria-label="搜索人工审核" /></label>
        <label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus | "ALL")}><option value="ALL">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label><span>风险</span><select value={risk} onChange={(event) => setRisk(event.target.value)}><option value="ALL">全部风险</option>{riskOptions.map((value) => <option value={value} key={value}>{riskText(value)}</option>)}</select></label>
      </div>

      <div className="proc-center-layout">
        <section className="proc-center-list review-list" aria-label="人工审核列表">
          <header><strong>{filtered.length} 项审核</strong><span>按优先级与等待时间排序</span></header>
          {loading ? <div className="proc-center-empty"><LoaderCircle className="spin" size={22} />正在读取审核队列</div> : null}
          {error ? <div className="proc-center-empty danger" role="alert"><AlertTriangle size={22} />{error}</div> : null}
          {!loading && !error ? filtered.map((item) => {
            const owner = requestMap.get(item.business_id);
            return (
              <button type="button" key={item.review_id} className={selectedId === item.review_id ? "selected" : ""} onClick={() => onSelect(item.review_id, true)}>
                <span className={`proc-queue-status ${item.status.toLowerCase()}`}>{STATUS_LABELS[item.status]}</span>
                <span className={`proc-priority ${item.priority >= 70 ? "high" : ""}`}>P{item.priority}</span>
                <strong>{owner?.title || item.business_id}</strong>
                <small>{owner?.reference || item.evidence_sha256}</small>
                <span className="proc-risk-row">{item.risk_flags.length ? item.risk_flags.map((value) => <em key={value}>{riskText(value)}</em>) : <em>常规复核</em>}</span>
                <span className="proc-center-list-meta"><span><Clock3 size={12} />{waitText(item.waiting_since)}</span><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></span>
              </button>
            );
          }) : null}
          {!loading && !error && !filtered.length ? <div className="proc-center-empty"><CheckCircle2 size={22} />当前筛选没有审核事项</div> : null}
        </section>

        <section className="proc-center-detail" aria-label="人工审核详情">
          {detailQuery.isPending && selectedId ? <div className="proc-center-empty"><LoaderCircle className="spin" size={22} />正在读取审核详情</div> : null}
          {detailQuery.isError ? <div className="proc-center-empty danger" role="alert"><AlertTriangle size={22} />{errorText(detailQuery.error)}</div> : null}
          {!selectedId && !detailQuery.isPending ? <div className="proc-center-empty"><ClipboardCheck size={25} />选择一项审核查看证据和操作</div> : null}
          {detail ? (
            <div className="proc-review-detail">
              <header className="proc-detail-head">
                <div><span>{request?.reference || detail.business_id}</span><h2>{request?.title || "采购人工审核"}</h2><p>{STATUS_LABELS[detail.status]} · 优先级 P{detail.priority} · 已等待 {waitText(detail.waiting_since)}</p></div>
                <button className="proc-button secondary" type="button" onClick={() => onOpenTask(detail.business_id)}>采购详情<ArrowRight size={15} /></button>
              </header>

              {detail.status === "STALE" ? <div className="proc-review-banner stale" role="alert"><AlertTriangle size={17} /><span><strong>审核证据已过期</strong><small>{detail.stale_reason || "采购输入或比价快照已经变化"}</small></span></div> : null}
              {submittingDecision ? <div className="proc-review-banner pending"><LoaderCircle className="spin" size={17} /><span><strong>审核动作已提交</strong><small>正式决定正在异步确认，页面会自动刷新。</small></span></div> : null}
              {detail.decision_id ? <div className="proc-review-banner success"><CheckCircle2 size={17} /><span><strong>正式决定已形成</strong><small>决定 ID {detail.decision_id}</small></span></div> : null}
              {detail.status === "REJECTED" ? <div className="proc-review-banner rejected"><RotateCcw size={17} /><span><strong>已驳回并返回分析</strong><small>{detail.reason}</small></span></div> : null}
              {notice ? <div className="proc-review-banner success" role="status"><CheckCircle2 size={17} /><span><strong>{notice}</strong></span></div> : null}

              <section className="proc-detail-section ai-advice">
                <header><div><ShieldCheck size={16} /><h3>AI 建议</h3></div><span>不可人工覆盖</span></header>
                <div className="proc-advice-grid">
                  <span><small>建议供应商</small><strong>{suggestion?.supplier_name || "无合格建议"}</strong></span>
                  <span><small>AI 摘要</small><strong>{String(detail.ai_result.structured_result.summary || "已生成结构化解释")}</strong></span>
                  <span><small>模型 / Prompt</small><strong>{detail.ai_result.model || "deterministic"} / {detail.ai_result.prompt_version}</strong></span>
                  <span><small>风险</small><strong>{detail.risk_flags.length ? detail.risk_flags.map(riskText).join("、") : "未标记额外风险"}</strong></span>
                </div>
              </section>

              <section className="proc-detail-section">
                <header><div><ClipboardCheck size={16} /><h3>报价与规则证据</h3></div><a href={`/api/artifacts/${detail.comparison.artifact_id}`} target="_blank" rel="noreferrer"><Fingerprint size={14} />快照 v{detail.comparison.version}<ExternalLink size={13} /></a></header>
                <div className="proc-review-quotes-wrap"><table className="proc-review-quotes"><thead><tr><th>供应商</th><th>资格</th><th>总到货成本</th><th>到货单价</th><th>起订量（MOQ）</th><th>交期</th><th>依据</th></tr></thead><tbody>{quotes.map((quote) => (
                  <tr key={quote.quote_id} className={`${quote.eligible ? "eligible" : "excluded"} ${detail.suggested_quote_id === quote.quote_id ? "suggested" : ""}`}>
                    <td><strong>{quote.supplier_name}</strong>{detail.suggested_quote_id === quote.quote_id ? <small>AI 建议</small> : null}</td>
                    <td>{quote.eligible ? <span className="proc-eligibility pass"><CheckCircle2 size={13} />通过</span> : <span className="proc-eligibility fail"><XCircle size={13} />淘汰</span>}</td>
                    <td><strong>{money(quote.cost.landed_total_base, quote.cost.base_currency)}</strong></td>
                    <td>{money(quote.cost.landed_unit_base, quote.cost.base_currency)}</td>
                    <td>{quote.commercial.moq.toLocaleString("zh-CN")}</td>
                    <td>{quote.commercial.lead_time_days} 天</td>
                    <td><span className="proc-quote-reason">{quote.eligible ? quote.warnings.join("；") || `成本排名 ${quote.rank || "-"}` : quote.exclusion_reasons.map((item) => item.message).join("；")}</span></td>
                  </tr>
                ))}</tbody></table></div>
                <div className="proc-evidence-strip"><span><small>审核证据</small><code title={detail.evidence_sha256 || "-"}>{detail.evidence_sha256?.slice(0, 20) || "-"}</code></span><span><small>比价输入</small><code title={detail.comparison.input_sha256}>{detail.comparison.input_sha256.slice(0, 20)}</code></span><span><small>AI 结果</small><code title={detail.ai_result.result_sha256}>{detail.ai_result.result_sha256.slice(0, 20)}</code></span></div>
              </section>

              {pending ? <section className="proc-detail-section proc-review-action-panel">
                <header><div><ClipboardEdit size={16} /><h3>人工决定</h3></div><span>提交后不可修改</span></header>
                <div className="proc-review-actions" role="group" aria-label="审核动作">
                  <button type="button" className={action === "APPROVE_SUGGESTION" ? "active" : ""} disabled={!suggestion} onClick={() => setAction("APPROVE_SUGGESTION")}><ShieldCheck size={15} />确认建议</button>
                  <button type="button" className={action === "REVISE_AND_APPROVE" ? "active" : ""} disabled={!hasAlternativeQuote} title={!hasAlternativeQuote ? "没有其他合格供应商可供修改" : "修改最终供应商后通过"} onClick={() => setAction("REVISE_AND_APPROVE")}><ClipboardEdit size={15} />修改后通过</button>
                  <button type="button" className={action === "REJECT_AND_RETRY" ? "active" : ""} onClick={() => setAction("REJECT_AND_RETRY")}><RotateCcw size={15} />驳回重跑</button>
                  <button type="button" className={action === "NO_AWARD" ? "active danger" : "danger"} disabled={hasEligibleQuotes} title={hasEligibleQuotes ? "存在合格报价时不能流标" : "结束本轮询价"} onClick={() => setAction("NO_AWARD")}><Ban size={15} />流标</button>
                </div>
                <div className="proc-review-form">
                  <label className="proc-field"><span>审核人</span><input value={actor} onChange={(event) => setActor(event.target.value)} maxLength={100} /></label>
                  {action === "REVISE_AND_APPROVE" ? <label className="proc-field"><span>最终供应商</span><select value={finalQuoteId} onChange={(event) => setFinalQuoteId(event.target.value)}>{quotes.filter((quote) => quote.eligible).map((quote) => <option value={quote.quote_id} key={quote.quote_id}>{quote.supplier_name}{quote.quote_id === detail.suggested_quote_id ? "（AI 建议）" : ""} · {money(quote.cost.landed_total_base, quote.cost.base_currency)}</option>)}</select></label> : null}
                  <label className="proc-field wide"><span>{action === "APPROVE_SUGGESTION" ? "审核说明（选填）" : action === "NO_AWARD" ? "流标原因" : action === "REJECT_AND_RETRY" ? "驳回原因" : "修改理由"}</span><textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} placeholder={action === "APPROVE_SUGGESTION" ? "可记录核对结论" : "请填写可审计的具体原因"} /></label>
                </div>
                {actionError ? <p className="proc-inline-error" role="alert">{actionError}</p> : null}
                <footer><button className={action === "NO_AWARD" ? "proc-button danger" : "proc-button primary"} type="button" disabled={!formValid || busy} onClick={() => { setConfirmed(false); setConfirmOpen(true); }}><ClipboardCheck size={15} />提交审核</button></footer>
              </section> : null}

              {!pending ? <section className="proc-detail-section proc-review-outcome"><header><div><ClipboardCheck size={16} /><h3>人工审核记录</h3></div><span>{detail.action ? ACTION_LABELS[detail.action] : STATUS_LABELS[detail.status]}</span></header><div className="proc-advice-grid"><span><small>人工动作</small><strong>{detail.action ? ACTION_LABELS[detail.action] : "未提交"}</strong></span><span><small>最终供应商</small><strong>{finalQuote?.supplier_name || (detail.status === "NO_AWARD" ? "本轮流标" : "-")}</strong></span><span><small>审核人 / 时间</small><strong>{detail.actor || "-"} · {timeText(detail.acted_at)}</strong></span><span><small>理由</small><strong>{detail.reason || "未填写"}</strong></span></div></section> : null}

              <section className="proc-detail-section compact-history"><header><div><Clock3 size={16} /><h3>审核历史</h3></div><span>{detail.history.length} 条</span></header><ol>{detail.history.map((item) => <li key={item.review_id}><span className={`proc-queue-status ${item.status.toLowerCase()}`}>{STATUS_LABELS[item.status]}</span><strong>{item.action ? ACTION_LABELS[item.action] : "进入审核队列"}</strong><small>{item.actor || "系统"}</small><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></li>)}</ol></section>
            </div>
          ) : null}
        </section>
      </div>

      {confirmOpen && detail ? <div className="proc-modal-backdrop" role="presentation"><section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="review-confirm-title"><header><div>{action === "NO_AWARD" ? <Ban size={18} /> : <ShieldCheck size={18} />}<h2 id="review-confirm-title">确认提交：{ACTION_LABELS[action]}</h2></div><button className="proc-icon-button" type="button" title="关闭" aria-label="关闭" disabled={busy} onClick={() => setConfirmOpen(false)}><X size={18} /></button></header><p className="proc-confirm-warning">本次操作绑定当前采购版本、AI 结果和比价快照。提交后会写入不可变审核历史；证据变化时服务端会拒绝旧页面提交。</p><div className="proc-confirm-supplier"><span>{confirmQuote?.supplier_name || (action === "NO_AWARD" ? "本轮不选定供应商" : "返回补充资料与重跑")}</span><strong>{ACTION_LABELS[action]}</strong><small>{reason.trim() || "未填写额外说明"}</small></div><label className="proc-check approval-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span><strong>我已核对 AI 建议、报价原件与确定性比价证据</strong><small>确认使用当前证据指纹提交人工决定</small></span></label>{actionError ? <p className="proc-form-error" role="alert"><AlertTriangle size={14} />{actionError}</p> : null}<footer><button type="button" className="proc-button secondary" disabled={busy} onClick={() => setConfirmOpen(false)}>取消</button><button type="button" className={action === "NO_AWARD" ? "proc-button danger" : "proc-button primary"} disabled={!confirmed || busy} onClick={() => void submit()}>{busy ? <LoaderCircle className="spin" size={16} /> : <ClipboardCheck size={16} />}确认提交</button></footer></section></div> : null}
    </div>
  );
}
