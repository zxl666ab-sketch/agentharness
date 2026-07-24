import { useEffect, useMemo, useState } from "react";
import { Braces, FileClock, MessageSquareText } from "lucide-react";
import type {
  ApprovalRow,
  CheckpointRow,
  ContextManifestRow,
  EventRow,
  MessageRow,
  RunRow,
  ToolCallRow,
  TranscriptTurn,
} from "../api/client";
import { runStatusLabel } from "../runs/status";
import { formatOutputPreview, formatToolName, summarizeArgs } from "../trace/formatters";
import { api } from "../api/client";
import { ContextInspector, type RunEvaluation } from "./ContextInspector";
import { PanelState } from "./PanelState";

type Props = {
  run: RunRow | null;
  event: EventRow | null;
  tree: RunRow[];
  messages: MessageRow[];
  approvals: ApprovalRow[];
  checkpoint: CheckpointRow | null;
  transcript: TranscriptTurn[];
  contexts?: ContextManifestRow[];
  onSelectRun?: (id: string) => void;
  onRunUpdated?: (run: RunRow) => void;
  onEvaluationComplete?: (
    run: RunRow,
    evaluation: Record<string, unknown>,
    turn?: TranscriptTurn
  ) => void;
  loading?: boolean;
  error?: string | null;
  initialTab?: Tab;
};

type Tab = "detail" | "run" | "context";

export function Inspector({
  run,
  event,
  tree,
  messages,
  approvals,
  checkpoint,
  transcript,
  contexts = [],
  onSelectRun,
  onRunUpdated,
  onEvaluationComplete,
  loading = false,
  error = null,
  initialTab,
}: Props) {
  const [tab, setTab] = useState<Tab>(run ? initialTab || "context" : "detail");
  const toolCallId =
    typeof event?.payload?.tool_call_id === "string"
      ? event.payload.tool_call_id
      : null;
  const toolCall = useMemo(
    () => findToolCall(messages, toolCallId),
    [messages, toolCallId]
  );
  const toolResult = toolCallId
    ? messages.find((message) => message.tool_call_id === toolCallId)
    : undefined;

  useEffect(() => {
    setTab(event ? "detail" : "context");
  }, [event, run?.id]);

  if (loading) {
    return (
      <div className="inspector" data-testid="inspector">
        <PanelState kind="loading" title="正在载入检查器" detail="读取运行身份与检查点" />
      </div>
    );
  }
  if (error && !run) {
    return (
      <div className="inspector" data-testid="inspector">
        <PanelState kind="error" title="运行详情载入失败" detail={error} />
      </div>
    );
  }
  if (!run) {
    return (
      <div className="inspector" data-testid="inspector">
        <PanelState kind="empty" title="没有可检查的运行" detail="先从运行列表中选择一项" />
      </div>
    );
  }

  return (
    <div className="inspector" data-testid="inspector">
      <div className="inspector-tabs" role="tablist" aria-label="检查器视图">
        <button
          type="button"
          className={tab === "detail" ? "active" : ""}
          onClick={() => setTab("detail")}
          role="tab"
          aria-selected={tab === "detail"}
        >
          <Braces size={14} aria-hidden="true" /> 事件
        </button>
        <button
          type="button"
          className={tab === "run" ? "active" : ""}
          onClick={() => setTab("run")}
          role="tab"
          aria-selected={tab === "run"}
        >
          <FileClock size={14} aria-hidden="true" /> 运行信息
        </button>
        <button
          type="button"
          className={tab === "context" ? "active" : ""}
          onClick={() => setTab("context")}
          role="tab"
          aria-selected={tab === "context"}
        >
          <MessageSquareText size={14} aria-hidden="true" /> 上下文
        </button>
      </div>

      <div className="inspector-scroll">
        {error && <PanelState kind="error" title="部分详情载入失败" detail={error} compact />}
        {tab === "detail" && (
          <>
            {!event && (
              <PanelState kind="empty" title="请选择追踪事件" detail="事件载荷会显示在这里" />
            )}
            {event && (
              <section className="inspect-section">
                <SectionTitle title="已选事件" meta={`序号 ${event.global_seq}`} />
                <Definition label="类型" value={event.type} mono />
                <Definition label="时间" value={formatDate(event.timestamp)} />
                {event.span_id && <Definition label="执行跨度 ID" value={event.span_id} mono />}
                <details className="developer-data">
                  <summary>查看事件载荷</summary>
                  <JsonBlock label="事件载荷" value={event.payload} />
                </details>
              </section>
            )}
            {toolCall && (
              <section className="inspect-section" data-testid="tool-detail">
                <SectionTitle title="工具调用" meta={formatToolName(toolCall.name)} />
                <Definition label="工具调用 ID" value={toolCall.id} mono />
                <Definition label="参数摘要" value={summarizeArgs(toolCall.arguments) || "无参数"} mono />
                <JsonBlock label="参数" value={toolCall.arguments} />
                <div className="definition">
                  <span>结果</span>
                  <pre>{formatOutputPreview(toolResult?.content || "等待结果", 1200)}</pre>
                </div>
                {typeof event?.payload?.error_code === "string" && (
                  <>
                    <Definition label="错误代码" value={event.payload.error_code} mono />
                    <Definition
                      label="错误类别"
                      value={errorCategoryLabel(String(event.payload.error_category || "tool"))}
                      mono
                    />
                    <Definition
                      label="可重试"
                      value={event.payload.retryable ? "是" : "否"}
                    />
                    {typeof event.payload.recovery_hint === "string" &&
                      event.payload.recovery_hint && (
                        <Definition label="恢复建议" value={event.payload.recovery_hint} />
                      )}
                  </>
                )}
              </section>
            )}
            {!!approvals.length && (
              <section className="inspect-section">
                <SectionTitle title="审批" meta={`${approvals.length} 项`} />
                {approvals.map((approval) => (
                  <div className="approval-row" key={approval.id}>
                    <span>
                      <strong>{formatToolName(approval.tool_name)}</strong>
                      <small>{effectLabel(approval.effect)}</small>
                    </span>
                    <span className={`decision ${approval.decision || "pending"}`}>
                      {decisionLabel(approval.decision || "pending")}
                    </span>
                  </div>
                ))}
              </section>
            )}
          </>
        )}

        {tab === "run" && run && (
          <>
            <section className="inspect-section">
              <SectionTitle title="身份" meta={runStatusLabel(run)} />
              <Definition label="运行 ID" value={run.id} mono />
              <Definition label="会话 ID" value={run.session_id} mono />
              <Definition label="根运行 ID" value={run.root_run_id} mono />
              {run.parent_run_id && (
                <Definition label="父运行 ID" value={run.parent_run_id} mono />
              )}
              <Definition label="服务商 / 模型" value={`${run.provider || "-"} / ${run.model || "-"}`} />
              <Definition label="角色" value={actorLabel(run.actor || (run.parent_run_id ? "delegate" : "user"))} />
              <Definition label="深度" value={String(run.depth ?? run.delegate_depth ?? 0)} />
              <Definition label="审批策略" value={run.approval || "-"} />
              <Definition label="工作目录" value={run.cwd || "-"} mono />
            </section>
            <EvalSection
              run={run}
              onRunUpdated={onRunUpdated}
              onEvaluationComplete={(updated, evaluation) =>
                onEvaluationComplete?.(
                  updated,
                  evaluation,
                  transcript.find((turn) => turn.run_id === updated.id)
                )
              }
            />
            <section className="inspect-section">
              <SectionTitle title="执行" meta={`${run.steps || 0} 步`} />
              <Definition label="创建时间" value={formatDate(run.created_at)} />
              <Definition label="结束时间" value={formatDate(run.finished_at)} />
              <Definition label="耗时" value={formatDuration(run.created_at, run.finished_at)} />
              <details className="developer-data">
                <summary>开发者数据</summary>
                <JsonBlock label="用量数据" value={parseJson(run.usage_json)} />
                <JsonBlock label="元数据" value={parseJson(run.metadata_json)} />
              </details>
            </section>
            {run.error && (
              <section className="inspect-section failure-section" data-testid="failure-detail">
                <SectionTitle title="失败信息" meta={failureKind(run.error)} />
                <Definition label="错误" value={run.error} danger />
                <Definition
                  label="服务商 / 模型"
                  value={`${run.provider || "-"} / ${run.model || "-"}`}
                />
                <Definition
                  label="耗时"
                  value={formatDuration(run.created_at, run.finished_at)}
                />
                <Definition label="检查点" value={checkpointPhaseLabel(checkpoint?.phase)} />
                <div className="definition recovery-command">
                  <span>恢复方式</span>
                  <code>{`agentharness resume ${run.id}`}</code>
                </div>
              </section>
            )}
            <section className="inspect-section">
              <SectionTitle title="检查点" meta={checkpointPhaseLabel(checkpoint?.phase)} />
              {checkpoint ? (
                <>
                  <Definition label="状态" value={runtimeStatusLabel(checkpoint.status)} />
                  <Definition label="步骤" value={String(checkpoint.step)} />
                  <Definition
                    label="已完成工具"
                    value={String(checkpoint.completed_tool_call_ids.length)}
                  />
                  <Definition
                    label="待执行工具"
                    value={String(checkpoint.pending_tool_calls.length)}
                  />
                  <details className="developer-data">
                    <summary>查看检查点数据</summary>
                    <JsonBlock label="用量数据" value={checkpoint.usage} />
                  </details>
                </>
              ) : (
                <div className="muted-line">无检查点</div>
              )}
            </section>
            <section className="inspect-section">
              <SectionTitle title="运行树" meta={`${tree.length} 个节点`} />
              <div className="tree-list">
                {tree.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    className={`tree-item ${item.parent_run_id ? "child" : ""} ${
                      item.id === run?.id ? "selected" : ""
                    }`}
                    onClick={() => onSelectRun?.(item.id)}
                    data-testid={`tree-run-${item.id}`}
                    style={{ paddingLeft: `${8 + Math.min(item.depth ?? item.delegate_depth ?? 0, 8) * 16}px` }}
                    aria-pressed={item.id === run.id}
                    aria-label={`${item.id.slice(0, 12)}，${runStatusLabel(item)}`}
                  >
                    <span className="tree-identity">
                      <code>{item.id.slice(0, 12)}</code>
                      <small>{actorLabel(item.actor || (item.parent_run_id ? "delegate" : "user"))} · 深度 {item.depth ?? item.delegate_depth ?? 0}</small>
                    </span>
                    <span className={`status-text ${item.status}`}>{runStatusLabel(item)}</span>
                  </button>
                ))}
              </div>
            </section>
          </>
        )}

        {tab === "context" && (
          <ContextInspector
            run={run}
            transcript={transcript}
            contexts={contexts}
            onRunUpdated={onRunUpdated}
            onEvaluationComplete={onEvaluationComplete}
          />
        )}
      </div>
    </div>
  );
}


type RunEval = RunEvaluation;

function parseRunEval(run: RunRow): { state: "missing" | "unsupported" | "ok"; eval?: RunEval } {
  const meta = parseJson(run.metadata_json);
  if (!meta || typeof meta !== "object" || Array.isArray(meta)) {
    return { state: "missing" };
  }
  const raw = (meta as Record<string, unknown>).eval;
  if (raw === undefined || raw === null) {
    return { state: "missing" };
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return { state: "unsupported" };
  }
  const ev = raw as RunEval;
  const version = ev.schema_version;
  if (typeof version !== "number" || version !== 1) {
    return { state: "unsupported", eval: ev };
  }
  return { state: "ok", eval: ev };
}

function EvalSection({
  run,
  onRunUpdated,
  onEvaluationComplete,
}: {
  run: RunRow;
  onRunUpdated?: (run: RunRow) => void;
  onEvaluationComplete?: (run: RunRow, evaluation: Record<string, unknown>) => void;
}) {
  const [manualEval, setManualEval] = useState<RunEval | null>(null);
  const [grading, setGrading] = useState(false);
  const [gradeError, setGradeError] = useState<string | null>(null);
  useEffect(() => {
    setManualEval(null);
    setGradeError(null);
  }, [run.id]);
  const terminal = ["completed", "failed", "cancelled", "interrupted"].includes(run.status);
  const parsed = manualEval
    ? ({ state: "ok", eval: manualEval } as const)
    : parseRunEval(run);
  const grade = async () => {
    setGrading(true);
    setGradeError(null);
    try {
      const result = await api.gradeRun(run.id);
      setManualEval(result.eval as RunEval);
      onRunUpdated?.(result.run);
      onEvaluationComplete?.(result.run, result.eval);
    } catch (error) {
      setGradeError(error instanceof Error ? error.message : String(error));
    } finally {
      setGrading(false);
    }
  };
  const action = (
    <div className="eval-actions">
      <button
        type="button"
        onClick={() => void grade()}
        disabled={!terminal || grading}
        data-testid="run-grade-button"
      >
        {grading ? "评测中…" : parsed.state === "ok" ? "重新评分" : "评测"}
      </button>
      {!terminal && <span>运行结束后可评测</span>}
    </div>
  );
  if (parsed.state === "missing") {
    return (
      <section className="inspect-section" data-testid="run-eval">
        <SectionTitle title="评估" meta="待评测" />
        <div className="muted-line" data-testid="run-eval-missing">
          尚未评测；可执行免费的规则与运行健康检查
        </div>
        {action}
        {gradeError && <div className="eval-error">{gradeError}</div>}
      </section>
    );
  }
  if (parsed.state === "unsupported") {
    return (
      <section className="inspect-section" data-testid="run-eval">
        <SectionTitle title="评估" meta="版本" />
        <div className="muted-line" data-testid="run-eval-unsupported">
          评估数据版本不受支持
        </div>
        {action}
        {gradeError && <div className="eval-error">{gradeError}</div>}
      </section>
    );
  }
  const ev = parsed.eval!;
  const healthOnly = ev.mode !== "ai" && ev.evaluation_mode !== "scored";
  const reasons = Array.isArray(ev.reasons) ? ev.reasons : [];
  return (
    <section className="inspect-section" data-testid="run-eval">
      <SectionTitle
        title="评估"
        meta={ev.passed ? "通过" : "未通过"}
      />
      <Definition
        label="结果"
        value={ev.passed ? "通过" : "未通过"}
        danger={!ev.passed}
      />
      <Definition
        label="分数"
        value={healthOnly ? "仅健康检查" : typeof ev.score === "number" ? `${Math.round(ev.score * 100)} / 100` : "未评分"}
      />
      {ev.mode && <Definition label="模式" value={ev.mode === "ai" ? "智能评测 + 硬规则" : healthOnly ? "运行健康（无质量分）" : "确定性评测"} />}
      {ev.failure_category && ev.failure_category !== "none" && (
        <Definition label="失败分类" value={ev.failure_category} />
      )}
      {ev.grader && <Definition label="评分器" value={ev.grader} mono />}
      {ev.graded_at && (
        <Definition label="评分时间" value={formatDate(ev.graded_at)} />
      )}
      {typeof ev.latency_s === "number" && (
        <Definition
          label="评分耗时"
          value={`${ev.latency_s.toFixed(3)} 秒`}
        />
      )}
      {action}
      {gradeError && <div className="eval-error">{gradeError}</div>}
      <div className="definition" data-testid="run-eval-reasons">
        <span>原因</span>
        {reasons.length === 0 ? (
          <div className="muted-line">无</div>
        ) : (
          <details open={reasons.length <= 3}>
            <summary>{`${reasons.length} 条`}</summary>
            <ul className="eval-reasons">
              {reasons.map((reason, index) => (
                <li key={`${index}-${reason.slice(0, 24)}`}>{reason}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  );
}

function findToolCall(messages: MessageRow[], id: string | null): ToolCallRow | null {
  if (!id) return null;
  for (const message of messages) {
    const match = message.tool_calls?.find((call) => call.id === id);
    if (match) return match;
  }
  return null;
}

function SectionTitle({ title, meta }: { title: string; meta?: string }) {
  return (
    <h3>
      {title}
      {meta && <span>{meta}</span>}
    </h3>
  );
}

function Definition({
  label,
  value,
  mono,
  danger,
}: {
  label: string;
  value: string;
  mono?: boolean;
  danger?: boolean;
}) {
  return (
    <div className={`definition ${danger ? "danger" : ""}`}>
      <span>{label}</span>
      <div className={mono ? "mono" : ""}>{value}</div>
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="definition">
      <span>{label}</span>
      <pre>{JSON.stringify(value ?? {}, null, 2)}</pre>
    </div>
  );
}

function parseJson(raw?: string): unknown {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatDuration(start: string, end?: string | null): string {
  const started = new Date(start).getTime();
  const finished = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(started) || Number.isNaN(finished)) return "-";
  const milliseconds = Math.max(0, finished - started);
  if (milliseconds < 1000) return `${milliseconds} 毫秒`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} 秒`;
  return `${(milliseconds / 60_000).toFixed(1)} 分钟`;
}

function failureKind(error: string): string {
  const prefix = error.split(":", 1)[0].trim();
  return ({
    rate_limit: "请求频率受限",
    provider: "模型服务商错误",
    provider_error: "模型服务商错误",
    tool: "工具错误",
    timeout: "执行超时",
    budget: "预算错误",
    cancelled: "运行已取消",
    interrupted: "运行已中断",
  } as Record<string, string>)[prefix] || (prefix && prefix.length < 32 ? prefix : "执行错误");
}

function actorLabel(actor: string): string {
  if (actor === "user") return "用户";
  if (actor === "delegate") return "委派代理";
  return actor;
}

function decisionLabel(value: string): string {
  return ({ pending: "待处理", approved: "已批准", approve: "已批准", denied: "已拒绝", deny: "已拒绝" } as Record<string, string>)[value] || value;
}

function effectLabel(value: string): string {
  return ({ read: "读取", write: "写入", shell: "命令执行", network: "网络访问", destructive: "破坏性操作" } as Record<string, string>)[value] || value;
}

function checkpointPhaseLabel(value?: string | null): string {
  if (!value) return "无";
  return ({ model_turn: "模型轮次", tool_execution: "工具执行", verification: "结果验证", approval: "等待审批", completed: "已完成" } as Record<string, string>)[value] || value;
}

function runtimeStatusLabel(value: string): string {
  return ({ pending: "等待中", running: "运行中", completed: "已完成", failed: "失败", interrupted: "已中断", cancelled: "已取消", waiting_approval: "等待审批", require_human: "需要人工处理" } as Record<string, string>)[value] || value;
}

function errorCategoryLabel(value: string): string {
  return ({ tool: "工具错误", provider: "模型服务商错误", environment: "环境错误", validation: "校验错误", permission: "权限错误", timeout: "超时" } as Record<string, string>)[value] || value;
}
