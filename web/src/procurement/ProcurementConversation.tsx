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
  UserRound,
  X,
} from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import { api, type ToolInvocationRow } from "../api/client";
import type { ProcurementRequest } from "./types";

const TOOL_LABELS: Record<string, string> = {
  procurement_read_request: "读取采购任务",
  procurement_capture_requirement: "结构化采购需求",
  procurement_execute_analysis: "解析、匹配、复算并比价",
  procurement_approve_supplier: "写入供应商审批",
};

const RUN_LABELS: Record<string, string> = {
  pending: "等待运行",
  running: "Agent 分析中",
  waiting_approval: "等待操作批准",
  require_human: "等待人工复核",
  completed: "采购决策已完成",
  failed: "运行失败",
  cancelled: "运行已取消",
  interrupted: "运行已中断",
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

function toolSucceeded(status?: string | null) {
  return status === "succeeded" || status === "completed";
}

function toolTone(status: string) {
  if (toolSucceeded(status)) return "success";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "indeterminate") return "warning";
  return "active";
}

function staleAssistantMessage(content: string) {
  return content.includes("暂不能进行")
    || content.includes("暂无法完成")
    || content.includes("检测到报价信息缺失")
    || (content.includes("报价解析阶段") && content.includes("需要人工复核"));
}

function userFacingRunError(error: string | null | undefined, request: ProcurementRequest) {
  if (!error) return null;
  if (!error.startsWith("verification requires human review")) return error;
  if (request.comparison || request.decision) return null;
  if (request.unresolved_field_count > 0 || request.status === "collecting" || request.status === "review") {
    return "报价字段尚未全部确认，请在右侧复核后继续。";
  }
  return "采购 Agent 已暂停，请点击恢复后继续。";
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
        <small>{toolSucceeded(invocation.status) ? "已完成" : invocation.status === "failed" ? "失败" : invocation.status === "indeterminate" ? "结果待确认" : "执行中"}</small>
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
  actionError?: string | null;
  onResume: (message: string) => Promise<void>;
  onRecover: () => Promise<void>;
  onOpenComparison: () => void;
};

export function ProcurementConversation({
  request,
  actionError,
  onResume,
  onRecover,
  onOpenComparison,
}: ConversationProps) {
  const runId = request.analysis_run_id || null;
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const runQuery = useQuery({
    queryKey: ["procurement-run", runId],
    queryFn: () => api.run(runId!),
    enabled: !!runId,
    refetchInterval: (query) => working(query.state.data?.status) ? 750 : false,
  });
  const active = working(runQuery.data?.status);
  const messagesQuery = useQuery({
    queryKey: ["procurement-messages", runId],
    queryFn: () => api.messages(runId!),
    enabled: !!runId,
    refetchInterval: active ? 750 : false,
  });
  const toolsQuery = useQuery({
    queryKey: ["procurement-tools", runId],
    queryFn: () => api.toolInvocations(runId!),
    enabled: !!runId,
    refetchInterval: active ? 750 : false,
  });
  const tools = toolsQuery.data || [];
  const finalized = !!request.comparison;
  const messages = useMemo(
    () => (messagesQuery.data || []).filter((item) =>
      (item.role === "user" || item.role === "assistant")
      && item.content.trim().length > 0
      && !item.content.includes("[procurement_supplier_selection]")
      && (!finalized || item.role !== "assistant" || !staleAssistantMessage(item.content))
    ),
    [finalized, messagesQuery.data]
  );
  const status = runQuery.data?.status || (runId ? "pending" : "");
  // A failed requirement capture can leave unresolved_field_count at zero even
  // though the Agent has asked the buyer to confirm or correct an input.
  const needsClarification = status === "require_human" && !request.comparison && !request.decision;
  const canRecover = status === "failed"
    || status === "cancelled"
    || status === "interrupted"
    || (status === "require_human" && !request.comparison && !request.decision);
  const visibleTools = finalized
    ? tools.filter((item) => item.status !== "failed" && item.status !== "cancelled" && item.status !== "indeterminate")
    : tools;
  const foldedToolCount = tools.length - visibleTools.length;
  const visibleRunError = userFacingRunError(runQuery.data?.error, request);
  const runLabel = status === "require_human" && !needsClarification
    ? "等待恢复"
    : RUN_LABELS[status] || "准备中";

  async function submitReply(event: FormEvent) {
    event.preventDefault();
    const value = reply.trim();
    if (!value) return;
    setSending(true);
    try {
      await onResume(value);
      setReply("");
      await Promise.all([runQuery.refetch(), messagesQuery.refetch(), toolsQuery.refetch()]);
    } catch {
      // The workbench surfaces the failure via the actionError banner; the
      // reply text is preserved so the user can retry.
    } finally {
      setSending(false);
    }
  }

  return (
    <aside className="proc-conversation" aria-label="采购 Agent 对话">
      <header className="proc-conversation-head">
        <div><Bot size={17} /><strong>采购 Agent</strong></div>
        <span className={`proc-run-state ${status}`}>{active ? <LoaderCircle className="spin" size={13} /> : <Circle size={10} fill="currentColor" />}{runLabel}</span>
      </header>
      <div className="proc-conversation-ids">
        <span><small>SESSION</small><code title={request.session_id || "-"}>{request.session_id ? request.session_id.slice(0, 10) : "-"}</code></span>
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
        {visibleTools.length ? (
          <section className="proc-conversation-tools">
            <header><span>工具进度</span><strong>{visibleTools.filter((item) => toolSucceeded(item.status)).length}/{visibleTools.length}</strong></header>
            <ol>{visibleTools.map((item) => <ToolState invocation={item} key={item.id} />)}</ol>
            {foldedToolCount ? <small className="proc-conversation-history-note">已折叠 {foldedToolCount} 次失败尝试，完整记录见运行审计</small> : null}
          </section>
        ) : null}
        {runQuery.isError ? <p className="proc-conversation-error" role="alert">运行状态读取失败</p> : null}
        {canRecover && visibleRunError ? <p className="proc-conversation-error" role="alert">{visibleRunError}</p> : null}
        {actionError ? <p className="proc-conversation-error" role="alert">{actionError}</p> : null}
      </div>
      {needsClarification ? (
        <form className="proc-conversation-composer reply" onSubmit={(event) => void submitReply(event)}>
          <textarea aria-label="补充澄清信息" value={reply} onChange={(event) => setReply(event.target.value)} placeholder="补充 Agent 请求的信息" maxLength={20_000} disabled={sending} />
          <button type="submit" title="提交澄清" aria-label="提交澄清" disabled={sending || !reply.trim()}>{sending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button>
        </form>
      ) : null}
      {request.comparison && !request.decision ? (
        <button className="proc-conversation-next" type="button" onClick={onOpenComparison}><CheckCircle2 size={15} />查看比价并选择供应商</button>
      ) : null}
      {canRecover ? (
        <button className="proc-conversation-next warning" type="button" onClick={() => void onRecover()}><RefreshCw size={15} />{status === "require_human" ? "恢复采购 Agent" : "从持久化状态重新分析"}</button>
      ) : null}
    </aside>
  );
}
