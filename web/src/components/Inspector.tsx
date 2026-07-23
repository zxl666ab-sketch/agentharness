import { useMemo, useState } from "react";
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

type Props = {
  run: RunRow | null;
  event: EventRow | null;
  tree: RunRow[];
  messages: MessageRow[];
  approvals: ApprovalRow[];
  checkpoint: CheckpointRow | null;
  transcript: TranscriptTurn[];
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
}: Props) {
  const [tab, setTab] = useState<Tab>("detail");
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

  return (
    <div className="inspector" data-testid="inspector">
      <div className="inspector-tabs" role="tablist" aria-label="Inspector view">
        <button
          type="button"
          className={tab === "detail" ? "active" : ""}
          onClick={() => setTab("detail")}
        >
          <Braces size={14} /> Detail
        </button>
        <button
          type="button"
          className={tab === "run" ? "active" : ""}
          onClick={() => setTab("run")}
        >
          <FileClock size={14} /> Run
        </button>
        <button
          type="button"
          className={tab === "context" ? "active" : ""}
          onClick={() => setTab("context")}
        >
          <MessageSquareText size={14} /> Context
        </button>
      </div>

      <div className="inspector-scroll">
        {tab === "detail" && (
          <>
            {!event && <div className="empty-state">Select a timeline event</div>}
            {event && (
              <section className="inspect-section">
                <SectionTitle title="Selected event" meta={`seq ${event.global_seq}`} />
                <Definition label="type" value={event.type} mono />
                <Definition label="timestamp" value={formatDate(event.timestamp)} />
                {event.span_id && <Definition label="span" value={event.span_id} mono />}
                <JsonBlock label="payload" value={event.payload} />
              </section>
            )}
            {toolCall && (
              <section className="inspect-section" data-testid="tool-detail">
                <SectionTitle title="Tool call" meta={toolCall.name} />
                <Definition label="tool_call_id" value={toolCall.id} mono />
                <JsonBlock label="arguments" value={toolCall.arguments} />
                <div className="definition">
                  <span>result</span>
                  <pre>{toolResult?.content || "Result pending"}</pre>
                </div>
              </section>
            )}
            {!!approvals.length && (
              <section className="inspect-section">
                <SectionTitle title="Approvals" meta={String(approvals.length)} />
                {approvals.map((approval) => (
                  <div className="approval-row" key={approval.id}>
                    <span>
                      <strong>{approval.tool_name}</strong>
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
              <SectionTitle title="Identity" meta={run.status} />
              <Definition label="run_id" value={run.id} mono />
              <Definition label="session_id" value={run.session_id} mono />
              <Definition label="root_run_id" value={run.root_run_id} mono />
              {run.parent_run_id && (
                <Definition label="parent_run_id" value={run.parent_run_id} mono />
              )}
              <Definition label="provider / model" value={`${run.provider || "-"} / ${run.model || "-"}`} />
              <Definition label="approval" value={run.approval || "-"} />
              <Definition label="cwd" value={run.cwd || "-"} mono />
            </section>
            <section className="inspect-section">
              <SectionTitle title="Execution" meta={`${run.steps || 0} steps`} />
              <Definition label="created" value={formatDate(run.created_at)} />
              <Definition label="finished" value={formatDate(run.finished_at)} />
              <Definition label="duration" value={formatDuration(run.created_at, run.finished_at)} />
              <JsonBlock label="usage" value={parseJson(run.usage_json)} />
              <JsonBlock label="budget / metadata" value={parseJson(run.metadata_json)} />
              {run.error && <Definition label="error" value={run.error} danger />}
            </section>
            <section className="inspect-section">
              <SectionTitle title="Checkpoint" meta={checkpoint?.phase || "none"} />
              {checkpoint ? (
                <>
                  <Definition label="status" value={checkpoint.status} />
                  <Definition label="step" value={String(checkpoint.step)} />
                  <Definition
                    label="completed tools"
                    value={String(checkpoint.completed_tool_call_ids.length)}
                  />
                  <Definition
                    label="pending tools"
                    value={String(checkpoint.pending_tool_calls.length)}
                  />
                  <JsonBlock label="checkpoint usage" value={checkpoint.usage} />
                </>
              ) : (
                <div className="muted-line">No checkpoint</div>
              )}
            </section>
            <section className="inspect-section">
              <SectionTitle title="Run tree" meta={`${tree.length} nodes`} />
              <div className="tree-list">
                {tree.map((item) => (
                  <div key={item.id} className={item.parent_run_id ? "child" : ""}>
                    <code>{item.id.slice(0, 12)}</code>
                    <span className={`status-text ${item.status}`}>{item.status}</span>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}

        {tab === "context" && (
          <section className="inspect-section context-section">
            <SectionTitle title="Conversation context" meta={`${transcript.length} turns`} />
            {!transcript.length && <div className="muted-line">No conversation context</div>}
            {transcript.map((turn) => (
              <details key={turn.run_id} open={turn.run_id === run?.id}>
                <summary>
                  <code>{turn.run_id.slice(0, 10)}</code>
                  <span className={`status-text ${turn.status}`}>{turn.status}</span>
                </summary>
                <div className="context-message user">
                  <span>User</span>
                  <p>{turn.user_content}</p>
                </div>
                <div className="context-message assistant">
                  <span>Assistant</span>
                  <p>{turn.assistant_content || turn.error || "No output"}</p>
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
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} s`;
  return `${(milliseconds / 60_000).toFixed(1)} min`;
}
