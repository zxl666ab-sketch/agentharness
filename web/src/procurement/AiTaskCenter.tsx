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
    <div className="proc-center-page">
      <header className="proc-page-head">
        <div><span>执行队列</span><h1>AI 任务中心</h1></div>
        <div className="proc-page-summary"><strong>{tasks.filter((item) => item.status === "FAILED" || item.stale).length}</strong><span>异常任务</span></div>
      </header>

      <div className="proc-center-filters" aria-label="AI 任务筛选">
        <label className="proc-filter-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或 Trace" aria-label="搜索 AI 任务" /></label>
        <label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value as AiTaskStatus | "ALL")}><option value="ALL">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label><span>任务类型</span><select aria-label="AI 任务类型" defaultValue="QUOTE_ANALYSIS"><option value="QUOTE_ANALYSIS">报价分析</option></select></label>
        <label><span>负责人</span><select value={assignee} onChange={(event) => setAssignee(event.target.value)}><option value="ALL">全部负责人</option>{assignees.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
        <label><span>时间</span><select value={time} onChange={(event) => setTime(event.target.value as TimeFilter)}><option value="all">全部时间</option><option value="day">最近 24 小时</option><option value="week">最近 7 天</option><option value="month">最近 30 天</option></select></label>
      </div>

      <div className="proc-center-layout">
        <section className="proc-center-list" aria-label="AI 任务列表">
          <header><strong>{filtered.length} 个任务</strong><span>采购状态与 AI 状态独立</span></header>
          {loading ? <div className="proc-center-empty"><LoaderCircle className="spin" size={22} />正在读取任务</div> : null}
          {error ? <div className="proc-center-empty danger" role="alert"><AlertTriangle size={22} />{error}</div> : null}
          {!loading && !error ? filtered.map((item) => {
            const owner = requestMap.get(item.business_id);
            return (
              <button type="button" key={item.ai_task_id} className={selectedId === item.ai_task_id ? "selected" : ""} onClick={() => onSelect(item.ai_task_id, true)}>
                <span className={`proc-queue-status ${item.stale ? "stale" : item.status.toLowerCase()}`}>{item.stale ? "已过期" : STATUS_LABELS[item.status]}</span>
                <strong>{owner?.title || item.business_id}</strong>
                <small>{owner?.reference || item.trace_id}</small>
                <span className="proc-center-list-meta"><span>{item.current_step ? STEP_LABELS[item.current_step] : "等待步骤"}</span><time dateTime={item.updated_at}>{timeText(item.updated_at)}</time></span>
              </button>
            );
          }) : null}
          {!loading && !error && !filtered.length ? <div className="proc-center-empty"><CheckCircle2 size={22} />当前筛选没有 AI 任务</div> : null}
        </section>

        <section className="proc-center-detail" aria-label="AI 任务详情">
          {detailQuery.isPending && selectedId ? <div className="proc-center-empty"><LoaderCircle className="spin" size={22} />正在读取任务详情</div> : null}
          {detailQuery.isError ? <div className="proc-center-empty danger" role="alert"><AlertTriangle size={22} />{errorText(detailQuery.error)}</div> : null}
          {!selectedId && !detailQuery.isPending ? <div className="proc-center-empty"><Bot size={25} />选择一个 AI 任务查看执行详情</div> : null}
          {detail ? (
            <div className="proc-ai-detail">
              <header className="proc-detail-head">
                <div><span>{request?.reference || detail.business_id}</span><h2>{request?.title || "采购 AI 任务"}</h2><p>AI {STATUS_LABELS[detail.status]} · 采购 {request?.status || "-"}</p></div>
                <button className="proc-button secondary" type="button" onClick={() => onOpenTask(detail.business_id)}>采购详情<ArrowRight size={15} /></button>
              </header>

              <div className="proc-detail-facts">
                <span><small>任务类型</small><strong>报价分析</strong></span>
                <span><small>负责人</small><strong>{detail.assignee || "未分配"}</strong></span>
                <span><small>总耗时</small><strong>{elapsed(detail.started_at, detail.finished_at)}</strong></span>
                <span><small>尝试次数</small><strong>{detail.retry_count + 1} / {(detail.max_retries ?? 3) + 1}</strong></span>
              </div>

              <section className={`proc-ai-state-panel ${detail.status.toLowerCase()}`}>
                <div><span>{active ? <LoaderCircle className="spin" size={17} /> : detail.status === "FAILED" ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}</span><div><strong>{detail.stale ? "结果已过期" : STATUS_LABELS[detail.status]}</strong><small>{detail.error_message || (detail.current_step ? STEP_LABELS[detail.current_step] : "状态已持久化")}</small></div><b>{Math.round(Number(detail.progress) * 100)}%</b></div>
                <span className="proc-ai-progress"><i style={{ width: `${Math.round(Number(detail.progress) * 100)}%` }} /></span>
                {detail.error_code || detail.stale_reason ? <p role="alert">{detail.error_category ? `${detail.error_category} · ` : ""}{detail.error_code || detail.stale_reason}</p> : null}
                <footer>
                  <button className="proc-button secondary" type="button" disabled={!retryAllowed || busy !== null} title={!retryAllowed ? "仅可重试未过期的可重试失败任务" : "重试任务"} onClick={() => void runAction("retry")}>{busy === "retry" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重试</button>
                  <button className="proc-button secondary danger-text" type="button" disabled={!cancelAllowed || busy !== null} onClick={() => {
                    if (!confirmCancel) { setConfirmCancel(true); return; }
                    void runAction("cancel");
                  }}>{busy === "cancel" ? <LoaderCircle className="spin" size={15} /> : confirmCancel ? <AlertTriangle size={15} /> : <Ban size={15} />}{confirmCancel ? "再次点击确认取消" : "取消"}</button>
                </footer>
                {actionError ? <p className="proc-inline-error" role="alert">{actionError}</p> : null}
              </section>

              <section className="proc-detail-section">
                <header><div><Timer size={16} /><h3>执行步骤</h3></div><span>{detail.records.length} 条记录</span></header>
                {detail.records.length ? <ol className="proc-step-timeline">{detail.records.map((record) => (
                  <li className={record.status.toLowerCase()} key={record.record_id}>
                    <span>{record.status === "SUCCEEDED" ? <CheckCircle2 size={14} /> : record.status === "FAILED" ? <AlertTriangle size={14} /> : <LoaderCircle size={14} />}</span>
                    <div><strong>{STEP_LABELS[record.step]}</strong><small>{record.summary || record.error_message || record.status}</small></div>
                    <em>第 {record.attempt} 次</em><time dateTime={record.created_at}>{record.duration_ms != null ? `${record.duration_ms} ms` : timeText(record.created_at)}</time>
                  </li>
                ))}</ol> : <p className="proc-detail-empty">任务仍在等待调度，尚无步骤记录。</p>}
              </section>

              <section className="proc-detail-section">
                <header><div><FileCode2 size={16} /><h3>分析结果</h3></div>{detail.result ? <span>{detail.result.provider || "offline"} / {detail.result.model || "deterministic"}</span> : null}</header>
                {detail.result ? <>
                  <div className="proc-result-summary"><Bot size={17} /><p>{String(detail.result.structured_result.summary || "结构化 AI 结果已持久化")}</p></div>
                  <div className="proc-result-meta">
                    <span><small>Prompt</small><strong>{detail.result.prompt_version}</strong></span>
                    <span><small>Parser</small><strong>{detail.result.parser_version || "-"}</strong></span>
                    <span><small>输入指纹</small><code title={detail.result.input_sha256}>{detail.result.input_sha256.slice(0, 16)}</code></span>
                    <span><small>结果指纹</small><code title={detail.result.result_sha256}>{detail.result.result_sha256.slice(0, 16)}</code></span>
                  </div>
                  <div className="proc-result-json">
                    <details open><summary>结构化结果</summary><pre>{jsonText(detail.result.structured_result)}</pre></details>
                    <details><summary>原始结果</summary><pre>{jsonText(detail.result.raw_result)}</pre></details>
                  </div>
                  <div className="proc-source-list"><header><Fingerprint size={15} /><strong>来源 Artifact</strong><span>{detail.result.sources.length} 项</span></header>{detail.result.sources.length ? detail.result.sources.map((source, index) => (
                    <a href={`/api/artifacts/${source.artifact_id}/raw`} target="_blank" rel="noreferrer" key={`${source.artifact_id}-${index}`}>
                      <span><strong>{source.locator || "原始报价"}</strong><small>{source.excerpt || "查看原件"}</small></span><em>{Math.round(source.confidence * 100)}%</em><ExternalLink size={14} />
                    </a>
                  )) : <p>本次结果没有可展示的来源。</p>}</div>
                </> : <p className="proc-detail-empty">任务完成后将在此显示结构化结果、原始结果、模型版本和来源。</p>}
              </section>

              <section className="proc-trace-strip"><span><Fingerprint size={14} />Trace <code title={detail.trace_id}>{detail.trace_id}</code></span><span><Clock3 size={14} />Operation <code title={detail.operation_id || "-"}>{detail.operation_id || "-"}</code></span></section>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
