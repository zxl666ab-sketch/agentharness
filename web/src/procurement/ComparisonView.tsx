import {
  Ban,
  BadgeCheck,
  Calculator,
  CheckCircle2,
  ClipboardEdit,
  ExternalLink,
  Fingerprint,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button, Modal } from "../components/ui";
import type { ComparisonQuote, ProcurementRequest } from "./types";
import { rulesetLabel } from "./viewModel";

type Props = {
  request: ProcurementRequest;
  busy: string | null;
  error?: string | null;
  onAnalyze: () => Promise<void>;
  onApprove: (quoteId: string, note: string) => Promise<void>;
  onOpenRequirement?: () => void;
  onOpenQuotes?: () => void;
  onNoAward?: (note: string) => Promise<void>;
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
  onOpenRequirement,
  onOpenQuotes,
  onNoAward,
}: Props) {
  const snapshot = request.comparison;
  const result = snapshot?.result;
  const [selectedId, setSelectedId] = useState<string | null>(
    result?.recommended_quote_id || null
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [noAwardOpen, setNoAwardOpen] = useState(false);
  const [noAwardConfirmed, setNoAwardConfirmed] = useState(false);
  const [noAwardNote, setNoAwardNote] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const confirmDialogRef = useRef<HTMLElement | null>(null);
  const confirmCancelRef = useRef<HTMLButtonElement | null>(null);
  const noAwardDialogRef = useRef<HTMLElement | null>(null);
  const noAwardCancelRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    setSelectedId(result?.recommended_quote_id || null);
    setConfirmOpen(false);
    setConfirmed(false);
    setNoAwardOpen(false);
    setNoAwardConfirmed(false);
    setNoAwardNote("");
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
  const allExcluded = Boolean(result && rows.length > 0 && result.eligible_count === 0);

  // 定标/流标都是不可回退的正式决策：弹窗焦点圈定、Esc 关闭与 busy 保护由共享 Modal 承担，
  // 初始焦点落在「取消」上防回车误触。

  if (!snapshot || !result) {
    return (
      <section className="proc-empty-state">
        <span className="proc-empty-symbol"><Calculator size={30} /></span>
        <h2>尚未生成比价快照</h2>
        <p>{!request.requirement_confirmed ? "先保存采购需求的人工确认。" : request.unresolved_field_count ? "先完成低置信度字段复核。" : "系统会先执行硬约束，再按总到货成本排序。"}</p>
        <button
          className="proc-button primary"
          type="button"
          disabled={!request.requirement_confirmed || request.quote_count < 2 || request.unresolved_field_count > 0 || busy === "analyze"}
          title={!request.requirement_confirmed ? "先保存采购需求的人工确认" : request.quote_count < 2 ? "至少需要 2 家报价" : request.unresolved_field_count > 0 ? "先完成低置信度字段复核" : "生成确定性比价快照"}
          onClick={() => void onAnalyze()}
        >
          {busy === "analyze" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
          生成比价
        </button>
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : null}
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
        <details className="proc-evidence-panel compact" aria-label="比价证据详情">
          <summary>
            <span className="proc-evidence-badge"><ShieldCheck size={14} />证据已验证</span>
            <small>快照指纹与比对原件</small>
          </summary>
          <div className="proc-comparison-proof">
            <span><Fingerprint size={14} />快照 v{snapshot.version}</span>
            <code title={snapshot.input_sha256}>{snapshot.input_sha256.slice(0, 16)}</code>
            <a href={`/api/artifacts/${snapshot.artifact_id}`} target="_blank" rel="noreferrer">
              <ExternalLink size={14} />证据
            </a>
          </div>
        </details>
        <div className="proc-comparison-counts">
          <span><strong>{result.eligible_count}</strong>符合</span>
          <span><strong>{result.excluded_count}</strong>淘汰</span>
          <span><strong title={result.ruleset_version}>{rulesetLabel(result.ruleset_version)}</strong>规则集</span>
        </div>
      </header>

      <div className="proc-comparison-table-wrap">
        <div className="proc-comparison-table-toolbar">
          <span>默认收起税额 / 运费 / 成本指数等明细列</span>
          <button type="button" className="proc-button secondary compact" onClick={() => setShowDetails((value) => !value)}>
            {showDetails ? "收起详情" : "展开详情"}
          </button>
        </div>
        <table className="proc-comparison-table">
          <thead>
            <tr>
              <th aria-label="选择供应商" />
              <th>供应商</th>
              <th>资格</th>
              <th>报价口径</th>
              {showDetails ? <th>税额</th> : null}
              {showDetails ? <th>运费</th> : null}
              <th>总到货成本</th>
              <th>到货单价</th>
              <th>起订量（MOQ）</th>
              <th>交期</th>
              {showDetails ? <th>成本指数</th> : null}
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
                    disabled={!quote.eligible || request.status === "approved"}
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
                {showDetails ? <td>{money(quote.cost.tax_quote_currency, quote.cost.quote_currency)}</td> : null}
                {showDetails ? <td>{money(quote.cost.freight_quote_currency, quote.cost.quote_currency)}</td> : null}
                <td><strong>{money(quote.cost.landed_total_base, quote.cost.base_currency)}</strong></td>
                <td><strong>{money(quote.cost.landed_unit_base, quote.cost.base_currency)}</strong></td>
                <td>{quote.commercial.moq.toLocaleString("zh-CN")}</td>
                <td>{quote.commercial.lead_time_days} 天</td>
                {showDetails ? <td>{quote.score || "-"}</td> : null}
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

      {allExcluded && request.status !== "approved" && request.status !== "no_award" ? (
        <section className="proc-no-eligible-panel" aria-label="没有合格报价的恢复操作">
          <div className="proc-no-eligible-copy">
            <span className="proc-section-icon"><Ban size={18} /></span>
            <div>
              <strong>本轮没有合格供应商</strong>
              <p>不能审批被淘汰的报价。请调整需求、补充报价或结束本轮询价。</p>
            </div>
          </div>
          <div className="proc-no-eligible-actions">
            <button className="proc-button secondary" type="button" onClick={onOpenRequirement}>
              <ClipboardEdit size={15} />调整需求
            </button>
            <button className="proc-button secondary" type="button" onClick={onOpenQuotes}>
              <Upload size={15} />补充报价
            </button>
            <button className="proc-button secondary" type="button" disabled={busy === "analyze"} onClick={() => void onAnalyze()}>
              <RefreshCw size={15} />重新比价
            </button>
            <button className="proc-button danger" type="button" onClick={() => setNoAwardOpen(true)}>
              <Ban size={15} />本轮流标
            </button>
          </div>
        </section>
      ) : null}

      <footer className="proc-comparison-actions">
        {error ? <p className="proc-inline-error" role="alert">{error}</p> : <span />}
        {request.status === "approved" ? (
          <span className="proc-approved-banner"><CheckCircle2 size={16} />供应商已人工批准</span>
        ) : request.status === "no_award" ? (
          <span className="proc-no-award-banner"><Ban size={16} />本轮已流标</span>
        ) : allExcluded ? (
          <span className="proc-action-hint">请选择上方恢复操作</span>
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
        <Modal
          titleId="confirm-title"
          title="正式选定供应商"
          icon={<ShieldCheck size={18} />}
          tone="accent"
          busy={busy === "approve"}
          dismissible={busy !== "approve"}
          onClose={() => setConfirmOpen(false)}
          dialogRef={confirmDialogRef}
          initialFocusRef={confirmCancelRef}
          footer={
            <>
              <Button variant="secondary" ref={confirmCancelRef} onClick={() => setConfirmOpen(false)} disabled={busy === "approve"}>取消</Button>
              <Button
                variant="primary"
                icon={<ShieldCheck size={15} />}
                loading={busy === "approve"}
                disabled={!confirmed}
                onClick={() => void onApprove(selected.quote_id, note)}
              >
                确认选定
              </Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong>{selected.supplier_name}</strong>
            <span className="mono">{money(selected.cost.landed_total_base, selected.cost.base_currency)} · 到货单价 {money(selected.cost.landed_unit_base, selected.cost.base_currency)}</span>
          </div>
          <label className="proc-field">
            <span>审批备注</span>
            <textarea className="proc-input" value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="选填" rows={3} />
          </label>
          <label className="proc-check approval-confirm">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            <span><strong>我已核对报价原件、硬性条件与到货成本</strong><small>该操作会形成正式供应商选定结论并写入审计记录</small></span>
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {noAwardOpen ? (
        <Modal
          titleId="no-award-title"
          title="确认本轮流标"
          icon={<Ban size={18} />}
          tone="danger"
          busy={busy === "no_award"}
          dismissible={busy !== "no_award"}
          onClose={() => setNoAwardOpen(false)}
          dialogRef={noAwardDialogRef}
          initialFocusRef={noAwardCancelRef}
          footer={
            <>
              <Button variant="secondary" ref={noAwardCancelRef} onClick={() => setNoAwardOpen(false)} disabled={busy === "no_award"}>取消</Button>
              <Button
                variant="danger"
                icon={<Ban size={15} />}
                loading={busy === "no_award"}
                disabled={!noAwardConfirmed || !noAwardNote.trim() || !onNoAward}
                onClick={() => onNoAward && void onNoAward(noAwardNote.trim())}
              >
                确认流标
              </Button>
            </>
          }
        >
          <p className="proc-muted">当前快照中没有任何合格报价。流标后本任务不可编辑，只能复制为新任务重新询价。</p>
          <label className="proc-field">
            <span>流标原因</span>
            <textarea className="proc-input" value={noAwardNote} onChange={(event) => setNoAwardNote(event.target.value)} maxLength={2000} placeholder="请说明未满足的条件或下一步安排" rows={3} />
          </label>
          <label className="proc-check approval-confirm">
            <input type="checkbox" checked={noAwardConfirmed} onChange={(event) => setNoAwardConfirmed(event.target.checked)} />
            <span><strong>我确认本轮没有合格报价，并结束本轮询价</strong><small>该操作会写入不可编辑的采购决策和审计记录</small></span>
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}
    </div>
  );
}
