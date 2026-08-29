import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  BadgeCheck,
  CheckCircle2,
  FileSignature,
  LoaderCircle,
  Play,
  RotateCcw,
  Send,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";

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
import type { ContractStatus, ContractView } from "./types";
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
const STATUS_TONES: Record<ContractStatus, string> = {
  DRAFT: "info",
  PENDING_APPROVAL: "warning",
  EFFECTIVE: "success",
  EXECUTING: "info",
  CHANGE_REQUEST: "warning",
  CLOSED: "neutral",
};

const RISK_TONES: Record<string, string> = {
  高风险: "danger",
  提示: "warning",
  低: "success",
};

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

  const contractsQuery = useQuery({
    queryKey: ["procurement-contracts", status],
    queryFn: () => procurementApi.contracts(status || undefined, undefined, 0, 100),
    // 全部终态（CLOSED）后停止轮询；在途合同才持续刷新。
    // W-M4：一条长期滞留的在途合同不能把轮询变成永续（fetchCount 封顶）。
    refetchInterval: (query) =>
      pollFetchCount(query.state) > POLL_FETCH_CAP ? false
        : query.state.data?.items?.some((item) => item.status !== "CLOSED") ? 5_000 : false,
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
    // 仅在途态（AI 草拟中/变更审批）持续刷新；终态不轮询。
    // W-M4：读取 query.state（回调参数）而非渲染闭包里的 `selected`，并封顶 fetchCount。
    refetchInterval: (query) => {
      if (pollFetchCount(query.state) > POLL_FETCH_CAP) return false;
      const state = query.state.data?.status ?? selected?.status;
      return state === "DRAFT" || state === "CHANGE_REQUEST" ? 5_000 : false;
    },
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
    <CenterPage
      header={
        <PageHeader
          icon={<FileSignature size={18} />}
          title="合同中心"
          subtitle="定标 → 草拟（AI）→ 风险提示（AI）→ 人工审批 → 生效 → 关联订单 → 执行/变更/关闭"
          aside={<CountBadge>共 {contractsQuery.data?.total ?? 0} 份</CountBadge>}
        />
      }
      toolbar={
        <>
          <div className="proc-action-bar">
            <select className="proc-input is-grow" aria-label="选择已定标任务" value={taskId} onChange={(event) => setTaskId(event.target.value)}>
              <option value="">选择已批准（定标）任务生成合同…</option>
              {approvedTasks.map((task) => (
                <option key={task.id} value={task.id}>{task.reference} · {task.title}</option>
              ))}
            </select>
            <Button
              variant="primary"
              icon={<FileSignature size={15} />}
              loading={busy?.startsWith("draft:") ?? false}
              onClick={() => void createDraft()}
            >
              生成合同（AI 草拟）
            </Button>
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
              <strong>合同列表</strong>
              <small>点击行查看详情</small>
            </header>
            {contractsQuery.isPending ? (
              <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载合同…</div>
            ) : null}
            {contractsQuery.isError ? (
              <ErrorState title="合同加载失败" detail={contractsQuery.error instanceof Error ? contractsQuery.error.message : "未知错误"} onRetry={() => void contractsQuery.refetch()} />
            ) : null}
            {!contractsQuery.isPending && !contractsQuery.isError && !contracts.length ? (
              <EmptyState
                variant="inline"
                icon={<Archive size={24} />}
                title={status ? "该状态下没有合同" : "还没有合同"}
                hint="在上方选择已批准任务，AI 草拟后提交人工审批。"
              />
            ) : null}
            {contracts.map((contract) => {
              const riskCount = (contract.clauses || []).filter((clause) => clause.risk_level === "高风险").length;
              return (
                <ListRow key={contract.id} selected={selectedId === contract.id} onClick={() => setSelectedId(contract.id)}>
                  <span className="proc-list-row-head">
                    <code>{contract.contract_no}</code>
                    <StatusPill tone={STATUS_TONES[contract.status]} size="compact">{CONTRACT_STATUS_LABELS[contract.status]}</StatusPill>
                  </span>
                  <strong className="proc-list-row-title">{contract.supplier_name} · {contract.item_name}</strong>
                  <span className="proc-list-row-meta">
                    <small className="tnum">金额 {formatMoney(contract.amount)} · 交期 {contract.lead_days} 天</small>
                    {riskCount ? <em className="proc-risk-chip">{riskCount} 项高风险条款</em> : null}
                  </span>
                </ListRow>
              );
            })}
          </>
        }
        detail={
          <>
            {detailQuery.isError && selectedId ? (
              <ErrorState title="合同详情加载失败" detail={detailQuery.error instanceof Error ? detailQuery.error.message : "未知错误"} onRetry={() => { void detailQuery.refetch(); }} />
            ) : detail ? (
              <>
                <header className="proc-detail-head">
                  <div>
                    <h2><FileSignature size={16} /> <code>{detail.contract_no}</code></h2>
                    <p>{detail.supplier_name} · {detail.item_name}</p>
                  </div>
                  <StatusPill tone={STATUS_TONES[detail.status]}>{CONTRACT_STATUS_LABELS[detail.status]}</StatusPill>
                </header>

                {detailQuery.isPending ? (
                  <span className="proc-muted">列表快照 — 正在加载完整草拟与条款…</span>
                ) : null}

                <div className="proc-fact-grid">
                  <Fact label="合同金额" mono>{formatMoney(detail.amount)}</Fact>
                  <Fact label="交期" mono>{detail.lead_days} 天</Fact>
                  <Fact label="关联任务" mono>{detail.task_reference || "—"}</Fact>
                  <Fact label="关联订单" mono>{detail.order_no || "—"}</Fact>
                </div>

                {detail.consistency ? (
                  <Card head={{ icon: <ShieldAlert size={15} />, title: "草拟一致性校验（Java 权威）" }}>
                    <p className="proc-contract-consistency">
                      金额：文本 {detail.consistency.amount_in_text || "未识别"} {detail.consistency.amount_matches ? <BadgeCheck size={14} className="ok" /> : <X size={14} className="bad" />}
                      ；交期：文本 {detail.consistency.lead_days_in_text || "未识别"} {detail.consistency.lead_days_matches ? <BadgeCheck size={14} className="ok" /> : <X size={14} className="bad" />}
                    </p>
                    {detail.consistency.consistent ? null : (
                      <p className="proc-contract-warning" role="alert">草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认或重新草拟。</p>
                    )}
                  </Card>
                ) : null}

                {detail.clauses?.length ? (
                  <Card head={{ icon: <ShieldCheck size={15} />, title: "条款与风险识别（AI，结构化）" }}>
                    <div className="proc-contract-clauses">
                      {detail.clauses.map((clause) => (
                        <div className="proc-contract-clause" key={clause.title}>
                          <span className="proc-contract-clause-head">
                            <strong>{clause.title}</strong>
                            <StatusPill tone={RISK_TONES[clause.risk_level] || "neutral"} size="compact">{clause.risk_level}</StatusPill>
                          </span>
                          <p>{clause.content}</p>
                          <small>{clause.risk_reason}</small>
                        </div>
                      ))}
                    </div>
                  </Card>
                ) : null}

                {detail.draft_text ? (
                  <Card head={{ icon: <FileSignature size={15} />, title: "草拟文本" }}>
                    <pre className="proc-contract-draft">{detail.draft_text}</pre>
                  </Card>
                ) : null}

                {detail.change_history?.length ? (
                  <Card head={{ icon: <RotateCcw size={15} />, title: "变更留痕（旧条款快照）" }}>
                    {detail.change_history.map((entry, index) => (
                      <div className="proc-contract-clause" key={`${entry.captured_at}-${index}`}>
                        <span className="proc-contract-clause-head">
                          <strong>变更 {index + 1} · {entry.reason}</strong>
                          <small className="mono">{entry.captured_at}</small>
                        </span>
                        {entry.new_amount || entry.new_lead_days ? (
                          <span className="proc-muted">
                            修订：金额 {formatMoney(entry.new_amount)} · 交期 {entry.new_lead_days} 天
                            {entry.applied ? "（已批准落定）" : "（待审批）"}
                          </span>
                        ) : null}
                      </div>
                    ))}
                  </Card>
                ) : null}

                <div className="proc-detail-actions">
                  {detail.status === "DRAFT" ? (
                    <>
                      {detail.draft_text ? (
                        <Button variant="primary" icon={<Send size={14} />} onClick={() => void submitForApproval(detail)}>提交审批</Button>
                      ) : (
                        <span className="proc-muted">AI 草拟中…（完成后自动填充条款与风险）</span>
                      )}
                      <Button variant="secondary" icon={<RotateCcw size={14} />} loading={busy === `regen:${detail.id}`} onClick={() => void regenDraft(detail)}>重新草拟</Button>
                    </>
                  ) : null}
                  {(detail.status === "PENDING_APPROVAL" || detail.status === "CHANGE_REQUEST") ? (
                    <>
                      <Button variant="primary" icon={<CheckCircle2 size={14} />} onClick={() => { setApproveTarget(detail); setApproveNotes(""); setApproveConfirmed(false); setError(null); }}>批准</Button>
                      <Button variant="secondary" icon={<X size={14} />} loading={busy === `reject:${detail.id}`} onClick={() => void reject(detail)}>驳回</Button>
                      {detail.status === "CHANGE_REQUEST" ? (
                        <Button variant="secondary" icon={<RotateCcw size={14} />} loading={busy === `regen:${detail.id}`} onClick={() => void regenDraft(detail)}>按修订值重新草拟</Button>
                      ) : null}
                    </>
                  ) : null}
                  {detail.status === "EFFECTIVE" ? (
                    <>
                      <Button variant="primary" icon={<Play size={14} />} loading={busy === `execute:${detail.id}`} onClick={() => void executeOrClose(detail, "execute")}>开始执行</Button>
                      <Button variant="secondary" icon={<RotateCcw size={14} />} onClick={() => openChange(detail)}>发起变更</Button>
                    </>
                  ) : null}
                  {detail.status === "EXECUTING" ? (
                    <>
                      <Button variant="primary" icon={<CheckCircle2 size={14} />} loading={busy === `close:${detail.id}`} onClick={() => void executeOrClose(detail, "close")}>完成关闭</Button>
                      <Button variant="secondary" icon={<RotateCcw size={14} />} onClick={() => openChange(detail)}>发起变更</Button>
                    </>
                  ) : null}
                </div>
              </>
            ) : (
              <EmptyState
                variant="inline"
                icon={<FileSignature size={24} />}
                title="选择一份合同"
                hint="查看草拟文本、条款风险与审批操作；定标后的任务可在上方一键 AI 草拟。"
              />
            )}
          </>
        }
      />

      {approveTarget ? (
        <Modal
          titleId="approve-contract-title"
          title={approveTarget.status === "CHANGE_REQUEST" ? "批准合同变更（重新审批）" : "批准合同（allow-once）"}
          icon={<CheckCircle2 size={18} />}
          busy={busy === `approve:${approveTarget.id}`}
          onClose={() => setApproveTarget(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setApproveTarget(null)} disabled={busy === `approve:${approveTarget.id}`}>取消</Button>
              <Button variant="primary" icon={<CheckCircle2 size={15} />} loading={busy === `approve:${approveTarget.id}`} onClick={() => void approve()}>确认批准</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{approveTarget.contract_no}</strong>
            <span>{approveTarget.supplier_name} · 金额 {formatMoney(approveTarget.amount)}</span>
          </div>
          {approveTarget.status === "CHANGE_REQUEST" ? (
            <p className="proc-contract-warning" role="alert">批准后修订金额/交期将落定为正式合同值（旧快照留痕）。</p>
          ) : null}
          <label className="proc-field">
            <span>人工备注 <b>*</b></span>
            <input className="proc-input" autoFocus value={approveNotes} onChange={(event) => setApproveNotes(event.target.value)} placeholder="批准意见（写入审计）" />
          </label>
          <label className="proc-confirm-check">
            <input type="checkbox" checked={approveConfirmed} onChange={(event) => setApproveConfirmed(event.target.checked)} />
            <span>我已核对草拟文本与条款风险，确认批准（一次性）</span>
          </label>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}

      {changeTarget ? (
        <Modal
          titleId="change-contract-title"
          title="发起合同变更（修订金额/交期，需重新审批）"
          icon={<RotateCcw size={18} />}
          tone="warning"
          busy={busy === `change:${changeTarget.id}`}
          onClose={() => setChangeTarget(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setChangeTarget(null)} disabled={busy === `change:${changeTarget.id}`}>取消</Button>
              <Button variant="warning" icon={<AlertTriangle size={15} />} loading={busy === `change:${changeTarget.id}`} onClick={() => void requestChange()}>确认发起变更</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong className="mono">{changeTarget.contract_no}</strong>
            <span>当前金额 {formatMoney(changeTarget.amount)} · 交期 {changeTarget.lead_days} 天；变更后旧条款快照留痕</span>
          </div>
          <div className="proc-dialog-form">
            <label className="proc-field proc-field-wide">
              <span>变更原因 <b>*</b></span>
              <input className="proc-input" autoFocus value={changeNotes} onChange={(event) => setChangeNotes(event.target.value)} placeholder="例如：供应商调价，金额 8000、交期 20 天" />
            </label>
            <label className="proc-field">
              <span>修订后金额（元）<b>*</b></span>
              <input className="proc-input mono" type="number" step="any" min="0" value={changeAmount} onChange={(event) => setChangeAmount(event.target.value)} placeholder={String(changeTarget.amount)} />
            </label>
            <label className="proc-field">
              <span>修订后交期（天）<b>*</b></span>
              <input className="proc-input mono" type="number" step="1" min="1" value={changeLeadDays} onChange={(event) => setChangeLeadDays(event.target.value)} placeholder={String(changeTarget.lead_days)} />
            </label>
          </div>
          <p className="proc-muted">变更提交后需「按修订值重新草拟」→ 一致性/条款校验 → 人工批准才生效。</p>
          {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
        </Modal>
      ) : null}
    </CenterPage>
  );
}
