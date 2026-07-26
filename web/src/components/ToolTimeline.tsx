import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  ExternalLink,
  Play,
  RotateCcw,
  SkipForward,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import type { ToolInvocationRow, ToolRecoveryDecision } from "../api/client";
import { EffectBadge } from "./EffectBadge";

type Props = {
  invocations: ToolInvocationRow[];
  onResolve?: (
    invocation: ToolInvocationRow,
    decision: ToolRecoveryDecision
  ) => Promise<void>;
};

const SUCCESS = new Set(["succeeded"]);
const FAILURE = new Set(["failed", "cancelled"]);
const ACTIVE = new Set(["received", "validated", "approved", "running"]);

function icon(status: string) {
  if (SUCCESS.has(status)) return <CheckCircle2 size={16} />;
  if (FAILURE.has(status)) return <XCircle size={16} />;
  if (status === "indeterminate") return <AlertTriangle size={16} />;
  if (status === "waiting_approval") return <Clock3 size={16} />;
  return <CircleDashed size={16} />;
}

function summary(argumentsValue: Record<string, unknown>) {
  const preferred = ["action", "path", "url", "command", "query", "selector"];
  for (const key of preferred) {
    const value = argumentsValue[key];
    if (value !== undefined && value !== "") return `${key}=${String(value).slice(0, 120)}`;
  }
  return Object.keys(argumentsValue).slice(0, 3).join(", ") || "无参数";
}

export function ToolTimeline({ invocations, onResolve }: Props) {
  const [resolving, setResolving] = useState<string | null>(null);
  const [resolutionError, setResolutionError] = useState<{
    invocationId: string;
    message: string;
  } | null>(null);
  if (!invocations.length) return null;
  const resolve = async (
    invocation: ToolInvocationRow,
    decision: ToolRecoveryDecision
  ) => {
    if (!onResolve) return;
    setResolving(invocation.id);
    setResolutionError(null);
    try {
      await onResolve(invocation, decision);
    } catch (error) {
      setResolutionError({
        invocationId: invocation.id,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setResolving(null);
    }
  };
  const done = invocations.filter((invocation) =>
    SUCCESS.has(invocation.status) || FAILURE.has(invocation.status)
  ).length;
  return (
    <section className="run-steps" aria-label="运行步骤">
      <div className="run-steps-heading">
        <span>运行步骤</span>
        <span className="run-steps-count">{done}/{invocations.length}</span>
      </div>
      <div className="run-steps-list">
        {invocations.map((invocation) => {
          const result = invocation.result;
          const tone = SUCCESS.has(invocation.status)
            ? "success"
            : FAILURE.has(invocation.status)
              ? "danger"
              : invocation.status === "indeterminate"
                ? "warning"
                : "active";
          return (
            <details className={`run-step ${tone}`} key={invocation.id}>
              <summary>
                <span className="step-state">{icon(invocation.status)}</span>
                <EffectBadge effect={invocation.effect} />
                <span className="step-copy">
                  <strong>{invocation.tool_name}</strong>
                  <code>{summary(invocation.arguments)}</code>
                </span>
                <span className="step-meta">
                  {invocation.attempt_count > 1 ? (
                    <span className="step-retries" title={`重试 ${invocation.attempt_count} 次`}>
                      <RotateCcw size={12} />{invocation.attempt_count}
                    </span>
                  ) : null}
                  {result?.duration_ms != null
                    ? `${Math.round(result.duration_ms)}ms`
                    : ACTIVE.has(invocation.status)
                      ? "执行中"
                      : invocation.status === "waiting_approval"
                        ? "等待批准"
                        : null}
                </span>
                <ChevronRight className="step-chevron" size={15} />
              </summary>
              <div className="step-detail">
                <div className="step-detail-grid">
                  <span>Effect<strong>{invocation.effect}</strong></span>
                  <span>恢复策略<strong>{invocation.replay_policy}</strong></span>
                  <span>参数哈希<strong>{invocation.arguments_sha256.slice(0, 12)}</strong></span>
                </div>
                {result?.content ? <pre>{result.content}</pre> : null}
                {result?.recovery_hint ? (
                  <p className="step-hint">{result.recovery_hint}</p>
                ) : null}
                {result?.artifact_id ? (
                  <a href={`/api/artifacts/${result.artifact_id}`} target="_blank" rel="noreferrer">
                    <ExternalLink size={13} />查看 Artifact
                  </a>
                ) : null}
                {invocation.status === "indeterminate" && onResolve ? (
                  <div className="step-recovery">
                    <p className="step-recovery-why">
                      运行中断时这个操作可能已产生外部影响，请核对后选择：
                    </p>
                    <div className="step-recovery-actions">
                      <button
                        type="button"
                        disabled={resolving !== null}
                        onClick={() => void resolve(invocation, "mark_succeeded")}
                      >
                        <CheckCircle2 size={14} /><span>确认已完成</span>
                      </button>
                      <button
                        type="button"
                        disabled={resolving !== null}
                        onClick={() => void resolve(invocation, "skip")}
                      >
                        <SkipForward size={14} /><span>跳过</span>
                      </button>
                      <button
                        type="button"
                        disabled={resolving !== null}
                        onClick={() => void resolve(invocation, "retry")}
                      >
                        <Play size={14} /><span>重新执行</span>
                      </button>
                    </div>
                  </div>
                ) : null}
                {resolutionError?.invocationId === invocation.id &&
                invocation.status === "indeterminate" ? (
                  <p className="step-error" role="alert">{resolutionError.message}</p>
                ) : null}
              </div>
            </details>
          );
        })}
      </div>
    </section>
  );
}
