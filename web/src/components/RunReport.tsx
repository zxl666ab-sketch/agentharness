import {
  AlertTriangle,
  Box,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Fingerprint,
  Gauge,
  History,
  ListChecks,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  XCircle,
} from "lucide-react";

import type { RunReport as RunReportData } from "../api/client";
import { eventLabel } from "../viewModel";
import { EffectBadge } from "./EffectBadge";

type Props = {
  report: RunReportData | null;
  loading?: boolean;
  error?: string | null;
};

function conclusionIcon(status: RunReportData["conclusion"]["status"]) {
  if (status === "passed") return <CheckCircle2 size={18} />;
  if (status === "failed") return <XCircle size={18} />;
  if (status === "needs_review") return <ShieldAlert size={18} />;
  if (status === "pending") return <Clock3 size={18} />;
  return <AlertTriangle size={18} />;
}

function actionLabel(action: string) {
  return {
    pass: "通过",
    retry: "重试",
    require_human: "需要人工处理",
    stop: "未通过",
    pending: "进行中",
  }[action] || action;
}

function statusText(status?: string | null) {
  if (!status) return "待处理";
  return {
    pending: "待处理",
    running: "进行中",
    waiting_approval: "等待审批",
    completed: "已完成",
    succeeded: "已成功",
    resolved: "已处理",
    expired: "已失效",
    failed: "失败",
    cancelled: "已停止",
    interrupted: "已中断",
    budget_stopped: "已停在安全边界",
    indeterminate: "结果待确认",
    allow_once: "已允许一次",
    allow_run: "本次运行已允许",
    deny: "已拒绝",
  }[status] || status;
}

function validatorLabel(validator?: string | null) {
  if (!validator) return "验收规则";
  return {
    output: "输出内容",
    procurement_deterministic_rules: "采购确定性规则",
  }[validator] || validator;
}

function toolLabel(tool?: string | null) {
  if (!tool) return "系统操作";
  return {
    "procurement.select_supplier": "选定供应商",
  }[tool] || tool;
}

function formatNumber(value: unknown): string {
  return typeof value === "number" ? new Intl.NumberFormat("zh-CN").format(value) : "0";
}

function formatBytes(value?: number | null): string {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function timestamp(value?: string | null): string {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN");
}

function JsonEvidence({ value }: { value: unknown }) {
  return <pre className="report-json">{JSON.stringify(value, null, 2)}</pre>;
}

export function RunReport({ report, loading = false, error = null }: Props) {
  if (loading && !report) {
    return (
      <section className="run-report report-loading" aria-label="结果证据报告">
        <Clock3 size={16} /><span>正在恢复结果证据…</span>
      </section>
    );
  }
  if (error && !report) {
    return (
      <section className="run-report report-error" aria-label="结果证据报告">
        <AlertTriangle size={16} /><span>{error}</span>
      </section>
    );
  }
  if (!report) return null;

  const { conclusion, verification, usage, source, convergence } = report;
  const validators = verification.policy?.validators || [];
  const cost = usage.estimated_cost_usd;
  const costStatus = usage.cost_status;
  const costLabel = costStatus === "estimated" && typeof cost === "number"
    ? ` · $${cost.toFixed(4)}`
    : costStatus === "unknown"
      ? " · 成本未知"
      : "";

  return (
    <section className={`run-report ${conclusion.status}`} aria-label="结果证据报告">
      <div className="report-heading">
        <div className="report-title">
          <ShieldCheck size={17} />
          <strong>结果与证据</strong>
        </div>
        <span className={`report-verdict ${conclusion.status}`}>
          {conclusionIcon(conclusion.status)}
          {conclusion.label}
        </span>
      </div>

      <p className="report-reason">{conclusion.reason}</p>
      <div className="report-provenance">
        <span title={report.evidence_sha256}>
          <Fingerprint size={13} />证据 {report.evidence_sha256.slice(0, 12)}
        </span>
        <span><History size={13} />{timestamp(report.as_of)}</span>
        <span>事件 #{source.max_global_seq}</span>
      </div>

      <div className="report-metrics">
        <span><ListChecks size={15} /><strong>{validators.length}</strong>验收项</span>
        <span><Terminal size={15} /><strong>{source.tool_count}</strong>工具调用</span>
        <span><ShieldCheck size={15} /><strong>{source.approval_count}</strong>审批</span>
        <span><Box size={15} /><strong>{source.artifact_count}</strong>证据文件</span>
        <span><Gauge size={15} /><strong>{formatNumber(usage.total_tokens)}</strong>Token</span>
      </div>

      {conclusion.status !== "passed" && verification.failure_reasons.length ? (
        <div className="report-failures" role="alert">
          <strong><AlertTriangle size={14} />失败原因</strong>
          <ul>
            {verification.failure_reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="report-sections">
        <details className="report-section" open>
          <summary>
            <span><ListChecks size={15} />验证证据</span>
            <span>{verification.configured ? `${verification.attempts.length} 次` : "未配置"}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body">
            {verification.policy ? (
              <div className="report-policy">
                <div>
                  <span>规则</span>
                  <strong>{validators.length}</strong>
                </div>
                <div>
                  <span>最多重试</span>
                  <strong>{verification.policy.max_retries || 0}</strong>
                </div>
                <div>
                  <span>耗尽后</span>
                  <strong>{statusText(verification.policy.on_exhausted || "failed")}</strong>
                </div>
              </div>
            ) : (
              <p className="report-empty">此运行没有验收策略。</p>
            )}
            {verification.attempts.map((attempt, index) => (
              <details
                className={`verification-attempt ${attempt.passed ? "passed" : "failed"}`}
                key={`${attempt.attempt}-${attempt.result_event_id || index}`}
                open={index === verification.attempts.length - 1}
              >
                <summary>
                  <span>第 {attempt.attempt + 1} 次</span>
                  <strong>{actionLabel(attempt.action)}</strong>
                  <span>{timestamp(attempt.finished_at || attempt.started_at)}</span>
                  <ChevronRight size={14} />
                </summary>
                <div className="attempt-evidence">
                  {attempt.failures.length ? (
                    <ul className="attempt-failures">
                      {attempt.failures.map((failure, failureIndex) => (
                        <li key={`${failure.error_code || "failure"}-${failureIndex}`}>
                          <strong>{validatorLabel(failure.validator)}</strong>
                          <span>{failure.message || failure.error_code || "验证失败"}</span>
                          {failure.recovery_hint ? <small>{failure.recovery_hint}</small> : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <JsonEvidence value={attempt.evidence} />
                </div>
              </details>
            ))}
          </div>
        </details>

        <details className="report-section">
          <summary>
            <span><Gauge size={15} />收敛指标</span>
            <span>{convergence ? `${convergence.model_turns} 回合` : "无"}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body">
            {convergence ? (
              <>
                <div className="report-policy">
                  <div><span>模型回合</span><strong>{formatNumber(convergence.model_turns)}</strong></div>
                  <div><span>工具调用</span><strong>{formatNumber(convergence.total_tool_calls)}</strong></div>
                  <div><span>重复调用</span><strong className={convergence.duplicate_calls ? "convergence-bad" : ""}>{convergence.duplicate_calls}</strong></div>
                  <div><span>越权调用</span><strong className={convergence.unauthorized_calls ? "convergence-bad" : ""}>{convergence.unauthorized_calls}</strong></div>
                </div>
                {Object.keys(convergence.tool_call_counts || {}).length ? (
                  <div className="report-policy">
                    {Object.entries(convergence.tool_call_counts || {}).map(([name, count]) => (
                      <div key={name}><span>{toolLabel(name)}</span><strong>{count}</strong></div>
                    ))}
                  </div>
                ) : null}
                <h3 className="report-subhead">工具调用理由</h3>
                {convergence.tool_reasons?.length ? convergence.tool_reasons.map((item, index) => (
                  <details className="audit-record" key={`${item.tool_name}-${item.step}-${index}`}>
                    <summary>
                      <span>{toolLabel(item.tool_name)}</span>
                      {typeof item.step === "number" ? <code>step {item.step}</code> : null}
                      <ChevronRight size={14} />
                    </summary>
                    <p className="report-reason">{item.reason || "无说明"}</p>
                  </details>
                )) : <p className="report-empty">没有记录到工具调用理由。</p>}
              </>
            ) : (
              <p className="report-empty">此运行没有收敛指标。</p>
            )}
          </div>
        </details>

        <details className="report-section">
          <summary>
            <span><Terminal size={15} />工具与审批</span>
            <span>{report.tools.length} / {report.approvals.length}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body report-audit-groups">
            <div>
              <h3>工具调用</h3>
              {report.tools.length ? report.tools.map((tool) => (
                <details className="audit-record" key={tool.id}>
                  <summary>
                    <span>{toolLabel(tool.tool_name)}</span>
                    <EffectBadge effect={tool.effect} />
                    <strong>{statusText(tool.status)}</strong>
                    <ChevronRight size={14} />
                  </summary>
                  <JsonEvidence value={tool} />
                </details>
              )) : <p className="report-empty">没有工具调用。</p>}
            </div>
            <div>
              <h3>审批记录</h3>
              {report.approvals.length ? report.approvals.map((approval) => (
                <details className="audit-record" key={approval.id}>
                  <summary>
                    <span>{toolLabel(approval.tool_name)}</span>
                    <EffectBadge effect={approval.effect} />
                    <strong>{statusText(approval.decision || approval.status)}</strong>
                    <ChevronRight size={14} />
                  </summary>
                  <JsonEvidence value={approval} />
                </details>
              )) : <p className="report-empty">没有审批记录。</p>}
            </div>
          </div>
        </details>

        <details className="report-section">
          <summary>
            <span><Box size={15} />证据文件</span>
            <span>{report.artifacts.length}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body">
            {report.artifacts.length ? (
              <div className="artifact-records">
                {report.artifacts.map((artifact) => (
                  <a
                    className="artifact-record"
                    href={`/api/artifacts/${artifact.id}`}
                    target="_blank"
                    rel="noreferrer"
                    key={artifact.id}
                  >
                    <span><Box size={14} /><strong>{artifact.summary || artifact.id}</strong></span>
                    <code>{artifact.sha256.slice(0, 16)}</code>
                    <small>{artifact.content_type || "application/octet-stream"} · {formatBytes(artifact.size_bytes)}</small>
                  </a>
                ))}
              </div>
            ) : (
              <p className="report-empty">没有关联的证据文件。</p>
            )}
          </div>
        </details>

        <details className="report-section">
          <summary>
            <span><Gauge size={15} />资源消耗</span>
            <span>{formatNumber(usage.total_tokens)} Token{costLabel}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body">
            <JsonEvidence value={{ steps: report.run.steps || 0, usage }} />
          </div>
        </details>

        <details className="report-section">
          <summary>
            <span><History size={15} />事件追踪</span>
            <span>{source.event_count}</span>
            <ChevronRight size={15} />
          </summary>
          <div className="report-section-body event-records">
            {report.events.map((event) => (
              <details className="audit-record" key={event.event_id}>
                <summary>
                  <code>#{event.run_seq}</code>
                  <span>{eventLabel(event)}</span>
                  <small>{timestamp(event.timestamp)}</small>
                  <ChevronRight size={14} />
                </summary>
                <JsonEvidence value={event.payload} />
              </details>
            ))}
          </div>
          {report.events_truncated ? (
            <p className="report-empty">
              事件过多，仅显示最近 {report.events.length} 条（共 {report.events_total} 条）。
            </p>
          ) : null}
        </details>
      </div>
    </section>
  );
}
