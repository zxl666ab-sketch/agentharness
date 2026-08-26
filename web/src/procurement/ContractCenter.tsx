import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  BadgeCheck,
  CheckCircle2,
  FileSignature,
  LoaderCircle,
  RotateCcw,
  Send,
  ShieldAlert,
  X,
} from "lucide-react";
import { useState } from "react";

import { procurementApi } from "./api";
import type { ContractStatus, ContractView } from "./types";
import { useEscape } from "./useEscape";
import { CONTRACT_STATUS_LABELS } from "./viewModel";

const STATUS_FILTERS: Array<{ value: ContractStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "DRAFT", label: "草拟中" },
  { value: "PENDING_APPROVAL", label: "待审批" },
  { value: "EFFECTIVE", label: "已生效" },
  { value: "EXECUTING", label: "执行中" },
  { value: "CHANGE_REQUEST", label: "变更审批" },
  { value: "CLOSED", label: "已关闭" },
];

// 文案唯一来源在 viewModel（工作台任务详情同源引用，避免内联重复）
const STATUS_LABELS: Record<ContractStatus, { label: string; tone: string }> = {
  DRAFT: { label: CONTRACT_STATUS_LABELS.DRAFT, tone: "info" },
  PENDING_APPROVAL: { label: CONTRACT_STATUS_LABELS.PENDING_APPROVAL, tone: "warning" },
  EFFECTIVE: { label: CONTRACT_STATUS_LABELS.EFFECTIVE, tone: "success" },
  EXECUTING: { label: CONTRACT_STATUS_LABELS.EXECUTING, tone: "info" },
  CHANGE_REQUEST: { label: CONTRACT_STATUS_LABELS.CHANGE_REQUEST, tone: "warning" },
  CLOSED: { label: CONTRACT_STATUS_LABELS.CLOSED, tone: "neutral" },
};

const RISK_TONES: Record<string, string> = {
  高风险: "danger",
  提示: "warning",
  低: "success",
};

function money(value: string | null | undefined) {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(parsed)
    : String(value);
}

export function ContractCenter() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ContractStatus | "">("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [approveTarget, setApproveTarget] = useState<ContractView | null>(null);
  const [approveNotes, setApproveNotes] = useState("");
  const [approveConfirmed, setApproveConfirmed] = useState(false);
  const [changeTarget, setChangeTarget] = useState<ContractView | null>(null);
  const [changeNotes, setChangeNotes] = useState("");
  const [changeAmount, setChangeAmount] = useState("");
  const [changeLeadDays, setChangeLeadDays] = useState("");

  useEscape(!!approveTarget, () => setApproveTarget(null), busy?.startsWith("approve:") ?? false);
  useEscape(!!changeTarget, () => setChangeTarget(null), busy?.startsWith("change:") ?? false);

  const contractsQuery = useQuery({
    queryKey: ["procurement-contracts", status],
    queryFn: () => procurementApi.contracts(status || undefined, undefined, 0, 100),
    // 全部终态（CLOSED）后停止轮询；在途合同才持续刷新
    refetchInterval: (query) =>
      query.state.data?.items?.some((item) => item.status !== "CLOSED") ? 5_000 : false,
  });
  const tasksQuery = useQuery({
    queryKey: ["procurement-contracts-tasks"],
    queryFn: procurementApi.requests,
  });
  const contracts = contractsQuery.data?.items || [];
  const selected = contracts.find((item) => item.id === selectedId) || null;
  const detailQuery = useQuery({
    queryKey: ["procurement-contract", selectedId],
    queryFn: () => procurementApi.contract(selectedId!),
    enabled: !!selectedId,
    // 仅在途态（AI 草拟中/变更审批）持续刷新；终态不轮询
    refetchInterval: () =>
      selected?.status === "DRAFT" || selected?.status === "CHANGE_REQUEST" ? 5_000 : false,
  });
  const detail = detailQuery.data ?? selected ?? null;

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-contracts"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-contract"] }),
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

  async function createDraft() {
    if (!taskId) {
      setError("请先选择已定标（已批准）的采购任务");
      return;
    }
    const ok = await run(`draft:${taskId}`, () => procurementApi.createContractDraft(taskId), "已请求 AI 草拟合同（金额/交期/供应商由定标结果注入）。");
    if (ok) setTaskId("");
  }

  const submitForApproval = async (contract: ContractView) => {
    const ok = await run(`submit:${contract.id}`, () =>
      procurementApi.contractAction(contract.id, "submit", {}), "已提交人工审批。");
    if (ok) setSelectedId(contract.id);
  };

  const approve = async () => {
    if (!approveTarget) return;
    if (!approveConfirmed || !approveNotes.trim()) {
      setError("批准合同必须勾选确认并填写人工备注");
      return;
    }
    const ok = await run(`approve:${approveTarget.id}`, () =>
      procurementApi.contractAction(approveTarget.id, "approve", { confirmed: true, notes: approveNotes.trim() }),
      approveTarget.status === "CHANGE_REQUEST" ? "变更已批准，修订金额/交期已落定，合同恢复生效。" : "合同已生效，并关联采购订单。");
    if (ok) setApproveTarget(null);
  };

  const reject = async (contract: ContractView) => {
    const ok = await run(`reject:${contract.id}`, () =>
      procurementApi.contractAction(contract.id, "reject", { notes: "审批驳回，返回草拟" }), "已驳回，合同回到草拟状态。");
    if (ok) setSelectedId(contract.id);
  };

  const openChange = (contract: ContractView) => {
    setChangeTarget(contract);
    setChangeNotes("");
    setChangeAmount(contract.amount != null ? String(contract.amount) : "");
    setChangeLeadDays(contract.lead_days != null ? String(contract.lead_days) : "");
    setError(null);
  };

  const requestChange = async () => {
    if (!changeTarget) return;
    if (!changeNotes.trim()) {
      setError("合同变更必须填写变更原因");
      return;
    }
    const newAmount = Number(changeAmount);
    const newLeadDays = Number(changeLeadDays);
    if (!Number.isFinite(newAmount) || newAmount <= 0 || !Number.isInteger(newLeadDays) || newLeadDays <= 0) {
      setError("合同变更必须提供修订后的金额（>0）与交期（正整数天）");
      return;
    }
    const ok = await run(`change:${changeTarget.id}`, () =>
      procurementApi.contractAction(changeTarget.id, "request_change", {
        notes: changeNotes.trim(),
        new_amount: newAmount,
        new_lead_days: newLeadDays,
      }),
      "变更单已发起（旧条款留痕，修订金额/交期待重新草拟后审批）。");
    if (ok) setChangeTarget(null);
  };

  const regenDraft = async (contract: ContractView) => {
    const ok = await run(`regen:${contract.id}`, () => procurementApi.regenContractDraft(contract.id),
      contract.status === "CHANGE_REQUEST" ? "已请求按修订值重新草拟（金额/交期由定标口径注入）。" : "已请求重新草拟。");
    if (ok) setSelectedId(contract.id);
  };

  const executeOrClose = async (contract: ContractView, action: "execute" | "close") => {
    const ok = await run(`${action}:${contract.id}`, () =>
      procurementApi.contractAction(contract.id, action, {}),
      action === "execute" ? "合同已开始执行。" : "合同已关闭。");
    if (ok) setSelectedId(contract.id);
  };

  const approvedTasks = (tasksQuery.data || []).filter((task) => task.status === "approved");

  return (
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <FileSignature className="w-5 h-5 text-accent" />
            合同中心
          </h1>
          <p className="text-xs text-text-muted mt-1">定标 → 草拟（AI）→ 风险提示（AI）→ 人工审批 → 生效 → 关联订单 → 执行/变更/关闭</p>
        </div>
        <span className="proc-page-count text-xs font-medium text-text-secondary bg-surface-subtle px-3 py-1 rounded-full border border-border">共 {contractsQuery.data?.total ?? 0} 份</span>
      </header>

      <div className="proc-invoice-upload glass-panel bg-surface/80 p-4 rounded-xl border border-border/80 flex flex-wrap items-center gap-3 shadow-sm">
        <select aria-label="选择已定标任务" className="flex-1 min-w-[240px] px-3 py-2 rounded-lg border border-border bg-surface-subtle text-xs text-text focus:outline-accent" value={taskId} onChange={(event) => setTaskId(event.target.value)}>
          <option value="">选择已批准（定标）任务生成合同…</option>
          {approvedTasks.map((task) => (
            <option key={task.id} value={task.id}>{task.reference} · {task.title}</option>
          ))}
        </select>
        <button className="proc-button inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" disabled={busy?.startsWith("draft:")} onClick={() => void createDraft()}>
          {busy?.startsWith("draft:") ? <LoaderCircle className="spin" size={15} /> : <FileSignature size={15} />}
          生成合同（AI 草拟）
        </button>
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
        <div className="proc-invoice-list lg:col-span-4 flex flex-col gap-3" aria-busy={contractsQuery.isPending}>
          {contractsQuery.isPending ? (
            <div className="proc-loading-state py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在加载合同…</div>
          ) : null}
          {contractsQuery.isError ? (
            <section className="proc-empty-state compact py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
              <AlertTriangle size={26} className="text-danger" />
              <h2 className="text-sm font-semibold text-text">合同加载失败</h2>
              <p className="text-text-muted">{contractsQuery.error instanceof Error ? contractsQuery.error.message : "未知错误"}</p>
            </section>
          ) : null}
          {!contractsQuery.isPending && !contractsQuery.isError && !contracts.length ? (
            <div className="proc-empty-state py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
              <Archive size={30} className="text-text-muted" />
              <h2 className="text-sm font-semibold text-text">{status ? "该状态下没有合同" : "还没有合同"}</h2>
              <p>选择已批准任务，AI 草拟后提交人工审批。</p>
            </div>
          ) : null}
          {contracts.map((contract) => {
            const state = STATUS_LABELS[contract.status];
            const riskCount = (contract.clauses || []).filter((clause) => clause.risk_level === "高风险").length;
            return (
              <button
                type="button"
                key={contract.id}
                className={`proc-invoice-card glass-panel text-left p-4 rounded-xl border transition-all duration-150 flex flex-col gap-2 ${selectedId === contract.id ? "selected border-accent bg-accent-soft/30 shadow-xs ring-1 ring-accent/30" : "bg-surface/80 border-border/80 hover:border-border-strong hover:bg-surface"}`}
                onClick={() => setSelectedId(contract.id)}
              >
                <span className="proc-invoice-card-head flex items-center justify-between gap-2">
                  <code className="font-mono text-xs font-semibold text-accent">{contract.contract_no}</code>
                  <i className={`proc-status ${state.tone} not-italic text-[11px] font-medium px-2 py-0.5 rounded-full border inline-flex items-center gap-1.5`}><span className="w-1.5 h-1.5 rounded-full bg-current" />{state.label}</i>
                </span>
                <strong className="text-xs font-semibold text-text truncate">{contract.supplier_name} · {contract.item_name}</strong>
                <span className="proc-invoice-card-facts flex items-center justify-between gap-2 text-[11px] text-text-muted">
                  <small>金额 {money(contract.amount)} · 交期 {contract.lead_days} 天</small>
                  {riskCount ? <small className="proc-invoice-diff text-danger font-medium bg-danger-soft px-1.5 py-0.5 rounded border border-danger/20">{riskCount} 项高风险条款</small> : null}
                </span>
              </button>
            );
          })}
        </div>

        <div className="proc-invoice-detail lg:col-span-8 glass-panel rounded-xl p-6 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-5">
          {detailQuery.isError && selectedId ? (
            <section className="proc-empty-state compact py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
              <AlertTriangle size={26} className="text-danger" />
              <h2 className="text-sm font-semibold text-text">合同详情加载失败</h2>
              <p className="text-text-muted">{detailQuery.error instanceof Error ? detailQuery.error.message : "未知错误"}</p>
              <button type="button" className="proc-button px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" onClick={() => { void detailQuery.refetch(); }}>重试</button>
            </section>
          ) : detail ? (
            <>
              <header className="proc-panel-head flex items-start justify-between gap-3 pb-3 border-b border-border/60">
                <div className="flex items-center gap-2"><FileSignature size={18} className="text-accent" /><div><h2 className="text-base font-bold text-text font-mono">{detail.contract_no}</h2><span className="text-xs text-text-muted">{detail.supplier_name} · {detail.item_name}</span></div></div>
                <span className={`proc-status ${STATUS_LABELS[detail.status].tone} text-xs font-medium px-2.5 py-0.5 rounded-full border inline-flex items-center gap-1.5`}><i className="w-1.5 h-1.5 rounded-full bg-current" />{STATUS_LABELS[detail.status].label}</span>
              </header>

              {detailQuery.isPending ? (
                <span className="proc-muted text-xs text-text-muted">列表快照 — 正在加载完整草拟与条款…</span>
              ) : null}

              <div className="proc-invoice-facts grid grid-cols-2 sm:grid-cols-4 gap-3 bg-surface-subtle/60 p-3.5 rounded-lg border border-border/40 text-xs">
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">合同金额（注入）</small><strong className="font-mono font-bold text-text mt-0.5">{money(detail.amount)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">交期（注入）</small><strong className="font-mono font-bold text-text mt-0.5">{detail.lead_days} 天</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">关联任务</small><strong className="font-mono text-text mt-0.5">{detail.task_reference || "—"}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">关联订单</small><strong className="font-mono text-text mt-0.5">{detail.order_no || "—"}</strong></span>
              </div>

              {detail.consistency ? (
                <section className="proc-report-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-2.5">
                  <header className="flex items-center gap-2 pb-1 border-b border-border/30"><ShieldAlert size={16} className="text-warning" /><h3 className="text-xs font-bold text-text">草拟一致性校验（Java 权威）</h3></header>
                  <p className="proc-contract-consistency text-xs text-text-secondary flex flex-wrap items-center gap-1.5">
                    金额：文本 {detail.consistency.amount_in_text || "未识别"} {detail.consistency.amount_matches ? <BadgeCheck size={14} className="text-accent" /> : <X size={14} className="text-danger" />}
                    ；交期：文本 {detail.consistency.lead_days_in_text || "未识别"} {detail.consistency.lead_days_matches ? <BadgeCheck size={14} className="text-accent" /> : <X size={14} className="text-danger" />}
                  </p>
                  {detail.consistency.consistent ? null : (
                    <p className="proc-contract-warning text-xs text-danger font-medium p-2 rounded-lg bg-danger-soft border border-danger/30" role="alert">草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。</p>
                  )}
                </section>
              ) : null}

              {detail.clauses?.length ? (
                <section className="proc-report-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                  <header className="flex items-center gap-2 pb-1 border-b border-border/30"><ShieldAlert size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">条款与风险识别（AI，结构化）</h3></header>
                  <div className="proc-contract-clauses grid grid-cols-1 gap-2.5">
                    {detail.clauses.map((clause) => (
                      <div className="proc-contract-clause p-3 rounded-lg border border-border/60 bg-surface/80 flex flex-col gap-1.5 text-xs" key={clause.title}>
                        <span className="proc-contract-clause-head flex items-center justify-between gap-2">
                          <strong className="font-semibold text-text">{clause.title}</strong>
                          <i className={`proc-status ${RISK_TONES[clause.risk_level] || "neutral"} not-italic text-[11px] font-medium px-2 py-0.5 rounded-full border inline-flex items-center gap-1`}><span className="w-1.5 h-1.5 rounded-full bg-current" />{clause.risk_level}</i>
                        </span>
                        <p className="text-text-secondary leading-relaxed">{clause.content}</p>
                        <small className="text-text-muted">{clause.risk_reason}</small>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              {detail.draft_text ? (
                <section className="proc-report-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-2.5">
                  <header className="flex items-center gap-2 pb-1 border-b border-border/30"><FileSignature size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">草拟文本</h3></header>
                  <pre className="proc-contract-draft font-mono text-xs p-4 rounded-lg bg-surface-subtle border border-border/60 overflow-x-auto whitespace-pre-wrap leading-relaxed text-text">{detail.draft_text}</pre>
                </section>
              ) : null}

              {detail.change_history?.length ? (
                <section className="proc-report-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-2.5">
                  <header className="flex items-center gap-2 pb-1 border-b border-border/30"><RotateCcw size={16} className="text-info" /><h3 className="text-xs font-bold text-text">变更留痕（旧条款快照）</h3></header>
                  {detail.change_history.map((entry, index) => (
                    <div className="proc-contract-clause p-3 rounded-lg border border-border/60 bg-surface/80 flex flex-col gap-1 text-xs" key={`${entry.captured_at}-${index}`}>
                      <strong className="font-semibold text-text">变更 {index + 1} · {entry.reason}</strong>
                      <small className="text-text-muted font-mono">{entry.captured_at}</small>
                      {entry.new_amount || entry.new_lead_days ? (
                        <span className="proc-muted text-text-secondary mt-0.5">
                          修订：金额 {money(entry.new_amount)} · 交期 {entry.new_lead_days} 天
                          {entry.applied ? "（已批准落定）" : "（待审批）"}
                        </span>
                      ) : null}
                    </div>
                  ))}
                </section>
              ) : null}

              <div className="proc-invoice-actions flex flex-wrap items-center gap-2.5 pt-3 border-t border-border/60">
                {detail.status === "DRAFT" ? (
                  <>
                    {detail.draft_text ? (
                      <button className="proc-button primary inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={() => void submitForApproval(detail)}>
                        <Send size={14} />提交审批
                      </button>
                    ) : (
                      <span className="proc-muted text-xs text-text-muted">AI 草拟中…（完成后自动填充条款与风险）</span>
                    )}
                    <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => void regenDraft(detail)} disabled={busy === `regen:${detail.id}`}>
                      <RotateCcw size={14} />重新草拟
                    </button>
                  </>
                ) : null}
                {(detail.status === "PENDING_APPROVAL" || detail.status === "CHANGE_REQUEST") ? (
                  <>
                    <button className="proc-button primary inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={() => { setApproveTarget(detail); setApproveNotes(""); setApproveConfirmed(false); setError(null); }}>
                      <CheckCircle2 size={14} />批准
                    </button>
                    <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => void reject(detail)} disabled={busy === `reject:${detail.id}`}>
                      <X size={14} />驳回
                    </button>
                    {detail.status === "CHANGE_REQUEST" ? (
                      <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => void regenDraft(detail)} disabled={busy === `regen:${detail.id}`}>
                        <RotateCcw size={14} />按修订值重新草拟
                      </button>
                    ) : null}
                  </>
                ) : null}
                {detail.status === "EFFECTIVE" ? (
                  <>
                    <button className="proc-button inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={() => void executeOrClose(detail, "execute")} disabled={busy === `execute:${detail.id}`}>
                      <RotateCcw size={14} />开始执行
                    </button>
                    <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => openChange(detail)}>
                      <RotateCcw size={14} />发起变更
                    </button>
                  </>
                ) : null}
                {detail.status === "EXECUTING" ? (
                  <>
                    <button className="proc-button inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={() => void executeOrClose(detail, "close")} disabled={busy === `close:${detail.id}`}>
                      <CheckCircle2 size={14} />完成关闭
                    </button>
                    <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-subtle transition-all" type="button" onClick={() => openChange(detail)}>
                      <RotateCcw size={14} />发起变更
                    </button>
                  </>
                ) : null}
              </div>
            </>
          ) : (
            <div className="proc-empty-panel py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
              <FileSignature size={30} className="text-text-muted" />
              <h2 className="text-sm font-semibold text-text">合同详情</h2>
              <p>选择一份合同查看草拟文本、条款风险与审批操作。</p>
            </div>
          )}
        </div>
      </div>

      {approveTarget ? (
        <div className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `approve:${approveTarget.id}`) setApproveTarget(null); }}>
          <section className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="approve-contract-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base"><CheckCircle2 size={18} className="text-accent" /><h2 id="approve-contract-title">{approveTarget.status === "CHANGE_REQUEST" ? "批准合同变更（重新审批）" : "批准合同（allow-once）"}</h2></div></header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs"><strong className="font-mono text-sm font-bold text-text">{approveTarget.contract_no}</strong><span className="text-text-muted">{approveTarget.supplier_name} · 金额 {money(approveTarget.amount)}</span></div>
            {approveTarget.status === "CHANGE_REQUEST" ? (
              <p className="proc-contract-warning text-xs text-warning font-medium p-2.5 rounded-lg bg-warning-soft border border-warning/30" role="alert">批准后修订金额/交期将落定为正式合同值（旧快照留痕）。</p>
            ) : null}
            <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>人工备注 <b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" autoFocus value={approveNotes} onChange={(event) => setApproveNotes(event.target.value)} placeholder="批准意见（写入审计）" /></label>
            <label className="proc-invoice-confirm flex items-center gap-2 text-xs text-text cursor-pointer"><input type="checkbox" className="rounded border-border text-accent focus:ring-accent" checked={approveConfirmed} onChange={(event) => setApproveConfirmed(event.target.checked)} /><span>我已核对草拟文本与条款风险，确认批准（一次性）</span></label>
            {error ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setApproveTarget(null)} disabled={busy === `approve:${approveTarget.id}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `approve:${approveTarget.id}`} onClick={() => void approve()}>
                {busy === `approve:${approveTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}确认批准
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {changeTarget ? (
        <div className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busy !== `change:${changeTarget.id}`) setChangeTarget(null); }}>
          <section className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="change-contract-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60"><div className="flex items-center gap-2 text-text font-bold text-base"><RotateCcw size={18} className="text-warning" /><h2 id="change-contract-title">发起合同变更（修订金额/交期，需重新审批）</h2></div></header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs"><strong className="font-mono text-sm font-bold text-text">{changeTarget.contract_no}</strong><span className="text-text-muted">当前金额 {money(changeTarget.amount)} · 交期 {changeTarget.lead_days} 天；变更后旧条款快照留痕</span></div>
            <div className="proc-supplier-form flex flex-col gap-3">
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>变更原因 <b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" autoFocus value={changeNotes} onChange={(event) => setChangeNotes(event.target.value)} placeholder="例如：供应商调价，金额 8000、交期 20 天" /></label>
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>修订后金额（元）<b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm font-mono" type="number" step="any" min="0" value={changeAmount} onChange={(event) => setChangeAmount(event.target.value)} placeholder={String(changeTarget.amount)} /></label>
              <label className="proc-field flex flex-col gap-1 text-xs font-medium text-text"><span>修订后交期（天）<b>*</b></span><input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm font-mono" type="number" step="1" min="1" value={changeLeadDays} onChange={(event) => setChangeLeadDays(event.target.value)} placeholder={String(changeTarget.lead_days)} /></label>
            </div>
            <p className="proc-muted text-xs text-text-muted">变更提交后需「按修订值重新草拟」→ 一致性/条款校验 → 人工批准才生效。</p>
            {error ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{error}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setChangeTarget(null)} disabled={busy === `change:${changeTarget.id}`}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-warning text-white hover:bg-amber-600 inline-flex items-center gap-1.5 shadow-xs" type="button" disabled={busy === `change:${changeTarget.id}`} onClick={() => void requestChange()}>
                {busy === `change:${changeTarget.id}` ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}确认发起变更
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
