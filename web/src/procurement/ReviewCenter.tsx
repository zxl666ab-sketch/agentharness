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
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { procurementApi } from "./api";
import {
  Button,
  CenterPage,
  CountBadge,
  EmptyState,
  ErrorState,
  Fact,
  ListRow,
  MasterDetail,
  Modal,
  PageHeader,
  StatusPill,
} from "../components/ui";
import { actionablePendingReviewCount, reviewIsGhost } from "./viewModel";
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

const STATUS_TONES: Record<ReviewStatus, string> = {
  PENDING: "warning",
  APPROVED: "success",
  REJECTED: "danger",
  NO_AWARD: "neutral",
  STALE: "warning",
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
  // P-UX⑦：深链不再把筛选重置为"全部状态"——选中项始终保留在列表里（见 filtered），
  // 默认视图永远是"待审核"行动队列，历史记录需要用户主动切换查看。
  const [status, setStatus] = useState<ReviewStatus | "ALL">("PENDING");
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
  const confirmCancelRef = useRef<HTMLButtonElement | null>(null);
  const requestMap = useMemo(() => new Map(requests.map((item) => [item.id, item])), [requests]);
  const riskOptions = useMemo(() => [...new Set(reviews.flatMap((item) => item.risk_flags))], [reviews]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return [...reviews]
      .filter((item) => item.review_id === selectedId
        || ((status === "ALL" || item.status === status)
          && (risk === "ALL" || item.risk_flags.includes(risk))
          && (!query || [requestMap.get(item.business_id)?.reference, requestMap.get(item.business_id)?.title, item.evidence_sha256]
            .filter(Boolean).join(" ").toLowerCase().includes(query))))
      .sort((left, right) => right.priority - left.priority
        || new Date(left.waiting_since).getTime() - new Date(right.waiting_since).getTime());
  }, [requestMap, reviews, risk, search, status, selectedId]);

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
  // P-UX⑦：任务已正式定标/关闭但审核仍 PENDING 的"幽灵记录"——禁止提交，只解释。
  const ghostReview = pending && reviewIsGhost(detail!, requests);
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

  // W-M5：待审核详情每 1.5s 轮询刷新，`detail` 的对象身份每轮都变。
  // 表单只在切换到另一条审核（review_id 变化）时重置，避免覆盖用户正在
  // 填写的动作/供应商/理由/确认勾选；轮询结果通过渲染路径正常反映状态。
  const detailId = detail?.review_id;
  const detailRef = useRef(detail);
  detailRef.current = detail;
  useEffect(() => {
    const current = detailRef.current;
    if (!current) return;
    const nextAction: ReviewAction = current.suggested_quote_id ? "APPROVE_SUGGESTION" : "NO_AWARD";
    setAction(nextAction);
    setFinalQuoteId(
      current.comparison.result.quotes.find((item) => item.eligible && item.quote_id !== current.suggested_quote_id)?.quote_id
      || current.suggested_quote_id
      || current.comparison.result.quotes.find((item) => item.eligible)?.quote_id
      || "",
    );
    setReason("");
    setConfirmed(false);
    setConfirmOpen(false);
    setActionError(null);
    setNotice(null);
  }, [detailId]);

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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-review", detail.review_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-reviews"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-request", detail.business_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      ]);
    } finally {
      setBusy(false);
    }
  }

  const confirmQuote = action === "APPROVE_SUGGESTION" ? suggestion : action === "REVISE_AND_APPROVE" ? selectedQuote : null;

  return (
    <CenterPage
      header={
        <PageHeader
          icon={<ShieldCheck size={18} />}
          eyebrow="决策队列"
          title="人工审核"
          subtitle="比价结果必须由人工确认后才形成正式采购决定"
          aside={<CountBadge tone={actionablePendingReviewCount(reviews, requests) > 0 ? "warning" : "neutral"}>
            {actionablePendingReviewCount(reviews, requests)} 项等待审核
          </CountBadge>}
        />
      }
      toolbar={
        <div className="proc-action-bar is-filters" aria-label="人工审核筛选">
          <label className="proc-search">
            <Search size={15} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或证据指纹" aria-label="搜索人工审核" />
          </label>
          <label className="proc-filter">
            <span>状态</span>
            <select className="proc-select" value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus | "ALL")}>
              <option value="ALL">全部状态</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label className="proc-filter">
            <span>风险</span>
            <select className="proc-select" value={risk} onChange={(event) => setRisk(event.target.value)}>
              <option value="ALL">全部风险</option>
              {riskOptions.map((value) => <option value={value} key={value}>{riskText(value)}</option>)}
            </select>
          </label>
        </div>
      }
    >
      <MasterDetail
        list={
          <>
            <header className="proc-master-list-head">
              <strong>{filtered.length} 项审核</strong>
              <small>按优先级与等待时间排序</small>
            </header>
            {loading ? <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取审核队列</div> : null}
            {error ? <ErrorState title="审核队列加载失败" detail={error} /> : null}
            {!loading && !error ? filtered.map((item) => {
              const owner = requestMap.get(item.business_id);
              const ghost = item.status === "PENDING" && reviewIsGhost(item, requests);
              return (
                <ListRow key={item.review_id} selected={selectedId === item.review_id} onClick={() => onSelect(item.review_id, true)}>
                  <span className="proc-list-row-head">
                    <StatusPill tone={STATUS_TONES[item.status]} size="compact">{STATUS_LABELS[item.status]}</StatusPill>
                    <span className={`proc-priority${item.priority >= 70 ? " is-high" : ""}`} title={`优先级 P${item.priority}`}>P{item.priority}</span>
                  </span>
                  <strong className="proc-list-row-title">{owner?.title || item.business_id}</strong>
                  <span className="proc-list-row-meta">
                    <small>{owner?.reference || item.evidence_sha256}</small>
                    <span className="mono"><Clock3 size={11} /> {waitText(item.waiting_since)}</span>
                  </span>
                  {ghost ? (
                    <em className="proc-risk-chip is-muted">任务已定标 · 无需提交</em>
                  ) : item.risk_flags.length ? (
                    <span className="proc-risk-chip-row">
                      {item.risk_flags.map((value) => <em className="proc-risk-chip" key={value}>{riskText(value)}</em>)}
                    </span>
                  ) : null}
                </ListRow>
              );
            }) : null}
            {!loading && !error && !filtered.length ? (
              <EmptyState variant="inline" icon={<CheckCircle2 size={22} />} title="当前筛选没有审核事项" hint="切换状态或风险筛选可查看历史记录。" />
            ) : null}
          </>
        }
        detail={
          <>
            {detailQuery.isPending && selectedId ? <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取审核详情</div> : null}
            {detailQuery.isError ? <ErrorState title="审核详情加载失败" detail={errorText(detailQuery.error)} onRetry={() => void detailQuery.refetch()} /> : null}
            {!selectedId ? (
              <EmptyState variant="inline" icon={<ClipboardCheck size={24} />} title="选择一项审核" hint="查看 AI 建议、报价证据并提交人工决定。" />
            ) : null}
            {detail ? (
              <>
                <header className="proc-detail-head">
                  <div>
                    <small className="mono proc-detail-ref">{request?.reference || detail.business_id}</small>
                    <h2>{request?.title || "采购人工审核"}</h2>
                    <p>{STATUS_LABELS[detail.status]} · 优先级 P{detail.priority} · 已等待 {waitText(detail.waiting_since)}</p>
                  </div>
                  <Button variant="secondary" size="sm" icon={<ArrowRight size={14} />} onClick={() => onOpenTask(detail.business_id)}>采购详情</Button>
                </header>

                {detail.status === "STALE" ? (
                  <div className="proc-review-banner is-warning" role="alert"><AlertTriangle size={16} /><span><strong>审核证据已过期</strong><small>{detail.stale_reason || "采购输入或比价快照已经变化"}</small></span></div>
                ) : null}
                {ghostReview ? (
                  <div className="proc-review-banner is-warning" role="alert">
                    <AlertTriangle size={16} />
                    <span>
                      <strong>任务已定标，本条审核无需提交</strong>
                      <small>该采购任务已通过正式决定完成定标，这条"待审核"是状态遗留的历史记录；提交会被证据校验拒绝。结论请在采购详情与审批报告中查看。</small>
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => onOpenTask(detail.business_id)}>查看采购详情</Button>
                  </div>
                ) : null}
                {submittingDecision ? (
                  <div className="proc-review-banner is-accent"><LoaderCircle className="spin" size={16} /><span><strong>审核动作已提交</strong><small>正式决定正在异步确认，页面会自动刷新。</small></span></div>
                ) : null}
                {detail.decision_id ? (
                  <div className="proc-review-banner is-accent"><CheckCircle2 size={16} /><span><strong>正式决定已形成</strong><small>决定 ID {detail.decision_id}</small></span></div>
                ) : null}
                {detail.status === "REJECTED" ? (
                  <div className="proc-review-banner is-danger"><RotateCcw size={16} /><span><strong>已驳回并返回分析</strong><small>{detail.reason}</small></span></div>
                ) : null}
                {notice ? (
                  <div className="proc-review-banner is-accent" role="status"><CheckCircle2 size={16} /><span><strong>{notice}</strong></span></div>
                ) : null}

                <section className="proc-detail-section">
                  <header>
                    <h3><ShieldCheck size={15} /> AI 建议</h3>
                    <small>结论由确定性规则约束，不可人工直接覆盖</small>
                  </header>
                  <div className="proc-fact-grid">
                    <Fact label="建议供应商">{suggestion?.supplier_name || "无合格建议"}</Fact>
                    <Fact label="AI 摘要">{String(detail.ai_result.structured_result.summary || "已生成结构化解释")}</Fact>
                    <Fact label="模型 / Prompt" mono>{detail.ai_result.model || "deterministic"} / {detail.ai_result.prompt_version}</Fact>
                    <Fact label="风险">{detail.risk_flags.length ? detail.risk_flags.map(riskText).join("、") : "未标记额外风险"}</Fact>
                  </div>
                </section>

                <section className="proc-detail-section">
                  <header>
                    <h3><ClipboardCheck size={15} /> 报价与规则证据</h3>
                    <a className="proc-artifact-link mono" href={`/api/artifacts/${detail.comparison.artifact_id}`} target="_blank" rel="noreferrer">
                      <Fingerprint size={13} />快照 v{detail.comparison.version}<ExternalLink size={12} />
                    </a>
                  </header>
                  <div className="proc-table-scroll">
                    <table className="proc-data-table">
                      <thead>
                        <tr><th>供应商</th><th>资格</th><th>总到货成本</th><th>到货单价</th><th>起订量（MOQ）</th><th>交期</th><th>依据</th></tr>
                      </thead>
                      <tbody>
                        {quotes.map((quote) => (
                          <tr key={quote.quote_id} className={`${quote.eligible ? "eligible" : "excluded"}${detail.suggested_quote_id === quote.quote_id ? " is-suggested" : ""}`}>
                            <td>
                              <strong>{quote.supplier_name}</strong>
                              {detail.suggested_quote_id === quote.quote_id ? <small className="proc-suggested-mark">AI 建议</small> : null}
                            </td>
                            <td>
                              {quote.eligible
                                ? <span className="proc-eligibility pass"><CheckCircle2 size={13} />通过</span>
                                : <span className="proc-eligibility fail"><XCircle size={13} />淘汰</span>}
                            </td>
                            <td className="mono"><strong>{money(quote.cost.landed_total_base, quote.cost.base_currency)}</strong></td>
                            <td className="mono">{money(quote.cost.landed_unit_base, quote.cost.base_currency)}</td>
                            <td className="mono">{quote.commercial.moq.toLocaleString("zh-CN")}</td>
                            <td className="mono">{quote.commercial.lead_time_days} 天</td>
                            <td><span className="proc-quote-reason">{quote.eligible ? quote.warnings.join("；") || `成本排名 ${quote.rank || "-"}` : quote.exclusion_reasons.map((item) => item.message).join("；")}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="proc-evidence-strip">
                    <span><small>审核证据</small><code className="mono" title={detail.evidence_sha256 || "-"}>{detail.evidence_sha256?.slice(0, 20) || "-"}</code></span>
                    <span><small>比价输入</small><code className="mono" title={detail.comparison.input_sha256}>{detail.comparison.input_sha256.slice(0, 20)}</code></span>
                    <span><small>AI 结果</small><code className="mono" title={detail.ai_result.result_sha256}>{detail.ai_result.result_sha256.slice(0, 20)}</code></span>
                  </div>
                </section>

                {pending ? (
                  ghostReview ? null : (
                    <section className="proc-detail-section proc-review-action-panel">
                      <header>
                        <h3><ClipboardEdit size={15} /> 人工决定</h3>
                        <small>提交后不可修改</small>
                      </header>
                      <div className="proc-review-actions" role="group" aria-label="审核动作">
                        <button type="button" className={`proc-action-toggle is-accent${action === "APPROVE_SUGGESTION" ? " active" : ""}`} disabled={!suggestion} onClick={() => setAction("APPROVE_SUGGESTION")}>
                          <ShieldCheck size={14} />确认建议
                        </button>
                        <button type="button" className={`proc-action-toggle is-accent${action === "REVISE_AND_APPROVE" ? " active" : ""}`} disabled={!hasAlternativeQuote} title={!hasAlternativeQuote ? "没有其他合格供应商可供修改" : "修改最终供应商后通过"} onClick={() => setAction("REVISE_AND_APPROVE")}>
                          <ClipboardEdit size={14} />修改后通过
                        </button>
                        <button type="button" className={`proc-action-toggle is-warning${action === "REJECT_AND_RETRY" ? " active" : ""}`} onClick={() => setAction("REJECT_AND_RETRY")}>
                          <RotateCcw size={14} />驳回重跑
                        </button>
                        <button type="button" className={`proc-action-toggle is-danger${action === "NO_AWARD" ? " active" : ""}`} disabled={hasEligibleQuotes} title={hasEligibleQuotes ? "存在合格报价时不能流标" : "结束本轮询价"} onClick={() => setAction("NO_AWARD")}>
                          <Ban size={14} />流标
                        </button>
                      </div>
                      <div className="proc-review-form">
                        <label className="proc-field">
                          <span>审核人</span>
                          <input className="proc-input" value={actor} onChange={(event) => setActor(event.target.value)} maxLength={100} />
                        </label>
                        {action === "REVISE_AND_APPROVE" ? (
                          <label className="proc-field">
                            <span>最终供应商</span>
                            <select className="proc-input" value={finalQuoteId} onChange={(event) => setFinalQuoteId(event.target.value)}>
                              {quotes.filter((quote) => quote.eligible).map((quote) => (
                                <option value={quote.quote_id} key={quote.quote_id}>
                                  {quote.supplier_name}{quote.quote_id === detail.suggested_quote_id ? "（AI 建议）" : ""} · {money(quote.cost.landed_total_base, quote.cost.base_currency)}
                                </option>
                              ))}
                            </select>
                          </label>
                        ) : null}
                        <label className="proc-field proc-field-wide">
                          <span>{action === "APPROVE_SUGGESTION" ? "审核说明（选填）" : action === "NO_AWARD" ? "流标原因" : action === "REJECT_AND_RETRY" ? "驳回原因" : "修改理由"}</span>
                          <textarea className="proc-input" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={3} placeholder={action === "APPROVE_SUGGESTION" ? "可记录核对结论" : "请填写可审计的具体原因"} />
                        </label>
                      </div>
                      {actionError ? <p className="proc-inline-error" role="alert">{actionError}</p> : null}
                      <footer>
                        <Button
                          variant={action === "NO_AWARD" ? "danger" : "primary"}
                          icon={<ClipboardCheck size={15} />}
                          disabled={!formValid}
                          loading={busy}
                          onClick={() => { setConfirmed(false); setConfirmOpen(true); }}
                        >
                          提交审核
                        </Button>
                      </footer>
                    </section>
                  )
                ) : (
                  <section className="proc-detail-section proc-review-outcome">
                    <header>
                      <h3><ClipboardCheck size={15} /> 人工审核记录</h3>
                      <small>{detail.action ? ACTION_LABELS[detail.action] : STATUS_LABELS[detail.status]}</small>
                    </header>
                    <div className="proc-fact-grid">
                      <Fact label="人工动作">{detail.action ? ACTION_LABELS[detail.action] : "未提交"}</Fact>
                      <Fact label="最终供应商">{finalQuote?.supplier_name || (detail.status === "NO_AWARD" ? "本轮流标" : "-")}</Fact>
                      <Fact label="审核人 / 时间">{detail.actor || "-"} · {timeText(detail.acted_at)}</Fact>
                      <Fact label="理由">{detail.reason || "未填写"}</Fact>
                    </div>
                  </section>
                )}

                <section className="proc-detail-section">
                  <header>
                    <h3><Clock3 size={15} /> 审核历史</h3>
                    <small>{detail.history.length} 条</small>
                  </header>
                  <ol className="proc-history-rows">
                    {detail.history.map((item) => (
                      <li key={item.review_id}>
                        <StatusPill tone={item.status === "PENDING" ? "warning" : "success"} size="compact">{STATUS_LABELS[item.status]}</StatusPill>
                        <strong>{item.action ? ACTION_LABELS[item.action] : "进入审核队列"}</strong>
                        <small>{item.actor || "系统"}</small>
                        <time className="mono" dateTime={item.updated_at}>{timeText(item.updated_at)}</time>
                      </li>
                    ))}
                  </ol>
                </section>
              </>
            ) : null}
          </>
        }
      />

      {confirmOpen && detail ? (
        <Modal
          titleId="review-confirm-title"
          title={`确认提交：${ACTION_LABELS[action]}`}
          icon={action === "NO_AWARD" ? <Ban size={18} /> : <ShieldCheck size={18} />}
          tone={action === "NO_AWARD" ? "danger" : "accent"}
          busy={busy}
          onClose={() => setConfirmOpen(false)}
          initialFocusRef={confirmCancelRef}
          footer={
            <>
              <Button variant="secondary" ref={confirmCancelRef} disabled={busy} onClick={() => setConfirmOpen(false)}>取消</Button>
              <Button variant={action === "NO_AWARD" ? "danger" : "primary"} icon={<ClipboardCheck size={15} />} disabled={!confirmed} loading={busy} onClick={() => void submit()}>确认提交</Button>
            </>
          }
        >
          <p className="proc-confirm-warning">本次操作绑定当前采购版本、AI 结果和比价快照。提交后会写入不可变审核历史；证据变化时服务端会拒绝旧页面提交。</p>
          <div className="proc-dialog-target">
            <strong>{ACTION_LABELS[action]}</strong>
            <span>{confirmQuote?.supplier_name || (action === "NO_AWARD" ? "本轮不选定供应商" : "返回补充资料与重跑")}</span>
            <small>{reason.trim() || "未填写额外说明"}</small>
          </div>
          <label className="proc-confirm-check">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            <span>
              <strong>我已核对 AI 建议、报价原件与确定性比价证据</strong>
              <small>确认使用当前证据指纹提交人工决定</small>
            </span>
          </label>
          {actionError ? <p className="proc-dialog-error" role="alert">{actionError}</p> : null}
        </Modal>
      ) : null}
    </CenterPage>
  );
}
