import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Bot,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileCode2,
  Fingerprint,
  LoaderCircle,
  RefreshCw,
  Search,
  Timer,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { procurementApi } from "./api";
import type {
  AiTaskStatus,
  AiTaskView,
  ProcurementRequestSummary,
} from "./types";

type Props = {
  requests: ProcurementRequestSummary[];
  tasks: AiTaskView[];
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (id: string | null, push?: boolean) => void;
  onOpenTask: (id: string) => void;
};

const STATUS_LABELS: Record<AiTaskStatus, string> = {
  PENDING: "等待调度",
  DISPATCHING: "正在投递",
  RUNNING: "正在分析",
  SUCCEEDED: "已成功",
  FAILED: "失败",
  RETRYING: "重试中",
  CANCELLED: "已取消",
};

const STEP_LABELS = {
  INPUT_VALIDATE: "输入校验",
  ARTIFACT_FETCH: "读取资料",
  QUOTE_PARSE: "核对报价",
  RULE_ANALYSIS: "规则分析",
  EXPLANATION: "生成解释",
  RESULT_PUBLISH: "发布结果",
};

type TimeFilter = "all" | "day" | "week" | "month";

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "操作失败");
}

function timeText(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function elapsed(start?: string | null, end?: string | null) {
  if (!start) return "尚未开始";
  const duration = Math.max(0, new Date(end || Date.now()).getTime() - new Date(start).getTime());
  if (duration < 60_000) return `${Math.max(1, Math.round(duration / 1_000))} 秒`;
  if (duration < 3_600_000) return `${Math.round(duration / 60_000)} 分钟`;
  return `${(duration / 3_600_000).toFixed(1)} 小时`;
}

function jsonText(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function cutoff(filter: TimeFilter) {
  const days = filter === "day" ? 1 : filter === "week" ? 7 : filter === "month" ? 30 : 0;
  return days ? Date.now() - days * 86_400_000 : 0;
}

export function AiTaskCenter({
  requests,
  tasks,
  loading,
  error,
  selectedId,
  onSelect,
  onOpenTask,
}: Props) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AiTaskStatus | "ALL">("ALL");
  const [assignee, setAssignee] = useState("ALL");
  const [time, setTime] = useState<TimeFilter>("all");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<"retry" | "cancel" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const requestMap = useMemo(() => new Map(requests.map((item) => [item.id, item])), [requests]);
  const assignees = useMemo(() => [...new Set(tasks.map((item) => item.assignee).filter(Boolean))] as string[], [tasks]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const after = cutoff(time);
    return tasks.filter((item) => {
      const request = requestMap.get(item.business_id);
      return (status === "ALL" || item.status === status)
        && (assignee === "ALL" || item.assignee === assignee)
        && (!after || new Date(item.updated_at).getTime() >= after)
        && (!query || [request?.reference, request?.title, item.trace_id, item.error_code]
          .filter(Boolean).join(" ").toLowerCase().includes(query));
    });
  }, [assignee, requestMap, search, status, tasks, time]);

  useEffect(() => {
    if (loading) return;
    if (!filtered.length) {
      if (selectedId && tasks.some((item) => item.ai_task_id === selectedId)) onSelect(null, false);
      return;
    }
    if (!selectedId) {
      onSelect(filtered[0].ai_task_id, false);
    } else if (tasks.some((item) => item.ai_task_id === selectedId)
      && !filtered.some((item) => item.ai_task_id === selectedId)) {
      onSelect(null, false);
    }
  }, [filtered, loading, onSelect, selectedId, tasks]);

  const detailQuery = useQuery({
    queryKey: ["procurement-ai-task", selectedId],
    queryFn: () => procurementApi.aiTask(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => query.state.data
      && ["PENDING", "DISPATCHING", "RUNNING", "RETRYING"].includes(query.state.data.status)
      ? 1_500 : false,
  });
  const detail = detailQuery.data || null;
  const request = detail ? requestMap.get(detail.business_id) : null;
  const active = detail ? ["PENDING", "DISPATCHING", "RUNNING", "RETRYING"].includes(detail.status) : false;
  const retryAllowed = Boolean(detail
    && detail.status === "FAILED"
    && detail.retryable
    && !detail.stale
    && detail.retry_count < (detail.max_retries ?? 3));
  const cancelAllowed = Boolean(detail
    && !["SUCCEEDED", "CANCELLED"].includes(detail.status)
    && !detail.stale);

  useEffect(() => {
    if (!confirmCancel) return;
    const timer = window.setTimeout(() => setConfirmCancel(false), 4_000);
    return () => window.clearTimeout(timer);
  }, [confirmCancel]);

  async function runAction(action: "retry" | "cancel") {
    if (!detail || busy) return;
    setBusy(action);
    setActionError(null);
    setConfirmCancel(false);
    try {
      if (action === "retry") await procurementApi.retryAiTask(detail.ai_task_id);
      else await procurementApi.cancelAiTask(detail.ai_task_id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-task", detail.ai_task_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks"] }),
      ]);
    } catch (cause) {
      setActionError(errorText(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <span className="text-xs font-semibold text-accent uppercase tracking-wider">执行队列</span>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2 mt-0.5">
            <Bot className="w-5 h-5 text-accent" />
            AI 任务中心
          </h1>
        </div>
        <div className="proc-page-summary flex items-center gap-2 bg-surface-subtle px-3.5 py-1.5 rounded-full border border-border">
          <strong className="text-sm font-mono font-bold text-danger">{tasks.filter((item) => item.status === "FAILED" || item.stale).length}</strong>
          <span className="text-xs text-text-secondary">异常任务</span>
        </div>
      </header>

      <div className="proc-center-filters flex flex-wrap items-center gap-3 p-4 rounded-xl glass-panel bg-surface/80 border border-border/80 shadow-sm" aria-label="AI 任务筛选">
        <label className="proc-filter-search flex-1 min-w-[200px] flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus-within:border-accent">
          <Search size={14} className="text-text-muted" />
          <input className="w-full bg-transparent border-none outline-none text-xs text-text placeholder:text-text-muted" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或 Trace" aria-label="搜索 AI 任务" />
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">状态</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" value={status} onChange={(event) => setStatus(event.target.value as AiTaskStatus | "ALL")}>
            <option value="ALL">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">任务类型</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" aria-label="AI 任务类型" defaultValue="QUOTE_ANALYSIS"><option value="QUOTE_ANALYSIS">报价分析</option></select>
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">负责人</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" value={assignee} onChange={(event) => setAssignee(event.target.value)}>
            <option value="ALL">全部负责人</option>
            {assignees.map((value) => <option value={value} key={value}>{value}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-text font-medium">
          <span className="text-text-muted">时间</span>
          <select className="px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent" value={time} onChange={(event) => setTime(event.target.value as TimeFilter)}>
            <option value="all">全部时间</option>
            <option value="day">最近 24 小时</option>
            <option value="week">最近 7 天</option>
            <option value="month">最近 30 天</option>
          </select>
        </label>
      </div>

      <div className="proc-center-layout grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <section className="proc-center-list lg:col-span-4 flex flex-col gap-3" aria-label="AI 任务列表">
          <header className="flex items-center justify-between text-xs text-text-muted pb-1">
            <strong className="font-semibold text-text">{filtered.length} 个任务</strong>
            <span>采购状态与 AI 状态独立</span>
          </header>
          {loading ? <div className="proc-center-empty py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={22} />正在读取任务</div> : null}
          {error ? <div className="proc-center-empty danger p-4 rounded-xl bg-danger-soft border border-danger/30 text-xs text-danger flex items-center gap-2" role="alert"><AlertTriangle size={22} />{error}</div> : null}
          {!loading && !error ? filtered.map((item) => {
            const owner = requestMap.get(item.business_id);
            return (
              <button type="button" key={item.ai_task_id} className={`proc-invoice-card glass-panel text-left p-4 rounded-xl border transition-all duration-150 flex flex-col gap-2 ${selectedId === item.ai_task_id ? "selected border-accent bg-accent-soft/30 shadow-xs ring-1 ring-accent/30" : "bg-surface/80 border-border/80 hover:border-border-strong hover:bg-surface"}`} onClick={() => onSelect(item.ai_task_id, true)}>
                <div className="flex items-center justify-between gap-2">
                  <span className={`proc-queue-status not-italic text-[11px] font-medium px-2 py-0.5 rounded-full border ${item.stale ? "stale bg-warning-soft text-warning border-warning/30" : item.status === "SUCCEEDED" ? "bg-accent-soft text-accent border-accent/30" : item.status === "FAILED" ? "bg-danger-soft text-danger border-danger/30" : "bg-surface-subtle text-text-muted border-border"}`}>{item.stale ? "已过期" : STATUS_LABELS[item.status]}</span>
                  <span className="text-[11px] text-text-muted font-mono">{item.current_step ? STEP_LABELS[item.current_step] : "等待步骤"}</span>
                </div>
                <strong className="text-xs font-semibold text-text truncate">{owner?.title || item.business_id}</strong>
                <small className="text-[11px] text-text-muted font-mono truncate">{owner?.reference || item.trace_id}</small>
                <span className="proc-center-list-meta flex items-center justify-between pt-2 border-t border-border/40 text-[11px] text-text-muted"><span>{item.current_step ? STEP_LABELS[item.current_step] : "等待步骤"}</span><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></span>
              </button>
            );
          }) : null}
          {!loading && !error && !filtered.length ? <div className="proc-center-empty py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted"><CheckCircle2 size={22} className="text-accent" />当前筛选没有 AI 任务</div> : null}
        </section>

        <section className="proc-center-detail lg:col-span-8 glass-panel rounded-xl p-6 border border-border/80 bg-surface/80 shadow-sm flex flex-col gap-5" aria-label="AI 任务详情">
          {detailQuery.isPending && selectedId ? <div className="proc-center-empty py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={22} />正在读取任务详情</div> : null}
          {detailQuery.isError ? <div className="proc-center-empty danger p-4 rounded-xl bg-danger-soft border border-danger/30 text-xs text-danger flex items-center gap-2" role="alert"><AlertTriangle size={22} />{errorText(detailQuery.error)}</div> : null}
          {!selectedId && !detailQuery.isPending ? <div className="proc-center-empty py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted"><Bot size={25} />选择一个 AI 任务查看执行详情</div> : null}
          {detail ? (
            <div className="proc-ai-detail flex flex-col gap-5">
              <header className="proc-detail-head flex items-start justify-between gap-3 pb-3 border-b border-border/60">
                <div>
                  <span className="font-mono text-xs text-accent font-semibold">{request?.reference || detail.business_id}</span>
                  <h2 className="text-base font-bold text-text mt-0.5">{request?.title || "采购 AI 任务"}</h2>
                  <p className="text-xs text-text-muted mt-0.5">AI {STATUS_LABELS[detail.status]} · 采购 {request?.status || "-"}</p>
                </div>
                <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => onOpenTask(detail.business_id)}>采购详情<ArrowRight size={15} /></button>
              </header>

              <div className="proc-detail-facts grid grid-cols-2 sm:grid-cols-4 gap-3 bg-surface-subtle/60 p-3.5 rounded-lg border border-border/40 text-xs">
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">任务类型</small><strong className="font-semibold text-text mt-0.5">报价分析</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">负责人</small><strong className="font-semibold text-text mt-0.5">{detail.assignee || "未分配"}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">总耗时</small><strong className="font-mono text-text mt-0.5">{elapsed(detail.started_at, detail.finished_at)}</strong></span>
                <span className="flex flex-col"><small className="text-[11px] text-text-muted">尝试次数</small><strong className="font-mono text-text mt-0.5">{detail.retry_count + 1} / {(detail.max_retries ?? 3) + 1}</strong></span>
              </div>

              <section className={`proc-ai-state-panel ${detail.status.toLowerCase()} p-4 rounded-xl border flex flex-col gap-3 ${detail.status === "SUCCEEDED" ? "bg-accent-soft/30 border-accent/40" : detail.status === "FAILED" ? "bg-danger-soft/30 border-danger/40" : "bg-surface-subtle border-border/80"}`}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <span className="w-8 h-8 rounded-lg bg-surface flex items-center justify-center border border-border/60">{active ? <LoaderCircle className="spin text-accent" size={17} /> : detail.status === "FAILED" ? <AlertTriangle className="text-danger" size={17} /> : <CheckCircle2 className="text-accent" size={17} />}</span>
                    <div>
                      <strong className="text-xs font-bold text-text block">{detail.stale ? "结果已过期" : STATUS_LABELS[detail.status]}</strong>
                      <small className="text-[11px] text-text-muted block">{detail.error_message || (detail.current_step ? STEP_LABELS[detail.current_step] : "状态已持久化")}</small>
                    </div>
                  </div>
                  <b className="font-mono text-sm font-bold text-text">{Math.round(Number(detail.progress) * 100)}%</b>
                </div>
                <span className="proc-ai-progress w-full bg-surface rounded-full h-2 overflow-hidden border border-border/40 block">
                  <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${Math.round(Number(detail.progress) * 100)}%` }} />
                </span>
                {detail.error_code || detail.stale_reason ? <p className="text-xs text-danger font-medium p-2 rounded-lg bg-danger-soft/50 border border-danger/30" role="alert">{detail.error_category ? `${detail.error_category} · ` : ""}{detail.error_code || detail.stale_reason}</p> : null}
                <footer className="flex items-center gap-2 pt-2 border-t border-border/40">
                  <button className="proc-button secondary inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle disabled:opacity-40" type="button" disabled={!retryAllowed || busy !== null} title={!retryAllowed ? "仅可重试未过期的可重试失败任务" : "重试任务"} onClick={() => void runAction("retry")}>{busy === "retry" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重试</button>
                  <button className="proc-button secondary danger-text inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-danger hover:bg-danger-soft disabled:opacity-40" type="button" disabled={!cancelAllowed || busy !== null} onClick={() => {
                    if (!confirmCancel) { setConfirmCancel(true); return; }
                    void runAction("cancel");
                  }}>{busy === "cancel" ? <LoaderCircle className="spin" size={15} /> : confirmCancel ? <AlertTriangle size={15} /> : <Ban size={15} />}{confirmCancel ? "再次点击确认取消" : "取消"}</button>
                </footer>
                {actionError ? <p className="proc-inline-error text-xs text-danger font-medium p-2 rounded-lg bg-danger-soft border border-danger/30" role="alert">{actionError}</p> : null}
              </section>

              <section className="proc-detail-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                <header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><Timer size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">执行步骤</h3></div><span className="text-xs text-text-muted">{detail.records.length} 条记录</span></header>
                {detail.records.length ? <ol className="proc-step-timeline flex flex-col gap-2.5">{detail.records.map((record) => (
                  <li className={`flex items-center justify-between gap-3 p-2.5 rounded-lg border border-border/40 text-xs ${record.status.toLowerCase()} ${record.status === "SUCCEEDED" ? "bg-surface/80" : record.status === "FAILED" ? "bg-danger-soft/20 border-danger/30" : "bg-surface/60"}`} key={record.record_id}>
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="flex-shrink-0">{record.status === "SUCCEEDED" ? <CheckCircle2 size={14} className="text-accent" /> : record.status === "FAILED" ? <AlertTriangle size={14} className="text-danger" /> : <LoaderCircle size={14} className="text-accent spin" />}</span>
                      <div className="min-w-0">
                        <strong className="font-semibold text-text block truncate">{STEP_LABELS[record.step]}</strong>
                        <small className="text-[11px] text-text-muted block truncate">{record.summary || record.error_message || record.status}</small>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 text-text-muted text-[11px]">
                      <em className="not-italic font-mono">第 {record.attempt} 次</em>
                      <time className="font-mono" dateTime={record.created_at}>{record.duration_ms != null ? `${record.duration_ms} ms` : timeText(record.created_at)}</time>
                    </div>
                  </li>
                ))}</ol> : <p className="proc-detail-empty text-xs text-text-muted py-4 text-center">任务仍在等待调度，尚无步骤记录。</p>}
              </section>

              <section className="proc-detail-section glass-panel rounded-xl p-4 border border-border/60 bg-surface/60 flex flex-col gap-3">
                <header className="flex items-center justify-between pb-1 border-b border-border/30"><div className="flex items-center gap-2"><FileCode2 size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">分析结果</h3></div>{detail.result ? <span className="text-xs font-mono text-text-muted">{detail.result.provider || "offline"} / {detail.result.model || "deterministic"}</span> : null}</header>
                {detail.result ? <>
                  <div className="proc-result-summary flex items-start gap-2.5 p-3.5 rounded-lg bg-surface-subtle/80 border border-border/60 text-xs"><Bot size={17} className="text-accent flex-shrink-0 mt-0.5" /><p className="text-text-secondary leading-relaxed">{String(detail.result.structured_result.summary || "结构化 AI 结果已持久化")}</p></div>
                  <div className="proc-result-meta grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs bg-surface-subtle/40 p-3 rounded-lg border border-border/40">
                    <span className="flex flex-col"><small className="text-[11px] text-text-muted">Prompt</small><strong className="font-mono text-text">{detail.result.prompt_version}</strong></span>
                    <span className="flex flex-col"><small className="text-[11px] text-text-muted">Parser</small><strong className="font-mono text-text">{detail.result.parser_version || "-"}</strong></span>
                    <span className="flex flex-col"><small className="text-[11px] text-text-muted">输入指纹</small><code className="font-mono text-text" title={detail.result.input_sha256}>{detail.result.input_sha256.slice(0, 16)}</code></span>
                    <span className="flex flex-col"><small className="text-[11px] text-text-muted">结果指纹</small><code className="font-mono text-text" title={detail.result.result_sha256}>{detail.result.result_sha256.slice(0, 16)}</code></span>
                  </div>
                  <div className="proc-result-json flex flex-col gap-2">
                    <details open className="group rounded-lg border border-border/60 bg-surface/80 p-3 text-xs"><summary className="font-semibold text-text cursor-pointer hover:text-accent select-none">结构化结果</summary><pre className="mt-2 p-3 rounded bg-surface-subtle font-mono text-[11px] overflow-x-auto text-text leading-relaxed">{jsonText(detail.result.structured_result)}</pre></details>
                    <details className="group rounded-lg border border-border/60 bg-surface/80 p-3 text-xs"><summary className="font-semibold text-text cursor-pointer hover:text-accent select-none">原始结果</summary><pre className="mt-2 p-3 rounded bg-surface-subtle font-mono text-[11px] overflow-x-auto text-text leading-relaxed">{jsonText(detail.result.raw_result)}</pre></details>
                  </div>
                  <div className="proc-source-list flex flex-col gap-2 pt-2 border-t border-border/40"><header className="flex items-center justify-between text-xs text-text"><div className="flex items-center gap-1.5"><Fingerprint size={15} className="text-accent" /><strong>来源 Artifact</strong></div><span className="text-text-muted">{detail.result.sources.length} 项</span></header>{detail.result.sources.length ? detail.result.sources.map((source, index) => (
                    <a className="flex items-center justify-between p-2.5 rounded-lg border border-border/60 bg-surface/60 hover:bg-surface hover:border-accent/40 text-xs transition-all" href={`/api/artifacts/${source.artifact_id}/raw`} target="_blank" rel="noreferrer" key={`${source.artifact_id}-${index}`}>
                      <span className="flex flex-col min-w-0"><strong className="text-text truncate">{source.locator || "原始报价"}</strong><small className="text-[11px] text-text-muted truncate">{source.excerpt || "查看原件"}</small></span><div className="flex items-center gap-2 flex-shrink-0"><em className="not-italic font-mono font-semibold text-accent">{Math.round(source.confidence * 100)}%</em><ExternalLink size={14} className="text-text-muted" /></div>
                    </a>
                  )) : <p className="text-xs text-text-muted py-2">本次结果没有可展示的来源。</p>}</div>
                </> : <p className="proc-detail-empty text-xs text-text-muted py-4 text-center">任务完成后将在此显示结构化结果、原始结果、模型版本和来源。</p>}
              </section>

              <section className="proc-trace-strip flex flex-wrap items-center justify-between gap-3 p-3 rounded-lg bg-surface-subtle/50 border border-border/40 text-xs text-text-muted"><span><Fingerprint size={14} className="inline mr-1 text-accent" />Trace <code className="font-mono text-text" title={detail.trace_id}>{detail.trace_id}</code></span><span><Clock3 size={14} className="inline mr-1 text-accent" />Operation <code className="font-mono text-text" title={detail.operation_id || "-"}>{detail.operation_id || "-"}</code></span></section>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
