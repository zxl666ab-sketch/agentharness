import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Circle,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Paperclip,
  RefreshCw,
  Send,
  Square,
  UserRound,
  X,
} from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { api, type ToolInvocationRow } from "../api/client";
import { friendlyProcurementError, procurementApi } from "./api";
import type { ProcurementRequest } from "./types";

const TOOL_LABELS: Record<string, string> = {
  procurement_read_request: "读取采购任务",
  procurement_capture_requirement: "结构化采购需求",
  procurement_execute_analysis: "解析、匹配、复算并比价",
  procurement_approve_supplier: "等待采购员确认",
};

const RUN_LABELS: Record<string, string> = {
  pending: "等待运行",
  running: "Agent 分析中",
  waiting_approval: "等待操作批准",
  require_human: "需要人工处理",
  completed: "采购决策已完成",
  failed: "运行失败",
  cancelled: "运行已取消",
  interrupted: "运行已中断",
  budget_stopped: "已停在安全边界",
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function fileIcon(filename: string) {
  return filename.toLowerCase().endsWith(".xlsx")
    ? <FileSpreadsheet size={15} />
    : <FileText size={15} />;
}

function working(status?: string | null) {
  return status === "pending" || status === "running" || status === "waiting_approval";
}

function toolTone(status: string) {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "indeterminate") return "warning";
  return "active";
}

function ToolState({ invocation }: { invocation: ToolInvocationRow }) {
  const tone = toolTone(invocation.status);
  return (
    <li className={tone}>
      <span className="proc-conversation-tool-state">
        {tone === "success" ? <CheckCircle2 size={14} /> : null}
        {tone === "danger" ? <AlertTriangle size={14} /> : null}
        {tone === "warning" ? <AlertTriangle size={14} /> : null}
        {tone === "active" ? <LoaderCircle className="spin" size={14} /> : null}
      </span>
      <span>
        <strong>{TOOL_LABELS[invocation.tool_name] || invocation.tool_name}</strong>
        <small>{invocation.status === "succeeded" ? "已完成" : invocation.status === "failed" ? "失败" : invocation.status === "indeterminate" ? "结果待确认" : invocation.tool_name === "procurement_approve_supplier" ? "等待采购员确认" : "执行中"}</small>
      </span>
    </li>
  );
}

type NewConversationProps = {
  busy: boolean;
  error?: string | null;
  maxFileBytes: number;
  maxTotalBytes: number;
  maxQuotes: number;
  onStart: (message: string, files: File[]) => Promise<void>;
};

export function NewProcurementConversation({
  busy,
  error,
  maxFileBytes,
  maxTotalBytes,
  maxQuotes,
  onStart,
}: NewConversationProps) {
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);

  function addFiles(incoming: File[]) {
    const invalid = incoming.find((file) => !/\.(xlsx|pdf)$/i.test(file.name));
    if (invalid) {
      setLocalError(`不支持的文件：${invalid.name}`);
      return;
    }
    const oversized = incoming.find((file) => file.size > maxFileBytes);
    if (oversized) {
      setLocalError(`${oversized.name} 超过单文件 ${formatBytes(maxFileBytes)} 上限`);
      return;
    }
    const next = [...files];
    for (const file of incoming) {
      if (!next.some((item) => item.name === file.name && item.size === file.size)) next.push(file);
    }
    if (next.length > maxQuotes) {
      setLocalError(`每个采购任务最多上传 ${maxQuotes} 份报价`);
      return;
    }
    if (next.reduce((total, file) => total + file.size, 0) > maxTotalBytes) {
      setLocalError(`本次报价附件总计不得超过 ${formatBytes(maxTotalBytes)}`);
      return;
    }
    setFiles(next);
    setLocalError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const cleaned = message.trim();
    if (!cleaned) {
      setLocalError("请填写采购目标");
      return;
    }
    if (files.length < 2) {
      setLocalError("请至少上传 2 份供应商报价");
      return;
    }
    setLocalError(null);
    await onStart(cleaned, files);
  }

  return (
    <section className="proc-new-conversation" aria-label="新建采购对话">
      <div className="proc-new-conversation-head">
        <span><Bot size={22} /></span>
        <div>
          <h1>新建采购决策</h1>
          <p>采购目标与供应商报价</p>
        </div>
      </div>
      <form className="proc-conversation-composer new" onSubmit={(event) => void submit(event)}>
        <textarea
          aria-label="采购目标"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="描述物料、数量、规格、交期、预算、发票和送货要求"
          maxLength={20_000}
          disabled={busy}
        />
        {files.length ? (
          <ul className="proc-compose-files">
            {files.map((file, index) => (
              <li key={`${file.name}-${file.size}`}>
                <span>{fileIcon(file.name)}</span>
                <span><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span>
                <button
                  type="button"
                  title="移除附件"
                  aria-label={`移除 ${file.name}`}
                  disabled={busy}
                  onClick={() => setFiles((current) => current.filter((_, item) => item !== index))}
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="proc-compose-actions">
          <label className="proc-attach-button">
            <Paperclip size={16} />
            <span>报价附件</span>
            <input
              data-testid="conversation-upload"
              type="file"
              accept=".xlsx,.pdf"
              multiple
              disabled={busy}
              onChange={(event) => {
                addFiles(Array.from(event.target.files || []));
                event.target.value = "";
              }}
            />
          </label>
          <span className={files.length >= 2 ? "ready" : ""}>{files.length} / {maxQuotes} 份</span>
          <button className="proc-button primary" type="submit" disabled={busy || !message.trim() || files.length < 2}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}
            {busy ? "正在创建" : "开始分析"}
          </button>
        </div>
        {localError || error ? <p className="proc-compose-error" role="alert">{localError || error}</p> : null}
      </form>
    </section>
  );
}

type ConversationProps = {
  request: ProcurementRequest;
  streamLive: boolean;
  actionError?: string | null;
  onResume: (message: string) => Promise<void>;
  onRecover: () => Promise<void>;
  onOpenComparison: () => void;
};

export function ProcurementConversation({
  request,
  streamLive,
  actionError,
  onResume,
  onRecover,
  onOpenComparison,
}: ConversationProps) {
  const runId = request.analysis_run_id || null;
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const runQuery = useQuery({
    queryKey: ["procurement-run", runId],
    queryFn: () => api.run(runId!),
    enabled: !!runId,
    refetchInterval: (query) => !streamLive && working(query.state.data?.status) ? 750 : false,
  });
  const active = working(runQuery.data?.status);
  const messagesQuery = useQuery({
    queryKey: ["procurement-messages", runId],
    queryFn: () => api.messages(runId!),
    enabled: !!runId,
    refetchInterval: !streamLive && active ? 750 : false,
  });
  const toolsQuery = useQuery({
    queryKey: ["procurement-tools", runId],
    queryFn: () => api.toolInvocations(runId!),
    enabled: !!runId,
    refetchInterval: !streamLive && active ? 750 : false,
  });
  const messages = useMemo(
    () => (messagesQuery.data || []).filter((item) =>
      (item.role === "user" || item.role === "assistant")
      && item.content.trim().length > 0
      && !item.content.includes("[procurement_supplier_selection]")
    ),
    [messagesQuery.data]
  );
  const tools = toolsQuery.data || [];
  const status = runQuery.data?.status || (runId ? "pending" : "");
  const needsClarification = status === "require_human";
  const canRecover = status === "failed" || status === "cancelled" || status === "interrupted" || status === "budget_stopped";
  const canStop = status === "pending" || status === "running";

  async function submitReply(event: FormEvent) {
    event.preventDefault();
    const value = reply.trim();
    if (!value) return;
    setSending(true);
    try {
      await onResume(value);
      setReply("");
      await Promise.all([runQuery.refetch(), messagesQuery.refetch(), toolsQuery.refetch()]);
    } finally {
      setSending(false);
    }
  }

  async function stopAgent() {
    if (!runId) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await procurementApi.cancelRun(request.id);
      await Promise.all([runQuery.refetch(), messagesQuery.refetch(), toolsQuery.refetch()]);
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : String(err));
    } finally {
      setCancelling(false);
    }
  }

  return (
    <aside className="proc-conversation" aria-label="采购 Agent 对话">
      <header className="proc-conversation-head">
        <div><Bot size={17} /><strong>采购 Agent</strong></div>
        <div className="proc-conversation-head-actions">
          {canStop ? (
            <button
              type="button"
              className="proc-button"
              disabled={cancelling}
              onClick={stopAgent}
              title="停止当前 Agent 运行（例如卡在网关重试时）"
            >
              {cancelling ? <LoaderCircle className="spin" size={12} /> : <Square size={11} />}
              停止
            </button>
          ) : null}
          <span className={`proc-run-state ${status}`}>{active ? <LoaderCircle className="spin" size={13} /> : <Circle size={10} fill="currentColor" />}{RUN_LABELS[status] || "准备中"}</span>
        </div>
      </header>
      {cancelError ? <p className="proc-conversation-error" role="alert">{cancelError}</p> : null}
      <div className="proc-conversation-ids">
        <span><small>SESSION</small><code title={request.session_id}>{request.session_id.slice(0, 10)}</code></span>
        <span><small>REQUEST</small><code title={request.id}>{request.id.slice(0, 10)}</code></span>
        <span><small>RUN</small><code title={runId || "-"}>{runId ? runId.slice(0, 10) : "-"}</code></span>
      </div>
      <div className="proc-conversation-scroll">
        {messages.map((item, index) => (
          <article className={`proc-chat-message ${item.role}`} key={item.id}>
            <span className="proc-chat-avatar">{item.role === "user" ? <UserRound size={14} /> : <Bot size={14} />}</span>
            <div>
              <small>{item.role === "user" ? "采购员" : "采购 Agent"}</small>
              <p>{item.content}</p>
              {item.role === "user" && index === 0 && request.attachments.length ? (
                <ul className="proc-message-files">
                  {request.attachments.map((attachment) => (
                    <li key={attachment.sha256}>{fileIcon(attachment.filename)}<span title={attachment.sha256}>{attachment.filename}</span></li>
                  ))}
                </ul>
              ) : null}
            </div>
          </article>
        ))}
        {active && !messages.some((item) => item.role === "assistant") ? (
          <div className="proc-agent-working"><LoaderCircle className="spin" size={15} />Agent 正在分析报价</div>
        ) : null}
        {tools.length ? (
          <section className="proc-conversation-tools">
            <header><span>工具进度</span><strong>{tools.filter((item) => item.status === "succeeded").length}/{tools.length}</strong></header>
            <ol>{tools.map((item) => <ToolState invocation={item} key={item.id} />)}</ol>
          </section>
        ) : null}
        {runQuery.isError ? <p className="proc-conversation-error" role="alert">运行状态读取失败</p> : null}
        {canRecover && runQuery.data?.error ? <p className="proc-conversation-error" role="alert">{friendlyProcurementError(runQuery.data.error)}</p> : null}
        {actionError ? <p className="proc-conversation-error" role="alert">{actionError}</p> : null}
      </div>
      {needsClarification ? (
        <form className="proc-conversation-composer reply" onSubmit={(event) => void submitReply(event)}>
          <textarea aria-label="补充澄清信息" value={reply} onChange={(event) => setReply(event.target.value)} placeholder="补充或修正采购规格" maxLength={20_000} disabled={sending} />
          <button type="submit" title="提交澄清" aria-label="提交澄清" disabled={sending || !reply.trim()}>{sending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button>
        </form>
      ) : null}
      {request.comparison && !request.decision ? (
        <button className="proc-conversation-next" type="button" onClick={onOpenComparison}><CheckCircle2 size={15} />{request.comparison.result.eligible_count ? "查看比价并选择供应商" : "查看淘汰原因并确认结论"}</button>
      ) : null}
      {canRecover ? (
        <button className="proc-conversation-next warning" type="button" onClick={() => void onRecover()}><RefreshCw size={15} />一键恢复（从持久化状态重新分析）</button>
      ) : null}
    </aside>
  );
}
