import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FilePlus2,
  LoaderCircle,
  RefreshCw,
  ScrollText,
} from "lucide-react";
import { useState } from "react";

import type { AiTaskDetail, AiTaskStatus } from "./types";

type Props = {
  task: AiTaskDetail;
  busy: "retry" | "cancel" | null;
  error?: string | null;
  onRetry: () => Promise<void>;
  onCancel: () => Promise<void>;
  onSupplement: () => void;
};

const STATUS_LABELS: Record<AiTaskStatus, string> = {
  PENDING: "等待调度",
  DISPATCHING: "正在投递",
  RUNNING: "正在分析",
  SUCCEEDED: "分析成功",
  FAILED: "分析失败",
  RETRYING: "等待重试",
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

function timeText(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function AiTaskRecovery({ task, busy, error, onRetry, onCancel, onSupplement }: Props) {
  const [logsOpen, setLogsOpen] = useState(false);
  const active = ["PENDING", "DISPATCHING", "RUNNING", "RETRYING"].includes(task.status);
  const failed = task.status === "FAILED";
  const retryDisabled = !failed || !task.retryable || task.stale || busy !== null;
  const retryTitle = task.stale
    ? "采购输入已变化，请启动新的分析"
    : !task.retryable
      ? "该错误不可直接重试，请先补充或修正资料"
      : "重试 AI 任务";

  return (
    <section className={`proc-ai-recovery ${failed ? "failed" : active ? "running" : "terminal"}`} aria-label="AI 任务状态">
      <div className="proc-ai-recovery-summary">
        <span className="proc-ai-recovery-icon">
          {failed ? <AlertTriangle size={17} /> : active ? <LoaderCircle className="spin" size={17} /> : <CheckCircle2 size={17} />}
        </span>
        <div>
          <strong>{STATUS_LABELS[task.status]}</strong>
          <span>{task.error_message || (task.current_step ? STEP_LABELS[task.current_step] : "AI 任务状态已持久化")}</span>
        </div>
        <span className="proc-ai-progress" aria-label={`AI 分析进度 ${Math.round(Number(task.progress) * 100)}%`}>
          <i style={{ width: `${Math.round(Number(task.progress) * 100)}%` }} />
        </span>
        <small>第 {task.retry_count + 1} 次尝试</small>
      </div>

      <div className="proc-ai-recovery-actions">
        <button className="proc-button secondary" type="button" disabled={retryDisabled} title={retryTitle} onClick={() => void onRetry()}>
          {busy === "retry" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重试任务
        </button>
        <button className="proc-button secondary" type="button" onClick={onSupplement}>
          <FilePlus2 size={15} />补充资料
        </button>
        {active ? (
          <button className="proc-button secondary danger" type="button" disabled={busy !== null} onClick={() => void onCancel()}>
            {busy === "cancel" ? <LoaderCircle className="spin" size={15} /> : <Ban size={15} />}取消任务
          </button>
        ) : null}
        <button className="proc-button ghost" type="button" aria-expanded={logsOpen} onClick={() => setLogsOpen((value) => !value)}>
          <ScrollText size={15} />查看日志{logsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {error ? <p className="proc-ai-recovery-error" role="alert">{error}</p> : null}
      {logsOpen ? (
        <div className="proc-ai-log" role="region" aria-label="AI 任务步骤日志">
          <header>
            <span>Trace <code title={task.trace_id}>{task.trace_id}</code></span>
            <span>Operation <code title={task.operation_id || "-"}>{task.operation_id || "-"}</code></span>
          </header>
          {task.records.length ? (
            <ol>
              {task.records.map((record) => (
                <li key={record.record_id} className={record.status.toLowerCase()}>
                  <span>{record.status === "SUCCEEDED" ? <CheckCircle2 size={14} /> : record.status === "FAILED" ? <AlertTriangle size={14} /> : <LoaderCircle size={14} />}</span>
                  <div><strong>{STEP_LABELS[record.step]}</strong><small>{record.summary || record.error_message || record.status}</small></div>
                  <time dateTime={record.created_at}>{timeText(record.created_at)}</time>
                </li>
              ))}
            </ol>
          ) : <p>尚无步骤记录，任务仍在等待调度。</p>}
        </div>
      ) : null}
    </section>
  );
}
