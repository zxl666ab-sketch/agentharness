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
  Wrench,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import type { ToolInvocationRow, ToolRecoveryDecision } from "../api/client";

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
  if (SUCCESS.has(status)) return <CheckCircle2 size={15} />;
  if (FAILURE.has(status)) return <XCircle size={15} />;
  if (status === "indeterminate") return <AlertTriangle size={15} />;
  if (status === "waiting_approval") return <Clock3 size={15} />;
  return <CircleDashed size={15} />;
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
  return (
    <section className="tool-timeline" aria-label="工具调用">
      <div className="tool-timeline-heading"><Wrench size={14} /><span>工具调用</span></div>
      <div className="tool-invocation-list">
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
            <details className={`tool-invocation ${tone}`} key={invocation.id}>
              <summary>
                <span className="tool-state">{icon(invocation.status)}</span>
                <span className="tool-copy">
                  <strong>{invocation.tool_name}</strong>
                  <code>{summary(invocation.arguments)}</code>
                </span>
                <span className="tool-meta">
                  {invocation.attempt_count > 1 ? <><RotateCcw size={12} />{invocation.attempt_count}</> : null}
                  {result?.duration_ms != null ? `${Math.round(result.duration_ms)}ms` : invocation.status}
                </span>
                <ChevronRight className="tool-chevron" size={15} />
              </summary>
              <div className="tool-detail">
                <div className="tool-detail-grid">
                  <span>Effect<strong>{invocation.effect}</strong></span>
                  <span>恢复策略<strong>{invocation.replay_policy}</strong></span>
                  <span>参数哈希<strong>{invocation.arguments_sha256.slice(0, 12)}</strong></span>
                </div>
                {result?.content ? <pre>{result.content}</pre> : null}
                {result?.recovery_hint ? <p>{result.recovery_hint}</p> : null}
                {result?.artifact_id ? (
                  <a href={`/api/artifacts/${result.artifact_id}`} target="_blank" rel="noreferrer">
                    <ExternalLink size={13} />查看 Artifact
                  </a>
                ) : null}
                {invocation.status === "indeterminate" && onResolve ? (
                  <div className="tool-recovery-actions">
                    <button
                      type="button"
                      disabled={resolving !== null}
                      onClick={() => void resolve(invocation, "mark_succeeded")}
                    >
                      <CheckCircle2 size={13} /><span>确认已完成</span>
                    </button>
                    <button
                      type="button"
                      disabled={resolving !== null}
                      onClick={() => void resolve(invocation, "skip")}
                    >
                      <SkipForward size={13} /><span>跳过</span>
                    </button>
                    <button
                      type="button"
                      disabled={resolving !== null}
                      onClick={() => void resolve(invocation, "retry")}
                    >
                      <Play size={13} /><span>重新执行</span>
                    </button>
                  </div>
                ) : null}
                {resolutionError?.invocationId === invocation.id &&
                invocation.status === "indeterminate" ? (
                  <p role="alert">{resolutionError.message}</p>
                ) : null}
                {ACTIVE.has(invocation.status) ? <small>执行中</small> : null}
              </div>
            </details>
          );
        })}
      </div>
    </section>
  );
}
