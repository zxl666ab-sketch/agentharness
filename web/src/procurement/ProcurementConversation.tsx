import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Circle,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  UploadCloud,
  Paperclip,
  RefreshCw,
  Send,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import { DragEvent, FormEvent, useMemo, useState } from "react";

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

const PROMPT_SUGGESTIONS = [
  "华东仓热敏不干胶标签采购：数量 20,000 个，规格 100mm×150mm 白色，10 天内送达，需要开票",
  "电商打包定制五层瓦楞纸箱：数量 15,000 个，400×300×250mm，双色印刷，最长交期 15 天",
  "企业行政茶水间物料季度采购：按供应商提报清单比价，含税含运费，到货单价核算",
];

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
  const [dragging, setDragging] = useState(false);

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
    <section className="proc-new-conversation w-full max-w-3xl mx-auto my-6 p-6 sm:p-8 bg-surface rounded-2xl border border-border/80 shadow-sm space-y-5" aria-label="新建采购任务">
      <div className="proc-new-conversation-head flex items-start gap-4">
        <span className="w-11 h-11 rounded-xl bg-accent-soft text-accent flex items-center justify-center flex-shrink-0 shadow-2xs">
          <Bot size={22} />
        </span>
        <div className="flex flex-col gap-1 min-w-0">
          <h1 className="text-lg sm:text-xl font-bold text-text tracking-tight">新建采购询价与比价任务</h1>
          <p className="text-xs text-text-muted leading-relaxed">
            AI 自动提取采购需求与报价字段，Java 确定性规则引擎负责供应商资格检查、税费到货核算与智能比价。
          </p>
        </div>
      </div>

      <div className="proc-prompt-suggestions flex flex-wrap items-center gap-2 text-xs" aria-label="快速模板提示词">
        <span className="proc-prompt-suggestion-title inline-flex items-center gap-1 font-semibold text-text-muted text-xs">
          <Sparkles size={13} className="text-accent" />常用模板：
        </span>
        {PROMPT_SUGGESTIONS.map((tpl, i) => (
          <button
            key={i}
            type="button"
            className="proc-prompt-chip px-2.5 py-1 rounded-lg bg-surface-subtle hover:bg-accent-soft text-text-secondary hover:text-accent border border-border hover:border-accent/30 text-xs transition-all font-medium"
            disabled={busy}
            onClick={() => setMessage(tpl)}
          >
            {tpl.split("：")[0]}
          </button>
        ))}
      </div>

      <form className="proc-conversation-composer new rounded-xl border border-border bg-surface focus-within:border-accent focus-within:ring-2 focus-within:ring-accent-soft overflow-hidden transition-all shadow-2xs" onSubmit={(event) => void submit(event)}>
        <textarea
          aria-label="采购目标"
          className="w-full p-4 text-xs sm:text-sm text-text bg-transparent border-0 resize-none min-h-[96px] max-h-[220px] outline-none placeholder:text-text-muted/60 leading-relaxed"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !busy && message.trim() && files.length >= 2) {
              event.preventDefault();
              void submit(event);
            }
          }}
          placeholder="描述物料名称、采购数量、规格要求、最长交期、预算要求等（可点击上方模板快速填充，Ctrl+Enter 发起）"
          maxLength={20_000}
          disabled={busy}
        />
        <label
          className={`proc-quote-dropzone flex items-center gap-3 m-3 p-3.5 rounded-xl border-1.5 border-dashed cursor-pointer transition-all ${dragging ? "border-accent bg-accent-soft/30 text-accent" : "border-border hover:border-accent/60 bg-surface-subtle/70 text-text-secondary"}`}
          onDragEnter={(event: DragEvent<HTMLLabelElement>) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event: DragEvent<HTMLLabelElement>) => event.preventDefault()}
          onDragLeave={(event: DragEvent<HTMLLabelElement>) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={(event: DragEvent<HTMLLabelElement>) => {
            event.preventDefault();
            setDragging(false);
            addFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <div className="w-8 h-8 rounded-lg bg-surface flex items-center justify-center text-accent flex-shrink-0 shadow-2xs">
            <UploadCloud size={18} />
          </div>
          <span className="flex flex-col gap-0.5 min-w-0 flex-1">
            <strong className="text-xs font-semibold text-text truncate">拖拽供应商报价到这里，或点击选择文件</strong>
            <small className="text-[11px] text-text-muted truncate">支持 XLSX、PDF 格式 · 2–{maxQuotes} 家供应商 · 单文件上限 {formatBytes(maxFileBytes)}</small>
          </span>
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
        {files.length ? (
          <ul className="proc-compose-files flex flex-wrap gap-2 px-3 pb-3 list-none m-0">
            {files.map((file, index) => (
              <li key={`${file.name}-${file.size}`} className="inline-flex items-center gap-2 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border text-xs text-text">
                <span className="text-accent">{fileIcon(file.name)}</span>
                <span className="font-medium truncate max-w-[180px]">{file.name}</span>
                <small className="text-text-muted font-mono">{formatBytes(file.size)}</small>
                <button
                  type="button"
                  title="移除附件"
                  aria-label={`移除 ${file.name}`}
                  className="text-text-muted hover:text-danger p-0.5 rounded transition-colors"
                  disabled={busy}
                  onClick={() => setFiles((current) => current.filter((_, item) => item !== index))}
                >
                  <X size={13} />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="proc-compose-actions flex items-center justify-between gap-3 px-3.5 py-2.5 bg-surface-subtle/60 border-t border-border/60 text-xs">
          <div className="flex items-center gap-2 text-text-muted">
            <Paperclip size={13} />
            <span>已选报价：</span>
            <span className={`font-mono font-bold px-2 py-0.5 rounded-full text-xs ${files.length >= 2 ? "bg-accent-soft text-accent" : "bg-surface text-text-muted border border-border"}`}>
              {files.length} / {maxQuotes} 份
            </span>
            {files.length < 2 ? (
              <span className="text-[11px] text-text-muted/70 hidden sm:inline">（至少上传 2 家报价才能比价）</span>
            ) : null}
          </div>
          <button
            className="proc-button primary inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-strong disabled:opacity-50 shadow-xs transition-colors"
            type="submit"
            disabled={busy || !message.trim() || files.length < 2}
          >
            {busy ? <LoaderCircle className="spin" size={14} /> : <Send size={14} />}
            <span>{busy ? "正在解析报价" : "开始解析报价"}</span>
          </button>
        </div>
        {localError || error ? (
          <p className="proc-compose-error m-0 p-2.5 bg-danger-soft text-danger text-xs border-t border-danger/20 flex items-center gap-1.5" role="alert">
            <AlertTriangle size={14} />
            {localError || error}
          </p>
        ) : null}
      </form>
    </section>
  );
}

type ConversationProps = {
  request: ProcurementRequest;
  structuredInteractionActive?: boolean;
  actionError?: string | null;
  onResume: (message: string) => Promise<void>;
  onRecover: () => Promise<void>;
  onOpenComparison: () => void;
};

export function ProcurementConversation({
  request,
  structuredInteractionActive = false,
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
  const needsClarification = status === "require_human" && !structuredInteractionActive && !request.comparison && !request.decision;
  const canRecover = status === "failed"
    || status === "cancelled"
    || status === "interrupted"
    || (status === "require_human" && !structuredInteractionActive && !request.comparison && !request.decision);
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
          <textarea
            aria-label="补充澄清信息"
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !sending && reply.trim()) {
                event.preventDefault();
                void submitReply(event);
              }
            }}
            placeholder="补充 Agent 请求的信息（Enter 发送，Shift+Enter 换行）"
            maxLength={20_000}
            disabled={sending}
          />
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
