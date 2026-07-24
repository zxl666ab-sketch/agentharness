import { useState } from "react";
import {
  api,
  type ContextManifestRow,
  type RunRow,
  type TranscriptTurn,
} from "../api/client";

export type RunEvaluation = {
  schema_version?: number;
  passed?: boolean;
  score?: number;
  reasons?: string[];
  grader?: string;
  graded_at?: string;
  latency_s?: number;
  assertion_summary?: unknown;
  mode?: "deterministic" | "ai";
  evaluation_mode?: "scored" | "health_only" | "unscored";
  dimensions?: Record<string, { score?: number; reason?: string; applicable?: boolean }>;
  confidence?: number;
  failure_category?: string;
  evidence?: string[];
  improvements?: string[];
};

type Props = {
  run: RunRow;
  transcript: TranscriptTurn[];
  contexts?: ContextManifestRow[];
  onRunUpdated?: (run: RunRow) => void;
  onEvaluationComplete?: (
    run: RunRow,
    evaluation: Record<string, unknown>,
    turn?: TranscriptTurn
  ) => void;
};

export function ContextInspector({
  run,
  transcript,
  contexts = [],
  onRunUpdated,
  onEvaluationComplete,
}: Props) {
  const [aiEvaluation, setAiEvaluation] = useState(readAiEvaluationPreference);
  const [turnEvaluations, setTurnEvaluations] = useState<Record<string, RunEvaluation>>({});
  const [gradingTurn, setGradingTurn] = useState<string | null>(null);
  const [turnGradeErrors, setTurnGradeErrors] = useState<Record<string, string>>({});

  const updateAiEvaluation = (enabled: boolean) => {
    setAiEvaluation(enabled);
    try {
      window.localStorage.setItem("agentharness.ai-evaluation", enabled ? "1" : "0");
    } catch {
      // Preference persistence is best-effort.
    }
  };

  const gradeTurn = async (runId: string) => {
    setGradingTurn(runId);
    setTurnGradeErrors((current) => ({ ...current, [runId]: "" }));
    try {
      const result = await api.gradeRun(runId, aiEvaluation ? "ai" : "deterministic");
      setTurnEvaluations((current) => ({ ...current, [runId]: result.eval as RunEvaluation }));
      onRunUpdated?.(result.run);
      onEvaluationComplete?.(
        result.run,
        result.eval,
        transcript.find((turn) => turn.run_id === runId)
      );
    } catch (error) {
      setTurnGradeErrors((current) => ({
        ...current,
        [runId]: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      setGradingTurn(null);
    }
  };

  return (
    <section className="inspect-section context-section">
      <div className="context-heading">
        <div>
          <span>上下文控制面</span>
          <strong>{contexts.length} 个模型轮次 · {transcript.length} 个对话轮次</strong>
        </div>
        <label className="ai-eval-toggle">
          <input
            type="checkbox"
            checked={aiEvaluation}
            onChange={(event) => updateAiEvaluation(event.target.checked)}
          />
          <span aria-hidden="true" />
          <div>
            <strong>智能评测</strong>
            <small>{aiEvaluation ? "评价回答质量，会调用当前模型" : "仅规则与运行健康，不调用模型"}</small>
          </div>
        </label>
      </div>
      <ContextManifestView contexts={contexts} />
      <div className="context-subheading">
        <span>对话记录与逐轮评测</span>
        <strong>{transcript.length} 个轮次</strong>
      </div>
      {!transcript.length && <div className="muted-line context-empty">当前会话还没有可展示的对话。</div>}
      <div className="conversation-list">
        {transcript.map((turn, index) => {
          const evaluation = turnEvaluations[turn.run_id] || asRunEval(turn.evaluation);
          const isGrading = gradingTurn === turn.run_id;
          const terminal = ["completed", "failed", "cancelled", "interrupted"].includes(turn.status);
          return (
            <article className={`conversation-turn ${turn.run_id === run.id ? "current" : ""}`} key={turn.run_id}>
              <header className="turn-header">
                <div>
                  <span className="turn-index">第 {index + 1} 轮</span>
                  <span className={`status-text ${turn.status}`}>{statusLabel(turn.status)}</span>
                  {evaluation && <EvalBadge evaluation={evaluation} />}
                </div>
                <button
                  type="button"
                  className="turn-grade-button"
                  disabled={!terminal || isGrading}
                  onClick={() => void gradeTurn(turn.run_id)}
                  data-testid={`turn-grade-${turn.run_id}`}
                >
                  {isGrading ? "评测中…" : evaluation ? "重新评测" : "评测"}
                </button>
              </header>
              <div className="context-message user">
                <span>你</span>
                <p>{turn.user_content}</p>
              </div>
              <div className="context-message assistant">
                <span>助手</span>
                <p>{turn.assistant_content || turn.error || "无输出"}</p>
              </div>
              {evaluation && <TurnEvalDetails evaluation={evaluation} />}
              {turnGradeErrors[turn.run_id] && <div className="eval-error">{turnGradeErrors[turn.run_id]}</div>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

const CONTEXT_SECTIONS = [
  ["system", "系统提示"],
  ["workspace_rules", "工作区规则"],
  ["skills", "技能"],
  ["memories", "记忆"],
  ["messages", "对话消息"],
  ["tool_schemas", "工具规范"],
] as const;

function ContextManifestView({ contexts }: { contexts: ContextManifestRow[] }) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected =
    contexts.find((item) => `${item.run_id}:${item.model_turn}` === selectedKey) ||
    contexts.at(-1) ||
    null;

  if (!selected) {
    return (
      <div className="context-manifest-empty" data-testid="context-manifest-empty">
        此运行还没有模型上下文清单。清单会在每次真实模型服务调用前生成。
      </div>
    );
  }

  const percent = selected.budget_tokens > 0
    ? Math.min(100, Math.round((selected.total_tokens / selected.budget_tokens) * 100))
    : 0;
  return (
    <div className="context-manifest" data-testid="context-manifest">
      <div className="context-turn-picker" aria-label="模型上下文轮次">
        {contexts.map((manifest) => {
          const key = `${manifest.run_id}:${manifest.model_turn}`;
          return (
            <button
              type="button"
              key={`${key}:${manifest.event_id || manifest.global_seq || "manifest"}`}
              className={manifest === selected ? "active" : ""}
              onClick={() => setSelectedKey(key)}
              aria-pressed={manifest === selected}
            >
              模型轮次 {manifest.model_turn + 1}
            </button>
          );
        })}
      </div>
      <div className="context-budget-summary">
        <div>
          <span>上下文占用</span>
          <strong>{selected.total_tokens.toLocaleString()} / {selected.budget_tokens.toLocaleString()} 令牌</strong>
          <small>{percent}% · {tokenMethodLabel(selected.token_method)}{selected.compacted ? " · 已压缩或外置" : " · 未压缩"}</small>
        </div>
        <div className="context-budget-bar" aria-label={`上下文占用 ${percent}%`}>
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="context-fingerprint">
          <span>稳定前缀指纹</span>
          <code title={selected.prefix_fingerprint}>{selected.prefix_fingerprint || "-"}</code>
          {selected.artifact_id && <small>清单产物 · {selected.artifact_id.slice(0, 12)}</small>}
        </div>
      </div>
      <div className="context-sections-grid">
        {CONTEXT_SECTIONS.map(([section, label]) => {
          const items = selected.items.filter((item) => item.section === section);
          const includedTokens = items
            .filter((item) => item.included)
            .reduce((sum, item) => sum + item.token_estimate, 0);
          return (
            <section className="context-source-section" key={section} data-section={section}>
              <header>
                <strong>{label}</strong>
                <span>{includedTokens.toLocaleString()} 令牌 · 已纳入 {items.filter((item) => item.included).length}/{items.length}</span>
              </header>
              {!items.length && <div className="context-source-empty">本轮无来源</div>}
              {items.map((item, index) => (
                <article className={item.included ? "included" : "excluded"} key={`${item.source}:${index}`}>
                  <div>
                    <code title={item.source}>{item.source || "未知来源"}</code>
                    <span>{item.included ? "已纳入" : "已排除"}</span>
                    <span>{item.token_estimate.toLocaleString()} 令牌</span>
                    {item.compression !== "none" && <span>{compressionLabel(item.compression)}</span>}
                  </div>
                  <p>{contextReasonLabel(item.reason)}</p>
                  {item.preview && <small>{item.preview}</small>}
                  {item.artifact_id && <small>产物 · {item.artifact_id.slice(0, 12)}</small>}
                </article>
              ))}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function compressionLabel(value: string): string {
  return ({ summarized: "已摘要", externalized: "产物外置", excluded: "已剔除" } as Record<string, string>)[value] || value;
}

function tokenMethodLabel(value: string): string {
  return ({ heuristic: "启发式估算", estimate: "启发式估算", provider: "服务商计数", exact: "精确计数" } as Record<string, string>)[value] || value;
}

function contextReasonLabel(value: string): string {
  return ({
    "enabled for this run": "本次运行已启用",
    "required safety and run instructions": "必需的安全与运行指令",
    "workspace rule from root-to-cwd hierarchy": "从工作区根目录到当前目录继承的规则",
    "conversation history": "对话历史",
    "excluded: orphaned tool pair": "已排除：工具调用与结果不成对",
    "excluded by priority: older conversation history": "按优先级排除：较早的对话历史",
    "included with large tool result externalized": "已纳入；大型工具结果已外置为产物",
  } as Record<string, string>)[value] || value;
}

export function asRunEval(value: unknown): RunEvaluation | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const evaluation = value as RunEvaluation;
  return evaluation.schema_version === 1 ? evaluation : null;
}

function EvalBadge({ evaluation }: { evaluation: RunEvaluation }) {
  const healthOnly = isHealthOnly(evaluation);
  const score = typeof evaluation.score === "number" ? Math.round(evaluation.score * 100) : null;
  return (
    <span className={`turn-eval-badge ${evaluation.passed ? "ok" : "bad"}`}>
      {healthOnly ? "健康检查" : `${evaluation.mode === "ai" ? "AI" : "规则"} · ${score ?? "-"}`}
    </span>
  );
}

function TurnEvalDetails({ evaluation }: { evaluation: RunEvaluation }) {
  const healthOnly = isHealthOnly(evaluation);
  const dimensions = Object.entries(evaluation.dimensions || {}).filter(([, value]) => value?.applicable !== false);
  return (
    <div className="turn-eval-details" data-testid="turn-eval-details">
      <div className="turn-eval-result">
        <strong>{evaluation.passed ? "通过" : "未通过"}</strong>
        <span>{healthOnly ? "仅健康检查" : typeof evaluation.score === "number" ? `${Math.round(evaluation.score * 100)} / 100` : "未评分"}</span>
        <small>{evaluation.mode === "ai" ? "智能评测 + 硬规则" : healthOnly ? "不评价回答质量" : "确定性评测"}</small>
      </div>
      {!!dimensions.length && (
        <div className="turn-dimensions">
          {dimensions.map(([name, value]) => (
            <div key={name} title={value.reason || ""}>
              <span>{dimensionLabel(name)}</span>
              <strong>{typeof value.score === "number" ? Math.round(value.score * 100) : "-"}</strong>
            </div>
          ))}
        </div>
      )}
      {evaluation.failure_category && evaluation.failure_category !== "none" && <p>主要失败：{evaluationFailureLabel(evaluation.failure_category)}</p>}
      {!!evaluation.improvements?.length && <ul>{evaluation.improvements.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>}
    </div>
  );
}

function isHealthOnly(evaluation: RunEvaluation): boolean {
  return evaluation.mode !== "ai" && evaluation.evaluation_mode !== "scored";
}

function evaluationFailureLabel(value: string): string {
  return ({
    wrong_tool_selection: "工具选择错误",
    invalid_tool_arguments: "工具参数错误",
    duplicate_tool_call: "重复工具调用",
    retry_loop: "重试循环",
    missing_required_step: "缺少必要步骤",
    verification_missing: "缺少验证",
    context_drift: "上下文漂移",
    budget_exhaustion: "预算耗尽",
    provider_failure: "模型服务商故障",
    environment_failure: "环境故障",
    execution_or_assertion: "运行或硬规则失败",
    safety_permission: "安全或权限问题",
  } as Record<string, string>)[value] || value;
}

function dimensionLabel(name: string): string {
  return ({
    task_completion: "任务成功", correctness: "正确性", completeness: "完整交付",
    planning_recovery: "规划恢复", tool_use: "工具使用", execution_verification: "执行验证",
    efficiency: "效率", safety_control: "安全可控", user_experience: "沟通体验",
  } as Record<string, string>)[name] || name;
}

function readAiEvaluationPreference(): boolean {
  try {
    return window.localStorage.getItem("agentharness.ai-evaluation") === "1";
  } catch {
    return false;
  }
}

function statusLabel(status: string): string {
  return ({ completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断", running: "运行中", waiting_approval: "等待审批", require_human: "需要人工处理" } as Record<string, string>)[status] || status;
}
