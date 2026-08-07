import {
  AlertTriangle,
  BadgeCheck,
  Calculator,
  CheckCircle2,
  ExternalLink,
  Fingerprint,
  LoaderCircle,
  Play,
  ShieldAlert,
  ShieldCheck,
  X,
  XCircle,
  FileDown,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { KnowledgeReferences } from "./KnowledgeReferences";
import type {
  ComparisonQuote,
  KnowledgeFeedbackAction,
  ProcurementRequest,
} from "./types";

type Props = {
  request: ProcurementRequest;
  busy: string | null;
  error?: string | null;
  onAnalyze: () => Promise<void>;
  onApprove: (quoteId: string, note: string) => Promise<void>;
  onNoAward: (note: string) => Promise<void>;
  onKnowledgeFeedback?: (chunkId: string, action: KnowledgeFeedbackAction) => void;
};

function money(value: string, currency: string) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(Number(value));
}

function businessText(value: string) {
  return value.replace(/\bMOQ\s+(?=\d)/g, "起订量（MOQ）");
}

function QuoteStatus({ quote }: { quote: ComparisonQuote }) {
  return quote.eligible ? (
    <span className="proc-eligibility pass"><CheckCircle2 size={14} />通过</span>
  ) : (
    <span className="proc-eligibility fail"><XCircle size={14} />已淘汰</span>
  );
}

export function ComparisonView({
  request,
  busy,
  error,
  onAnalyze,
  onApprove,
  onNoAward,
  onKnowledgeFeedback,
}: Props) {
  const snapshot = request.comparison;
  const result = snapshot?.result;
  const [selectedId, setSelectedId] = useState<string | null>(
    result?.recommended_quote_id || null
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [noAwardOpen, setNoAwardOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    setSelectedId(result?.recommended_quote_id || null);
    setConfirmOpen(false);
    setNoAwardOpen(false);
    setConfirmed(false);
  }, [request.status, snapshot?.id, result?.recommended_quote_id]);
  const rows = useMemo(
    () =>
      [...(result?.quotes || [])].sort((left, right) => {
        if (left.eligible !== right.eligible) return left.eligible ? -1 : 1;
        if (left.rank && right.rank) return left.rank - right.rank;
        return left.supplier_name.localeCompare(right.supplier_name, "zh-CN");
      }),
    [result?.quotes]
  );
  const selected = rows.find((quote) => quote.quote_id === selectedId) || null;
  const terminal = request.status === "approved" || request.status === "no_award";
  const noEligibleQuotes = result?.eligible_count === 0;

  if (!snapshot || !result) {
    return (
      <section className="proc-empty-state">
        <span className="proc-empty-symbol"><Calculator size={30} /></span>
        <h2>尚未生成比价快照</h2>
        <p>{request.unresolved_field_count ? "先完成低置信度字段复核。" : "系统会先执行硬约束，再按总到货成本排序。"}</p>
        <button
          className="proc-button primary"
          type="button"
          disabled={request.quote_count < 2 || request.unresolved_field_count > 0 || busy === "analyze"}
          onClick={() => void onAnalyze()}
        >
          {busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
          生成比价
        </button>
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : null}
        <KnowledgeReferences
          references={request.knowledge_references || []}
          onFeedback={onKnowledgeFeedback}
        />
      </section>
    );
  }

  return (
    <div className="proc-comparison-view">
      <header className="proc-comparison-summary">
        <div className="proc-recommendation">
          <span className="proc-section-icon"><BadgeCheck size={18} /></span>
          <div>
            <span>规则推荐</span>
            <h2>{rows.find((quote) => quote.quote_id === result.recommended_quote_id)?.supplier_name || "无合格报价"}</h2>
            <p>{result.recommendation_explanation.map(businessText).join("；")}</p>
          </div>
        </div>
        <div className="proc-comparison-proof">
          <span><Fingerprint size={14} />快照 v{snapshot.version}</span>
          <code title={snapshot.input_sha256}>{snapshot.input_sha256.slice(0, 16)}</code>
          <a href={`/api/artifacts/${snapshot.artifact_id}`} target="_blank" rel="noreferrer">
            <ExternalLink size={14} />证据
          </a>
        </div>
        <div className="proc-comparison-counts">
          <span><strong>{result.eligible_count}</strong>符合</span>
          <span><strong>{result.excluded_count}</strong>淘汰</span>
          <span><strong>{result.ruleset_version}</strong>规则集</span>
        </div>
      </header>

      <KnowledgeReferences
        references={request.knowledge_references || []}
        onFeedback={onKnowledgeFeedback}
      />

      <div className="proc-comparison-table-wrap">
        <table className="proc-comparison-table">
          <thead>
            <tr>
              <th aria-label="选择供应商" />
              <th>供应商</th>
              <th>资格</th>
              <th>报价口径</th>
              <th>税额</th>
              <th>运费</th>
              <th>总到货成本</th>
              <th>到货单价</th>
              <th>起订量（MOQ）</th>
              <th>交期</th>
              <th>成本指数</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((quote) => (
              <tr
                key={quote.quote_id}
                className={`${quote.eligible ? "eligible" : "excluded"} ${selectedId === quote.quote_id ? "selected" : ""}`}
              >
                <td>
                  <input
                    type="radio"
                    name="supplier"
                    aria-label={`选择${quote.supplier_name}`}
                    value={quote.quote_id}
                    checked={selectedId === quote.quote_id}
                    disabled={!quote.eligible || terminal}
                    onChange={() => setSelectedId(quote.quote_id)}
                  />
                </td>
                <td>
                  <strong>{quote.supplier_name}</strong>
                  {quote.rank === 1 ? <span className="proc-best-mark">成本最优</span> : null}
                </td>
                <td>
                  <QuoteStatus quote={quote} />
                  {!quote.eligible ? (
                    <div className="proc-exclusion-list">
                      {quote.exclusion_reasons.map((reason) => <span key={reason.code}>{businessText(reason.message)}</span>)}
                    </div>
                  ) : null}
                </td>
                <td>
                  <span>{quote.cost.quoted_price} {quote.cost.quote_currency}</span>
                  <small>/ {quote.cost.price_basis.toLocaleString("zh-CN")} 个</small>
                </td>
                <td>{money(quote.cost.tax_quote_currency, quote.cost.quote_currency)}</td>
                <td>{money(quote.cost.freight_quote_currency, quote.cost.quote_currency)}</td>
                <td><strong>{money(quote.cost.landed_total_base, quote.cost.base_currency)}</strong></td>
                <td><strong>{money(quote.cost.landed_unit_base, quote.cost.base_currency)}</strong></td>
                <td>{quote.commercial.moq.toLocaleString("zh-CN")}</td>
                <td>{quote.commercial.lead_time_days} 天</td>
                <td>{quote.score || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="proc-rule-evidence">
        <div>
          <ShieldCheck size={17} />
          <span><strong>先资格、后排序</strong><small>起订量（MOQ）、交期、规格、发票和预算任一不满足即淘汰</small></span>
        </div>
        <div>
          <Calculator size={17} />
          <span><strong>精确金额核算</strong><small>使用 Decimal 计算单位、税率、汇率和运费，不调用模型</small></span>
        </div>
        <div>
          <ShieldAlert size={17} />
          <span><strong>人工最终决策</strong><small>规则推荐不会自动选定或下单</small></span>
        </div>
      </section>

      <footer className="proc-comparison-actions">
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : <span />}
        {request.status === "approved" ? (
          <span className="proc-approved-banner">
            <CheckCircle2 size={16} />供应商已人工批准
            <a
              className="proc-button secondary"
              href={`/api/procurement/requests/${request.id}/purchase-order.csv`}
              download
            >
              <FileDown size={15} />下载采购订单 CSV
            </a>
          </span>
        ) : request.status === "no_award" ? (
          <span className="proc-approved-banner"><XCircle size={16} />已确认本轮无合格报价</span>
        ) : noEligibleQuotes ? (
          <button
            className="proc-button primary"
            type="button"
            disabled={busy === "no_award"}
            onClick={() => setNoAwardOpen(true)}
          >
            <ShieldAlert size={16} />确认无合格报价
          </button>
        ) : (
          <button
            className="proc-button primary"
            type="button"
            disabled={!selected?.eligible || busy === "approve"}
            onClick={() => setConfirmOpen(true)}
          >
            <ShieldCheck size={16} />提交供应商审批
          </button>
        )}
      </footer>

      {confirmOpen && selected ? (
        <div className="proc-modal-backdrop" role="presentation">
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
            <header>
              <div><ShieldAlert size={18} /><h2 id="confirm-title">正式选定供应商</h2></div>
              <button className="proc-icon-button" type="button" title="关闭" aria-label="关闭" onClick={() => setConfirmOpen(false)}><X size={18} /></button>
            </header>
            <div className="proc-confirm-supplier">
              <span>{selected.supplier_name}</span>
              <strong>{money(selected.cost.landed_total_base, selected.cost.base_currency)}</strong>
              <small>到货单价 {money(selected.cost.landed_unit_base, selected.cost.base_currency)}</small>
            </div>
            <label className="proc-field"><span>审批备注</span><textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="选填" /></label>
            <label className="proc-check approval-confirm">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              <span><strong>我已核对报价原件、硬性条件与到货成本</strong><small>该操作会形成正式供应商选定结论并写入审计记录</small></span>
            </label>
            {error ? <p className="proc-form-error" role="alert"><AlertTriangle size={14} />{error}</p> : null}
            <footer>
              <button type="button" className="proc-button secondary" onClick={() => setConfirmOpen(false)}>取消</button>
              <button
                type="button"
                className="proc-button danger"
                disabled={!confirmed || busy === "approve"}
                onClick={() => void onApprove(selected.quote_id, note)}
              >
                {busy === "approve" ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                确认选定
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {noAwardOpen ? (
        <div className="proc-modal-backdrop" role="presentation">
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="no-award-title">
            <header>
              <div><ShieldAlert size={18} /><h2 id="no-award-title">确认无合格报价</h2></div>
              <button className="proc-icon-button" type="button" title="关闭" aria-label="关闭" onClick={() => setNoAwardOpen(false)}><X size={18} /></button>
            </header>
            <div className="proc-confirm-supplier">
              <span>本轮询价不选定供应商</span>
              <strong>{result.excluded_count} 家报价全部淘汰</strong>
              <small>提交后会固化当前比价快照与淘汰原因</small>
            </div>
            <label className="proc-field"><span>处理备注</span><textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="例如：全部报价超过预算，重新询价" /></label>
            <label className="proc-check approval-confirm">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              <span><strong>我确认当前没有满足全部硬性条件的报价</strong><small>不会选中被淘汰供应商，也不会生成采购下单结论</small></span>
            </label>
            {error ? <p className="proc-form-error" role="alert"><AlertTriangle size={14} />{error}</p> : null}
            <footer>
              <button type="button" className="proc-button secondary" onClick={() => setNoAwardOpen(false)}>取消</button>
              <button
                type="button"
                className="proc-button danger"
                disabled={!confirmed || busy === "no_award"}
                onClick={() => void onNoAward(note)}
              >
                {busy === "no_award" ? <LoaderCircle className="spin" size={16} /> : <ShieldAlert size={16} />}
                提交流标结论
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
