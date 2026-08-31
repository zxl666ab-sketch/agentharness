import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
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
import {
  Button,
  CenterPage,
  CountBadge,
  EmptyState,
  ErrorState,
  Fact,
  ListRow,
  MasterDetail,
  PageHeader,
  StatusPill,
} from "../components/ui";
import { aiStepInFlight, staleReasonLabel, statusLabel } from "./viewModel";
import { humanizeEngineError } from "./engineErrors";
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

const STATUS_TONES: Record<AiTaskStatus, string> = {
  PENDING: "neutral",
  DISPATCHING: "info",
  RUNNING: "info",
  SUCCEEDED: "success",
  FAILED: "danger",
  RETRYING: "warning",
  CANCELLED: "neutral",
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
    <CenterPage
      header={
        <PageHeader
          icon={<Bot size={18} />}
          eyebrow="执行队列"
          title="AI 任务中心"
          subtitle="AI 任务状态与采购任务状态相互独立；失败任务可重试或人工干预"
          aside={<CountBadge tone={tasks.some((item) => item.status === "FAILED" || item.stale) ? "danger" : "neutral"}>
            {tasks.filter((item) => item.status === "FAILED" || item.stale).length} 个异常
          </CountBadge>}
        />
      }
      toolbar={
        <div className="proc-action-bar is-filters">
          <label className="proc-search">
            <Search size={15} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="采购编号、任务或 Trace" aria-label="搜索 AI 任务" />
          </label>
          <label className="proc-filter">
            <span>状态</span>
            <select className="proc-select" aria-label="AI 任务状态筛选" value={status} onChange={(event) => setStatus(event.target.value as AiTaskStatus | "ALL")}>
              <option value="ALL">全部状态</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label className="proc-filter">
            <span>负责人</span>
            <select className="proc-select" aria-label="AI 任务负责人筛选" value={assignee} onChange={(event) => setAssignee(event.target.value)}>
              <option value="ALL">全部负责人</option>
              {assignees.map((value) => <option value={value} key={value}>{value}</option>)}
            </select>
          </label>
          <label className="proc-filter">
            <span>时间</span>
            <select className="proc-select" value={time} aria-label="AI 任务时间范围" onChange={(event) => setTime(event.target.value as TimeFilter)}>
              <option value="all">全部时间</option>
              <option value="day">最近 24 小时</option>
              <option value="week">最近 7 天</option>
              <option value="month">最近 30 天</option>
            </select>
          </label>
        </div>
      }
    >
      <MasterDetail
        list={
          <>
            <header className="proc-master-list-head">
              <strong>{filtered.length} 个任务</strong>
              <small>点击行查看详情</small>
            </header>
            {loading ? <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取任务</div> : null}
            {error ? <ErrorState title="AI 任务加载失败" detail={error} /> : null}
            {!loading && !error ? filtered.map((item) => {
              const owner = requestMap.get(item.business_id);
              return (
                <ListRow key={item.ai_task_id} selected={selectedId === item.ai_task_id} onClick={() => onSelect(item.ai_task_id, true)}>
                  <span className="proc-list-row-head">
                    <StatusPill tone={item.stale ? "warning" : STATUS_TONES[item.status]} size="compact">{item.stale ? "已过期" : STATUS_LABELS[item.status]}</StatusPill>
                    <small className="mono">{item.current_step ? STEP_LABELS[item.current_step] : "等待步骤"}</small>
                  </span>
                  <strong className="proc-list-row-title">{owner?.title || item.business_id}</strong>
                  <span className="proc-list-row-meta">
                    <small>{owner?.reference || item.trace_id}</small>
                    <time className="mono" dateTime={item.updated_at}>{timeText(item.updated_at)}</time>
                  </span>
                </ListRow>
              );
            }) : null}
            {!loading && !error && !filtered.length ? (
              <EmptyState variant="inline" icon={<CheckCircle2 size={22} />} title="当前筛选没有 AI 任务" hint="调整状态、负责人或时间范围后重试。" />
            ) : null}
          </>
        }
        detail={
          <>
            {detailQuery.isPending && selectedId ? <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在读取任务详情</div> : null}
            {detailQuery.isError ? <ErrorState title="任务详情加载失败" detail={errorText(detailQuery.error)} onRetry={() => void detailQuery.refetch()} /> : null}
            {!selectedId ? (
              <EmptyState variant="inline" icon={<Bot size={22} />} title="选择一个 AI 任务" hint="查看执行步骤、结果版本与来源证据。" />
            ) : null}
            {detail ? (
              <>
                <header className="proc-detail-head">
                  <div>
                    <small className="mono proc-detail-ref">{request?.reference || detail.business_id}</small>
                    <h2>{request?.title || "采购 AI 任务"}</h2>
                    <p>AI {STATUS_LABELS[detail.status]} · 采购 {request ? statusLabel(request.status) : "-"}</p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => onOpenTask(detail.business_id)}>采购详情</Button>
                </header>

                <div className="proc-fact-grid">
                  <Fact label="任务类型">报价分析</Fact>
                  <Fact label="负责人">{detail.assignee || "未分配"}</Fact>
                  <Fact label="总耗时" mono>{elapsed(detail.started_at, detail.finished_at)}</Fact>
                  <Fact label="尝试次数" mono>{detail.retry_count + 1} / {(detail.max_retries ?? 3) + 1}</Fact>
                </div>

                <section className={`proc-ai-state-panel is-${detail.status.toLowerCase()}${detail.stale ? " is-stale" : ""}`}>
                  <div className="proc-ai-state-head">
                    <span className="proc-ai-state-icon">
                      {active ? <LoaderCircle className="spin" size={17} /> : detail.status === "FAILED" ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
                    </span>
                    <div>
                      <strong>{detail.stale ? "结果已过期" : STATUS_LABELS[detail.status]}</strong>
                      <small>{detail.error_message || (detail.current_step ? STEP_LABELS[detail.current_step] : "状态已持久化")}</small>
                    </div>
                    <b className="tnum">{Math.round(Number(detail.progress) * 100)}%</b>
                  </div>
                  <span className="proc-ai-progress" role="progressbar" aria-valuenow={Math.round(Number(detail.progress) * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`AI 分析进度 ${Math.round(Number(detail.progress) * 100)}%`}>
                    <i style={{ width: `${Math.round(Number(detail.progress) * 100)}%` }} />
                  </span>
                  {detail.error_code || detail.stale_reason ? (
                    <p className={`proc-ai-state-error${detail.stale ? " is-stale" : ""}`} role="alert" title={detail.error_code || detail.stale_reason || undefined}>
                      {detail.stale
                        ? staleReasonLabel(detail.stale_reason || detail.error_code)
                        : `${detail.error_category ? `${detail.error_category} · ` : ""}${humanizeEngineError(detail.error_code) || detail.error_code}`}
                    </p>
                  ) : null}
                  {detail.stale ? (
                    <p className="proc-ai-stale-hint">
                      本条结果保留为历史证据；重试与取消已禁用（终态且输入已变化）。
                      <button type="button" className="proc-link-button" onClick={() => onOpenTask(detail.business_id)}>
                        前往采购任务重新发起比价 <ExternalLink size={12} />
                      </button>
                    </p>
                  ) : null}
                  <footer className="proc-ai-state-actions">
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<RefreshCw size={15} />}
                      disabled={!retryAllowed}
                      loading={busy === "retry"}
                      title={retryAllowed ? "重试任务" : detail.stale ? "采购输入已变化，请启动新的分析" : "仅可重试未过期的可重试失败任务"}
                      onClick={() => void runAction("retry")}
                    >
                      重试
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={confirmCancel ? <AlertTriangle size={15} /> : <Ban size={15} />}
                      disabled={!cancelAllowed}
                      loading={busy === "cancel"}
                      title={cancelAllowed ? "取消 AI 任务" : detail.stale ? "任务已终态且结果已过期，无可取消的执行" : "任务已进入终态，无可取消的执行"}
                      onClick={() => {
                        if (!confirmCancel) { setConfirmCancel(true); return; }
                        void runAction("cancel");
                      }}
                    >
                      {confirmCancel ? "再次点击确认取消" : "取消"}
                    </Button>
                  </footer>
                  {actionError ? <p className="proc-inline-error" role="alert">{actionError}</p> : null}
                </section>

                <section className="proc-detail-section">
                  <header>
                    <h3><Timer size={15} /> 执行步骤</h3>
                    <small>{detail.records.length} 条记录</small>
                  </header>
                  {detail.records.length ? (
                    <ol className="proc-step-timeline">
                      {detail.records.map((record) => {
                        // 只有任务本身仍在推进时，未结束的步骤才是"进行中"。
                        // 任务已终态却留下未结束步骤（历史数据/步骤事件缺失），必须按已结束渲染，否则转圈永不停止。
                        const openStep = record.status === "PENDING" || record.status === "RUNNING";
                        const inFlight = aiStepInFlight(detail.status, record.status);
                        return (
                          <li className={`is-${record.status.toLowerCase()}`} key={record.record_id}>
                            <span className="proc-step-icon">
                              {record.status === "SUCCEEDED" ? <CheckCircle2 size={14} /> : record.status === "FAILED" ? <AlertTriangle size={14} /> : inFlight ? <LoaderCircle size={14} className="spin" /> : <span title={openStep ? "任务已进入终态，该步骤未再推进" : undefined}><Clock3 size={14} /></span>}
                            </span>
                            <span className="proc-step-copy">
                              <strong>{STEP_LABELS[record.step]}</strong>
                              <small>{record.summary || record.error_message || record.status}</small>
                            </span>
                            <span className="proc-step-when mono">
                              <em>第 {record.attempt} 次</em>
                              <time dateTime={record.created_at}>{record.duration_ms != null ? `${record.duration_ms} ms` : timeText(record.created_at)}</time>
                            </span>
                          </li>
                        );
                      })}
                    </ol>
                  ) : <p className="proc-detail-empty">任务仍在等待调度，尚无步骤记录。</p>}
                </section>

                <section className="proc-detail-section">
                  <header>
                    <h3><FileCode2 size={15} /> 分析结果</h3>
                    {detail.result ? <small className="mono">{detail.result.provider || "offline"} / {detail.result.model || "deterministic"}</small> : null}
                  </header>
                  {detail.result ? (
                    <>
                      <div className="proc-result-summary">
                        <Bot size={16} />
                        <p>{String(detail.result.structured_result.summary || "结构化 AI 结果已持久化")}</p>
                      </div>
                      <div className="proc-fact-grid is-4">
                        <Fact label="Prompt" mono>{detail.result.prompt_version}</Fact>
                        <Fact label="Parser" mono>{detail.result.parser_version || "-"}</Fact>
                        <Fact label="输入指纹" mono title={detail.result.input_sha256}>{detail.result.input_sha256.slice(0, 16)}</Fact>
                        <Fact label="结果指纹" mono title={detail.result.result_sha256}>{detail.result.result_sha256.slice(0, 16)}</Fact>
                      </div>
                      <div className="proc-result-jsons">
                        <details open>
                          <summary>结构化结果</summary>
                          <pre>{jsonText(detail.result.structured_result)}</pre>
                        </details>
                        <details>
                          <summary>原始结果</summary>
                          <pre>{jsonText(detail.result.raw_result)}</pre>
                        </details>
                      </div>
                      <div className="proc-source-list">
                        <header>
                          <h4><Fingerprint size={14} /> 来源 Artifact</h4>
                          <small>{detail.result.sources.length} 项</small>
                        </header>
                        {detail.result.sources.length ? detail.result.sources.map((source, index) => (
                          <a href={`/api/artifacts/${source.artifact_id}/raw`} target="_blank" rel="noreferrer" key={`${source.artifact_id}-${index}`}>
                            <span>
                              <strong>{source.locator || "原始报价"}</strong>
                              <small>{source.excerpt || "查看原件"}</small>
                            </span>
                            <span className="proc-source-confidence mono">{Math.round(source.confidence * 100)}%</span>
                            <ExternalLink size={14} />
                          </a>
                        )) : <p className="proc-muted">本次结果没有可展示的来源。</p>}
                      </div>
                    </>
                  ) : <p className="proc-detail-empty">任务完成后将在此显示结构化结果、原始结果、模型版本和来源。</p>}
                </section>

                <section className="proc-trace-strip">
                  <span><Fingerprint size={14} /> Trace <code title={detail.trace_id}>{detail.trace_id}</code></span>
                  <span><Clock3 size={14} /> Operation <code title={detail.operation_id || "-"}>{detail.operation_id || "-"}</code></span>
                </section>
              </>
            ) : null}
          </>
        }
      />
    </CenterPage>
  );
}
