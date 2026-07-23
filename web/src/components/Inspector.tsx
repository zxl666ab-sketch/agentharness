import { useEffect, useMemo, useState } from "react";
import { Braces, FileClock, MessageSquareText } from "lucide-react";
import type {
  ApprovalRow,
  CheckpointRow,
  EventRow,
  MessageRow,
  RunRow,
  ToolCallRow,
  TranscriptTurn,
} from "../api/client";
import { runStatusLabel } from "../runs/status";
import { formatOutputPreview, formatToolName, summarizeArgs } from "../trace/formatters";
import { PanelState } from "./PanelState";

type Props = {
  run: RunRow | null;
  event: EventRow | null;
  tree: RunRow[];
  messages: MessageRow[];
  approvals: ApprovalRow[];
  checkpoint: CheckpointRow | null;
  transcript: TranscriptTurn[];
  onSelectRun?: (id: string) => void;
  loading?: boolean;
  error?: string | null;
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
  onSelectRun,
  loading = false,
  error = null,
}: Props) {
  const [tab, setTab] = useState<Tab>(run ? "run" : "detail");
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
    setTab(event ? "detail" : "run");
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
          <Braces size={14} aria-hidden="true" /> 详情
        </button>
        <button
          type="button"
          className={tab === "run" ? "active" : ""}
          onClick={() => setTab("run")}
          role="tab"
          aria-selected={tab === "run"}
        >
          <FileClock size={14} aria-hidden="true" /> 运行
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
                {event.span_id && <Definition label="span" value={event.span_id} mono />}
                <JsonBlock label="载荷" value={event.payload} />
              </section>
            )}
            {toolCall && (
              <section className="inspect-section" data-testid="tool-detail">
                <SectionTitle title="工具调用" meta={formatToolName(toolCall.name)} />
                <Definition label="tool_call_id" value={toolCall.id} mono />
                <Definition label="参数摘要" value={summarizeArgs(toolCall.arguments) || "无参数"} mono />
                <JsonBlock label="参数" value={toolCall.arguments} />
                <div className="definition">
                  <span>结果</span>
                  <pre>{formatOutputPreview(toolResult?.content || "等待结果", 1200)}</pre>
                </div>
              </section>
            )}
            {!!approvals.length && (
              <section className="inspect-section">
                <SectionTitle title="审批" meta={`${approvals.length} 项`} />
                {approvals.map((approval) => (
                  <div className="approval-row" key={approval.id}>
                    <span>
                      <strong>{formatToolName(approval.tool_name)}</strong>
                      <small>{approval.effect}</small>
                    </span>
                    <span className={`decision ${approval.decision || "pending"}`}>
                      {approval.decision || "pending"}
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
              <Definition label="run_id" value={run.id} mono />
              <Definition label="session_id" value={run.session_id} mono />
              <Definition label="root_run_id" value={run.root_run_id} mono />
              {run.parent_run_id && (
                <Definition label="parent_run_id" value={run.parent_run_id} mono />
              )}
              <Definition label="provider / model" value={`${run.provider || "-"} / ${run.model || "-"}`} />
              <Definition label="角色" value={actorLabel(run.actor || (run.parent_run_id ? "delegate" : "user"))} />
              <Definition label="深度" value={String(run.depth ?? run.delegate_depth ?? 0)} />
              <Definition label="审批策略" value={run.approval || "-"} />
              <Definition label="工作目录" value={run.cwd || "-"} mono />
            </section>
            <section className="inspect-section">
              <SectionTitle title="执行" meta={`${run.steps || 0} 步`} />
              <Definition label="创建时间" value={formatDate(run.created_at)} />
              <Definition label="结束时间" value={formatDate(run.finished_at)} />
              <Definition label="耗时" value={formatDuration(run.created_at, run.finished_at)} />
              <JsonBlock label="用量" value={parseJson(run.usage_json)} />
              <JsonBlock label="预算 / 元数据" value={parseJson(run.metadata_json)} />
            </section>
            {run.error && (
              <section className="inspect-section failure-section" data-testid="failure-detail">
                <SectionTitle title="失败信息" meta={failureKind(run.error)} />
                <Definition label="错误" value={run.error} danger />
                <Definition
                  label="provider / model"
                  value={`${run.provider || "-"} / ${run.model || "-"}`}
                />
                <Definition
                  label="耗时"
                  value={formatDuration(run.created_at, run.finished_at)}
                />
                <Definition label="检查点" value={checkpoint?.phase || "无"} />
                <div className="definition recovery-command">
                  <span>恢复方式</span>
                  <code>{`agentharness resume ${run.id}`}</code>
                </div>
              </section>
            )}
            <section className="inspect-section">
              <SectionTitle title="检查点" meta={checkpoint?.phase || "无"} />
              {checkpoint ? (
                <>
                  <Definition label="状态" value={checkpoint.status} />
                  <Definition label="步骤" value={String(checkpoint.step)} />
                  <Definition
                    label="已完成工具"
                    value={String(checkpoint.completed_tool_call_ids.length)}
                  />
                  <Definition
                    label="待执行工具"
                    value={String(checkpoint.pending_tool_calls.length)}
                  />
                  <JsonBlock label="检查点用量" value={checkpoint.usage} />
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
          <section className="inspect-section context-section">
            <SectionTitle title="对话上下文" meta={`${transcript.length} 个轮次`} />
            {!transcript.length && <div className="muted-line">无对话上下文</div>}
            {transcript.map((turn) => (
              <details key={turn.run_id} open={turn.run_id === run?.id}>
                <summary>
                  <code>{turn.run_id.slice(0, 10)}</code>
                  <span className={`status-text ${turn.status}`}>{turn.status}</span>
                </summary>
                <div className="context-message user">
                  <span>用户</span>
                  <p>{turn.user_content}</p>
                </div>
                <div className="context-message assistant">
                  <span>助手</span>
                  <p>{turn.assistant_content || turn.error || "无输出"}</p>
                </div>
              </details>
            ))}
          </section>
        )}
      </div>
    </div>
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
  return prefix && prefix.length < 32 ? prefix : "执行错误";
}

function actorLabel(actor: string): string {
  if (actor === "user") return "用户";
  if (actor === "delegate") return "委派代理";
  return actor;
}
